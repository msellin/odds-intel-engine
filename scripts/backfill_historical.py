"""
OddsIntel — Historical Data Backfill

Fetches historical match data (fixtures, odds, statistics, events) from API-Football
during spare quota windows. Designed to run as GitHub Actions cron jobs.

Usage:
    python scripts/backfill_historical.py --phase 1 --batch-size 500
    python scripts/backfill_historical.py --phase 1 --dry-run
    python scripts/backfill_historical.py --phase 2 --max-requests 5000

Safety:
    - Aborts if < 10,000 API requests remaining today
    - Idempotent: --skip-existing is default, re-running picks up where it left off
    - Tracks progress in backfill_progress table
    - Respects built-in 150ms rate throttle (6.7 req/sec)
"""

import sys
import os
import signal
import argparse
from pathlib import Path
from datetime import datetime, date, timezone

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import (
    get_remaining_requests,
    get_fixtures_by_league_season,
    get_fixture_statistics,
    get_fixture_events,
    fixture_to_match_dict,
    parse_fixture_stats,
    parse_fixture_events,
)
from workers.api_clients.supabase_client import (
    get_client,
    store_match,
    store_match_stats_full,
)
from workers.utils.pipeline_utils import (
    log_pipeline_start,
    log_pipeline_complete,
    log_pipeline_failed,
)

console = Console()

# ─── Budget thresholds ──────────────────────────────────────────────────────

MIN_BUDGET_TO_START = 10_000   # Don't start if fewer than 10K requests remain
MIN_BUDGET_TO_CONTINUE = 2_000  # Stop mid-run if budget drops below this

# ─── League definitions by phase ────────────────────────────────────────────
# AF league IDs — curated from API-Football documentation

PHASE_1_LEAGUES = [
    # Top 5 European leagues
    39,   # England: Premier League
    140,  # Spain: La Liga
    78,   # Germany: Bundesliga
    135,  # Italy: Serie A
    61,   # France: Ligue 1
    # Strong European leagues
    88,   # Netherlands: Eredivisie
    94,   # Portugal: Primeira Liga
    203,  # Turkey: Süper Lig
    144,  # Belgium: Jupiler Pro League
    179,  # Scotland: Premiership
    # Nordic
    113,  # Sweden: Allsvenskan
    103,  # Norway: Eliteserien
    119,  # Denmark: Superligaen
    # Other Europe
    218,  # Austria: Bundesliga
    207,  # Switzerland: Super League
    # Americas
    71,   # Brazil: Série A
    128,  # Argentina: Liga Profesional
    253,  # USA: MLS
    262,  # Mexico: Liga MX
]

PHASE_2_LEAGUES = [
    # Second divisions of top 5
    40,   # England: Championship
    141,  # Spain: Segunda División
    79,   # Germany: 2. Bundesliga
    136,  # Italy: Serie B
    62,   # France: Ligue 2
    # Second divisions - other
    89,   # Netherlands: Eerste Divisie
    95,   # Portugal: Segunda Liga
    204,  # Turkey: 1. Lig
    145,  # Belgium: First Division B
    # More top leagues
    235,  # Russia: Premier League
    197,  # Greece: Super League
    333,  # Ukraine: Premier League
    106,  # Poland: Ekstraklasa
    345,  # Czech Republic: First League
    283,  # Australia: A-League
    292,  # South Korea: K League 1
    98,   # Japan: J1 League
    # Other strong leagues
    188,  # Romania: Liga I
    210,  # Hungary: NB I
    271,  # Croatia: HNL
    286,  # Serbia: Super Liga
    169,  # China: Super League (if active)
    72,   # Brazil: Série B
    129,  # Argentina: Primera Nacional
    # English lower + Scottish
    41,   # England: League One
    42,   # England: League Two
    180,  # Scotland: Championship
]

