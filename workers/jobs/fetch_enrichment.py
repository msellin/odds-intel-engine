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
  python -m workers.jobs.fetch_enrichment                                        # All components
  python -m workers.jobs.fetch_enrichment --components injuries,standings        # Specific components
  python -m workers.jobs.fetch_enrichment --components team_stats --team 12345   # One team's stats only
  python -m workers.jobs.fetch_enrichment --date 2026-04-29                      # Specific date
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
    get_coaches, parse_coaches,
    get_venue, parse_venue,
    get_sidelined, parse_sidelined,
    get_transfers, parse_transfers,
)
from workers.api_clients.supabase_client import (
    store_team_season_stats, store_match_injuries,
    store_league_standings, store_match_h2h,
    store_team_coaches, store_venues,
    store_player_sidelined, store_team_transfers,
)
from workers.api_clients.db import execute_query
from workers.utils.pipeline_utils import (
    check_fixtures_ready, log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed, log_pipeline_skipped, get_league_coverage_map,
    league_has_coverage,
)

console = Console()

ALL_COMPONENTS = {"injuries", "team_stats", "standings", "h2h", "coaches", "venues", "sidelined", "transfers"}


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
                venue_id = af_fix.get("fixture", {}).get("venue", {}).get("id")
                fixture_meta[fid]["venue_af_id"] = venue_id
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


def fetch_team_stats(fixture_meta: dict, coverage_map: dict, team_af_id: int = None) -> int:
    """T2: Fetch team statistics (Tier A / tier 1 only).

    Pass team_af_id to target a single team instead of all today's Tier A teams.
    """
    if team_af_id:
        console.print(f"\n[cyan]T2: Fetching team statistics for team {team_af_id} only...[/cyan]")
    else:
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
            if team_af_id and api_id != team_af_id:
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
    """T10: Fetch H2H history — Tier 1 only, with same-day cache.

    Two optimisations over the naive approach:
    - Tier 1 only: H2H matters most for top leagues where the model has enough
      data to act on it. Cuts ~440 daily calls down to ~50-80.
    - Same-day cache: h2h_raw IS NOT NULL on the match means we already fetched
      it today. Intraday enrichment runs (10:30, 13:00, 16:00) make 0 H2H calls
      once the morning run has populated them.
    """
    console.print("\n[cyan]T10: Fetching H2H history (Tier 1, same-day cache)...[/cyan]")

    # Tier 1 only — H2H too expensive (1 call/fixture) to run for all tiers
    tier1 = {
        fid: meta for fid, meta in fixture_meta.items()
        if meta.get("league_tier", 3) == 1
        and meta.get("match_id")
        and meta.get("home_team_api_id")
        and meta.get("away_team_api_id")
    }

    if not tier1:
        console.print("  No Tier 1 fixtures — skipping H2H")
        return 0

    # Same-day cache: skip matches that already have h2h_raw populated
    match_ids = [meta["match_id"] for meta in tier1.values()]
    try:
        cached_rows = execute_query(
            "SELECT id FROM matches WHERE id = ANY(%s::uuid[]) AND h2h_raw IS NOT NULL",
            [match_ids]
        )
        cached_ids = {r["id"] for r in cached_rows}
    except Exception:
        cached_ids = set()

    to_fetch = {fid: meta for fid, meta in tier1.items()
                if meta["match_id"] not in cached_ids}

    console.print(f"  {len(tier1)} Tier 1 fixtures, {len(cached_ids)} cached, {len(to_fetch)} to fetch")

    if not to_fetch:
        return 0

    stored = 0
    for fid, meta in to_fetch.items():
        match_id = meta["match_id"]
        home_id = meta["home_team_api_id"]
        away_id = meta["away_team_api_id"]
        try:
            raw = get_h2h(home_id, away_id, last=5)
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


def fetch_coaches(fixture_meta: dict) -> int:
    """MGR-CHANGE: Fetch and cache coach history for all teams playing today.

    Calls /coachs?team={id} once per unique team (skip if fetched within 48h).
    Only the morning wave (full enrichment) runs this — coaches don't change intraday.
    """
    console.print("\n[cyan]Coaches: Fetching manager change data...[/cyan]")
    from datetime import datetime, timezone, timedelta
    from workers.api_clients.db import execute_query as _eq

    # Collect unique AF team IDs from today's fixtures
    team_af_ids: set[int] = set()
    for meta in fixture_meta.values():
        if meta.get("home_team_api_id"):
            team_af_ids.add(meta["home_team_api_id"])
        if meta.get("away_team_api_id"):
            team_af_ids.add(meta["away_team_api_id"])

    if not team_af_ids:
        console.print("  No team AF IDs available — skipping coaches fetch")
        return 0

    # Skip teams whose coach data was fetched within 48h (coaches rarely change)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    try:
        recent_rows = _eq(
            "SELECT DISTINCT team_af_id FROM team_coaches WHERE fetched_at > %s AND team_af_id = ANY(%s)",
            [cutoff, list(team_af_ids)]
        )
        recently_fetched = {r["team_af_id"] for r in recent_rows}
    except Exception:
        recently_fetched = set()

    to_fetch = list(team_af_ids - recently_fetched)[:50]  # cap per run
    console.print(f"  {len(team_af_ids)} teams total, {len(recently_fetched)} cached, {len(to_fetch)} to fetch (capped at 50)")

    stored = 0
    for team_af_id in to_fetch:
        try:
            raw = get_coaches(team_af_id)
            if not raw:
                continue
            entries = parse_coaches(raw)
            stored += store_team_coaches(team_af_id, entries)
        except Exception as e:
            console.print(f"  [yellow]Coaches fetch failed for team {team_af_id}: {e}[/yellow]")

    console.print(f"  {stored} coach records upserted across {len(to_fetch)} teams")
    return stored


