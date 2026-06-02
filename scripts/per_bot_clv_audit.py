"""
PER-BOT-CLV-AUDIT — which bots actually beat the closing line?

The 30-day prematch->closing drift analysis showed our model loses 2-3pp
of edge by close on average, and the widened/narrowed cohort split was
both unprofitable. But the aggregate hides per-bot variation. This script
breaks the same dataset by bot to surface which bots are systematic CLV
losers (model edge inflated by book margin / loose calibration) vs which
ones might still hold up.

For each bot:
  - n picks with both pick odds and a T-0 (closing) snapshot
  - median pick edge
  - median closing edge (model_prob - 1/closing_odds)
  - median drift_pp (closing edge - pick edge)
  - % of picks where closing edge stayed > 0
  - % where edge widened
  - win rate
  - actual ROI (paper)
  - "beat-close ROI": ROI of just the bets where closing edge > 0
    (proxy for "what would ROI be if we filtered to picks the market
    confirmed by close")

Usage:
  python scripts/per_bot_clv_audit.py
  python scripts/per_bot_clv_audit.py --days 60 --bookmaker Pinnacle
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import get_conn  # noqa: E402


REPO_ROOT = Path(__file__).parent.parent
OUT_DIR = REPO_ROOT / "dev" / "active"

DEFAULT_BOOKMAKERS = ("Coolbet", "Unibet", "Bet365", "Pinnacle")
ACTIVE_BOOKMAKERS = DEFAULT_BOOKMAKERS

MIN_BETS_FOR_REPORT = 10


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


def fetch_bets(conn, days: int):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              sb.id, sb.match_id, sb.market, sb.selection,
              sb.odds_at_pick, sb.model_probability, sb.calibrated_prob,
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
            """,
            (days,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_close_snap(conn, match_id, market, selection, hl, kickoff, slack=5):
    """Closest snapshot to kickoff (T-0)."""
    lo = kickoff - timedelta(minutes=slack)
    hi = kickoff + timedelta(minutes=slack)
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
                (match_id, selection, hl, lo, hi, list(ACTIVE_BOOKMAKERS), kickoff),
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
                (match_id, market, selection, lo, hi, list(ACTIVE_BOOKMAKERS), kickoff),
            )
        return {bm: float(o) for bm, o in cur.fetchall()}


def pick_best(snaps):
    for bm in ACTIVE_BOOKMAKERS:
        if bm in snaps:
            return bm, snaps[bm]
    return None


def enrich(conn, bets):
    out = []
    for i, b in enumerate(bets):
        if i and i % 200 == 0:
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
        snaps = fetch_close_snap(conn, b["match_id"], snap_m, snap_s, hl, b["kickoff"])
        best = pick_best(snaps)
        if best is None:
            continue
        bm, odds = best
        close_edge = float(prob) - 1.0 / odds
        out.append({
            "bet_id": b["id"],
            "match_id": b["match_id"],
            "market": b["market"],
            "bot": b["bot_name"],
            "stake": float(b["stake"]),
            "pnl": float(b["pnl"]),
            "result": b["result"],
            "pick_edge": pick_edge,
            "close_edge": close_edge,
            "drift_pp": (close_edge - pick_edge) * 100,
            "close_book": bm,
        })
    return out


