"""AF-WASTE-HALFTIME-STATS-2026-09-05 (backfill half) — recover half-time splits.

`get_fixture_statistics_halftime` sent `half=1` then `half=2` for months.
API-Football rejects both ("The Half field must be one of: true,false") and a
bare `except` hid it, so `match_stats` holds **0 of 53,954 rows** with half-time
data despite the columns existing since May.

The fetch is fixed (`half=true`, which returns `statistics_1h` inline in the
call we already make), but that only helps matches settled from now on. This
recovers the recent past.

Retention, probed 2026-09-05 on covered leagues: 4/4 fixtures at 1 and 3 days,
3/4 at 7 days, 1/4 at 14 and 30 days, 0/4 at 60. So it is worth a ~2-week reach
and not much more.

SAFETY: this UPDATEs only the seventeen `*_ht` columns. Full-match statistics
are never rewritten — a re-fetch that disagreed with what settlement stored
would silently alter history, and those columns feed the model.

Usage:
    python3 scripts/backfill_halftime_stats.py --dry-run
    python3 scripts/backfill_halftime_stats.py --days 14
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.api_football import (  # noqa: E402
    get_fixture_statistics, parse_fixture_stats_halftime,
)
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

HT_COLUMNS = [
    "shots_home_ht", "shots_away_ht", "shots_on_target_home_ht",
    "shots_on_target_away_ht", "possession_home_ht", "corners_home_ht",
    "corners_away_ht", "fouls_home_ht", "fouls_away_ht",
    "yellow_cards_home_ht", "yellow_cards_away_ht", "xg_home_ht", "xg_away_ht",
    "passes_home_ht", "passes_away_ht", "offsides_home_ht", "offsides_away_ht",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = execute_query(
        """SELECT m.api_football_id afid, m.id::text mid
             FROM matches m
             JOIN leagues l ON l.id = m.league_id
             JOIN match_stats ms ON ms.match_id = m.id
            WHERE m.status = 'finished' AND m.api_football_id IS NOT NULL
              AND COALESCE(l.coverage_statistics_fixtures, false)
              AND ms.shots_home_ht IS NULL
              AND m.date > now() - (%s || ' days')::interval
            ORDER BY m.date DESC""",
        [args.days],
    )
    print(f"{len(rows):,} finished matches in covered leagues without half-time data")

    filled = empty = 0
    for i, r in enumerate(rows, 1):
        try:
            ht = parse_fixture_stats_halftime(get_fixture_statistics(int(r["afid"])))
        except Exception:
            continue
        if not ht:
            empty += 1
            continue
        present = [c for c in HT_COLUMNS if ht.get(c) is not None]
        if not present:
            empty += 1
            continue
        filled += 1
        if args.dry_run:
            continue
        # Only the _ht columns. Full-match stats are never rewritten.
        sets = ", ".join(f"{c} = %s" for c in present)
        execute_write(
            f"UPDATE match_stats SET {sets} WHERE match_id = %s::uuid",
            [ht[c] for c in present] + [r["mid"]],
        )
        if i % 200 == 0:
            print(f"  {i}/{len(rows)} — {filled:,} filled, {empty:,} had no split")

    print(f"\n{'would fill' if args.dry_run else 'filled'} {filled:,} matches; "
          f"{empty:,} returned no half-time split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
