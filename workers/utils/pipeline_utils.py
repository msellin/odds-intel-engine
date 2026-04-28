"""
OddsIntel — Pipeline Utilities

Shared helpers for the fragmented pipeline jobs:
- Job readiness checks (are fixtures in DB for today?)
- Pipeline run logging (track job completion)
- Coverage-aware fetching (skip unsupported leagues)
"""

from datetime import date, datetime, timezone
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
    next_day = run_date[:8] + str(int(run_date[8:]) + 1).zfill(2)
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
