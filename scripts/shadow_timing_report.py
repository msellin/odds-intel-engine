"""
Shadow timing analysis — which hours produce the best value bets?

Groups shadow_bets by UTC hour and computes win rate, ROI, avg CLV per slot.
Also identifies 'late discovery' matches: bets that only appear in afternoon
shadow runs but never in the morning (i.e. odds not priced at 06:00).

Usage:
    python scripts/shadow_timing_report.py              # last 30 days
    python scripts/shadow_timing_report.py --days 14
    python scripts/shadow_timing_report.py --bot bot_home_value
"""

import argparse
import sys
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--bot", type=str, default=None, help="Filter by bot name substring")
    args = p.parse_args()

    # ── Per-hour ROI table ────────────────────────────────────────────────────
    hour_sql = """
        SELECT
            CASE
                WHEN LENGTH(sb.shadow_cohort) = 4 AND sb.shadow_cohort ~ '^\\d{4}$'
                    THEN CAST(LEFT(sb.shadow_cohort, 2) AS int)
                WHEN sb.shadow_cohort = 'morning'  THEN 6
                WHEN sb.shadow_cohort = 'midday'   THEN 11
                WHEN sb.shadow_cohort = 'pre_ko'   THEN 15
                ELSE NULL
            END AS hour_utc,
            COUNT(*) FILTER (WHERE sb.result IS NOT NULL AND sb.result != 'pending') AS settled,
            COUNT(*) FILTER (WHERE sb.result = 'won')  AS won,
            COUNT(*) FILTER (WHERE sb.result = 'lost') AS lost,
            ROUND(AVG(sb.edge_percent)::numeric, 2)    AS avg_edge,
            ROUND(AVG(sb.odds_at_pick)::numeric, 3)    AS avg_odds,
            ROUND(
                SUM(sb.pnl) FILTER (WHERE sb.result IS NOT NULL AND sb.result != 'pending')
                / NULLIF(COUNT(*) FILTER (WHERE sb.result IS NOT NULL AND sb.result != 'pending'), 0)
                / 10.0 * 100, 2
            ) AS roi_pct,
            ROUND(AVG(sb.clv) FILTER (WHERE sb.clv IS NOT NULL)::numeric * 100, 2) AS avg_clv_pct
        FROM shadow_bets sb
        LEFT JOIN bots ON bots.id = sb.bot_id
        WHERE sb.created_at >= NOW() - INTERVAL '%s days'
          AND (%s OR bots.name ILIKE '%%' || %s || '%%')
        GROUP BY hour_utc
        HAVING hour_utc IS NOT NULL
        ORDER BY hour_utc
    """
    bot_filter_off = args.bot is None
    bot_name = args.bot or ""
    rows = execute_query(hour_sql, [args.days, bot_filter_off, bot_name])

    print(f"\n=== Shadow bet ROI by UTC hour (last {args.days} days) ===\n")
    print(f"{'Hour':>5}  {'Settled':>7}  {'Won':>5}  {'Lost':>5}  {'Win%':>6}  {'ROI%':>7}  {'AvgOdds':>8}  {'AvgEdge%':>9}  {'AvgCLV%':>8}")
    print("─" * 80)
    for r in rows:
        hour = r["hour_utc"]
        settled = r["settled"] or 0
        won = r["won"] or 0
        lost = r["lost"] or 0
        win_pct = f"{won/settled*100:.1f}%" if settled else "—"
        roi = f"{r['roi_pct']:+.1f}%" if r["roi_pct"] is not None else "—"
        avg_odds = f"{r['avg_odds']:.3f}" if r["avg_odds"] else "—"
        avg_edge = f"{r['avg_edge']:+.2f}%" if r["avg_edge"] is not None else "—"
        avg_clv = f"{r['avg_clv_pct']:+.2f}%" if r["avg_clv_pct"] is not None else "—"
        print(f"  {hour:02d}:xx  {settled:>7}  {won:>5}  {lost:>5}  {win_pct:>6}  {roi:>7}  {avg_odds:>8}  {avg_edge:>9}  {avg_clv:>8}")

    # ── Late-discovery bets ───────────────────────────────────────────────────
    # Bets found in afternoon shadow runs (hour >= 11) on a match that had
    # NO shadow bet at all in the morning window (hour < 11) on the same day.
    late_sql = """
        WITH morning_matches AS (
            SELECT DISTINCT
                sb.match_id,
                DATE(sb.created_at) AS run_date
            FROM shadow_bets sb
            WHERE sb.created_at >= NOW() - INTERVAL '%s days'
              AND (
                  (LENGTH(sb.shadow_cohort) = 4 AND sb.shadow_cohort ~ '^\\d{4}$'
                   AND CAST(LEFT(sb.shadow_cohort, 2) AS int) < 11)
                  OR sb.shadow_cohort = 'morning'
              )
        ),
        afternoon_bets AS (
            SELECT sb.*, bots.name AS bot_name,
                   ht.name AS home_team, at2.name AS away_team,
                   DATE(sb.created_at) AS run_date
            FROM shadow_bets sb
            LEFT JOIN bots ON bots.id = sb.bot_id
            LEFT JOIN matches m ON m.id = sb.match_id
            LEFT JOIN teams ht ON ht.id = m.home_team_id
            LEFT JOIN teams at2 ON at2.id = m.away_team_id
            WHERE sb.created_at >= NOW() - INTERVAL '%s days'
              AND (
                  (LENGTH(sb.shadow_cohort) = 4 AND sb.shadow_cohort ~ '^\\d{4}$'
                   AND CAST(LEFT(sb.shadow_cohort, 2) AS int) >= 11)
                  OR sb.shadow_cohort IN ('midday', 'pre_ko')
              )
              AND sb.result IS NOT NULL
              AND sb.result != 'pending'
        )
        SELECT
            ab.shadow_cohort,
            ab.bot_name,
            ab.market,
            ab.selection,
            ab.odds_at_pick,
            ab.edge_percent,
            ab.result,
            ab.pnl,
            ab.home_team,
            ab.away_team
        FROM afternoon_bets ab
        LEFT JOIN morning_matches mm
            ON mm.match_id = ab.match_id AND mm.run_date = ab.run_date
        WHERE mm.match_id IS NULL
        ORDER BY ab.run_date DESC, ab.edge_percent DESC
        LIMIT 50
    """
    late_rows = execute_query(late_sql, [args.days, args.days])

    if late_rows:
        won_late = sum(1 for r in late_rows if r["result"] == "won")
        total_late = len(late_rows)
        pnl_late = sum(float(r["pnl"] or 0) for r in late_rows)
        roi_late = pnl_late / (total_late * 10) * 100 if total_late else 0

        print(f"\n=== Late-discovery bets (not priced at morning, found afternoon) ===")
        print(f"    {total_late} bets shown  |  win rate {won_late/total_late*100:.1f}%  |  ROI {roi_late:+.1f}%\n")
        print(f"  {'Cohort':>6}  {'Result':>6}  {'PnL':>6}  {'Odds':>6}  {'Edge%':>6}  Match")
        print("─" * 80)
        for r in late_rows[:20]:
            match = f"{r['home_team'] or '?'} vs {r['away_team'] or '?'}"[:40]
            pnl = f"{float(r['pnl']):+.1f}" if r["pnl"] is not None else "—"
            edge = f"{float(r['edge_percent']):+.2f}" if r["edge_percent"] else "—"
            print(f"  {r['shadow_cohort']:>6}  {r['result']:>6}  {pnl:>6}  "
                  f"{float(r['odds_at_pick']):.2f}  {edge:>6}  {match}")
    else:
        print("\n(No late-discovery bets found in this window — need more 30-min shadow data.)")

    print()


if __name__ == "__main__":
    main()
