"""
PREMATCH-CLOSING-DRIFT — does our prematch pick's model edge widen or narrow
between pick time and kickoff?

This is a CLV decomposition over time. We already track per-bet CLV (pick
odds vs closing odds), but we don't break it into intervals. If the edge
*widens* between T-6h, T-2h, T-30m, T-0, the market is moving toward us
(sharp signal). If it narrows, the line shopped against us.

What the script does:
  1. Pull settled prematch picks from last N days (default 30) for placeable
     markets. Same query / filters as analyze_inplay_edge_drift.py.
  2. For each pick, query odds_snapshots for the same (match_id, market,
     selection, line) at four pre-kickoff anchors: T-6h, T-2h, T-30m, T-0
     (each with +/- 10 min slack — these are sparser intervals than the
     inplay 30s ticks, so the slack is wider).
  3. Compute edge at each anchor = (calibrated_prob OR model_probability)
     − 1/snapshot_odds. Compare to pick edge.
  4. Aggregate per (market, anchor):
       - n, median pick edge, median anchor edge
       - drift_pp = anchor_edge − pick_edge
       - % rows where edge widened by this point
       - Actual ROI of the placement
       - ROI bucketed by drift sign at T-0 (closing line) — sharpest test
         of "did we beat the close?"

  Output:
    - Console table
    - Per-bet CSV at dev/active/prematch-closing-drift-Nd.csv

Usage:
  python scripts/analyze_prematch_closing_drift.py
  python scripts/analyze_prematch_closing_drift.py --days 60
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import get_conn  # noqa: E402


REPO_ROOT = Path(__file__).parent.parent
OUT_DIR = REPO_ROOT / "dev" / "active"

# Pre-kickoff anchors expressed in minutes BEFORE kickoff. Slack widens
# with the interval because snapshot density falls off the further out
# from kickoff (most odds polling happens in the last few hours).
ANCHORS: tuple[tuple[str, int, int], ...] = (
    ("T-6h",   360, 15),
    ("T-2h",   120, 10),
    ("T-30m",   30,  7),
    ("T-0",      0,  5),
)

DEFAULT_BOOKMAKERS = ("Coolbet", "Unibet", "Bet365", "Pinnacle")
# Mutable at module scope when --bookmaker is passed on the CLI. The
# snapshot query and pick_best() both read from this list, so passing
# `--bookmaker Pinnacle` turns the analysis into a strict CLV test
# against the sharp closing book.
ACTIVE_BOOKMAKERS: tuple[str, ...] = DEFAULT_BOOKMAKERS


def _snap_key(market: str, selection: str):
    m = (market or "").lower()
    s = (selection or "").lower().strip()
    if m == "1x2" and s in ("home", "draw", "away"):
        return ("1x2", s, None)
    if m == "btts" and s in ("yes", "no"):
        return ("btts", s, None)
    if m == "double_chance":
        dc = s.replace(" ", "")
        if dc in ("1x", "x2", "12"):
            return ("double_chance", dc, None)
    if m == "draw_no_bet" and s in ("home", "away"):
        return ("draw_no_bet", s, None)
    if m == "o/u":
        for line in ("0.5", "1.5", "2.5", "3.5", "4.5"):
            if s.startswith(f"over {line}"):
                return (f"over_under_{line.replace('.', '')}", "over", None)
            if s.startswith(f"under {line}"):
                return (f"over_under_{line.replace('.', '')}", "under", None)
    if m == "asian_handicap":
        parts = s.split()
        if len(parts) == 2 and parts[0] in ("home", "away"):
            try:
                return ("asian_handicap", parts[0], float(parts[1]))
            except ValueError:
                return None
    return None


def fetch_bets(conn, days: int, markets: list[str] | None):
    """Settled prematch bets from the last N days."""
    with conn.cursor() as cur:
        market_clause = ""
        params: list = [days]
        if markets:
            market_clause = "AND sb.market = ANY(%s)"
            params.append(markets)
        cur.execute(
            f"""
            SELECT
              sb.id, sb.match_id, sb.market, sb.selection,
              sb.odds_at_pick, sb.model_probability, sb.calibrated_prob,
              sb.stake, sb.pnl, sb.result,
              sb.pick_time,
              m.date AS kickoff,
              b.name AS bot_name
            FROM simulated_bets sb
            JOIN matches m ON m.id = sb.match_id
            JOIN bots b ON b.id = sb.bot_id
            WHERE sb.pick_time >= NOW() - (%s || ' days')::interval
              AND sb.result IN ('won', 'lost')
              AND sb.market != 'combo'
              AND b.name NOT LIKE 'inplay\\_%%'
              {market_clause}
            """,
            params,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_snap_at(conn, match_id, market, selection, hl, kickoff, mins_before, slack):
    target = kickoff - timedelta(minutes=mins_before)
    lo = target - timedelta(minutes=slack)
    hi = target + timedelta(minutes=slack)
    with conn.cursor() as cur:
        if market == "asian_handicap" and hl is not None:
            cur.execute(
                """
                SELECT DISTINCT ON (bookmaker)
                  bookmaker, odds
                FROM odds_snapshots
                WHERE match_id=%s AND market='asian_handicap'
                  AND selection=%s AND handicap_line=%s
                  AND timestamp BETWEEN %s AND %s
                  AND bookmaker = ANY(%s)
                ORDER BY bookmaker, ABS(EXTRACT(EPOCH FROM (timestamp - %s))) ASC
                """,
                (match_id, selection, hl, lo, hi, list(ACTIVE_BOOKMAKERS), target),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT ON (bookmaker)
                  bookmaker, odds
                FROM odds_snapshots
                WHERE match_id=%s AND market=%s AND selection=%s
                  AND timestamp BETWEEN %s AND %s
                  AND bookmaker = ANY(%s)
                ORDER BY bookmaker, ABS(EXTRACT(EPOCH FROM (timestamp - %s))) ASC
                """,
                (match_id, market, selection, lo, hi, list(ACTIVE_BOOKMAKERS), target),
            )
        return {bm: float(o) for bm, o in cur.fetchall()}


