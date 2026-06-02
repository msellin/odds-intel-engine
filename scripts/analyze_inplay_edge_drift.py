"""
INPLAY-EDGE-DRIFT — does the live edge on a prematch pick move favorably
in the first 5-10-15-20 minutes after kickoff, before anything has happened?

Hypothesis: books re-quote conservatively in early minutes (long open-play
sequences, no shots, no goals yet) so prematch picks that already had +EV
may show even higher EV at +5/10/15/20 min. If true: a delayed inplay
placer that re-checks the edge N minutes in and places at the live price
could outperform the at-pick placement, especially on side markets like
OU and BTTS.

What the script does:
  1. Pull settled prematch picks from the last N days (default 30) for
     placeable markets (1x2, btts, over_under_*, draw_no_bet, double_chance,
     asian_handicap). Excludes inplay-bot picks (name LIKE 'inplay_%').
  2. For each pick: query odds_snapshots for the same
     (match_id, market, selection, bookmaker) within +/-2 min windows
     around kickoff +5/+10/+15/+20 minutes. Bookmaker priority:
     Coolbet > Unibet > Bet365 > Pinnacle.
  3. Compute edge at each window using calibrated_prob (fallback:
     model_probability) − 1/live_odds.
  4. Aggregate by market + minute window:
       - n bets matched
       - median pick edge / median live edge
       - % rows where live edge > pick edge ("drift positive")
       - actual ROI of the pick (won/lost outcome from simulated_bets)
       - synthetic ROI as if we had skipped+replayed at the live edge
         using the same selection (won/lost stays the same, stake
         re-scaled proportionally to whether live edge ≥ 0)

Output:
  - Console table per (market, window) cell
  - CSV at dev/active/inplay-edge-drift-{N}d.csv with the per-bet rows
    so we can post-process or chart externally.

Usage:
  python scripts/analyze_inplay_edge_drift.py
  python scripts/analyze_inplay_edge_drift.py --days 60
  python scripts/analyze_inplay_edge_drift.py --markets 1x2 o/u
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import get_conn  # noqa: E402


REPO_ROOT = Path(__file__).parent.parent
OUT_DIR = REPO_ROOT / "dev" / "active"

# Minute windows (after kickoff) to inspect. Each window pulls the
# snapshot nearest to the target minute within +/-2 min.
WINDOWS_MIN = (5, 10, 15, 20)

# Bookmaker preference order for picking which live odds to use as the
# "in-play price". Coolbet first because that's where we'd actually
# place. Unibet shares the Kambi backend so it's a reasonable proxy
# when Coolbet's snapshot ingest missed the event.
BOOKMAKER_PRIORITY = ("Coolbet", "Unibet", "Bet365", "Pinnacle")


# ─── Map paper-bet (market, selection) to odds_snapshots key ────────────────
def _snap_key(market: str, selection: str) -> Optional[tuple[str, str, Optional[float]]]:
    """Return (snapshot_market, snapshot_selection, handicap_line) or None
    when the paper-bet shape can't be mapped (e.g. combo)."""
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
        # selection looks like "home -1" or "away +0.5" — parse number
        parts = s.split()
        if len(parts) == 2 and parts[0] in ("home", "away"):
            try:
                hl = float(parts[1])
                return ("asian_handicap", parts[0], hl)
            except ValueError:
                pass
    return None


# ─── Pull candidate bets ────────────────────────────────────────────────────
def fetch_settled_prematch_bets(conn, days: int, markets: list[str] | None):
    """Settled (won/lost/void) prematch bets from the last N days. Skips
    inplay-bot picks and combo rows."""
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
              sb.odds_at_pick,
              sb.model_probability,
              sb.calibrated_prob,
              sb.edge_percent,
              sb.stake, sb.pnl, sb.result,
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