def per_bot_stats(rows):
    by_bot = defaultdict(list)
    for r in rows:
        by_bot[r["bot"]].append(r)
    out = {}
    for bot, bot_rows in by_bot.items():
        if len(bot_rows) < MIN_BETS_FOR_REPORT:
            continue
        wins = sum(1 for r in bot_rows if r["result"] == "won")
        total_stake = sum(r["stake"] for r in bot_rows)
        total_pnl = sum(r["pnl"] for r in bot_rows)
        n_widened = sum(1 for r in bot_rows if r["drift_pp"] > 0)
        n_close_pos = sum(1 for r in bot_rows if r["close_edge"] > 0)
        # Beat-close cohort: bets where closing edge stayed > 0
        confirmed = [r for r in bot_rows if r["close_edge"] > 0]
        conf_stake = sum(r["stake"] for r in confirmed)
        conf_pnl = sum(r["pnl"] for r in confirmed)
        conf_wins = sum(1 for r in confirmed if r["result"] == "won")
        out[bot] = {
            "n": len(bot_rows),
            "median_pick_edge": median(r["pick_edge"] for r in bot_rows) * 100,
            "median_close_edge": median(r["close_edge"] for r in bot_rows) * 100,
            "median_drift_pp": median(r["drift_pp"] for r in bot_rows),
            "pct_widened": 100.0 * n_widened / len(bot_rows),
            "pct_close_positive": 100.0 * n_close_pos / len(bot_rows),
            "win_rate": 100.0 * wins / len(bot_rows),
            "roi": 100.0 * total_pnl / total_stake if total_stake else 0.0,
            "confirmed_n": len(confirmed),
            "confirmed_win_rate": 100.0 * conf_wins / len(confirmed) if confirmed else 0.0,
            "confirmed_roi": 100.0 * conf_pnl / conf_stake if conf_stake else 0.0,
        }
    return out


def print_report(stats, days, bookmaker_label):
    print()
    print(f"━━━ PER-BOT CLV AUDIT · last {days} days · close vs {bookmaker_label} ━━━")
    print()
    header = (
        f"{'bot':<28} {'n':>5} {'pickEdge':>9} {'closeEdge':>10} {'drift_pp':>9} "
        f"{'%clsPos':>8} {'winRate':>8} {'ROI':>8}  "
        f"{'confN':>6} {'cfWR':>6} {'cfROI':>8}"
    )
    print(header)
    print("-" * len(header))
    # Sort by ROI ascending so worst CLV losers surface first
    keys = sorted(stats.keys(), key=lambda k: stats[k]["roi"])
    for bot in keys:
        s = stats[bot]
        print(
            f"{bot:<28} {s['n']:>5} {s['median_pick_edge']:>+8.2f}% {s['median_close_edge']:>+9.2f}% "
            f"{s['median_drift_pp']:>+8.2f} {s['pct_close_positive']:>7.1f}% "
            f"{s['win_rate']:>7.1f}% {s['roi']:>+7.1f}%  "
            f"{s['confirmed_n']:>6} {s['confirmed_win_rate']:>5.1f}% {s['confirmed_roi']:>+7.1f}%"
        )
    print()
    print("Legend:")
    print("  pickEdge    median model_prob − 1/odds_at_pick (%)")
    print("  closeEdge   median model_prob − 1/closing_odds (%)")
    print("  drift_pp    median (closeEdge − pickEdge) in pp — negative = market disagreed")
    print("  %clsPos     share of picks where closing edge stayed > 0")
    print("  confN/cfWR  filtered cohort: picks where closing edge confirmed > 0")
    print("  cfROI       ROI of the confirmed cohort — is this a viable filter?")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--bookmaker", nargs="+", default=None)
    args = ap.parse_args()

    global ACTIVE_BOOKMAKERS
    if args.bookmaker:
        ACTIVE_BOOKMAKERS = tuple(args.bookmaker)
    bm_label = ", ".join(ACTIVE_BOOKMAKERS)

    suffix = f"-{'_'.join(args.bookmaker).lower()}" if args.bookmaker else ""
    csv_path = OUT_DIR / f"per-bot-clv-{args.days}d{suffix}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        print(f"Fetching settled prematch bets (last {args.days}d)…", file=sys.stderr)
        bets = fetch_bets(conn, args.days)
        print(f"  → {len(bets)} candidates", file=sys.stderr)
        if not bets:
            return 1
        print("Enriching with T-0 close snapshots…", file=sys.stderr)
        rows = enrich(conn, bets)
        print(f"  → {len(rows)} rows matched", file=sys.stderr)
        if not rows:
            return 1

    stats = per_bot_stats(rows)
    print_report(stats, args.days, bm_label)

    fields = list(rows[0].keys())
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\nPer-bet CSV: {csv_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
