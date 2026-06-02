"""
INPLAY-PICK-FOLLOWUP — for inplay-bot picks, does the edge widen or narrow
in the +2/+5/+10/+15 minutes after the pick?

Same question as the prematch script but anchored to PICK TIME instead of
kickoff. This tests inplay-bot timing: if edge consistently improves after
the pick, the bot is firing too early; if it consistently erodes, the
bot is well-tuned (or worse, late). Helps decide whether a "wait N minutes
then re-evaluate" tweak makes sense for an inplay bot.

What the script does:
  1. Pull settled inplay-bot picks from the last N days. `b.name LIKE
     'inplay_%'` plus the xg_source-non-null sanity check the production
     bots use.
  2. For each pick: query odds_snapshots at pick_time + 2/5/10/15 min
     (+/- 2 min slack), bookmaker priority Coolbet > Unibet > Bet365 >
     Pinnacle.
  3. Compute edge at each window using calibrated_prob (fallback
     model_probability) − 1/snapshot_odds.
  4. Aggregate per (bot, window) and roll up ALL-INPLAY:
       - median pick edge / median window edge
       - drift_pp = window edge − pick edge
       - % rows where edge widened
       - actual ROI of the pick
       - synthetic ROI = if we had skipped placing and only fired when the
         window edge stayed positive (flat 1u stake, settled at the window
         snapshot odds)
  5. ROI split by drift sign at +5 min — sharpest single test of "did the
     line move with us or against us shortly after the pick".

Usage:
  python scripts/analyze_inplay_pick_followup_drift.py
  python scripts/analyze_inplay_pick_followup_drift.py --days 60
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

# Minutes after pick_time. Slack +/- 2 min. Inplay polling is 30s/60s in
# active periods so the windows should usually have hits.
WINDOWS_MIN = (2, 5, 10, 15)
BOOKMAKER_PRIORITY = ("Coolbet", "Unibet", "Bet365", "Pinnacle")


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


def fetch_inplay_bets(conn, days: int, markets: list[str] | None):
    """Settled inplay-bot picks."""
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
              AND b.name LIKE 'inplay\\_%%'
              {market_clause}
            """,
            params,
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_snap_at(conn, match_id, market, selection, hl, pick_time, mins_after, slack=2):
    target = pick_time + timedelta(minutes=mins_after)
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
                (match_id, selection, hl, lo, hi, list(BOOKMAKER_PRIORITY), target),
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
                (match_id, market, selection, lo, hi, list(BOOKMAKER_PRIORITY), target),
            )
        return {bm: float(o) for bm, o in cur.fetchall()}


def pick_best(snaps):
    for bm in BOOKMAKER_PRIORITY:
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
        for w in WINDOWS_MIN:
            snaps = fetch_snap_at(conn, b["match_id"], snap_m, snap_s, hl, b["pick_time"], w)
            best = pick_best(snaps)
            if best is None:
                row[f"odds_+{w}m"] = None
                row[f"edge_+{w}m"] = None
                row[f"drift_+{w}m"] = None
            else:
                bm, odds = best
                edge = float(prob) - 1.0 / odds
                row[f"odds_+{w}m"] = odds
                row[f"edge_+{w}m"] = edge
                row[f"drift_+{w}m"] = edge - pick_edge
        out.append(row)
    return out


def aggregate(rows):
    summary: dict[tuple[str, int], dict] = {}
    by_bot = defaultdict(list)
    for r in rows:
        by_bot[r["bot"]].append(r)
    by_bot["__all_inplay__"] = rows  # type: ignore[index]
    for bot, bot_rows in by_bot.items():
        for w in WINDOWS_MIN:
            matched = [r for r in bot_rows if r.get(f"edge_+{w}m") is not None]
            if not matched:
                continue
            pick_edges = [r["pick_edge"] for r in matched]
            win_edges = [r[f"edge_+{w}m"] for r in matched]
            drifts = [r[f"drift_+{w}m"] for r in matched]
            n_widened = sum(1 for d in drifts if d > 0)
            wins = sum(1 for r in matched if r["result"] == "won")
            total_stake = sum(r["stake"] for r in matched)
            total_pnl = sum(r["pnl"] for r in matched)
            # Synthetic ROI: place only if window edge > 0, flat 1u, settle at window odds
            syn_stake = 0.0
            syn_pnl = 0.0
            for r in matched:
                if r[f"edge_+{w}m"] is None or r[f"edge_+{w}m"] <= 0:
                    continue
                odds = r[f"odds_+{w}m"]
                syn_stake += 1.0
                if r["result"] == "won":
                    syn_pnl += odds - 1.0
                else:
                    syn_pnl += -1.0
            summary[(bot, w)] = {
                "n": len(matched),
                "median_pick_edge": median(pick_edges),
                "median_window_edge": median(win_edges),
                "median_drift_pp": median(drifts) * 100,
                "pct_widened": 100.0 * n_widened / len(matched),
                "win_rate": 100.0 * wins / len(matched),
                "actual_roi": 100.0 * total_pnl / total_stake if total_stake else 0.0,
                "syn_n": int(syn_stake),
                "syn_roi": 100.0 * syn_pnl / syn_stake if syn_stake else 0.0,
            }
    return summary


