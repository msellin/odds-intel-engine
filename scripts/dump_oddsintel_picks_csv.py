"""
Dump the OddsIntel side of the head-to-head cohort to ledger/picks_oddsintel.csv.

Same scope as the "our_stats_same_window" block used by audit_vs_*.py: production
maturity (calibrated + beta + active), pre-match only (inplay_% bots excluded —
we compare against pre-match tipsters), 1X2 + OU 2.5 markets, settled bets.

Usage:
    python3 scripts/dump_oddsintel_picks_csv.py
    python3 scripts/dump_oddsintel_picks_csv.py --start 2026-05-04 --end 2026-08-02

The landing "Verify · view raw picks ↗" link lands users on the ledger
directory that contains this CSV alongside the per-competitor picks.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from workers.api_clients.db import execute_query  # noqa: E402
from scripts._picks_csv import compute_pnl, write_picks_csv  # noqa: E402

OUT_PATH = ROOT / "ledger" / "picks_oddsintel.csv"
DEFAULT_START = "2026-05-04"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    start = args.start
    end = args.end or (date.today() + timedelta(days=1)).isoformat()
    print(f"Dumping OddsIntel picks {start} → {end} → {OUT_PATH}")

    rows = execute_query(
        """
        SELECT
          sb.created_at,
          sb.market,
          sb.selection,
          sb.odds_at_pick::float AS odds,
          sb.result::text AS result,
          sb.pnl::float AS pnl,
          sb.stake::float AS stake,
          ht.name AS home_team,
          at.name AS away_team,
          m.date AS kickoff_utc,
          m.score_home,
          m.score_away,
          l.name AS league,
          b.name AS bot
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        LEFT JOIN matches m ON m.id = sb.match_id
        LEFT JOIN teams ht ON ht.id = m.home_team_id
        LEFT JOIN teams at ON at.id = m.away_team_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE sb.created_at >= %s::date
          AND sb.created_at <  %s::date
          AND sb.result::text IN ('won','lost')
          AND sb.market IN ('1x2','over_under_25','o/u')
          AND b.maturity_label IN ('calibrated','beta','active')
          AND b.name NOT LIKE 'inplay_%%'
        ORDER BY sb.created_at
        """,
        (start, end),
    )
    print(f"Fetched {len(rows)} rows")

    csv_rows = []
    for r in rows:
        odds_f = float(r["odds"]) if r.get("odds") else None
        result = r.get("result") or ""
        market_map = {"o/u": "over_under_25"}
        csv_rows.append({
            "source": "oddsintel",
            "kickoff_date": (r["kickoff_utc"].isoformat()[:10]
                             if r.get("kickoff_utc") else r["created_at"].isoformat()[:10]),
            "league": r.get("league") or "",
            "home_team": r.get("home_team") or "",
            "away_team": r.get("away_team") or "",
            "market": market_map.get(r.get("market"), r.get("market") or ""),
            "pick": r.get("selection") or "",
            "odds": f"{odds_f:.3f}" if odds_f else "",
            "result": result,
            "pnl_per_unit": compute_pnl(odds_f, result),
            "ref_url": "https://oddsintel.app/picks",
        })

    n = write_picks_csv(OUT_PATH, csv_rows)
    print(f"Wrote {n} rows to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