def pick_best(snaps: dict[str, float]) -> Optional[tuple[str, float]]:
    for bm in ACTIVE_BOOKMAKERS:
        if bm in snaps:
            return bm, snaps[bm]
    return None


def enrich(conn, bets):
    out = []
    for i, b in enumerate(bets):
        if i and i % 100 == 0:
            print(f"  … {i}/{len(bets)}", file=sys.stderr)
        mapped = _snap_key(b["market"], b["selection"])
        if mapped is None:
            continue
        snap_m, snap_s, hl = mapped
        prob = b["calibrated_prob"] or b["model_probability"]
        if prob is None or prob <= 0:
            continue
        try:
            pick_edge = float(prob) - 1.0 / float(b["odds_at_pick"])
        except (TypeError, ZeroDivisionError):
            continue
        row = {
            "bet_id": b["id"],
            "match_id": b["match_id"],
            "market": b["market"],
            "selection": b["selection"],
            "bot": b["bot_name"],
            "odds_at_pick": float(b["odds_at_pick"]),
            "model_prob": float(prob),
            "pick_edge": pick_edge,
            "stake": float(b["stake"]),
            "pnl": float(b["pnl"]),
            "result": b["result"],
            "pick_time": b["pick_time"].isoformat() if b["pick_time"] else None,
            "kickoff": b["kickoff"].isoformat() if b["kickoff"] else None,
        }
        for label, mins_before, slack in ANCHORS:
            snaps = fetch_snap_at(conn, b["match_id"], snap_m, snap_s, hl, b["kickoff"], mins_before, slack)
            best = pick_best(snaps)
            if best is None:
                row[f"odds_{label}"] = None
                row[f"edge_{label}"] = None
                row[f"drift_{label}"] = None
            else:
                bm, odds = best
                edge = float(prob) - 1.0 / odds
                row[f"odds_{label}"] = odds
                row[f"edge_{label}"] = edge
                row[f"drift_{label}"] = edge - pick_edge
        out.append(row)
    return out


def aggregate(rows):
    """Per (market, anchor) summary, plus ROI bucketed by closing-line drift sign."""
    summary: dict[tuple[str, str], dict] = {}
    by_market = defaultdict(list)
    for r in rows:
        by_market[r["market"]].append(r)
    by_market["__all__"] = rows  # type: ignore[index]
    for market, market_rows in by_market.items():
        for label, _, _ in ANCHORS:
            matched = [r for r in market_rows if r.get(f"edge_{label}") is not None]
            if not matched:
                continue
            pick_edges = [r["pick_edge"] for r in matched]
            anchor_edges = [r[f"edge_{label}"] for r in matched]
            drifts = [r[f"drift_{label}"] for r in matched]
            n_widened = sum(1 for d in drifts if d > 0)
            wins = sum(1 for r in matched if r["result"] == "won")
            total_stake = sum(r["stake"] for r in matched)
            total_pnl = sum(r["pnl"] for r in matched)
            summary[(market, label)] = {
                "n": len(matched),
                "median_pick_edge": median(pick_edges),
                "median_anchor_edge": median(anchor_edges),
                "median_drift_pp": median(drifts) * 100,
                "pct_widened": 100.0 * n_widened / len(matched),
                "win_rate": 100.0 * wins / len(matched),
                "roi": 100.0 * total_pnl / total_stake if total_stake else 0.0,
            }
    return summary