PHASE_3_LEAGUES = [
    # Third divisions
    43,   # England: National League
    142,  # Spain: Segunda División B
    80,   # Germany: 3. Liga
    137,  # Italy: Serie C - Group A
    63,   # France: National
    # More second/third tiers
    114,  # Sweden: Superettan
    104,  # Norway: OBOS-ligaen
    120,  # Denmark: 1. Division
    219,  # Austria: 2. Liga
    208,  # Switzerland: Challenge League
    234,  # Bulgaria: First Professional League
    332,  # Ukraine: Persha Liga
    107,  # Poland: I Liga
    346,  # Czech Republic: FNL
    181,  # Scotland: League One
    182,  # Scotland: League Two
    # South America extras
    73,   # Brazil: Série C
    130,  # Argentina: Primera B Metropolitana
    265,  # Chile: Primera División
    239,  # Colombia: Liga BetPlay
    # Asia/Oceania
    99,   # Japan: J2 League
    293,  # South Korea: K League 2
    284,  # Australia: A-League Women (skip if not relevant)
]

# Seasons to backfill per phase
PHASE_SEASONS = {
    1: [2023, 2024, 2025],
    2: [2024, 2025],
    3: [2025],
}

# ─── Graceful shutdown ──────────────────────────────────────────────────────

_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    console.print("[yellow]⚠ SIGTERM received — finishing current match then saving progress[/yellow]")


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


# ─── Progress tracking ──────────────────────────────────────────────────────

def get_or_create_progress(client, league_api_id: int, season: int, phase: int) -> dict:
    """Get existing progress row or create one."""
    result = (
        client.table("backfill_progress")
        .select("*")
        .eq("league_api_id", league_api_id)
        .eq("season", season)
        .execute()
    )
    if result.data:
        return result.data[0]

    row = {
        "league_api_id": league_api_id,
        "season": season,
        "phase": phase,
        "status": "pending",
    }
    client.table("backfill_progress").insert(row).execute()
    return row


def update_progress(client, league_api_id: int, season: int, **kwargs):
    """Update progress counters for a league/season."""
    kwargs["last_run_at"] = datetime.now(timezone.utc).isoformat()
    (
        client.table("backfill_progress")
        .update(kwargs)
        .eq("league_api_id", league_api_id)
        .eq("season", season)
        .execute()
    )


def check_all_complete(client) -> bool:
    """Check if all backfill work is done."""
    result = (
        client.table("backfill_progress")
        .select("league_api_id", count="exact")
        .neq("status", "complete")
        .execute()
    )
    return result.count == 0


# ─── Core backfill logic ────────────────────────────────────────────────────

def get_match_map(client, af_fixture_ids: list[int]) -> dict[int, str]:
    """Get {af_fixture_id: match_uuid} for a list of AF IDs. Paginated for large sets."""
    match_map = {}
    # Supabase PostgREST has a URL length limit; chunk the IN clause
    for i in range(0, len(af_fixture_ids), 200):
        chunk = af_fixture_ids[i:i + 200]
        result = (
            client.table("matches")
            .select("id, api_football_id")
            .in_("api_football_id", chunk)
            .execute()
        )
        for row in result.data:
            match_map[row["api_football_id"]] = row["id"]
    return match_map


def get_uuids_with_data(client, table: str, match_uuids: list[str]) -> set[str]:
    """Get which match UUIDs already have rows in a related table (odds/stats/events)."""
    has_data = set()
    for i in range(0, len(match_uuids), 200):
        chunk = match_uuids[i:i + 200]
        # Use select distinct match_id to find which have data
        result = (
            client.table(table)
            .select("match_id")
            .in_("match_id", chunk)
            .execute()
        )
        has_data.update(row["match_id"] for row in result.data)
    return has_data


def get_af_ids_needing(client, table: str, match_map: dict[int, str]) -> set[int]:
    """Find AF IDs whose matches don't have data in a given table."""
    if not match_map:
        return set()
    uuids = list(match_map.values())
    has_data = get_uuids_with_data(client, table, uuids)
    uuid_to_af = {v: k for k, v in match_map.items()}
    return {uuid_to_af[uuid] for uuid in uuids if uuid not in has_data}


