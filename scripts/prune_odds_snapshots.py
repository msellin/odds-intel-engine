"""
Prune odds_snapshots to prevent DB bloat.

Conservative strategy — data is king:
  - NEVER touch snapshots for non-finished matches (upcoming/live/scheduled)
  - For finished matches: keep only
      • The opening snapshot per (match, bookmaker, market, selection) — needed for odds_drift + charts
      • The closing snapshot per (match, bookmaker, market, selection) — needed for CLV
      • All is_closing=true rows
      • Delete all intermediate snapshots

Signals (overnight_line_move, odds_volatility, steam_move, odds_drift) are computed
from live data at pipeline runtime and stored in match_signals before a match finishes.
Post-settlement, only opening + closing snapshots are needed.

Performance: Batches by match_id to avoid massive NOT IN subqueries.

Run: python scripts/prune_odds_snapshots.py [--apply]
Scheduled: daily in settlement_pipeline() after core settlement + ML ETL.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

from workers.api_clients.db import execute_query, get_conn


def prune(dry_run: bool = True):
    print(f"{'[DRY RUN] ' if dry_run else ''}Pruning odds_snapshots (finished matches only)")
    print("Strategy: keep first + last snapshot per (match, bookmaker, market, selection) + is_closing rows")
    print()

    # Count rows before
    before = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots", [])
    before_cnt = before[0]["cnt"] if before else 0
    print(f"Total rows before: {before_cnt:,}")

    # Get finished match IDs that have odds snapshots
    finished = execute_query("""
        SELECT DISTINCT o.match_id
        FROM odds_snapshots o
        JOIN matches m ON o.match_id = m.id
        WHERE m.status = 'finished'
    """, [])
    match_ids = [r["match_id"] for r in finished]
    print(f"Finished matches with snapshots: {len(match_ids)}")

    if not match_ids:
        print("Nothing to prune.")
        return

    total_deleted = 0
    batch_size = 50

    for i in range(0, len(match_ids), batch_size):
        batch = match_ids[i:i + batch_size]

        # CTE approach: rank rows per (match, bookmaker, market, selection) by timestamp,
        # then delete everything except first, last, and is_closing rows
        delete_sql = """
            WITH ranked AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_id, bookmaker, market, selection
                           ORDER BY timestamp ASC
                       ) AS rn_first,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_id, bookmaker, market, selection
                           ORDER BY timestamp DESC
                       ) AS rn_last,
                       is_closing
                FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[])
            )
            DELETE FROM odds_snapshots
            WHERE id IN (
                SELECT id FROM ranked
                WHERE rn_first > 1
                  AND rn_last > 1
                  AND is_closing = false
            )
        """

        if dry_run:
            # Count instead of delete
            count_sql = """
                WITH ranked AS (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY match_id, bookmaker, market, selection
                               ORDER BY timestamp ASC
                           ) AS rn_first,
                           ROW_NUMBER() OVER (
                               PARTITION BY match_id, bookmaker, market, selection
                               ORDER BY timestamp DESC
                           ) AS rn_last,
                           is_closing
                    FROM odds_snapshots
                    WHERE match_id = ANY(%s::uuid[])
                )
                SELECT COUNT(*) AS cnt FROM ranked
                WHERE rn_first > 1 AND rn_last > 1 AND is_closing = false
            """
            result = execute_query(count_sql, [batch])
            batch_count = result[0]["cnt"] if result else 0
            total_deleted += batch_count
        else:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(delete_sql, (batch,))
                    batch_count = cur.rowcount
                    conn.commit()
            total_deleted += batch_count

        progress = min(i + batch_size, len(match_ids))
        print(f"  Batch {progress}/{len(match_ids)}: {'would delete' if dry_run else 'deleted'} {batch_count:,} rows")

    print()
    print(f"Total {'eligible for deletion' if dry_run else 'deleted'}: {total_deleted:,}")
    if before_cnt > 0:
        print(f"Reduction: {total_deleted / before_cnt * 100:.1f}%")

    if not dry_run:
        after = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots", [])
        after_cnt = after[0]["cnt"] if after else 0
        print(f"Rows remaining: {after_cnt:,}")

    if dry_run:
        print("\nThis was a DRY RUN. Run with --apply to actually delete.")

    print("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete rows (default is dry run)")
    args = parser.parse_args()
    prune(dry_run=not args.apply)
