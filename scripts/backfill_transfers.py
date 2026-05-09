"""
Backfill team transfers for all teams not yet in team_transfer_cache.

One AF call per team. ~6,000 teams remaining → ~7 min at 70ms/call.

Every team is marked in team_transfer_cache after the attempt (success or error)
so failed teams don't re-enter the queue on the next run.

Usage:
  python scripts/backfill_transfers.py              # full run
  python scripts/backfill_transfers.py --limit 200  # first N teams only
  python scripts/backfill_transfers.py --dry-run    # count only, no API calls
"""

import sys
import time
import argparse
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, BarColumn, MofNCompleteColumn, TimeRemainingColumn, SpinnerColumn

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import get_transfers, parse_transfers, budget
from workers.api_clients.supabase_client import store_team_transfers
from workers.api_clients.db import execute_query, execute_write

console = Console()

RATE_DELAY = 0.07       # 70ms ≈ 14 req/s — safe on Mega plan (900 req/min)
MIN_BUDGET = 2_000      # abort if fewer AF requests remain
BUDGET_CHECK_EVERY = 50 # recheck remaining quota every N teams


def _missing_teams(limit: int | None = None) -> list[int]:
    """Single SQL query: AF team IDs in matches but not yet in team_transfer_cache."""
    sql = """
        SELECT DISTINCT af_id
        FROM (
            SELECT home_team_api_id AS af_id FROM matches WHERE home_team_api_id IS NOT NULL
            UNION
            SELECT away_team_api_id AS af_id FROM matches WHERE away_team_api_id IS NOT NULL
        ) t
        WHERE af_id NOT IN (SELECT team_api_id FROM team_transfer_cache)
        ORDER BY af_id
    """
    if limit:
        sql += f" LIMIT {limit}"
    rows = execute_query(sql)
    return [r["af_id"] for r in rows]


def _mark_fetched(team_api_id: int) -> None:
    """Record team as attempted in cache — prevents re-queuing on error."""
    execute_write(
        "INSERT INTO team_transfer_cache (team_api_id, fetched_at) VALUES (%s, NOW())"
        " ON CONFLICT (team_api_id) DO UPDATE SET fetched_at = NOW()",
        (team_api_id,),
    )


def run(limit: int | None = None, dry_run: bool = False) -> int:
    """Fetch and store transfers for all uncovered teams. Returns records stored."""
    missing = _missing_teams(limit)

    if not missing:
        console.print("[green]Transfers backfill complete — all teams already covered.[/green]")
        return 0

    console.print(f"[cyan]Transfers backfill: {len(missing)} teams to fetch[/cyan]")

    if dry_run:
        return 0

    remaining = budget.remaining()
    console.print(f"  AF budget: {remaining:,} remaining")
    if remaining < MIN_BUDGET:
        console.print(f"[red]Budget too low ({remaining} < {MIN_BUDGET}). Aborting.[/red]")
        return 0

    stored = 0
    errors = 0

    with Progress(
        SpinnerColumn(),
        "[progress.description]{task.description}",
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("Fetching transfers...", total=len(missing))

        for i, team_af_id in enumerate(missing):
            if i > 0 and i % BUDGET_CHECK_EVERY == 0:
                if budget.remaining() < MIN_BUDGET:
                    console.print(
                        f"\n[yellow]Budget low ({budget.remaining()} remaining) — "
                        f"stopping at {i}/{len(missing)} teams[/yellow]"
                    )
                    break

            try:
                raw = get_transfers(team_af_id)
                if raw:
                    rows = parse_transfers(raw, team_api_id=team_af_id)
                    stored += store_team_transfers(team_af_id, rows)
            except Exception as e:
                errors += 1
                progress.console.print(f"  [yellow]team {team_af_id}: {e}[/yellow]")
            finally:
                _mark_fetched(team_af_id)

            progress.advance(task)
            time.sleep(RATE_DELAY)

    console.print(
        f"\n[bold green]Done: {stored} transfer records stored "
        f"({errors} errors)[/bold green]"
    )
    return stored


def run_batch(batch_size: int = 25) -> None:
    """Scheduler entry point: fetch transfers for the next batch of uncovered teams."""
    missing = _missing_teams(limit=batch_size)
    if not missing:
        return

    stored = 0
    errors = 0
    for team_af_id in missing:
        try:
            raw = get_transfers(team_af_id)
            if raw:
                rows = parse_transfers(raw, team_api_id=team_af_id)
                stored += store_team_transfers(team_af_id, rows)
        except Exception as e:
            errors += 1
            console.print(f"  [yellow]transfers {team_af_id}: {e}[/yellow]")
        finally:
            _mark_fetched(team_af_id)
        time.sleep(RATE_DELAY)

    if stored or errors:
        console.print(f"[dim]backfill_transfers: {stored} records stored, {errors} errors[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill team transfers from API-Football")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max teams to process (default: all remaining)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print count only, make no API calls")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run)
