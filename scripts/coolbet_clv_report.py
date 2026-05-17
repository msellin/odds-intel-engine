"""
COOLBET-CLV-REPORT — executable CLV on real_bets placed at Coolbet.

Measures: (coolbet_actual_odds / pinnacle_closing_odds) - 1
        = "how much better than the sharp closing line was your Coolbet price?"

Positive = you got a better-than-closing price at Coolbet (skill).
Negative = you took a worse price than the sharp line settled at (no edge).

Why this metric matters:
  Our paper-trading CLV (in simulated_bets.clv) compares our odds_at_pick to
  the latest snapshot from any AF book — useful for validating the model
  itself, but NOT what you actually executed. You bet at Coolbet, not at
  Bet365 or Pinnacle. This report uses Coolbet's actual price (stored on
  real_bets.actual_odds) and compares to Pinnacle's closing line — the gold
  standard sharp reference. The result tells you whether your real placements
  are beating the market.

Run: python scripts/coolbet_clv_report.py
     python scripts/coolbet_clv_report.py --days 30   (last 30 days only)
     python scripts/coolbet_clv_report.py --csv       (dump per-bet detail)
"""

import argparse
import csv
import sys
from pathlib import Path
from statistics import mean, median

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _normalize_bet_market,
    _normalize_bet_selection,
)


def fetch_coolbet_bets(days: int | None):
    """Settled real_bets at Coolbet with their match + bot context."""
    where_days = "AND rb.placed_at >= NOW() - INTERVAL '%s days'" % days if days else ""
    sql = f"""
        SELECT rb.id, rb.match_id::text AS match_id, rb.market, rb.selection,
               rb.actual_odds, rb.captured_odds, rb.stake, rb.result, rb.pnl,
               rb.placed_at, b.name AS bot,
               m.date AS kickoff, l.name AS league, l.tier
        FROM real_bets rb
        JOIN matches m ON m.id = rb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        LEFT JOIN bots b ON b.id = rb.bot_id
        WHERE rb.bookmaker = 'Coolbet'
          AND rb.result IN ('won','lost','void','half_won','half_lost')
          {where_days}
        ORDER BY rb.placed_at
    """
    return execute_query(sql, []) or []


def pinnacle_closing(match_id: str, market: str, selection: str) -> float | None:
    """Closing Pinnacle odds for (match, market, selection). Falls back to
    latest Pinnacle snapshot if is_closing isn't marked."""
    rows = execute_query(
        """SELECT odds FROM odds_snapshots
           WHERE match_id = %s AND market = %s AND selection = %s
             AND bookmaker = 'Pinnacle' AND is_closing = TRUE
           ORDER BY timestamp DESC LIMIT 1""",
        [match_id, market, selection],
    )
    if rows:
        return float(rows[0]["odds"])
    rows = execute_query(
        """SELECT odds FROM odds_snapshots
           WHERE match_id = %s AND market = %s AND selection = %s
             AND bookmaker = 'Pinnacle'
           ORDER BY timestamp DESC LIMIT 1""",
        [match_id, market, selection],
    )
    return float(rows[0]["odds"]) if rows else None


def compute_clv_rows(bets):
    """For each bet, compute executable CLV vs Pinnacle close. Drops bets where
    Pinnacle didn't quote the market (no fair reference)."""
    rows = []
    for b in bets:
        norm_market = _normalize_bet_market(b["market"])
        norm_sel = _normalize_bet_selection(b["selection"])
        pin = pinnacle_closing(b["match_id"], norm_market, norm_sel)
        coolbet = float(b["actual_odds"])
        if pin and pin > 1.0:
            clv = (coolbet / pin) - 1.0
        else:
            clv = None
        rows.append({
            **b,
            "norm_market": norm_market,
            "norm_selection": norm_sel,
            "pinnacle_close": pin,
            "coolbet_odds": coolbet,
            "clv": clv,
        })
    return rows


def fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    return f"{x * 100:+.2f}%"


