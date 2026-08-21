"""FEATURE-COVERAGE-BACKFILL-2026-08-21 — Backfill historical injury data.

Runs the injuries component of fetch_enrichment.py for every date since
CALIBRATED_SINCE (2026-05-04). One AF /injuries?date= call per date
(~110 total calls). Cheap given the coverage_injuries filter is now
dropped in fetch_enrichment.fetch_injuries — every league's data is
stored instead of the previous 10-of-445 leagues.

Idempotent — store_match_injuries upserts on (match_id, player_id) so
re-runs don't duplicate.

Run:
  python3 scripts/backfill_injuries_historical.py --dry-run     # count only
  python3 scripts/backfill_injuries_historical.py               # execute all dates
  python3 scripts/backfill_injuries_historical.py --since 2026-06-01  # partial
"""
from __future__ import annotations
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console

from workers.jobs.fetch_enrichment import run_enrichment

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-04", help="Start date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=1.0, help="Sleep seconds between dates")
    args = ap.parse_args()

    start = date.fromisoformat(args.since)
    end = date.today()
    dates = [start + timedelta(days=i) for i in range((end - start).days)]

    console.print(f"[bold]Injury backfill — {len(dates)} dates from {start} to {end}[/bold]")
    console.print(f"  Estimated AF quota cost: ~{len(dates)} calls (1 per date)")
    if args.dry_run:
        console.print("[yellow]Dry run — no fetches. Pass without --dry-run to execute.[/yellow]")
        return

    for i, d in enumerate(dates, start=1):
        ds = d.isoformat()
        console.print(f"\n[cyan]({i}/{len(dates)}) Fetching injuries for {ds}[/cyan]")
        try:
            run_enrichment(target_date=ds, components={"injuries"})
        except Exception as e:
            console.print(f"[red]  ✗ {ds} failed: {e}[/red]")
        if args.sleep > 0:
            time.sleep(args.sleep)
    console.print(f"\n[green]✓ Completed backfill for {len(dates)} dates[/green]")


if __name__ == "__main__":
    main()
