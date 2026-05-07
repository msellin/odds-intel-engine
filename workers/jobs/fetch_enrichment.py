"""
OddsIntel — Fetch Enrichment Job

Standalone job that enriches today's fixtures with:
  T2: Team statistics (Tier A only)
  T3: Injuries (batched)
  T9: League standings
  T10: H2H history

Coverage-aware: skips endpoints for leagues that AF doesn't support.
Reads fixtures from DB (depends on fetch_fixtures having run first).

Schedule:
  06:15 UTC — Wave 1: all components
  12:00 UTC — Wave 2: injuries + standings refresh
  16:00 UTC — Wave 3: injuries + standings refresh

Usage:
  python -m workers.jobs.fetch_enrichment                           # All components
  python -m workers.jobs.fetch_enrichment --components injuries,standings  # Specific
  python -m workers.jobs.fetch_enrichment --date 2026-04-29         # Specific date
"""

import sys
import argparse
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import (
    get_team_statistics, parse_team_statistics,
    get_injuries_batched, parse_injuries,
    get_standings, parse_standings,
    get_h2h, parse_h2h,
)
from workers.api_clients.supabase_client import (
    store_team_season_stats, store_match_injuries,
    store_league_standings, store_match_h2h,
)
from workers.api_clients.db import execute_query
from workers.utils.pipeline_utils import (
    check_fixtures_ready, log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed, log_pipeline_skipped, get_league_coverage_map,
    league_has_coverage,
)

console = Console()

ALL_COMPONENTS = {"injuries", "team_stats", "standings", "h2h"}


def _build_fixture_meta(target_date: str) -> dict[int, dict]:
    """Load today's fixtures from DB and build per-fixture metadata for enrichment calls."""
    today = date.fromisoformat(target_date)
    season = today.year if today.month >= 7 else today.year - 1

    next_day = (today.toordinal() + 1)
    from datetime import date as d
    next_date = d.fromordinal(next_day).isoformat()

    # Get matches with their AF IDs and team/league info
    matches = execute_query(
        "SELECT id, api_football_id, home_team_id, away_team_id, league_id FROM matches "
        "WHERE date >= %s AND date < %s",
        [f"{target_date}T00:00:00Z", f"{next_date}T00:00:00Z"]
    )

    if not matches:
        return {}

    # Get team AF IDs
    team_ids = set()
    league_ids = set()
    for m in matches:
        team_ids.add(m["home_team_id"])
        team_ids.add(m["away_team_id"])
        league_ids.add(m["league_id"])

    team_af_ids = {}
    tr = execute_query(
        "SELECT id, name FROM teams WHERE id = ANY(%s::uuid[])",
        [list(team_ids)]
    )
    for t in tr:
        team_af_ids[t["id"]] = t

    # Get league AF IDs
    league_af_ids = {}
    lr = execute_query(
        "SELECT id, api_football_id, tier FROM leagues WHERE id = ANY(%s::uuid[])",
        [list(league_ids)]
    )
    for league in lr:
        league_af_ids[league["id"]] = league

    # Build fixture metadata keyed by AF fixture ID
    fixture_meta = {}
    for m in matches:
        af_id = m.get("api_football_id")
        if not af_id:
            continue

        league_info = league_af_ids.get(m["league_id"], {})

        fixture_meta[af_id] = {
            "match_id": m["id"],
            "league_id": m["league_id"],
            "home_team_api_id": None,  # We need AF team IDs from the raw fixture
            "away_team_api_id": None,
            "league_api_id": league_info.get("api_football_id"),
            "league_tier": league_info.get("tier", 3),
            "season": season,
        }

    # We need AF team IDs — fetch from matches.api_football_id via AF fixtures
    # Since we don't store AF team IDs on the teams table, we need to get them
    # from the raw fixture data. Let's query matches that have h2h_raw or
    # use the AF fixtures endpoint to get team IDs.
    # For now, get AF team IDs from league_standings (team_api_id) or a direct lookup.

    # Simpler approach: fetch the AF fixture data for today to get team AF IDs
    # This costs 1 API call and gives us everything we need.
    from workers.api_clients.api_football import get_fixtures_by_date
    try:
        af_fixtures = get_fixtures_by_date(target_date)
        for af_fix in af_fixtures:
            fid = af_fix.get("fixture", {}).get("id")
            if fid in fixture_meta:
                teams = af_fix.get("teams", {})
                league = af_fix.get("league", {})
                fixture_meta[fid]["home_team_api_id"] = teams.get("home", {}).get("id")
                fixture_meta[fid]["away_team_api_id"] = teams.get("away", {}).get("id")
                # Update league_api_id if we didn't have it from DB
                if not fixture_meta[fid]["league_api_id"]:
                    fixture_meta[fid]["league_api_id"] = league.get("id")
                fixture_meta[fid]["season"] = league.get("season") or season
    except Exception as e:
        console.print(f"  [yellow]Could not fetch AF fixtures for team IDs: {e}[/yellow]")

    return fixture_meta