def backfill_league_season(
    client,
    league_id: int,
    season: int,
    phase: int,
    skip_existing: bool,
    dry_run: bool,
    budget_tracker: dict,
    batch_limit: int,
    league_cap: int = 200,
) -> dict:
    """
    Backfill one league/season. Returns stats dict.
    league_cap limits API calls per league/season to keep runs short.
    """
    stats = {"fixtures_stored": 0, "odds_stored": 0, "stats_stored": 0, "events_stored": 0, "api_calls": 0}
    league_calls = 0  # Track calls within this league/season

    progress = get_or_create_progress(client, league_id, season, phase)
    if progress.get("status") == "complete" and skip_existing:
        console.print(f"  [dim]Already complete — skipping[/dim]")
        return stats

    update_progress(client, league_id, season, status="in_progress")

    # Step 1: Fetch all fixtures for this league/season (1 API call)
    console.print(f"  Fetching fixtures for league {league_id}, season {season}...")

    if dry_run:
        console.print(f"  [yellow]DRY RUN — would fetch /fixtures?league={league_id}&season={season}[/yellow]")
        return stats

    fixtures = get_fixtures_by_league_season(league_id, season)
    stats["api_calls"] += 1
    budget_tracker["used"] += 1
    league_calls += 1

    # Filter to finished matches only
    finished = [
        f for f in fixtures
        if f.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")
    ]
    total_fixtures = len(finished)

    console.print(f"  Found {len(fixtures)} fixtures, {total_fixtures} finished")

    update_progress(client, league_id, season, fixtures_total=total_fixtures)

    if not finished:
        update_progress(client, league_id, season, status="complete")
        return stats

    # Step 2: Store fixtures (match records)
    fixtures_stored = 0
    match_af_to_uuid = {}  # af_id -> our match UUID

    for fixture in finished:
        if _shutdown_requested:
            break
        if fixtures_stored >= batch_limit:
            break

        af_id = fixture["fixture"]["id"]

        # Store match record (store_match handles dedup)
        match_dict = fixture_to_match_dict(fixture)
        match_uuid = store_match(match_dict)
        match_af_to_uuid[af_id] = match_uuid
        fixtures_stored += 1

    stats["fixtures_stored"] = fixtures_stored
    update_progress(client, league_id, season, fixtures_done=fixtures_stored)
    console.print(f"  Stored {fixtures_stored} match records")

    if _shutdown_requested:
        return stats

    # Build match map once, reuse for all data type checks
    match_map = get_match_map(client, list(match_af_to_uuid.keys()))

    # Step 3: Odds — SKIPPED
    # API-Football /odds endpoint only returns data for upcoming/recent fixtures,
    # not historical completed matches. Confirmed via live test 2026-04-30.
    # Historical odds would need a separate source (e.g. Odds API historical endpoint).
    console.print(f"  Odds: skipped (AF doesn't serve historical odds for completed fixtures)")

    # Step 4: Fetch statistics for matches that need them
    need_stats = get_af_ids_needing(client, "match_stats", match_map) if skip_existing else set(match_af_to_uuid.keys())
    stats_stored = 0

    for af_id in need_stats:
        if _shutdown_requested:
            break
        if budget_tracker["used"] >= budget_tracker["max"]:
            break
        if league_calls >= league_cap:
            console.print(f"  [yellow]League cap ({league_cap}) reached — moving to next[/yellow]")
            break

        match_uuid = match_af_to_uuid.get(af_id)
        if not match_uuid:
            continue

        try:
            stats_resp = get_fixture_statistics(af_id)
            stats["api_calls"] += 1
            budget_tracker["used"] += 1
            league_calls += 1

            if stats_resp:
                parsed = parse_fixture_stats(stats_resp)
                if parsed:
                    store_match_stats_full(match_uuid, parsed)
                    stats_stored += 1
        except Exception as e:
            console.print(f"  [red]Stats error for AF {af_id}: {e}[/red]")

    stats["stats_stored"] = stats_stored
    update_progress(client, league_id, season, stats_done=progress.get("stats_done", 0) + stats_stored)
    console.print(f"  Stored stats for {stats_stored} matches")

    if _shutdown_requested:
        return stats

    # Step 5: Fetch events for matches that need them
    need_events = get_af_ids_needing(client, "match_events", match_map) if skip_existing else set(match_af_to_uuid.keys())
    events_stored = 0

    for af_id in need_events:
        if _shutdown_requested:
            break
        if budget_tracker["used"] >= budget_tracker["max"]:
            break
        if league_calls >= league_cap:
            console.print(f"  [yellow]League cap ({league_cap}) reached — moving to next[/yellow]")
            break

        match_uuid = match_af_to_uuid.get(af_id)
        if not match_uuid:
            continue

        # Find home team API ID for this fixture
        fixture_data = next((f for f in finished if f["fixture"]["id"] == af_id), None)
        home_team_api_id = fixture_data["teams"]["home"]["id"] if fixture_data else None

        try:
            events_resp = get_fixture_events(af_id)
            stats["api_calls"] += 1
            budget_tracker["used"] += 1
            league_calls += 1

            if events_resp:
                parsed = parse_fixture_events(events_resp)
                if parsed:
                    now = datetime.now(timezone.utc).isoformat()
                    rows = []
                    for ev in parsed:
                        # Default to "home" — DB CHECK constraint requires home/away
                        team_side = "home"
                        if home_team_api_id and ev.get("team_api_id"):
                            team_side = "home" if ev["team_api_id"] == home_team_api_id else "away"
                        rows.append({
                            "match_id": match_uuid,
                            "minute": max(0, ev.get("minute", 0)),
                            "added_time": ev.get("added_time", 0),
                            "event_type": ev["event_type"],
                            "team": team_side,
                            "player_name": ev.get("player_name"),
                            "detail": ev.get("detail"),
                            "af_event_order": ev.get("af_event_order"),
                            "created_at": now,
                        })
                    if rows:
                        # Use insert (not upsert) — partial unique index on
                        # af_event_order doesn't support ON CONFLICT in PostgREST.
                        # Dedup is handled by skip-existing check at the batch level.
                        client.table("match_events").insert(rows).execute()
                        events_stored += 1
        except Exception as e:
            console.print(f"  [red]Events error for AF {af_id}: {e}[/red]")

    stats["events_stored"] = events_stored
    update_progress(client, league_id, season, events_done=progress.get("events_done", 0) + events_stored)
    console.print(f"  Stored events for {events_stored} matches")

    # Check if this league/season is complete
    progress_now = get_or_create_progress(client, league_id, season, phase)
    if (progress_now.get("fixtures_done", 0) >= total_fixtures
            and not need_stats and not need_events):
        update_progress(client, league_id, season, status="complete")
        console.print(f"  [green]✓ League {league_id} season {season} complete[/green]")

    return stats


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Historical data backfill from API-Football")
    parser.add_argument("--phase", type=int, choices=[1, 2, 3], default=1,
                        help="Which league tier to process (1=top, 2=secondary, 3=remaining)")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="Max matches to process per league/season (default 500)")
    parser.add_argument("--league-cap", type=int, default=200,
                        help="Max API calls per league/season before moving to next (default 200)")
    parser.add_argument("--max-requests", type=int, default=800,
                        help="Max API calls for this run (default 800)")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip matches already in DB (default true)")
    parser.add_argument("--no-skip-existing", action="store_true",
                        help="Re-fetch even if data exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count targets only, no writes")
    args = parser.parse_args()

    skip_existing = not args.no_skip_existing

    console.print(f"\n[bold green]═══ Historical Backfill — Phase {args.phase} ═══[/bold green]")
    console.print(f"Batch size: {args.batch_size} | League cap: {args.league_cap} | "
                  f"Max requests: {args.max_requests} | "
                  f"Skip existing: {skip_existing} | Dry run: {args.dry_run}\n")

    # Check API budget
    budget = get_remaining_requests()
    console.print(f"API Budget: {budget['remaining']} remaining of {budget['limit_day']}")

    if budget["remaining"] < MIN_BUDGET_TO_START:
        console.print(f"[red]✗ Budget too low ({budget['remaining']} < {MIN_BUDGET_TO_START}) — aborting[/red]")
        return

    # Log pipeline run
    run_id = log_pipeline_start("hist_backfill", date.today().isoformat())

    try:
        client = get_client()

        # Check if all backfill is already done
        if check_all_complete(client):
            # Only if we have progress rows at all
            check = client.table("backfill_progress").select("league_api_id", count="exact").execute()
            if check.count and check.count > 0:
                console.print("[green]✓ All backfill phases complete![/green]")
                log_pipeline_complete(run_id, metadata={"status": "all_complete"})
                return

        # Select leagues for this phase
        phase_leagues = {1: PHASE_1_LEAGUES, 2: PHASE_2_LEAGUES, 3: PHASE_3_LEAGUES}
        leagues = phase_leagues[args.phase]
        seasons = PHASE_SEASONS[args.phase]

        console.print(f"Target: {len(leagues)} leagues × {len(seasons)} seasons = "
                      f"{len(leagues) * len(seasons)} league/season combos\n")

        budget_tracker = {"used": 1, "max": args.max_requests}  # 1 for the status check
        totals = {"fixtures": 0, "odds": 0, "stats": 0, "events": 0, "api_calls": 1}

        for league_id in leagues:
            if _shutdown_requested:
                break
            if budget_tracker["used"] >= budget_tracker["max"]:
                console.print(f"\n[yellow]Budget cap reached — stopping[/yellow]")
                break

            for season in seasons:
                if _shutdown_requested:
                    break
                if budget_tracker["used"] >= budget_tracker["max"]:
                    break

                console.print(f"\n[bold]League {league_id} / Season {season}[/bold]")

                result = backfill_league_season(
                    client=client,
                    league_id=league_id,
                    season=season,
                    phase=args.phase,
                    skip_existing=skip_existing,
                    dry_run=args.dry_run,
                    budget_tracker=budget_tracker,
                    batch_limit=args.batch_size,
                    league_cap=args.league_cap,
                )

                totals["fixtures"] += result["fixtures_stored"]
                totals["odds"] += result["odds_stored"]
                totals["stats"] += result["stats_stored"]
                totals["events"] += result["events_stored"]
                totals["api_calls"] += result["api_calls"]

        # Final summary
        console.print(f"\n[bold green]═══ Backfill Complete ═══[/bold green]")
        console.print(f"Fixtures: {totals['fixtures']} | Odds: {totals['odds']} | "
                      f"Stats: {totals['stats']} | Events: {totals['events']}")
        console.print(f"API calls: {totals['api_calls']}")

        # Check remaining budget
        final_budget = get_remaining_requests()
        console.print(f"API budget remaining: {final_budget['remaining']}")

        # Check completion
        if check_all_complete(client):
            console.print("\n[bold green]🎉 ALL PHASES COMPLETE — backfill finished![/bold green]")

            # Log completion stats
            progress_summary = (
                client.table("backfill_progress")
                .select("phase, status, league_api_id")
                .execute()
            )
            phase_counts = {}
            for row in progress_summary.data:
                p = row["phase"]
                if p not in phase_counts:
                    phase_counts[p] = 0
                phase_counts[p] += 1

            log_pipeline_complete(run_id, fixtures_count=totals["fixtures"],
                                  records_count=totals["odds"] + totals["stats"] + totals["events"],
                                  metadata={
                                      "phase": args.phase,
                                      "totals": totals,
                                      "all_complete": True,
                                      "phase_league_counts": phase_counts,
                                  })

            # Create completion flag file for workflow auto-disable
            flag_path = Path(__file__).parent.parent / "backfill_complete.flag"
            flag_path.write_text(
                f"Backfill completed at {datetime.now(timezone.utc).isoformat()}\n"
                f"Phases: {phase_counts}\n"
                f"Total fixtures: {totals['fixtures']}\n"
            )
            console.print(f"Created {flag_path}")
        else:
            log_pipeline_complete(run_id, fixtures_count=totals["fixtures"],
                                  records_count=totals["odds"] + totals["stats"] + totals["events"],
                                  metadata={"phase": args.phase, "totals": totals})

    except Exception as e:
        console.print(f"\n[red]✗ Error: {e}[/red]")
        log_pipeline_failed(run_id, str(e))
        raise


if __name__ == "__main__":
    main()