def drift_sign_split(rows, w=5):
    widened = [r for r in rows if r.get(f"drift_+{w}m") is not None and r[f"drift_+{w}m"] > 0]
    narrowed = [r for r in rows if r.get(f"drift_+{w}m") is not None and r[f"drift_+{w}m"] <= 0]

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
    return {f"widened_+{w}m": stats(widened), f"narrowed_+{w}m": stats(narrowed)}


def print_report(summary, split, days):
    print()
    print(f"━━━ INPLAY PICK → +N MIN FOLLOW-UP · last {days} days ━━━")
    print()
    header = (
        f"{'bot':<22} {'win':>4} {'n':>5} "
        f"{'pickEdge':>9} {'liveEdge':>9} {'drift_pp':>9} "
        f"{'%widened':>9} {'winRate':>8} {'actROI':>8} {'synN':>5} {'synROI':>8}"
    )
    print(header)
    print("-" * len(header))
    keys = sorted(summary.keys(), key=lambda k: (k[0] != "__all_inplay__", k[0], k[1]))
    prev_bot = None
    for (bot, w), s in [(k, summary[k]) for k in keys]:
        label = "ALL INPLAY" if bot == "__all_inplay__" else bot
        if label != prev_bot:
            print()
            prev_bot = label
        pe = s["median_pick_edge"] * 100
        le = s["median_window_edge"] * 100
        print(
            f"{label:<22} +{w:>2}m  {s['n']:>5} "
            f"{pe:>+8.2f}% {le:>+8.2f}% {s['median_drift_pp']:>+8.2f} "
            f"{s['pct_widened']:>8.1f}% {s['win_rate']:>7.1f}% "
            f"{s['actual_roi']:>+7.1f}% {s['syn_n']:>5} {s['syn_roi']:>+7.1f}%"
        )

    print()
    print("Drift-sign cohort split at +5m:")
    print("-" * 50)
    for cohort, st in split.items():
        if st is None:
            print(f"  {cohort:<14} — no data")
            continue
        print(f"  {cohort:<14} n={st['n']:>4}  winRate={st['win_rate']:>5.1f}%  ROI={st['roi']:>+6.1f}%")

    print()
    print("Legend:")
    print("  pickEdge   median model_prob − 1/odds_at_pick (%)")
    print("  liveEdge   median model_prob − 1/odds at pick_time + N min (%)")
    print("  drift_pp   median (window edge − pick edge) in pp")
    print("  %widened   share of bets where window edge > pick edge")
    print("  synN/ROI   place only when window edge > 0, flat 1u at window odds")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--markets", nargs="+", default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()

    csv_path = Path(args.out) if args.out else OUT_DIR / f"inplay-pick-followup-{args.days}d.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        print(f"Fetching settled inplay-bot picks (last {args.days}d)…", file=sys.stderr)
        bets = fetch_inplay_bets(conn, args.days, args.markets)
        print(f"  → {len(bets)} candidates", file=sys.stderr)
        if not bets:
            print("No inplay picks found.", file=sys.stderr)
            return 1
        print("Enriching with snapshots at +2/+5/+10/+15 min after pick…", file=sys.stderr)
        rows = enrich(conn, bets)
        print(f"  → {len(rows)} rows after mapping", file=sys.stderr)
        if not rows:
            return 1

    summary = aggregate(rows)
    split = drift_sign_split(rows, w=5)
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
