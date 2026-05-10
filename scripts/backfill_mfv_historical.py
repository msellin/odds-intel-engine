"""
Stage 0e — match_feature_vectors historical rebuild.

`build_match_feature_vectors(client, date_str)` runs nightly for `yesterday`
only. For the historical backfill we want it to run for every distinct date
where we have finished matches. After Stages 0a (ELO) and 0b (form) finished,
rebuilding MFV picks up the new feature coverage.

Walks distinct match dates ASC and rebuilds all rows for each date.
Idempotent — UPSERTs on (match_id), so re-running is safe.

Usage:
    python3 scripts/backfill_mfv_historical.py
    python3 scripts/backfill_mfv_historical.py --from 2024-01-01
    python3 scripts/backfill_mfv_historical.py --skip-existing-dates  # only rebuild dates with no MFV row yet
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from workers.api_clients.supabase_client import build_match_feature_vectors, execute_query

console = Console()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", default=None)
    p.add_argument("--to", dest="date_to", default=None)
    p.add_argument("--skip-existing-dates", action="store_true",
                   help="Only rebuild dates with NO MFV rows. Default: rebuild all.")
    args = p.parse_args()

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    date_to = args.date_to or yesterday

    where = ["status = 'finished'", "date <= %s"]
    params = [f"{date_to}T23:59:59"]
    if args.date_from:
        where.insert(0, "date >= %s")
        params.insert(0, f"{args.date_from}T00:00:00")

    rows = execute_query(
        "SELECT DISTINCT DATE(date) AS d FROM matches WHERE " +
        " AND ".join(where) + " ORDER BY d ASC",
        params,
    )
    all_dates = [r["d"].isoformat() if hasattr(r["d"], "isoformat") else str(r["d"]) for r in rows]
    console.print(f"[cyan]Found {len(all_dates):,} distinct match dates in scope.[/cyan]")

    if args.skip_existing_dates:
        existing = execute_query(
            "SELECT DISTINCT DATE(match_date) AS d FROM match_feature_vectors",
            (),
        )
        have = {r["d"].isoformat() if hasattr(r["d"], "isoformat") else str(r["d"]) for r in existing}
        all_dates = [d for d in all_dates if d not in have]
        console.print(f"[cyan]After --skip-existing-dates: {len(all_dates):,} dates remaining.[/cyan]")

    if not all_dates:
        console.print("[green]Nothing to do.[/green]")
        return

    total_upserted = 0
    failed_dates: list[str] = []

    with Progress(TextColumn("[bold blue]MFV"), BarColumn(),
                  TextColumn("{task.completed}/{task.total} dates"),
                  TextColumn("[green]{task.fields[upserted]:,} rows[/green]"),
                  TimeRemainingColumn(), console=console) as bar:
        task = bar.add_task("walk", total=len(all_dates), upserted=0)

        for d in all_dates:
            try:
                n = build_match_feature_vectors(None, d)
                total_upserted += n or 0
            except Exception as e:
                failed_dates.append(d)
                console.print(f"  [red]MFV failed for {d}: {e}[/red]")
            bar.update(task, advance=1, upserted=total_upserted)

    console.print(
        f"\n[bold green]✓ MFV backfill complete — {total_upserted:,} rows upserted "
        f"across {len(all_dates) - len(failed_dates):,} dates.[/bold green]"
    )
    if failed_dates:
        console.print(f"[yellow]⚠ {len(failed_dates)} dates failed: {failed_dates[:5]}{'...' if len(failed_dates) > 5 else ''}[/yellow]")


if __name__ == "__main__":
    main()