def fetch_venues(fixture_meta: dict) -> int:
    """AF-VENUES: Fetch and cache venue surface + capacity.

    One call per unique venue ID. Skips venues already in the venues table.
    Backfills venue_af_id on today's matches so the signal block can join.
    """
    console.print("\n[cyan]Venues: Fetching surface + capacity...[/cyan]")
    from workers.api_clients.db import execute_query as _eq, execute_write as _ew

    # Collect unique venue_af_ids from fixture_meta
    venue_to_matches: dict[int, list[str]] = {}
    for meta in fixture_meta.values():
        vid = meta.get("venue_af_id")
        mid = meta.get("match_id")
        if vid and mid:
            venue_to_matches.setdefault(vid, []).append(mid)

    if not venue_to_matches:
        console.print("  No venue IDs available — skipping")
        return 0

    all_venue_ids = list(venue_to_matches.keys())

    # Backfill venue_af_id on matches that don't have it yet
    try:
        for vid, match_ids in venue_to_matches.items():
            for mid in match_ids:
                _ew(
                    "UPDATE matches SET venue_af_id = %s WHERE id = %s AND venue_af_id IS NULL",
                    (vid, mid)
                )
    except Exception as e:
        console.print(f"  [yellow]venue_af_id backfill error: {e}[/yellow]")

    # Check which venues are already cached
    try:
        cached_rows = _eq(
            "SELECT af_id FROM venues WHERE af_id = ANY(%s)",
            (all_venue_ids,)
        )
        cached_ids = {r["af_id"] for r in cached_rows}
    except Exception:
        cached_ids = set()

    to_fetch = [vid for vid in all_venue_ids if vid not in cached_ids]
    console.print(f"  {len(all_venue_ids)} venues total, {len(cached_ids)} cached, {len(to_fetch)} to fetch")

    fetched = []
    for vid in to_fetch:
        try:
            raw = get_venue(vid)
            if raw:
                fetched.append(parse_venue(raw))
        except Exception as e:
            console.print(f"  [yellow]Venue fetch failed for {vid}: {e}[/yellow]")

    stored = store_venues(fetched)
    console.print(f"  {stored} venue records upserted")
    return stored


def fetch_player_sidelined(fixture_meta: dict) -> int:
    """Fetch full injury history for each player currently injured in today's fixtures.

    Reads player_ids from match_injuries (populated by T3 fetch_injuries), fetches the
    full sidelined career history per player, and stores in player_sidelined.

    Uses a 7-day cache — skips players already fetched recently to stay quota-efficient.
    Only runs as part of the morning enrichment wave (not intraday refreshes).
    """
    console.print("\n[cyan]Sidelined: Fetching player injury histories...[/cyan]")
    from datetime import datetime, timezone, timedelta

    match_ids = [meta["match_id"] for meta in fixture_meta.values() if meta.get("match_id")]
    if not match_ids:
        console.print("  No matches — skipping sidelined fetch")
        return 0

    # Get all injured players for today's fixtures (already stored by T3 fetch_injuries)
    inj_rows = execute_query(
        "SELECT DISTINCT player_id, player_name, team_api_id "
        "FROM match_injuries WHERE match_id = ANY(%s::uuid[]) AND player_id IS NOT NULL",
        [match_ids]
    )
    if not inj_rows:
        console.print("  No injury records found — skipping sidelined fetch")
        return 0

    all_player_ids = [r["player_id"] for r in inj_rows]

    # 7-day cache: skip players whose history was already fetched recently
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    try:
        cached = execute_query(
            "SELECT DISTINCT player_id FROM player_sidelined WHERE player_id = ANY(%s) AND created_at > %s",
            [all_player_ids, cutoff]
        )
        cached_ids = {r["player_id"] for r in cached}
    except Exception:
        cached_ids = set()

    player_lookup = {r["player_id"]: r for r in inj_rows}
    to_fetch = [pid for pid in all_player_ids if pid not in cached_ids]
    console.print(f"  {len(all_player_ids)} injured players, {len(cached_ids)} cached, {len(to_fetch)} to fetch")

    stored = 0
    for player_id in to_fetch:
        info = player_lookup.get(player_id, {})
        try:
            raw = get_sidelined(player_id)
            if not raw:
                continue
            rows = parse_sidelined(
                raw,
                player_id=player_id,
                player_name=info.get("player_name"),
                team_api_id=info.get("team_api_id"),
            )
            stored += store_player_sidelined(rows)
        except Exception as e:
            console.print(f"  [yellow]Sidelined fetch failed for player {player_id}: {e}[/yellow]")

    console.print(f"  {stored} sidelined records stored across {len(to_fetch)} players")
    return stored


