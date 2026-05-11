"""
Prune odds_snapshots to prevent DB bloat.

Two modes:

  hourly (default — research phase):
    Keep one snapshot per HOUR per (match, bookmaker, market, selection) for
    finished matches, plus all is_closing=true rows.
    Max 16 rows per combination (07-22 UTC) instead of the original 2-3.
    ~8× more storage than compact, but preserves intraday shape for
    odds_timing_analysis.py to answer when odds peak during the day.
    Switch back to compact once the timing theory is validated.

  compact (post-validation):
    Keep only the opening + closing snapshot per (match, bookmaker, market,
    selection). Minimum storage. Use once timing analysis is complete and
    you no longer need intraday shape for finished matches.

Usage:
    python scripts/prune_odds_snapshots.py               # dry run, hourly mode
    python scripts/prune_odds_snapshots.py --apply       # apply, hourly mode
    python scripts/prune_odds_snapshots.py --mode compact --apply  # back to original
"""

import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
load_dotenv()

from workers.api_clients.db import execute_query, get_conn


def _build_sql(mode: str, for_count: bool) -> str:
    if mode == "compact":
        # Keep first + last + is_closing + is_opening per combination.
        cte = """
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
                       is_closing,
                       is_opening
                FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[])
            )
        """
        condition = "rn_first > 1 AND rn_last > 1 AND NOT is_closing AND NOT is_opening"
    else:
        # Hourly strategy: keep first snapshot per hour per combination + is_closing + is_opening
        cte = """
            WITH hourly AS (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY match_id, bookmaker, market, selection,
                                        EXTRACT(HOUR FROM timestamp)::int
                           ORDER BY timestamp ASC
                       ) AS rn_in_hour,
                       is_closing,
                       is_opening
                FROM odds_snapshots
                WHERE match_id = ANY(%s::uuid[])
            )
        """
        condition = "rn_in_hour > 1 AND NOT is_closing AND NOT is_opening"

    alias = "ranked" if mode == "compact" else "hourly"

    if for_count:
        return f"{cte} SELECT COUNT(*) AS cnt FROM {alias} WHERE {condition}"
    else:
        return f"{cte} DELETE FROM odds_snapshots WHERE id IN (SELECT id FROM {alias} WHERE {condition})"


def prune(dry_run: bool = True, mode: str = "hourly") -> int:
    mode_desc = {
        "hourly": "keep 1 snapshot/hour per (match, bookmaker, market, selection) + is_closing",
        "compact": "keep first + last snapshot per (match, bookmaker, market, selection) + is_closing",
    }
    print(f"{'[DRY RUN] ' if dry_run else ''}Pruning odds_snapshots (finished matches only)")
    print(f"Mode: {mode} — {mode_desc[mode]}")
    print()

    before = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots", [])
    before_cnt = before[0]["cnt"] if before else 0
    print(f"Total rows before: {before_cnt:,}")

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
        return 0

    total_deleted = 0
    batch_size = 50
    delete_sql = _build_sql(mode, for_count=False)
    count_sql = _build_sql(mode, for_count=True)

    for i in range(0, len(match_ids), batch_size):
        batch = match_ids[i:i + batch_size]

        if dry_run:
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
    return total_deleted


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete rows (default is dry run)")
    parser.add_argument("--mode", choices=["hourly", "compact"], default="hourly",
                        help="hourly=keep 1/hour (research phase); compact=keep first+last only (post-validation)")
    args = parser.parse_args()
    prune(dry_run=not args.apply, mode=args.mode)
