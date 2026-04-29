"""
Clean up matches (and all related rows) dated before a given cutoff date.

Deletes from:
  - odds_snapshots
  - predictions
  - match_signals
  - simulated_bets
  - live_match_snapshots
  - matches (last, after foreign-key dependents)

Usage:
  python scripts/cleanup_before_date.py 2026-04-29          # dry-run (shows counts)
  python scripts/cleanup_before_date.py 2026-04-29 --apply  # actually deletes

IMPORTANT: run without --apply first to see what would be deleted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from workers.api_clients.supabase_client import get_client
from rich.console import Console

console = Console()


def run(cutoff_date: str, apply: bool):
    client = get_client()
    mode = "[bold red]APPLY[/bold red]" if apply else "[bold yellow]DRY RUN[/bold yellow]"
    console.print(f"\n[bold cyan]═══ Match Cleanup — cutoff: {cutoff_date} ({mode}) ═══[/bold cyan]\n")
    console.print(f"  Will {'DELETE' if apply else 'count'} matches with date < {cutoff_date} and all related rows.\n")

    # 1. Find match IDs to delete
    console.print("[cyan]Finding matches before cutoff...[/cyan]")
    matches_r = client.table("matches").select("id, date").lt(
        "date", f"{cutoff_date}T00:00:00Z"
    ).execute()

    match_ids = [m["id"] for m in (matches_r.data or [])]
    console.print(f"  {len(match_ids)} matches to delete (dated before {cutoff_date})\n")

    if not match_ids:
        console.print("[green]Nothing to delete.[/green]")
        return

    # Show date range being deleted
    dates = sorted({(m["date"] or "")[:10] for m in (matches_r.data or [])})
    console.print(f"  Date range: {dates[0]} → {dates[-1]}")
    console.print()

    TABLES = [
        ("odds_snapshots",      "match_id"),
        ("predictions",         "match_id"),
        ("match_signals",       "match_id"),
        ("simulated_bets",      "match_id"),
        ("live_match_snapshots","match_id"),
        ("match_stats",         "match_id"),
        ("match_h2h",           "match_id"),
        ("match_injuries",      "match_id"),
    ]

    # 2. Count (and optionally delete) dependent rows
    total_rows = 0
    for table, col in TABLES:
        try:
            # Count via select (Supabase free doesn't expose count endpoint cleanly)
            r = client.table(table).select(col).in_(col, match_ids).execute()
            count = len(r.data or [])
            total_rows += count

            if apply and count > 0:
                # Delete in batches of 200 to avoid request size limits
                for i in range(0, len(match_ids), 200):
                    batch = match_ids[i:i+200]
                    client.table(table).delete().in_(col, batch).execute()
                console.print(f"  [red]DELETED[/red] {count:>6} rows from {table}")
            else:
                console.print(f"  {'would delete' if count > 0 else '        zero':>12} {count:>6} rows from {table}")
        except Exception as e:
            console.print(f"  [yellow]Skipped {table}: {e}[/yellow]")

    # 3. Delete matches themselves last
    if apply:
        for i in range(0, len(match_ids), 200):
            batch = match_ids[i:i+200]
            client.table("matches").delete().in_("id", batch).execute()
        console.print(f"  [red]DELETED[/red] {len(match_ids):>6} rows from matches")
    else:
        console.print(f"  {'would delete':>12} {len(match_ids):>6} rows from matches")

    console.print()
    if apply:
        console.print(f"[bold green]✓ Done. Deleted {len(match_ids)} matches + {total_rows} related rows.[/bold green]")
    else:
        console.print(f"[bold yellow]Dry run complete. Would delete {len(match_ids)} matches + ~{total_rows} related rows.[/bold yellow]")
        console.print(f"[bold]Run with --apply to actually delete:[/bold]")
        console.print(f"  python scripts/cleanup_before_date.py {cutoff_date} --apply")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("Usage: python scripts/cleanup_before_date.py YYYY-MM-DD [--apply]")
        sys.exit(1)

    cutoff = sys.argv[1]
    apply = "--apply" in sys.argv

    # Basic validation
    if len(cutoff) != 10 or cutoff[4] != "-" or cutoff[7] != "-":
        console.print(f"[red]Invalid date format: {cutoff}. Expected YYYY-MM-DD[/red]")
        sys.exit(1)

    run(cutoff, apply)