def fetch_injuries(fixture_meta: dict, coverage_map: dict) -> int:
    """T3: Fetch and store injuries (batched)."""
    console.print("\n[cyan]T3: Fetching injuries (batched)...[/cyan]")

    # Filter to fixtures with coverage
    fixture_ids = []
    for fid, meta in fixture_meta.items():
        if league_has_coverage(coverage_map, meta.get("league_id", ""), "injuries"):
            if meta.get("match_id"):
                fixture_ids.append(fid)

    if not fixture_ids:
        console.print("  No fixtures eligible for injury fetch")
        return 0

    console.print(f"  Fetching injuries for {len(fixture_ids)} fixtures...")
    injuries_by_fixture = {}
    try:
        injuries_by_fixture = get_injuries_batched(fixture_ids)
    except Exception as e:
        console.print(f"  [yellow]Injuries fetch error: {e}[/yellow]")
        return 0

    stored = 0
    for fid, injuries in injuries_by_fixture.items():
        if not injuries:
            continue
        meta = fixture_meta.get(fid, {})
        match_id = meta.get("match_id")
        if not match_id:
            continue
        parsed = parse_injuries(injuries, home_team_api_id=meta.get("home_team_api_id"))
        stored += store_match_injuries(match_id, fid, parsed)

    console.print(f"  {stored} injury records stored")
    return stored


def fetch_team_stats(fixture_meta: dict, coverage_map: dict) -> int:
    """T2: Fetch team statistics (Tier A / tier 1 only)."""
    console.print("\n[cyan]T2: Fetching team statistics (Tier A only)...[/cyan]")

    stored = 0
    seen: set[tuple] = set()

    for fid, meta in fixture_meta.items():
        if meta.get("league_tier", 3) != 1:
            continue
        if not league_has_coverage(coverage_map, meta.get("league_id", ""), "statistics_fixtures"):
            continue

        lg_api_id = meta.get("league_api_id")
        fix_season = meta.get("season")

        for api_id in [meta.get("home_team_api_id"), meta.get("away_team_api_id")]:
            if not api_id or not lg_api_id or not fix_season:
                continue
            key = (api_id, lg_api_id, fix_season)
            if key in seen:
                continue
            seen.add(key)
            try:
                raw = get_team_statistics(api_id, lg_api_id, fix_season)
                if raw:
                    parsed = parse_team_statistics(raw)
                    store_team_season_stats(api_id, lg_api_id, fix_season, parsed)
                    stored += 1
            except Exception as e:
                console.print(f"  [yellow]Team stats failed for team {api_id} league {lg_api_id}: {e}[/yellow]")
                continue

    console.print(f"  {stored} team stat records stored ({len(seen)} unique Tier A teams)")
    return stored


