"""
Quick rolling-window ROI snapshot for the pre-match cohort that powers
the head-to-head hero (calibrated + beta + active, inplay excluded,
1X2 + OU 2.5).

Windows: last 7 / 15 / 30 / 60 / 90 days.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402

TODAY = date.today()


def window_stats(days: int) -> dict:
    start = (TODAY - timedelta(days=days)).isoformat()
    rows = execute_query(
        """
        SELECT
          COUNT(*) AS n,
          SUM(sb.pnl)::float AS pnl,
          SUM(sb.stake)::float AS stake,
          COUNT(*) FILTER (WHERE sb.result::text = 'won') AS won,
          AVG(sb.odds_at_pick)::float AS avg_odds
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.created_at >= %s::date
          AND sb.result::text IN ('won','lost')
          AND sb.market IN ('1x2','over_under_25','o/u')
          AND b.maturity_label IN ('calibrated','beta','active')
          AND b.name NOT LIKE 'inplay_%%'
        """,
        (start,),
    )
    r = rows[0]
    n = int(r["n"] or 0)
    pnl = float(r["pnl"] or 0)
    stake = float(r["stake"] or 0)
    won = int(r["won"] or 0)
    return {
        "days": days,
        "start": start,
        "end": TODAY.isoformat(),
        "n": n,
        "won": won,
        "hit_rate_pct": round(100 * won / n, 2) if n else 0,
        "avg_odds": round(float(r["avg_odds"] or 0), 3),
        "pnl_total": round(pnl, 2),
        "stake_total": round(stake, 2),
        "roi_pct": round(100 * pnl / stake, 2) if stake else 0,
    }


def main() -> int:
    print(f"Rolling-window ROI · pre-match cohort · calibrated+beta+active · "
          f"1X2 + OU 2.5 · as of {TODAY}")
    print()
    print(f"{'window':>8} {'from':>12}    {'n':>4}  {'hit':>6}  {'avg_odds':>8}  "
          f"{'stake':>9}  {'pnl':>10}  {'ROI':>7}")
    print("-" * 82)
    for d in (7, 15, 30, 60, 90):
        s = window_stats(d)
        sign = "+" if s["roi_pct"] >= 0 else ""
        print(f"{'last '+str(d)+'d':>8} {s['start']:>12}    {s['n']:>4}  "
              f"{s['hit_rate_pct']:>5.2f}%  {s['avg_odds']:>8.3f}  "
              f"{s['stake_total']:>9.2f}  {s['pnl_total']:>+10.2f}  "
              f"{sign}{s['roi_pct']:>5.2f}%")
    print()
    # Per-market breakdown on last 30d
    print("Last 30d — by market:")
    rows = execute_query(
        """
        SELECT sb.market,
               COUNT(*) AS n,
               SUM(sb.pnl)::float AS pnl,
               SUM(sb.stake)::float AS stake,
               COUNT(*) FILTER (WHERE sb.result::text='won') AS won
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.created_at >= (CURRENT_DATE - INTERVAL '30 days')
          AND sb.result::text IN ('won','lost')
          AND sb.market IN ('1x2','over_under_25','o/u')
          AND b.maturity_label IN ('calibrated','beta','active')
          AND b.name NOT LIKE 'inplay_%%'
        GROUP BY sb.market
        ORDER BY sb.market
        """
    )
    for r in rows:
        n = int(r["n"]); pnl = float(r["pnl"] or 0); stake = float(r["stake"] or 0)
        won = int(r["won"])
        roi = 100 * pnl / stake if stake else 0
        print(f"  {r['market']:>16s}  n={n:>4}  hit={100*won/n:>5.2f}%  "
              f"stake={stake:>8.2f}  pnl={pnl:>+8.2f}  ROI={roi:>+6.2f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