# ─── Snapshot lookup ────────────────────────────────────────────────────────
def fetch_snapshots_for_window(
    conn, match_id: str, market: str, selection: str,
    handicap_line: Optional[float], kickoff, window_min: int, slack_min: int = 2,
) -> dict[str, float]:
    """Return {bookmaker: odds} for the (match, market, selection, line)
    using the snapshot whose timestamp is closest to kickoff + window_min,
    within +/- slack_min minutes."""
    from datetime import timedelta
    target = kickoff + timedelta(minutes=window_min)
    lo = target - timedelta(minutes=slack_min)
    hi = target + timedelta(minutes=slack_min)
    with conn.cursor() as cur:
        if market == "asian_handicap" and handicap_line is not None:
            cur.execute(
                """
                SELECT DISTINCT ON (bookmaker)
                  bookmaker, odds, ABS(EXTRACT(EPOCH FROM (timestamp - %s))) AS dist
                FROM odds_snapshots
                WHERE match_id = %s
                  AND market = 'asian_handicap'
                  AND selection = %s
                  AND handicap_line = %s
                  AND timestamp BETWEEN %s AND %s
                  AND bookmaker = ANY(%s)
                ORDER BY bookmaker, dist ASC
                """,
                (target, match_id, selection, handicap_line, lo, hi, list(BOOKMAKER_PRIORITY)),
            )
        else:
            cur.execute(
                """
                SELECT DISTINCT ON (bookmaker)
                  bookmaker, odds, ABS(EXTRACT(EPOCH FROM (timestamp - %s))) AS dist
                FROM odds_snapshots
                WHERE match_id = %s
                  AND market = %s
                  AND selection = %s
                  AND timestamp BETWEEN %s AND %s
                  AND bookmaker = ANY(%s)
                ORDER BY bookmaker, dist ASC
                """,
                (target, match_id, market, selection, lo, hi, list(BOOKMAKER_PRIORITY)),
            )
        return {bm: float(o) for bm, o, _ in cur.fetchall()}


def pick_live_odds(snapshots: dict[str, float]) -> tuple[str, float] | None:
    for bm in BOOKMAKER_PRIORITY:
        if bm in snapshots:
            return bm, snapshots[bm]
    return None


