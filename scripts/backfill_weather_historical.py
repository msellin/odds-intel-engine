"""FEATURE-COVERAGE-BACKFILL-2026-08-21 — Backfill historical weather data.

Weather is fetched via Open-Meteo (FREE, no AF quota cost). Historical
coverage sat at 7.2% because only today's upcoming matches were fetched.
Run once to catch up. Geocode cache builds as it runs — subsequent
same-venue matches are free.

Run:
  python3 scripts/backfill_weather_historical.py --dry-run
  python3 scripts/backfill_weather_historical.py
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

from workers.jobs.fetch_weather import fetch_weather

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-04")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.5)
    args = ap.parse_args()

    start = date.fromisoformat(args.since)
    end = date.today()
    dates = [start + timedelta(days=i) for i in range((end - start).days)]

    console.print(f"[bold]Weather backfill — {len(dates)} dates from {start} to {end}[/bold]")
    console.print(f"  Cost: FREE (Open-Meteo, no API key)")
    if args.dry_run:
        console.print("[yellow]Dry run.[/yellow]")
        return

    total = 0
    for i, d in enumerate(dates, start=1):
        ds = d.isoformat()
        console.print(f"\n[cyan]({i}/{len(dates)}) Weather for {ds}[/cyan]")
        try:
            total += fetch_weather(target_date=ds)
        except Exception as e:
            console.print(f"[red]  ✗ {ds} failed: {e}[/red]")
        if args.sleep > 0:
            time.sleep(args.sleep)
    console.print(f"\n[green]✓ Backfilled {total} weather rows across {len(dates)} dates[/green]")


if __name__ == "__main__":
    main()