def fetch_transfers(fixture_meta: dict) -> int:
    """Fetch recent transfer history for every team playing today.

    Calls /transfers?team={id} once per unique team AF ID, with a 30-day cache.
    Cache is tracked in team_transfer_cache so teams with no transfer activity
    are still marked fetched and not re-fetched every run.
    Stores into team_transfers. Powers the squad_disruption signal in the betting pipeline.
    """
    console.print("\n[cyan]Transfers: Fetching team transfer history...[/cyan]")
    from datetime import datetime, timezone, timedelta
    from workers.api_clients.db import execute_query as _eq, execute_write as _ew

    team_af_ids: set[int] = set()
    for meta in fixture_meta.values():
        if meta.get("home_team_api_id"):
            team_af_ids.add(meta["home_team_api_id"])
        if meta.get("away_team_api_id"):
            team_af_ids.add(meta["away_team_api_id"])

    if not team_af_ids:
        console.print("  No team AF IDs — skipping transfers fetch")
        return 0

    # 30-day cache tracked in team_transfer_cache (independent of whether any rows were stored)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        cached = _eq(
            "SELECT team_api_id FROM team_transfer_cache WHERE team_api_id = ANY(%s) AND fetched_at > %s",
            [list(team_af_ids), cutoff]
        )
        cached_ids = {r["team_api_id"] for r in cached}
    except Exception:
        cached_ids = set()

    to_fetch = list(team_af_ids - cached_ids)[:100]  # cap per run — rest picked up next day
    console.print(f"  {len(team_af_ids)} teams, {len(cached_ids)} cached, {len(to_fetch)} to fetch (capped at 100)")

    stored = 0
    for team_af_id in to_fetch:
        try:
            raw = get_transfers(team_af_id)
            if raw:
                rows = parse_transfers(raw, team_api_id=team_af_id)
                stored += store_team_transfers(team_af_id, rows)
            # Always mark fetched — even teams with no transfers shouldn't be re-fetched daily
            _ew(
                "INSERT INTO team_transfer_cache (team_api_id, fetched_at) VALUES (%s, NOW())"
                " ON CONFLICT (team_api_id) DO UPDATE SET fetched_at = NOW()",
                (team_af_id,)
            )
        except Exception as e:
            console.print(f"  [yellow]Transfers fetch failed for team {team_af_id}: {e}[/yellow]")

    console.print(f"  {stored} transfer records stored across {len(to_fetch)} teams")
    return stored


def run_enrichment(target_date: str = None, components: set = None, team_af_id: int = None):
    """Run enrichment pipeline. Callable by scheduler or CLI."""
    target_date = target_date or date.today().isoformat()
    components = components or ALL_COMPONENTS

    console.print(f"[bold green]═══ OddsIntel Enrichment: {target_date} ═══[/bold green]")
    console.print(f"  Components: {', '.join(sorted(components))}")
    if team_af_id:
        console.print(f"  Team filter: AF team ID {team_af_id}")

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
            total_records += fetch_team_stats(fixture_meta, coverage_map, team_af_id=team_af_id)

        if "standings" in components:
            total_records += fetch_standings(fixture_meta, coverage_map)

        if "h2h" in components:
            total_records += fetch_h2h(fixture_meta)

        if "coaches" in components:
            total_records += fetch_coaches(fixture_meta)

        if "venues" in components:
            total_records += fetch_venues(fixture_meta)

        if "sidelined" in components:
            total_records += fetch_player_sidelined(fixture_meta)

        if "transfers" in components:
            total_records += fetch_transfers(fixture_meta)

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
                        help="Comma-separated: injuries,team_stats,standings,h2h,coaches,venues,sidelined,transfers or 'all'")
    parser.add_argument("--team", type=int, default=None,
                        help="AF team ID — limit team_stats to a single team (use with --components team_stats)")
    args = parser.parse_args()
    components = ALL_COMPONENTS if args.components == "all" else set(args.components.split(","))
    run_enrichment(target_date=args.date, components=components, team_af_id=args.team)


if __name__ == "__main__":
    main()