def closing_line_split(rows):
    """ROI split between picks whose edge WIDENED by close vs ones that NARROWED.
    A robust CLV proxy without needing the closing_odds field."""
    widened = [r for r in rows if r.get("drift_T-0") is not None and r["drift_T-0"] > 0]
    narrowed = [r for r in rows if r.get("drift_T-0") is not None and r["drift_T-0"] <= 0]

    def stats(bucket):
        if not bucket:
            return None
        wins = sum(1 for r in bucket if r["result"] == "won")
        s = sum(r["stake"] for r in bucket)
        p = sum(r["pnl"] for r in bucket)
        return {
            "n": len(bucket),
            "win_rate": 100.0 * wins / len(bucket),
            "roi": 100.0 * p / s if s else 0.0,
        }

    return {"widened": stats(widened), "narrowed": stats(narrowed)}


def print_report(summary, split, days):
    print()
    print(f"━━━ PREMATCH → CLOSING EDGE DRIFT · last {days} days ━━━")
    print()
    header = f"{'market':<18} {'anchor':<7} {'n':>5} {'pickEdge':>9} {'liveEdge':>9} {'drift_pp':>9} {'%widened':>9} {'winRate':>8} {'ROI':>8}"
    print(header)
    print("-" * len(header))
    keys = sorted(summary.keys(), key=lambda k: (k[0] != "__all__", k[0], [a[0] for a in ANCHORS].index(k[1])))
    prev_market = None
    for (market, anchor), s in [(k, summary[k]) for k in keys]:
        label = "ALL MARKETS" if market == "__all__" else market
        if label != prev_market:
            print()
            prev_market = label
        pe = s["median_pick_edge"] * 100
        ae = s["median_anchor_edge"] * 100
        print(
            f"{label:<18} {anchor:<7} {s['n']:>5} "
            f"{pe:>+8.2f}% {ae:>+8.2f}% {s['median_drift_pp']:>+8.2f} "
            f"{s['pct_widened']:>8.1f}% {s['win_rate']:>7.1f}% {s['roi']:>+7.1f}%"
        )

    print()
    print("Closing-line edge cohort split (T-0):")
    print("-" * 50)
    for cohort, st in split.items():
        if st is None:
            print(f"  {cohort:<10} — no data")
            continue
        print(f"  {cohort:<10} n={st['n']:>4}  winRate={st['win_rate']:>5.1f}%  ROI={st['roi']:>+6.1f}%")

    print()
    print("Legend:")
    print("  pickEdge   median model_prob − 1/odds_at_pick (%)")
    print("  liveEdge   median model_prob − 1/odds at the anchor (%)")
    print("  drift_pp   median change in pp from pick edge to anchor edge")
    print("  %widened   share of bets where edge > pick edge at the anchor")
    print("  cohort     split at the closing anchor (T-0)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--markets", nargs="+", default=None)
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument(
        "--bookmaker",
        nargs="+",
        default=None,
        help="Restrict snapshot lookup to specific bookmakers (default: Coolbet, "
             "Unibet, Bet365, Pinnacle priority). Passing 'Pinnacle' alone turns "
             "this into a strict CLV test against the sharp book."
    )
    args = ap.parse_args()

    global ACTIVE_BOOKMAKERS
    if args.bookmaker:
        ACTIVE_BOOKMAKERS = tuple(args.bookmaker)

    suffix = f"-{'_'.join(args.bookmaker).lower()}" if args.bookmaker else ""
    csv_path = (
        Path(args.out)
        if args.out
        else OUT_DIR / f"prematch-closing-drift-{args.days}d{suffix}.csv"
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        print(f"Fetching settled prematch bets (last {args.days}d)…", file=sys.stderr)
        bets = fetch_bets(conn, args.days, args.markets)
        print(f"  → {len(bets)} candidates", file=sys.stderr)
        if not bets:
            return 1
        print("Enriching with pre-kickoff snapshots at T-6h / T-2h / T-30m / T-0…", file=sys.stderr)
        rows = enrich(conn, bets)
        print(f"  → {len(rows)} rows after mapping", file=sys.stderr)
        if not rows:
            return 1

    summary = aggregate(rows)
    split = closing_line_split(rows)
    print_report(summary, split, args.days)

    fields = list(rows[0].keys())
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nPer-bet CSV: {csv_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
