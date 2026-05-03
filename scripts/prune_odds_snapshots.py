"""
Prune odds_snapshots to prevent DB size from hitting the Supabase free tier (500 MB).

Conservative strategy — data is king:
  - NEVER touch snapshots for non-finished matches (upcoming/live/scheduled)
  - For finished matches: keep only
      • The opening snapshot per (match, market, selection) — needed for odds_drift + steam_move
      • All closing snapshots (is_closing=true) — needed for CLV
      • Delete all intermediate snapshots

Signals (overnight_line_move, odds_volatility, steam_move, odds_drift) are computed
from live data at pipeline runtime and stored in match_signals before a match finishes.
Post-settlement, only opening + closing snapshots are needed.

Performance: Uses a single SQL DELETE with a correlated NOT IN / DISTINCT ON subquery
instead of per-match Python loops. Processes all finished matches in one statement.

Run: python scripts/prune_odds_snapshots.py [--apply]
Scheduled: daily in settlement_pipeline() after core settlement + ML ETL.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

from workers.api_clients.db import execute_query, execute_write, get_conn


def prune(dry_run: bool = True):
    print(f"{'[DRY RUN] ' if dry_run else ''}Pruning odds_snapshots (finished matches only)")
    print("Strategy: keep opening snapshot + all is_closing=true, delete the rest")
    print()

    # Count rows before
    before = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots", [])
    before_cnt = before[0]["cnt"] if before else 0

    # Count how many rows WOULD be deleted without actually deleting
    # Rows to delete = non-closing rows for finished matches, excluding the opening (first) per group
    count_sql = """
        SELECT COUNT(*) AS cnt
        FROM odds_snapshots os
        WHERE os.is_closing = false
          AND os.match_id IN (SELECT id FROM matches WHERE status = 'finished')
          AND os.id NOT IN (
              SELECT DISTINCT ON (match_id, market, selection) id
              FROM odds_snapshots
              WHERE is_closing = false
                AND match_id IN (SELECT id FROM matches WHERE status = 'finished')
              ORDER BY match_id, market, selection, timestamp ASC
          )
    """
    count_result = execute_query(count_sql, [])
    to_delete = count_result[0]["cnt"] if count_result else 0

    print(f"Total rows in odds_snapshots:  {before_cnt:,}")
    print(f"Rows eligible for deletion:    {to_delete:,}")
    if before_cnt > 0:
        reduction_pct = to_delete / before_cnt * 100
        print(f"Reduction if applied:          {reduction_pct:.1f}%")
    print()

    if to_delete == 0:
        print("Nothing to prune.")
        return

    if dry_run:
        print("This was a DRY RUN. Run with --apply to actually delete.")
        return

    # Execute the DELETE
    delete_sql = """
        DELETE FROM odds_snapshots
        WHERE is_closing = false
          AND match_id IN (SELECT id FROM matches WHERE status = 'finished')
          AND id NOT IN (
              SELECT DISTINCT ON (match_id, market, selection) id
              FROM odds_snapshots
              WHERE is_closing = false
                AND match_id IN (SELECT id FROM matches WHERE status = 'finished')
              ORDER BY match_id, market, selection, timestamp ASC
          )
    """
    print(f"Deleting {to_delete:,} intermediate snapshots...")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(delete_sql)
            deleted = cur.rowcount
            conn.commit()

    after = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots", [])
    after_cnt = after[0]["cnt"] if after else 0

    print(f"Deleted:                       {deleted:,} rows")
    print(f"Rows remaining:                {after_cnt:,}")
    print(f"Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete rows (default is dry run)")
    args = parser.parse_args()
    prune(dry_run=not args.apply)