def fetch_standings(fixture_meta: dict, coverage_map: dict) -> int:
    """T9: Fetch league standings."""
    console.print("\n[cyan]T9: Fetching league standings...[/cyan]")

    seen: set[tuple] = set()
    stored = 0

    for fid, meta in fixture_meta.items():
        league_api_id = meta.get("league_api_id")
        fix_season = meta.get("season")
        if not league_api_id or not fix_season:
            continue
        if not league_has_coverage(coverage_map, meta.get("league_id", ""), "standings"):
            continue
        key = (league_api_id, fix_season)
        if key in seen:
            continue
        seen.add(key)

        try:
            raw = get_standings(league_api_id, fix_season)
            if not raw:
                continue
            rows = parse_standings(raw)
            stored += store_league_standings(league_api_id, fix_season, rows)
        except Exception as e:
            console.print(f"  [yellow]Standings failed for league {league_api_id}: {e}[/yellow]")
            continue

    console.print(f"  {stored} standing rows stored across {len(seen)} leagues")
    return stored


def fetch_h2h(fixture_meta: dict) -> int:
    """T10: Fetch H2H history."""
    console.print("\n[cyan]T10: Fetching H2H history...[/cyan]")

    stored = 0
    for fid, meta in fixture_meta.items():
        match_id = meta.get("match_id")
        home_id = meta.get("home_team_api_id")
        away_id = meta.get("away_team_api_id")
        if not match_id or not home_id or not away_id:
            continue

        try:
            raw = get_h2h(home_id, away_id, last=10)
            if not raw:
                continue
            parsed = parse_h2h(raw, home_team_api_id=home_id)
            store_match_h2h(match_id, parsed)
            stored += 1
        except Exception as e:
            console.print(f"  [yellow]H2H failed for {home_id} vs {away_id}: {e}[/yellow]")
            continue

    console.print(f"  {stored} H2H records stored")
    return stored


def run_enrichment(target_date: str = None, components: set = None):
    """Run enrichment pipeline. Callable by scheduler or CLI."""
    target_date = target_date or date.today().isoformat()
    components = components or ALL_COMPONENTS

    console.print(f"[bold green]═══ OddsIntel Enrichment: {target_date} ═══[/bold green]")
    console.print(f"  Components: {', '.join(sorted(components))}")

    # Readiness check
    if not check_fixtures_ready(target_date):
        console.print("[yellow]Fixtures not ready yet — skipping enrichment.[/yellow]")
        log_pipeline_skipped("fetch_enrichment", "Fixtures not ready", target_date)
        return

    run_id = log_pipeline_start("fetch_enrichment", target_date)

    try:
        # Build fixture metadata from DB + AF
        fixture_meta = _build_fixture_meta(target_date)
        console.print(f"  {len(fixture_meta)} fixtures with AF IDs")

        if not fixture_meta:
            console.print("[yellow]No fixtures found for enrichment.[/yellow]")
            log_pipeline_complete(run_id, fixtures_count=0, records_count=0)
            return

        # Load coverage map
        coverage_map = get_league_coverage_map()
        console.print(f"  {len(coverage_map)} leagues with coverage data")

        total_records = 0

        if "injuries" in components:
            total_records += fetch_injuries(fixture_meta, coverage_map)

        if "team_stats" in components:
            total_records += fetch_team_stats(fixture_meta, coverage_map)

        if "standings" in components:
            total_records += fetch_standings(fixture_meta, coverage_map)

        if "h2h" in components:
            total_records += fetch_h2h(fixture_meta)

        log_pipeline_complete(
            run_id,
            fixtures_count=len(fixture_meta),
            records_count=total_records,
            metadata={"components": list(components)}
        )

        console.print(f"\n[bold green]Done. {total_records} records stored.[/bold green]")

        from workers.api_clients.supabase_client import write_ops_snapshot
        write_ops_snapshot(target_date)

    except Exception as e:
        console.print(f"\n[red]Enrichment failed: {e}[/red]")
        if run_id:
            log_pipeline_failed(run_id, str(e))
        raise


def main():
    parser = argparse.ArgumentParser(description="Enrich today's fixtures with team stats, injuries, standings, H2H")
    parser.add_argument("--date", type=str, default=None, help="Date (YYYY-MM-DD, default: today)")
    parser.add_argument("--components", type=str, default="all",
                        help="Comma-separated: injuries,team_stats,standings,h2h or 'all'")
    args = parser.parse_args()
    components = ALL_COMPONENTS if args.components == "all" else set(args.components.split(","))
    run_enrichment(target_date=args.date, components=components)


if __name__ == "__main__":
    main()
