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

from workers.api_clients.api_football import get_fixtures_by_date, get_fixtures_by_league_season, fixture_to_match_dict, get_leagues
from workers.api_clients.supabase_client import bulk_store_matches
from workers.utils.pipeline_utils import (
    log_pipeline_start, log_pipeline_complete, log_pipeline_failed,
    store_league_coverage, set_daily_featured_leagues,
)

console = Console()


def fetch_and_store_fixtures(target_date: str | None = None, league: int | None = None, season: int | None = None) -> tuple[int, dict[int, str], list[dict]]:
    """
    Fetch fixtures from API-Football and store in Supabase matches table.

    Two modes:
      - by date (default): pass target_date, leave league/season None
      - by league+season (backfill): pass league + season, leave target_date None

    Returns: (stored_count, af_id_to_match_id, af_fixtures_raw)
    """
    if league is not None and season is not None:
        label = f"league={league} season={season}"
    else:
        label = target_date or "today"
    console.print(f"\n[cyan]Fetching fixtures for {label}...[/cyan]")

    af_fixtures_raw = []
    try:
        if league is not None and season is not None:
            af_fixtures_raw = get_fixtures_by_league_season(league, season)
        else:
            af_fixtures_raw = get_fixtures_by_date(target_date)
        console.print(f"  {len(af_fixtures_raw)} fixtures from API-Football")
    except Exception as e:
        console.print(f"  [red]API-Football error: {e}[/red]")
        return 0, {}, []

    if not af_fixtures_raw:
        console.print("[yellow]No fixtures from API-Football today.[/yellow]")
        return 0, {}, []

    # Store API-Football fixtures via bulk_store_matches (BULK-STORE-MATCHES):
    # one batched team SELECT + one bulk dedup SELECT + one execute_values INSERT
    # + one execute_values UPDATE — ~3-5s for 1500 fixtures vs ~15min serial.
    total = len(af_fixtures_raw)
    console.print(f"\n[cyan]Storing {total} fixtures in Supabase...[/cyan]")

    import time as _time
    t_start = _time.monotonic()
    match_dicts = [fixture_to_match_dict(af_fix) for af_fix in af_fixtures_raw]
    try:
        match_ids = bulk_store_matches(match_dicts)
    except Exception as e:
        console.print(f"  [red]Bulk store failed: {e}[/red]")
        return 0, {}, []

    stored = sum(1 for mid in match_ids if mid)
    af_id_to_match_id: dict[int, str] = {}
    for af_fix, mid in zip(af_fixtures_raw, match_ids):
        if not mid:
            continue
        af_id = af_fix.get("fixture", {}).get("id")
        if af_id:
            af_id_to_match_id[af_id] = mid

    elapsed = _time.monotonic() - t_start
    console.print(
        f"  {stored}/{total} fixtures stored, {len(af_id_to_match_id)} AF ID mappings "
        f"({elapsed:.1f}s)"
    )
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


def run_fixtures(target_date: str = None, refresh_leagues: bool = False, league: int | None = None, season: int | None = None):
    """Run fixture fetch pipeline. Callable by scheduler or CLI.

    Default mode: fetches all fixtures for `target_date` (or today).
    Backfill mode: pass league+season → fetches the full competition (skip
    date logic, daily-featured, ops snapshot — those are date-scoped).
    """
    backfill_mode = league is not None and season is not None
    if backfill_mode:
        label = f"league={league} season={season}"
        # pipeline_runs.run_date is a DATE column — use today's date as the
        # logical run_date in backfill mode (the actual league/season lives in metadata).
        run_date_for_log = date.today().isoformat()
    else:
        target_date = target_date or date.today().isoformat()
        label = target_date
        run_date_for_log = target_date
    console.print(f"[bold green]═══ OddsIntel Fixture Fetch: {label} ═══[/bold green]")

    run_id = log_pipeline_start("fetch_fixtures", run_date_for_log)

    try:
        # Refresh league coverage if requested (weekly on Mondays)
        leagues_count = 0
        if refresh_leagues:
            leagues_count = refresh_league_coverage()

        # Fetch and store fixtures
        if backfill_mode:
            stored, af_id_to_match_id, af_fixtures_raw = fetch_and_store_fixtures(
                league=league, season=season
            )
            featured = []
        else:
            stored, af_id_to_match_id, af_fixtures_raw = fetch_and_store_fixtures(target_date)
            # Daily-featured + ops snapshot only apply to date-mode runs
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
                "backfill_mode": backfill_mode,
                "backfill_league": league,
                "backfill_season": season,
            }
        )

        console.print(f"\n[bold green]Done. {stored} fixtures stored.[/bold green]")

        if not backfill_mode:
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
    parser.add_argument("--league", type=int, default=None, help="Backfill mode: AF league id (use with --season)")
    parser.add_argument("--season", type=int, default=None, help="Backfill mode: season year (use with --league)")
    args = parser.parse_args()
    if (args.league is None) != (args.season is None):
        parser.error("--league and --season must be used together")
    run_fixtures(target_date=args.date, refresh_leagues=args.refresh_leagues,
                 league=args.league, season=args.season)


if __name__ == "__main__":
    main()
