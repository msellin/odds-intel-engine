"""LIVE-SNAPSHOTS-PRUNE — keep `live_match_snapshots` lean.

Strategy: for matches finished ≥48h ago, keep only one snapshot per
match every 5 minutes of match-time PLUS all rows within ±60s of any
match_events row (goals, cards, subs, etc.). Drop the rest.

Why: LivePoller writes ~50K rows/day; without pruning the table hits
1M rows in 2-3 weeks. Most of the in-between minutes are redundant
state for finished matches. Keeping the event-adjacent rows preserves
xG and possession at goal moments — the only state actually used by
B-ML3 features and post-match analysis.

Dry-run mode counts rows that would be deleted. --apply runs the delete.

Run:
  python3 scripts/prune_live_snapshots.py             # dry run
  python3 scripts/prune_live_snapshots.py --apply     # delete
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console

from workers.api_clients.db import execute_query, get_conn

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--age-hours", type=int, default=48, help="Only prune matches finished this many hours ago")
    args = ap.parse_args()

    # Find candidate matches: finished + older than --age-hours
    matches = execute_query("""
        SELECT id FROM matches
        WHERE status = 'finished'
          AND date < NOW() - (%s || ' hours')::interval
          AND date >= NOW() - INTERVAL '90 days'  -- skip very old (already pruned)
    """, (args.age_hours,))
    if not matches:
        console.print("[yellow]No candidate matches to prune.[/yellow]")
        return
    match_ids = [m["id"] for m in matches]
    console.print(f"[bold]LIVE-SNAPSHOTS-PRUNE — {len(match_ids):,} candidate finished matches[/bold]")

    # Strategy:
    # 1. Keep snapshots where (match_id, minute) falls on a 5-minute boundary (0, 5, 10, ...)
    # 2. PLUS keep any snapshot within ±60s of a match_events.elapsed minute (event-adjacent)
    # 3. Drop the rest.
    #
    # We do this with a NOT EXISTS check using DELETE … WHERE id IN (subquery).
    # All-in-one delete to keep transaction count low. Operates in chunks of
    # 500 matches for safety on the EU pooler.
    chunk_size = 500
    total_before = 0
    total_after = 0
    total_deleted = 0

    for cs in range(0, len(match_ids), chunk_size):
        chunk = match_ids[cs: cs + chunk_size]
        # Count before
        bef = execute_query(
            "SELECT COUNT(*) AS n FROM live_match_snapshots WHERE match_id = ANY(%s::uuid[])",
            (chunk,),
        )[0]["n"]
        total_before += bef

        # Build delete: keep 5-min boundaries OR within 1 minute of any event
        delete_sql = """
        DELETE FROM live_match_snapshots
        WHERE id IN (
            SELECT lms.id FROM live_match_snapshots lms
            WHERE lms.match_id = ANY(%s::uuid[])
              AND (lms.minute %% 5 != 0)
              AND NOT EXISTS (
                  SELECT 1 FROM match_events ev
                  WHERE ev.match_id = lms.match_id
                    AND ev.minute BETWEEN lms.minute - 1 AND lms.minute + 1
              )
        )
        """
        if not args.apply:
            # Dry run — count what WOULD delete
            cnt = execute_query("""
                SELECT COUNT(*) AS n FROM live_match_snapshots lms
                WHERE lms.match_id = ANY(%s::uuid[])
                  AND (lms.minute %% 5 != 0)
                  AND NOT EXISTS (
                      SELECT 1 FROM match_events ev
                      WHERE ev.match_id = lms.match_id
                        AND ev.minute BETWEEN lms.minute - 1 AND lms.minute + 1
                  )
            """, (chunk,))[0]["n"]
            total_deleted += cnt
        else:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(delete_sql, (chunk,))
                    deleted = cur.rowcount
                conn.commit()
            total_deleted += deleted

        total_after += bef - (cnt if not args.apply else deleted)

    pct = (total_deleted / total_before * 100) if total_before else 0
    console.print(f"\n[bold]Rows: {total_before:,} → {total_before - total_deleted:,} ({pct:.1f}% pruned)[/bold]")
    if not args.apply:
        console.print("[yellow]Dry run — pass --apply to actually delete[/yellow]")
    else:
        console.print(f"[green]✓ Deleted {total_deleted:,} rows[/green]")


if __name__ == "__main__":
    main()
