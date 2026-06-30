#!/usr/bin/env python3
"""
CS2-MAP-STATS-EXPAND (2026-06-30): compute per-team per-map win% from
cs2_hltv_match_maps × cs2_hltv_matches history.

No HLTV auth required — pure DB computation from match results already in
the pipeline. Covers ~2000 teams vs 248 in the authenticated scraped table.
Results go into cs2_computed_team_map_stats; load_map_winrate_map() uses
scraped data first, computed as fallback.

Usage:
    python3 scripts/esports/cs2_compute_map_stats.py            # dry-run
    python3 scripts/esports/cs2_compute_map_stats.py --record   # write DB
    python3 scripts/esports/cs2_compute_map_stats.py --min-maps 3 --record
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dotenv import load_dotenv

load_dotenv()

from workers.api_clients.db import execute_query, execute_write, get_conn  # noqa: E402
import psycopg2.extras  # noqa: E402

# Maps that aren't real CS2 competitive maps — exclude from win% computation.
_SKIP_MAPS = {"Default", "", "Cache", "Cobblestone", "Tuscan"}


def compute_map_stats(min_maps: int = 5) -> list[dict]:
    """Return [{team_name, map_name, win_pct, maps_played}] from match history."""
    rows = execute_query("""
        WITH appearances AS (
            SELECT m.team1_name AS team, mm.map_name,
                   (mm.winner_name = m.team1_name) AS won
            FROM cs2_hltv_match_maps mm
            JOIN cs2_hltv_matches m ON m.hltv_match_id = mm.hltv_match_id
            WHERE mm.winner_name IS NOT NULL
              AND mm.map_name IS NOT NULL
              AND mm.team1_score IS NOT NULL
              AND mm.team2_score IS NOT NULL
            UNION ALL
            SELECT m.team2_name AS team, mm.map_name,
                   (mm.winner_name = m.team2_name) AS won
            FROM cs2_hltv_match_maps mm
            JOIN cs2_hltv_matches m ON m.hltv_match_id = mm.hltv_match_id
            WHERE mm.winner_name IS NOT NULL
              AND mm.map_name IS NOT NULL
              AND mm.team1_score IS NOT NULL
              AND mm.team2_score IS NOT NULL
        )
        SELECT
            team          AS team_name,
            map_name,
            ROUND(100.0 * SUM(won::int) / COUNT(*), 2)::float AS win_pct,
            COUNT(*)::int                                      AS maps_played
        FROM appearances
        WHERE team IS NOT NULL AND team != ''
        GROUP BY team, map_name
        HAVING COUNT(*) >= %s
        ORDER BY team, map_name
    """, (min_maps,))
    return [r for r in rows if r["map_name"] not in _SKIP_MAPS]


def write_stats(stats: list[dict]) -> int:
    if not stats:
        return 0
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO cs2_computed_team_map_stats
                    (team_name, map_name, win_pct, maps_played, computed_date)
                VALUES %s
                ON CONFLICT (team_name, map_name, computed_date) DO UPDATE SET
                    win_pct     = EXCLUDED.win_pct,
                    maps_played = EXCLUDED.maps_played
                """,
                [(r["team_name"], r["map_name"], r["win_pct"], r["maps_played"], today)
                 for r in stats],
                page_size=500,
            )
        conn.commit()
    return len(stats)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--min-maps", type=int, default=5,
                    help="Minimum maps played to include (default 5)")
    args = ap.parse_args()

    print(f"\n=== CS2 compute map stats  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    stats = compute_map_stats(min_maps=args.min_maps)
    teams = len({r["team_name"] for r in stats})
    print(f"  computed {len(stats)} team×map rows for {teams} teams  (min_maps={args.min_maps})")

    if not args.record:
        print("\n[DRY-RUN] Re-run with --record to persist.")
        for r in stats[:10]:
            print(f"    {r['team_name']:<30}  {r['map_name']:<12}  "
                  f"win%={r['win_pct']:5.1f}  n={r['maps_played']}")
        if len(stats) > 10:
            print(f"    … {len(stats) - 10} more")
        return 0

    written = write_stats(stats)
    print(f"  ✓ wrote {written} rows → cs2_computed_team_map_stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