def section(title: str, rows: list[dict], by_key: str, sort_desc: bool = False):
    """Group `rows` by `by_key` and print mean / median / N CLV."""
    if not rows:
        return
    print(f"\n— {title} —")
    buckets: dict[str, list[float]] = {}
    for r in rows:
        if r["clv"] is None:
            continue
        k = str(r.get(by_key) or "(unknown)")
        buckets.setdefault(k, []).append(r["clv"])
    items = sorted(
        buckets.items(),
        key=lambda kv: mean(kv[1]) if sort_desc else kv[0],
        reverse=sort_desc,
    )
    print(f"  {'bucket':<28} {'n':>5} {'mean':>10} {'median':>10}")
    for k, vals in items:
        print(f"  {k:<28} {len(vals):>5d} {fmt_pct(mean(vals)):>10} {fmt_pct(median(vals)):>10}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=None, help="Limit to last N days (default: all)")
    p.add_argument("--csv", action="store_true", help="Dump per-bet CSV to stdout instead of report")
    args = p.parse_args()

    bets = fetch_coolbet_bets(args.days)
    if not bets:
        print("No settled Coolbet bets found.")
        return

    rows = compute_clv_rows(bets)
    matched = [r for r in rows if r["clv"] is not None]
    unmatched = [r for r in rows if r["clv"] is None]

    if args.csv:
        w = csv.DictWriter(sys.stdout, fieldnames=[
            "placed_at", "bot", "league", "tier", "market", "selection",
            "coolbet_odds", "pinnacle_close", "clv", "result", "pnl",
        ])
        w.writeheader()
        for r in rows:
            w.writerow({
                "placed_at": r["placed_at"].isoformat() if r["placed_at"] else "",
                "bot": r["bot"] or "",
                "league": r["league"] or "",
                "tier": r["tier"] or "",
                "market": r["norm_market"],
                "selection": r["norm_selection"],
                "coolbet_odds": r["coolbet_odds"],
                "pinnacle_close": r["pinnacle_close"] or "",
                "clv": r["clv"] if r["clv"] is not None else "",
                "result": r["result"],
                "pnl": r["pnl"] if r["pnl"] is not None else "",
            })
        return

    days_label = f"last {args.days} days" if args.days else "all-time"
    print(f"╔══ Executable CLV — Coolbet bets ({days_label}) ══╗")
    print(f"  Total settled bets:    {len(rows)}")
    print(f"  Pinnacle-matched:      {len(matched)}  ({len(matched)/len(rows)*100:.0f}%)")
    print(f"  No Pinnacle ref:       {len(unmatched)}")

    if not matched:
        print("\nNo bets with Pinnacle reference odds — can't compute executable CLV.")
        return

    clvs = [r["clv"] for r in matched]
    positive = [c for c in clvs if c > 0]
    print()
    print(f"  Mean executable CLV:   {fmt_pct(mean(clvs))}")
    print(f"  Median executable CLV: {fmt_pct(median(clvs))}")
    print(f"  Positive-CLV bets:     {len(positive)} / {len(matched)}  ({len(positive)/len(matched)*100:.0f}%)")

    # Tied to win/loss outcome — does positive CLV correlate with wins?
    won_matched = [r for r in matched if r["result"] == "won"]
    lost_matched = [r for r in matched if r["result"] == "lost"]
    if won_matched and lost_matched:
        print(f"  Mean CLV on wins:      {fmt_pct(mean([r['clv'] for r in won_matched]))}  (n={len(won_matched)})")
        print(f"  Mean CLV on losses:    {fmt_pct(mean([r['clv'] for r in lost_matched]))}  (n={len(lost_matched)})")

    section("By market", matched, "norm_market", sort_desc=True)
    section("By selection", matched, "norm_selection", sort_desc=True)
    section("By tier", matched, "tier", sort_desc=True)
    section("By bot", matched, "bot", sort_desc=True)

    print()
    print(f"╚══ Notes ══╝")
    print("  • Positive mean CLV = you're systematically beating Pinnacle's close at Coolbet.")
    print("  • >5% mean CLV is the standard threshold for 'sharp' real-money play.")
    print("  • Unmatched bets are markets Pinnacle didn't quote (often AH quarter lines or rare leagues).")


if __name__ == "__main__":
    main()
