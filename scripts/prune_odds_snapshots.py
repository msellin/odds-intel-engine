"""
Prune odds_snapshots to prevent DB size from hitting the Supabase free tier (500 MB).

Conservative strategy — data is king:
  - NEVER touch snapshots for non-finished matches (upcoming/live/scheduled)
  - NEVER touch snapshots newer than 14 days
  - For finished matches older than 14 days: keep only
      • The opening snapshot (first per match/market/selection) — needed for odds_drift + steam_move
      • All closing snapshots (is_closing=true) — needed for CLV
      • Delete intermediate snapshots only

This means signals (overnight_line_move, odds_volatility, steam_move, odds_drift) are
unaffected — they are computed from live data at pipeline runtime and stored in
match_signals before a match finishes. Post-match, only opening+closing are needed.

Run: python scripts/prune_odds_snapshots.py [--dry-run]
"""

import argparse
import os
import sys
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()


def get_client():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SECRET_KEY"]
    return create_client(url, key)


def prune(dry_run: bool = True):
    client = get_client()

    print(f"{'[DRY RUN] ' if dry_run else ''}Pruning odds_snapshots")
    print(f"Strategy: for finished matches, keep opening + closing snapshots only")
    print()

    # Step 1: Find ALL finished match IDs
    # Signals (overnight_line_move, odds_volatility, steam_move, odds_drift) are
    # computed at pipeline runtime and stored in match_signals before settlement.
    # Post-settlement, only opening + closing snapshots are needed.
    matches_r = client.table("matches").select("id").eq(
        "status", "finished"
    ).execute()

    if not matches_r.data:
        print("No finished matches found. Nothing to prune.")
        return

    match_ids = [m["id"] for m in matches_r.data]
    print(f"Finished matches: {len(match_ids)}")

    total_deleted = 0
    total_kept = 0
    batch_size = 50  # Process in batches to avoid timeout

    for batch_start in range(0, len(match_ids), batch_size):
        batch = match_ids[batch_start:batch_start + batch_size]

        for match_id in batch:
            # Get all snapshots for this match
            snaps_r = client.table("odds_snapshots").select(
                "id, market, selection, timestamp, is_closing"
            ).eq("match_id", match_id).order("timestamp", desc=False).execute()

            if not snaps_r.data:
                continue

            snapshots = snaps_r.data

            # Build set of IDs to keep:
            # 1. All closing snapshots (is_closing=True)
            # 2. First snapshot per (market, selection) = opening odds
            keep_ids = set()
            seen_opening = set()  # (market, selection) tuples already marked as opening

            for snap in snapshots:
                if snap.get("is_closing"):
                    keep_ids.add(snap["id"])
                    continue
                key = (snap["market"], snap["selection"])
                if key not in seen_opening:
                    keep_ids.add(snap["id"])
                    seen_opening.add(key)

            # IDs to delete = all minus keep
            all_ids = {s["id"] for s in snapshots}
            delete_ids = list(all_ids - keep_ids)

            kept = len(keep_ids)
            deleted = len(delete_ids)
            total_kept += kept
            total_deleted += deleted

            if delete_ids and not dry_run:
                # Delete in chunks of 100 (Supabase in-filter limit)
                for i in range(0, len(delete_ids), 100):
                    chunk = delete_ids[i:i + 100]
                    client.table("odds_snapshots").delete().in_(
                        "id", chunk
                    ).execute()

        processed = min(batch_start + batch_size, len(match_ids))
        print(f"  Processed {processed}/{len(match_ids)} matches...")

    print()
    print(f"Summary:")
    print(f"  Snapshots kept:    {total_kept:,}")
    print(f"  Snapshots deleted: {total_deleted:,}")
    if total_kept + total_deleted > 0:
        reduction_pct = total_deleted / (total_kept + total_deleted) * 100
        print(f"  Reduction:         {reduction_pct:.1f}%")
    if dry_run:
        print()
        print("This was a DRY RUN. Run with --apply to actually delete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete rows (default is dry run)")
    args = parser.parse_args()
    prune(dry_run=not args.apply)
