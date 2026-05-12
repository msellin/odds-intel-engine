"""
Backfill is_opening=true for the earliest snapshot per (match_id, bookmaker,
market, selection) in odds_snapshots.

Safe to run while live — UPDATE is idempotent. Processes in chunks of 10k
match_ids to avoid statement timeouts.

Usage:
  python3 scripts/backfill_is_opening.py
  python3 scripts/backfill_is_opening.py --dry-run
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write

console = Console()
CHUNK = 10_000


def run(dry_run: bool = False):
    console.print("[cyan]Backfill is_opening flag[/cyan]")

    match_ids = [
        r["match_id"]
        for r in execute_query("SELECT DISTINCT match_id FROM odds_snapshots ORDER BY match_id")
    ]
    console.print(f"  {len(match_ids):,} distinct match_ids")

    total_updated = 0
    for i in range(0, len(match_ids), CHUNK):
        chunk = match_ids[i : i + CHUNK]
        if dry_run:
            console.print(f"  [yellow]DRY RUN: would update chunk {i}–{i+len(chunk)}[/yellow]")
            continue
        rows = execute_write(
            """
            WITH earliest AS (
                SELECT DISTINCT ON (match_id, bookmaker, market, selection) id
                FROM odds_snapshots
                WHERE match_id = ANY(%s)
                ORDER BY match_id, bookmaker, market, selection, timestamp ASC
            )
            UPDATE odds_snapshots SET is_opening = true
            WHERE id IN (SELECT id FROM earliest)
              AND is_opening = false
            """,
            (chunk,),
        )
        total_updated += rows or 0
        console.print(f"  chunk {i//CHUNK + 1}: +{rows or 0} rows  (total {total_updated:,})")

    if not dry_run:
        console.print(f"[green]Done — {total_updated:,} rows marked is_opening=true[/green]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
