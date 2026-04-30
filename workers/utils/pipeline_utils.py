"""
OddsIntel — Pipeline Utilities

Shared helpers for the fragmented pipeline jobs:
- Job readiness checks (are fixtures in DB for today?)
- Pipeline run logging (track job completion)
- Coverage-aware fetching (skip unsupported leagues)
"""

from datetime import date, datetime, timezone, timedelta
from workers.api_clients.supabase_client import get_client


# ─── Pipeline Run Logging ────────────────────────────────────────────────────

def log_pipeline_start(job_name: str, run_date: str = None) -> str:
    """Log a pipeline job as 'running'. Returns the run ID."""
    client = get_client()
    if not run_date:
        run_date = date.today().isoformat()
    row = {
        "job_name": job_name,
        "run_date": run_date,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    result = client.table("pipeline_runs").insert(row).execute()
    return result.data[0]["id"] if result.data else None


def log_pipeline_complete(run_id: str, fixtures_count: int = None,
                          records_count: int = None, metadata: dict = None):
    """Mark a pipeline job as completed."""
    client = get_client()
    update = {
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if fixtures_count is not None:
        update["fixtures_count"] = fixtures_count
    if records_count is not None:
        update["records_count"] = records_count
    if metadata:
        update["metadata"] = metadata
    client.table("pipeline_runs").update(update).eq("id", run_id).execute()


def log_pipeline_failed(run_id: str, error_message: str):
    """Mark a pipeline job as failed."""
    client = get_client()
    client.table("pipeline_runs").update({
        "status": "failed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "error_message": error_message[:1000],
    }).eq("id", run_id).execute()


def log_pipeline_skipped(job_name: str, reason: str, run_date: str = None):
    """Log a skipped pipeline run (e.g. fixtures not ready)."""
    client = get_client()
    if not run_date:
        run_date = date.today().isoformat()
    client.table("pipeline_runs").insert({
        "job_name": job_name,
        "run_date": run_date,
        "status": "skipped",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "error_message": reason,
    }).execute()


# ─── Readiness Checks ────────────────────────────────────────────────────────

def check_fixtures_ready(run_date: str = None) -> bool:
    """Check if fetch_fixtures has completed for today."""
    client = get_client()
    if not run_date:
        run_date = date.today().isoformat()
    result = (
        client.table("pipeline_runs")
        .select("id")
        .eq("job_name", "fetch_fixtures")
        .eq("run_date", run_date)
        .eq("status", "completed")
        .limit(1)
        .execute()
    )
    return len(result.data) > 0


def get_today_fixtures(run_date: str = None) -> list[dict]:
    """Load today's fixtures from DB with AF IDs, team IDs, league IDs."""
    client = get_client()
    if not run_date:
        run_date = date.today().isoformat()
    next_day = (date.fromisoformat(run_date) + timedelta(days=1)).isoformat()
    result = (
        client.table("matches")
        .select("id, api_football_id, home_team_id, away_team_id, league_id, date, status")
        .gte("date", f"{run_date}T00:00:00Z")
        .lt("date", f"{next_day}T00:00:00Z")
        .execute()
    )
    return result.data


# ─── Coverage Helpers ────────────────────────────────────────────────────────

def get_league_coverage_map() -> dict:
    """
    Load all league coverage flags into a dict keyed by league UUID.
    Returns: {league_uuid: {coverage_odds: bool, coverage_predictions: bool, ...}}
    """
    client = get_client()
    result = (
        client.table("leagues")
        .select("id, api_football_id, coverage_odds, coverage_predictions, "
                "coverage_injuries, coverage_lineups, coverage_standings, "
                "coverage_events, coverage_statistics_fixtures, coverage_statistics_players")
        .execute()
    )
    return {row["id"]: row for row in result.data}


def league_has_coverage(coverage_map: dict, league_id: str, feature: str) -> bool:
    """Check if a league supports a specific feature.

    Args:
        coverage_map: from get_league_coverage_map()
        league_id: UUID of the league
        feature: one of 'odds', 'predictions', 'injuries', 'lineups',
                 'standings', 'events', 'statistics_fixtures', 'statistics_players'
    """
    league = coverage_map.get(league_id)
    if not league:
        return True  # unknown league = try anyway (don't block)
    return league.get(f"coverage_{feature}", True)  # default True if column missing


# ─── League Coverage Storage ─────────────────────────────────────────────────

def store_league_coverage(af_leagues: list[dict]):
    """
    Upsert league coverage data from API-Football /leagues response.

    Each item in af_leagues has:
      - league: {id, name, type}
      - country: {name, code}
      - seasons: [{year, start, end, current, coverage: {...}}]
    """
    client = get_client()
    stored = 0

    for item in af_leagues:
        league_info = item.get("league", {})
        country_info = item.get("country", {})
        seasons = item.get("seasons", [])

        af_id = league_info.get("id")
        name = league_info.get("name", "")
        country = country_info.get("name", "")

        if not af_id or not name:
            continue

        # Find current season's coverage
        current_season = None
        coverage = {}
        for s in seasons:
            if s.get("current"):
                current_season = s.get("year")
                coverage = s.get("coverage", {})
                break

        # If no current season, use most recent
        if not current_season and seasons:
            last = seasons[-1]
            current_season = last.get("year")
            coverage = last.get("coverage", {})

        fixtures_cov = coverage.get("fixtures", {})

        row = {
            "api_football_id": af_id,
            "name": name,
            "country": country,
            "is_active": True,
            "coverage_odds": coverage.get("odds", False),
            "coverage_predictions": coverage.get("predictions", False),
            "coverage_injuries": coverage.get("injuries", False),
            "coverage_standings": coverage.get("standings", False),
            "coverage_lineups": fixtures_cov.get("lineups", False),
            "coverage_events": fixtures_cov.get("events", False),
            "coverage_statistics_fixtures": fixtures_cov.get("statistics_fixtures", False),
            "coverage_statistics_players": fixtures_cov.get("statistics_players", False),
            "af_season_current": current_season,
            "af_coverage_raw": coverage,
            "coverage_fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        # Try to find existing league by api_football_id
        existing = (
            client.table("leagues")
            .select("id")
            .eq("api_football_id", af_id)
            .limit(1)
            .execute()
        )

        if existing.data:
            # Update existing
            client.table("leagues").update(row).eq("id", existing.data[0]["id"]).execute()
        else:
            # Try matching by name + country (for leagues created by fixture fetch)
            by_name = (
                client.table("leagues")
                .select("id")
                .eq("name", name)
                .eq("country", country)
                .is_("api_football_id", "null")
                .limit(1)
                .execute()
            )
            if by_name.data:
                client.table("leagues").update(row).eq("id", by_name.data[0]["id"]).execute()
            else:
                # Insert new
                client.table("leagues").insert(row).execute()

        stored += 1

    return stored


# ─── Daily Featured Leagues ──────────────────────────────────────────────────

# Continental cups + major international tournaments that should be featured
# when they have matches today. AF league IDs.
FEATURED_WHEN_PLAYING = {
    2,    # UEFA Champions League
    3,    # UEFA Europa League
    848,  # UEFA Europa Conference League
    13,   # CONMEBOL Libertadores
    11,   # CONMEBOL Sudamericana
    16,   # CONCACAF Champions League
    480,  # Euro Championship
    531,  # Euro Championship - Qualification
    1,    # World Cup
    15,   # FIFA Club World Cup
    4,    # UEFA Nations League
    9,    # Copa America
    6,    # Africa Cup of Nations
    29,   # AFC Asian Cup
}

# Base priorities for leagues (used to restore after daily reset).
# Matches the static values set in migration 025.
BASE_PRIORITY = {
    # Continental cups + Big 5: priority 10
    2: 10, 3: 10, 848: 10, 13: 10, 11: 10, 16: 10, 480: 10, 531: 10,
    39: 10, 140: 10, 135: 10, 78: 10, 61: 10,
    # Major secondary + notable top flights: priority 20
    40: 20, 141: 20, 136: 20, 79: 20, 62: 20, 88: 20, 94: 20,
    144: 20, 203: 20, 253: 20, 262: 20, 71: 20, 128: 20,
    307: 20, 98: 20, 292: 20,
    # Other notable: priority 30
    119: 30, 113: 30, 103: 30, 106: 30, 218: 30, 207: 30, 179: 30,
    197: 30, 169: 30, 254: 30, 383: 30, 200: 30, 233: 30,
    332: 30, 286: 30, 72: 30, 73: 30, 188: 30, 210: 30, 271: 30,
}


def set_daily_featured_leagues(af_fixtures_raw: list[dict]) -> list[str]:
    """
    After fixtures are fetched, check which continental cups/tournaments
    have matches today and bump them to priority=1 (featured).
    Resets yesterday's featured leagues back to their base priority.

    Returns list of featured league names for logging.
    """
    client = get_client()

    # 1. Reset any league currently at priority=1 back to its base priority
    current_featured = client.table("leagues").select(
        "id, api_football_id"
    ).eq("priority", 1).execute()

    for league in (current_featured.data or []):
        af_id = league.get("api_football_id")
        base = BASE_PRIORITY.get(af_id)  # None if not in base map
        client.table("leagues").update(
            {"priority": base}
        ).eq("id", league["id"]).execute()

    # 2. Find which featured-eligible leagues have matches today
    today_af_league_ids = set()
    for fixture in af_fixtures_raw:
        league = fixture.get("league", {})
        af_id = league.get("id")
        if af_id:
            today_af_league_ids.add(af_id)

    featured_ids = today_af_league_ids & FEATURED_WHEN_PLAYING
    if not featured_ids:
        return []

    # 3. Set priority=1 on today's featured leagues
    featured_names = []
    for af_id in featured_ids:
        result = client.table("leagues").select("id, name").eq(
            "api_football_id", af_id
        ).execute()
        if result.data:
            league_id = result.data[0]["id"]
            name = result.data[0]["name"]
            client.table("leagues").update({"priority": 1}).eq("id", league_id).execute()
            featured_names.append(name)

    return sorted(featured_names)
