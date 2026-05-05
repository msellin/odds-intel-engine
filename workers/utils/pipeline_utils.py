"""
OddsIntel — Pipeline Utilities

Shared helpers for the fragmented pipeline jobs:
- Job readiness checks (are fixtures in DB for today?)
- Pipeline run logging (track job completion)
- Coverage-aware fetching (skip unsupported leagues)
"""

import json
from datetime import date, datetime, timezone, timedelta
from workers.api_clients.db import execute_query, execute_write


# ─── Pipeline Run Logging ────────────────────────────────────────────────────

def log_pipeline_start(job_name: str, run_date: str = None) -> str:
    """Log a pipeline job as 'running'. Returns the run ID."""
    if not run_date:
        run_date = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    result = execute_query(
        "INSERT INTO pipeline_runs (job_name, run_date, status, started_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        [job_name, run_date, "running", now]
    )
    return result[0]["id"] if result else None


def log_pipeline_complete(run_id: str, fixtures_count: int = None,
                          records_count: int = None, metadata: dict = None):
    """Mark a pipeline job as completed."""
    sets = ["status = %s", "completed_at = %s"]
    params: list = ["completed", datetime.now(timezone.utc).isoformat()]
    if fixtures_count is not None:
        sets.append("fixtures_count = %s")
        params.append(fixtures_count)
    if records_count is not None:
        sets.append("records_count = %s")
        params.append(records_count)
    if metadata:
        sets.append("metadata = %s::jsonb")
        params.append(json.dumps(metadata))
    params.append(run_id)
    execute_write(f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id = %s", params)


def log_pipeline_failed(run_id: str, error_message: str):
    """Mark a pipeline job as failed."""
    execute_write(
        "UPDATE pipeline_runs SET status = %s, completed_at = %s, error_message = %s WHERE id = %s",
        ["failed", datetime.now(timezone.utc).isoformat(), error_message[:4000], run_id]
    )


def log_pipeline_skipped(job_name: str, reason: str, run_date: str = None):
    """Log a skipped pipeline run (e.g. fixtures not ready)."""
    if not run_date:
        run_date = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    execute_write(
        "INSERT INTO pipeline_runs (job_name, run_date, status, started_at, completed_at, error_message) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        [job_name, run_date, "skipped", now, now, reason]
    )


# ─── Readiness Checks ────────────────────────────────────────────────────────

def check_fixtures_ready(run_date: str = None) -> bool:
    """Check if fetch_fixtures has completed for today."""
    if not run_date:
        run_date = date.today().isoformat()
    result = execute_query(
        "SELECT id FROM pipeline_runs WHERE job_name = %s AND run_date = %s AND status = %s LIMIT 1",
        ["fetch_fixtures", run_date, "completed"]
    )
    return len(result) > 0


def get_today_fixtures(run_date: str = None) -> list[dict]:
    """Load today's fixtures from DB with AF IDs, team IDs, league IDs."""
    if not run_date:
        run_date = date.today().isoformat()
    next_day = (date.fromisoformat(run_date) + timedelta(days=1)).isoformat()
    return execute_query(
        "SELECT id, api_football_id, home_team_id, away_team_id, league_id, date, status "
        "FROM matches WHERE date >= %s AND date < %s",
        [f"{run_date}T00:00:00Z", f"{next_day}T00:00:00Z"]
    )


# ─── Coverage Helpers ────────────────────────────────────────────────────────

def get_league_coverage_map() -> dict:
    """
    Load all league coverage flags into a dict keyed by league UUID.
    Returns: {league_uuid: {coverage_odds: bool, coverage_predictions: bool, ...}}
    """
    result = execute_query(
        "SELECT id, api_football_id, coverage_odds, coverage_predictions, "
        "coverage_injuries, coverage_lineups, coverage_standings, "
        "coverage_events, coverage_statistics_fixtures, coverage_statistics_players "
        "FROM leagues",
        []
    )
    return {row["id"]: row for row in result}


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

_UPDATE_COVERAGE_SQL = """
UPDATE leagues SET
    api_football_id = %s, name = %s, country = %s, is_active = %s,
    coverage_odds = %s, coverage_predictions = %s, coverage_injuries = %s,
    coverage_standings = %s, coverage_lineups = %s, coverage_events = %s,
    coverage_statistics_fixtures = %s, coverage_statistics_players = %s,
    af_season_current = %s, af_coverage_raw = %s::jsonb, coverage_fetched_at = %s
WHERE id = %s
"""

_INSERT_COVERAGE_SQL = """
INSERT INTO leagues (
    api_football_id, name, country, is_active,
    coverage_odds, coverage_predictions, coverage_injuries,
    coverage_standings, coverage_lineups, coverage_events,
    coverage_statistics_fixtures, coverage_statistics_players,
    af_season_current, af_coverage_raw, coverage_fetched_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
"""


def store_league_coverage(af_leagues: list[dict]):
    """
    Upsert league coverage data from API-Football /leagues response.

    Each item in af_leagues has:
      - league: {id, name, type}
      - country: {name, code}
      - seasons: [{year, start, end, current, coverage: {...}}]
    """
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
        now = datetime.now(timezone.utc).isoformat()

        params = (
            af_id, name, country, True,
            coverage.get("odds", False),
            coverage.get("predictions", False),
            coverage.get("injuries", False),
            coverage.get("standings", False),
            fixtures_cov.get("lineups", False),
            fixtures_cov.get("events", False),
            fixtures_cov.get("statistics_fixtures", False),
            fixtures_cov.get("statistics_players", False),
            current_season,
            json.dumps(coverage),
            now,
        )

        # Try to find existing league by api_football_id
        existing = execute_query(
            "SELECT id FROM leagues WHERE api_football_id = %s LIMIT 1",
            [af_id]
        )

        if existing:
            execute_write(_UPDATE_COVERAGE_SQL, list(params) + [existing[0]["id"]])
        else:
            # Try matching by name + country (for leagues created by fixture fetch)
            by_name = execute_query(
                "SELECT id FROM leagues WHERE name = %s AND country = %s AND api_football_id IS NULL LIMIT 1",
                [name, country]
            )
            if by_name:
                execute_write(_UPDATE_COVERAGE_SQL, list(params) + [by_name[0]["id"]])
            else:
                execute_write(_INSERT_COVERAGE_SQL, list(params))

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

# Featured priority: what priority a continental cup gets on days it has matches.
# Tier 1 = top of page (CL, World Cup, Euros). Tier 2 = just below.
# All others in FEATURED_WHEN_PLAYING default to 2 if not listed here.
FEATURED_PRIORITY = {
    2: 1,    # UEFA Champions League
    1: 1,    # FIFA World Cup
    15: 1,   # FIFA Club World Cup
    480: 1,  # UEFA Euro Championship
    9: 1,    # Copa America
    3: 2,    # UEFA Europa League
    848: 2,  # UEFA Europa Conference League
    13: 2,   # CONMEBOL Libertadores
    11: 2,   # CONMEBOL Sudamericana
    4: 2,    # UEFA Nations League
    531: 2,  # Euro Championship - Qualification
    6: 2,    # Africa Cup of Nations
    29: 2,   # AFC Asian Cup
    16: 2,   # CONCACAF Champions League
}

# Base priorities for leagues (used to restore after daily reset).
# 6-tier system (lower = higher on page). Set by migration 044.
#
#  10 — Tier 1 continental: CL, WC, Euros, Copa America, Club WC
#  12 — Tier 2 continental: EL, ECL, Libertadores, Sudamericana, Nations League, etc.
#  14 — Big 5 domestic: PL, La Liga, Serie A, Bundesliga, Ligue 1
#  20 — Strong secondary: Championship, Eredivisie, Primeira, MLS, Brasileirao, etc.
#  25 — Other notable top flights: Scottish Prem, Austrian BL, Greek SL, etc.
#  30 — Rest
BASE_PRIORITY = {
    # Tier 1 continental (priority 10)
    2: 10, 1: 10, 15: 10, 480: 10, 9: 10,
    # Tier 2 continental (priority 12)
    3: 12, 848: 12, 13: 12, 11: 12, 4: 12, 531: 12, 6: 12, 29: 12, 16: 12,
    # Big 5 domestic (priority 14)
    39: 14, 140: 14, 135: 14, 78: 14, 61: 14,
    # Strong secondary (priority 20)
    40: 20, 141: 20, 136: 20, 79: 20, 62: 20, 88: 20, 94: 20,
    144: 20, 203: 20, 253: 20, 262: 20, 71: 20, 128: 20,
    307: 20, 98: 20, 292: 20,
    # Other notable top flights (priority 25)
    179: 25, 106: 25, 345: 25, 197: 25, 207: 25, 218: 25,
    119: 25, 113: 25, 103: 25, 283: 25, 271: 25, 333: 25,
    210: 25, 188: 25, 200: 25, 233: 25, 254: 25, 383: 25,
    332: 25, 286: 25, 72: 25, 73: 25, 386: 25,
    # All others fall through to their DB value (priority 30 or higher)
}


def set_daily_featured_leagues(af_fixtures_raw: list[dict]) -> list[str]:
    """
    After fixtures are fetched, check which continental cups/tournaments
    have matches today and bump them to their featured priority tier.
    Resets yesterday's featured leagues back to their base priority.

    Returns list of featured league names for logging.
    """
    # 1. Reset any league currently at a featured priority back to its base priority.
    # Featured priorities are 1 and 2 (base priorities start at 10).
    current_featured = execute_query(
        "SELECT id, api_football_id FROM leagues WHERE priority < 10",
        []
    )
    for league in current_featured:
        af_id = league.get("api_football_id")
        base = BASE_PRIORITY.get(af_id)  # None if not in base map
        execute_write(
            "UPDATE leagues SET priority = %s WHERE id = %s",
            [base, league["id"]]
        )

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

    # 3. Set tiered priority on today's featured leagues (CL=1, others=2).
    featured_names = []
    for af_id in featured_ids:
        result = execute_query(
            "SELECT id, name FROM leagues WHERE api_football_id = %s",
            [af_id]
        )
        if result:
            league_id = result[0]["id"]
            name = result[0]["name"]
            featured_prio = FEATURED_PRIORITY.get(af_id, 2)
            execute_write(
                "UPDATE leagues SET priority = %s WHERE id = %s",
                [featured_prio, league_id]
            )
            featured_names.append(name)

    return sorted(featured_names)