# ─── Per-bet enrichment ─────────────────────────────────────────────────────
def enrich(conn, bets: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, b in enumerate(bets):
        if i and i % 100 == 0:
            print(f"  … processed {i}/{len(bets)}", file=sys.stderr)
        mapped = _snap_key(b["market"], b["selection"])
        if mapped is None:
            continue
        snap_market, snap_sel, hl = mapped
        model_prob = b["calibrated_prob"] or b["model_probability"]
        if model_prob is None or model_prob <= 0:
            continue
        try:
            pick_edge = float(model_prob) - 1.0 / float(b["odds_at_pick"])
        except (TypeError, ZeroDivisionError):
            continue
        row = {
            "bet_id": b["id"],
            "match_id": b["match_id"],
            "market": b["market"],
            "selection": b["selection"],
            "bot": b["bot_name"],
            "odds_at_pick": float(b["odds_at_pick"]),
            "model_prob": float(model_prob),
            "pick_edge": pick_edge,
            "stake": float(b["stake"]),
            "pnl": float(b["pnl"]),
            "result": b["result"],
            "kickoff": b["kickoff"].isoformat() if b["kickoff"] else None,
        }
        for w in WINDOWS_MIN:
            snaps = fetch_snapshots_for_window(
                conn, b["match_id"], snap_market, snap_sel, hl, b["kickoff"], w,
            )
            picked = pick_live_odds(snaps)
            if picked is None:
                row[f"live_book_{w}"] = None
                row[f"live_odds_{w}"] = None
                row[f"live_edge_{w}"] = None
                row[f"drift_{w}"] = None
            else:
                bm, odds = picked
                live_edge = float(model_prob) - 1.0 / odds
                row[f"live_book_{w}"] = bm
                row[f"live_odds_{w}"] = odds
                row[f"live_edge_{w}"] = live_edge
                row[f"drift_{w}"] = live_edge - pick_edge
        out.append(row)
    return out


# ─── Aggregation ────────────────────────────────────────────────────────────
def aggregate(rows: list[dict]) -> dict:
    """Build per-(market, window) summary rows."""
    out: dict[tuple[str, int], dict] = {}
    by_market = defaultdict(list)
    for r in rows:
        by_market[r["market"]].append(r)
    by_market["__all__"] = rows  # type: ignore[index]
    for market, market_rows in by_market.items():
        for w in WINDOWS_MIN:
            matched = [r for r in market_rows if r.get(f"live_edge_{w}") is not None]
            if not matched:
                continue
            pick_edges = [r["pick_edge"] for r in matched]
            live_edges = [r[f"live_edge_{w}"] for r in matched]
            drifts = [r[f"drift_{w}"] for r in matched]
            n_pos_drift = sum(1 for d in drifts if d > 0)
            n_higher_edge = sum(1 for r in matched if r[f"live_edge_{w}"] > r["pick_edge"])
            wins = sum(1 for r in matched if r["result"] == "won")
            total_stake = sum(r["stake"] for r in matched)
            total_pnl = sum(r["pnl"] for r in matched)
            # Synthetic ROI: pretend we placed only when live_edge > 0; otherwise stake=0.
            # Same outcome (the match still finished the same way). Approximate the
            # "would have placed at live price" PnL as (odds - 1) * stake for wins, -stake for losses,
            # 0 for void. Stake fixed at 1 unit for the synthetic counterfactual.
            syn_stake = 0.0
            syn_pnl = 0.0
            for r in matched:
                if r[f"live_edge_{w}"] is None or r[f"live_edge_{w}"] <= 0:
                    continue
                live_odds = r[f"live_odds_{w}"]
                syn_stake += 1.0
                if r["result"] == "won":
                    syn_pnl += live_odds - 1.0
                else:
                    syn_pnl += -1.0
            out[(market, w)] = {
                "n": len(matched),
                "median_pick_edge": median(pick_edges),
                "median_live_edge": median(live_edges),
                "median_drift_pp": median(drifts) * 100,
                "pct_positive_drift": 100.0 * n_pos_drift / len(matched),
                "pct_live_better": 100.0 * n_higher_edge / len(matched),
                "win_rate": 100.0 * wins / len(matched),
                "actual_roi": 100.0 * total_pnl / total_stake if total_stake else 0.0,
                "synthetic_n": int(syn_stake),
                "synthetic_roi": 100.0 * syn_pnl / syn_stake if syn_stake else 0.0,
            }
    return out


# ─── Reporting ──────────────────────────────────────────────────────────────
def print_report(summary: dict, days: int) -> None:
    print()
    print(f"━━━ INPLAY EDGE-DRIFT REPORT · last {days} days ━━━")
    print()
    header = (
        f"{'market':<18} {'win':>4} {'n':>5} "
        f"{'pickEdge':>9} {'liveEdge':>9} {'drift_pp':>9} "
        f"{'%posDrft':>9} {'winRate':>8} "
        f"{'actROI':>8} {'synN':>5} {'synROI':>8}"
    )
    print(header)
    print("-" * len(header))
    # Order: __all__ first, then per-market sorted by name
    keys = sorted(summary.keys(), key=lambda k: (k[0] != "__all__", k[0], k[1]))
    prev_market = None
    for (market, w), s in [(k, summary[k]) for k in keys]:
        label = "ALL MARKETS" if market == "__all__" else market
        if label != prev_market:
            print()
            prev_market = label
        pe = s["median_pick_edge"] * 100
        le = s["median_live_edge"] * 100
        print(
            f"{label:<18} {w:>3}'  {s['n']:>5} "
            f"{pe:>+8.2f}% {le:>+8.2f}% {s['median_drift_pp']:>+8.2f} "
            f"{s['pct_positive_drift']:>8.1f}% {s['win_rate']:>7.1f}% "
            f"{s['actual_roi']:>+7.1f}% {s['synthetic_n']:>5} {s['synthetic_roi']:>+7.1f}%"
        )
    print()
    print("Legend:")
    print("  pickEdge   = median model_prob − 1/odds_at_pick (%)")
    print("  liveEdge   = median model_prob − 1/odds at kickoff+N min (%)")
    print("  drift_pp   = median (liveEdge − pickEdge) in percentage points")
    print("  %posDrft   = fraction of bets where live edge > pick edge")
    print("  actROI     = ROI of the actual placement (paper)")
    print("  synN/synROI= ROI of the counterfactual: only place when live edge > 0,")
    print("              flat 1u stake, settle at the live snapshot odds.")


# ─── Main ───────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30,
                    help="Lookback window in days (default 30)")
    ap.add_argument("--markets", nargs="+", default=None,
                    help="Filter to specific markets (paper-bet names: 1x2, btts, "
                         "o/u, double_chance, draw_no_bet, asian_handicap). "
                         "Default = all placeable markets.")
    ap.add_argument("--out", type=str, default=None,
                    help="CSV path for per-bet output (default: dev/active/inplay-edge-drift-Nd.csv)")
    args = ap.parse_args()

    csv_path = Path(args.out) if args.out else OUT_DIR / f"inplay-edge-drift-{args.days}d.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        print(f"Fetching settled prematch bets from the last {args.days} days…", file=sys.stderr)
        bets = fetch_settled_prematch_bets(conn, args.days, args.markets)
        print(f"  → {len(bets)} candidate rows", file=sys.stderr)
        if not bets:
            print("No bets found.", file=sys.stderr)
            return 1
        print(f"Enriching with odds_snapshots at +5/+10/+15/+20 min windows…", file=sys.stderr)
        rows = enrich(conn, bets)
        print(f"  → {len(rows)} rows after mapping", file=sys.stderr)
        if not rows:
            print("No rows could be mapped to odds_snapshots.", file=sys.stderr)
            return 1

    summary = aggregate(rows)
    print_report(summary, args.days)

    # Persist per-bet rows
    fields = list(rows[0].keys())
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nPer-bet CSV: {csv_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
