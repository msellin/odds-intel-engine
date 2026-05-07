"""
OddsIntel — Fetch Fixtures Job

Standalone job that fetches today's fixtures from API-Football and stores them.
Optionally refreshes league coverage data (weekly, on Mondays).

Schedule: 06:00 UTC daily (before enrichment + odds + betting)
Workflow: .github/workflows/fetch_fixtures.yml

Usage:
  python -m workers.jobs.fetch_fixtures                    # Fetch today's fixtures
  python -m workers.jobs.fetch_fixtures --date 2026-04-29  # Specific date
  python -m workers.jobs.fetch_fixtures --refresh-leagues   # Also refresh league coverage
"""

import sys
import argparse
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import get_fixtures_by_date, fixture_to_match_dict, get_leagues
from workers.api_clients.supabase_client import store_match
from workers.utils.pipeline_utils import (
    log_pipeline_start, log_pipeline_complete, log_pipeline_failed,
    store_league_coverage, set_daily_featured_leagues,
)

console = Console()


def fetch_and_store_fixtures(target_date: str) -> tuple[int, dict[int, str], list[dict]]:
    """
    Fetch all fixtures for a date from API-Football.
    Store in Supabase matches table.

    Returns: (stored_count, af_id_to_match_id, af_fixtures_raw)
    """
    console.print(f"\n[cyan]Fetching fixtures for {target_date}...[/cyan]")

    af_fixtures_raw = []
    try:
        af_fixtures_raw = get_fixtures_by_date(target_date)
        console.print(f"  {len(af_fixtures_raw)} fixtures from API-Football")
    except Exception as e:
        console.print(f"  [red]API-Football error: {e}[/red]")
        return 0, {}, []

    if not af_fixtures_raw:
        console.print("[yellow]No fixtures from API-Football today.[/yellow]")
        return 0, {}, []

    # Store API-Football fixtures
    console.print(f"\n[cyan]Storing {len(af_fixtures_raw)} fixtures in Supabase...[/cyan]")
    stored = 0
    af_id_to_match_id: dict[int, str] = {}

    for af_fix in af_fixtures_raw:
        match_dict = fixture_to_match_dict(af_fix)
        try:
            match_id = store_match(match_dict)
            af_id = af_fix.get("fixture", {}).get("id")
            if match_id and af_id:
                af_id_to_match_id[af_id] = match_id
            stored += 1
        except Exception as e:
            console.print(f"  [yellow]Could not store {match_dict.get('home_team')} vs {match_dict.get('away_team')}: {e}[/yellow]")

    console.print(f"  {stored} fixtures stored, {len(af_id_to_match_id)} AF ID mappings")
    return stored, af_id_to_match_id, af_fixtures_raw


def refresh_league_coverage():
    """Fetch all leagues from API-Football and update coverage flags in DB."""
    console.print("\n[cyan]Refreshing league coverage from API-Football...[/cyan]")
    try:
        leagues = get_leagues(current=True)
        console.print(f"  {len(leagues)} leagues returned from API-Football")

        stored = store_league_coverage(leagues)
        console.print(f"  {stored} leagues coverage updated in DB")
        return stored
    except Exception as e:
        console.print(f"  [red]League coverage refresh failed: {e}[/red]")
        return 0


def run_fixtures(target_date: str = None, refresh_leagues: bool = False):
    """Run fixture fetch pipeline. Callable by scheduler or CLI."""
    target_date = target_date or date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Fixture Fetch: {target_date} ═══[/bold green]")

    run_id = log_pipeline_start("fetch_fixtures", target_date)

    try:
        # Refresh league coverage if requested (weekly on Mondays)
        leagues_count = 0
        if refresh_leagues:
            leagues_count = refresh_league_coverage()

        # Fetch and store fixtures
        stored, af_id_to_match_id, af_fixtures_raw = fetch_and_store_fixtures(target_date)

        # Set daily featured leagues (continental cups with matches today → priority 1)
        featured = set_daily_featured_leagues(af_fixtures_raw)
        if featured:
            console.print(f"\n[yellow]Featured today:[/yellow] {', '.join(featured)}")

        log_pipeline_complete(
            run_id,
            fixtures_count=stored,
            records_count=stored,
            metadata={
                "af_fixtures": len(af_fixtures_raw),
                "af_id_mappings": len(af_id_to_match_id),
                "leagues_refreshed": leagues_count,
                "featured_leagues": featured,
            }
        )

        console.print(f"\n[bold green]Done. {stored} fixtures stored.[/bold green]")

        from workers.api_clients.supabase_client import write_ops_snapshot
        write_ops_snapshot(target_date)

    except Exception as e:
        console.print(f"\n[red]Pipeline failed: {e}[/red]")
        if run_id:
            log_pipeline_failed(run_id, str(e))
        raise


def main():
    parser = argparse.ArgumentParser(description="Fetch fixtures and optionally refresh league coverage")
    parser.add_argument("--date", type=str, default=None, help="Date to fetch (YYYY-MM-DD, default: today)")
    parser.add_argument("--refresh-leagues", action="store_true", help="Also refresh league coverage data")
    args = parser.parse_args()
    run_fixtures(target_date=args.date, refresh_leagues=args.refresh_leagues)


if __name__ == "__main__":
    main()
