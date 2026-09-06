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


def prune_old_simple(max_matches: int = 5000, dry_run: bool = False) -> int:
    """
    Fast backlog cleaner for finished matches older than 30 days.
    No window functions — just deletes everything that isn't is_closing or is_opening.
    Safe to re-run (already-compacted matches delete 0 rows).
    Uses SET LOCAL statement_timeout per transaction to override Supabase's 1-min default.
    Called nightly at 03:00 UTC to drain the historical backlog incrementally.
    """
    import psycopg2

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set")
        return 0

    print(f"{'[DRY RUN] ' if dry_run else ''}prune_old_simple: max_matches={max_matches}")

    conn = psycopg2.connect(db_url)
    conn.autocommit = False
    cur = conn.cursor()

    cur.execute("SET LOCAL statement_timeout = '10min'")
    # ── ODDS-PRUNE-CURSOR-BUG-2026-09-06 ────────────────────────────────────
    # This was `ORDER BY id LIMIT %s` on a UUID primary key, with NO predicate
    # excluding matches that are already pruned. A UUID ordering is stable and
    # arbitrary, so the query returned THE SAME 5,000 matches every single
    # night since the job shipped, and the nightly cron drained nothing.
    #
    # Measured 2026-09-06: 151,154 finished matches older than 30 days exist,
    # so the job saw 3.3% of them — always the same 3.3%. Prunable rows inside
    # that frozen window were 8,313; prunable rows across the whole backlog were
    # 26,374,272, roughly 35% of the table and ~8 GB, all of it condemned by a
    # retention policy that was agreed and implemented at the time.
    #
    # Two changes, and the EXISTS clause is the important one: ordering by date
    # alone would still re-visit already-compacted matches forever, just in a
    # different order. Excluding matches with nothing left to prune is what
    # makes the cursor actually advance.
    cur.execute("""
        SELECT m.id FROM matches m
        WHERE m.status = 'finished'
          AND m.date < NOW() - INTERVAL '30 days'
          AND EXISTS (
                SELECT 1 FROM odds_snapshots o
                 WHERE o.match_id = m.id
                   AND NOT COALESCE(o.is_closing, false)
                   AND NOT COALESCE(o.is_opening, false)
              )
        ORDER BY m.date ASC
        LIMIT %s
    """, (max_matches,))
    match_ids = [str(r[0]) for r in cur.fetchall()]
    conn.commit()

    print(f"  Matches fetched: {len(match_ids):,}")
    if not match_ids:
        print("  Nothing to do.")
        conn.close()
        return 0

    BATCH = 100
    total_deleted = 0

    for i in range(0, len(match_ids), BATCH):
        batch = match_ids[i:i + BATCH]
        attempt = 0
        while attempt < 5:
            try:
                cur.execute("SET LOCAL statement_timeout = '10min'")
                if not dry_run:
                    cur.execute("""
                        DELETE FROM odds_snapshots
                        WHERE match_id = ANY(%s::uuid[])
                          AND NOT COALESCE(is_closing, false)
                          AND NOT COALESCE(is_opening, false)
                    """, (batch,))
                    deleted = cur.rowcount
                    conn.commit()
                else:
                    cur.execute("""
                        SELECT COUNT(*) FROM odds_snapshots
                        WHERE match_id = ANY(%s::uuid[])
                          AND NOT COALESCE(is_closing, false)
                          AND NOT COALESCE(is_opening, false)
                    """, (batch,))
                    deleted = cur.fetchone()[0]
                    conn.rollback()
                total_deleted += deleted
                break
            except psycopg2.errors.QueryCanceled:
                conn.rollback()
                attempt += 1
                if len(batch) > 10:
                    batch = batch[:max(len(batch) // 2, 10)]
                else:
                    print(f"  batch {i} skipped after {attempt} timeouts")
                    break
            except Exception as e:
                conn.rollback()
                print(f"  batch {i} error: {type(e).__name__}: {e}")
                break

    print(f"  {'Would delete' if dry_run else 'Deleted'}: {total_deleted:,} rows from {len(match_ids):,} matches")
    conn.close()
    return total_deleted


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete rows (default is dry run)")
    parser.add_argument("--mode", choices=["hourly", "compact", "simple_old"], default="hourly",
                        help="hourly=keep 1/hour; compact=keep first+last; simple_old=backlog drain (>30d, no window fn)")
    parser.add_argument("--max-matches", type=int, default=5000,
                        help="Max matches to process (simple_old mode only)")
    args = parser.parse_args()
    if args.mode == "simple_old":
        prune_old_simple(max_matches=args.max_matches, dry_run=not args.apply)
    else:
        prune(dry_run=not args.apply, mode=args.mode)
