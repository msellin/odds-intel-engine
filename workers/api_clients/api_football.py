"""
OddsIntel — API-Football Client
Fetches live fixtures, lineups, results, and stats.

Free tier: 100 requests/day, all endpoints, all leagues.
Docs: https://www.api-football.com/documentation-v3
"""

import os
import time
import requests
from datetime import date, datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY,
}

# Our target leagues (API-Football league IDs)
# Map from our football-data.co.uk codes to API-Football IDs
LEAGUE_MAP = {
    # Top leagues
    "E0": 39,    # Premier League
    "SP1": 140,  # La Liga
    "D1": 78,    # Bundesliga
    "I1": 135,   # Serie A
    "F1": 61,    # Ligue 1
    # Second divisions
    "E1": 40,    # Championship
    "SP2": 141,  # Segunda Division
    "D2": 79,    # 2. Bundesliga
    "I2": 136,   # Serie B
    "F2": 62,    # Ligue 2
    # Third/fourth (English lower leagues)
    "E2": 41,    # League One
    "E3": 42,    # League Two
    # Other
    "N1": 88,    # Eredivisie
    "B1": 144,   # Jupiler Pro League
    "P1": 94,    # Liga Portugal
    "T1": 203,   # Super Lig
    "G1": 197,   # Super League Greece
    "SC0": 179,  # Scottish Premiership
}

# Reverse map
ID_TO_CODE = {v: k for k, v in LEAGUE_MAP.items()}


def _request(endpoint: str, params: dict = None) -> dict | None:
    """Make a request to API-Football"""
    if not API_KEY:
        print("WARNING: API_FOOTBALL_KEY not set. Set it in .env")
        return None

    try:
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            headers=HEADERS,
            params=params or {},
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get("errors"):
                print(f"API Error: {data['errors']}")
                return None
            return data
        else:
            print(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None

    except requests.RequestException as e:
        print(f"Request error: {e}")
        return None


def get_todays_fixtures(league_ids: list[int] = None) -> list[dict]:
    """
    Get all fixtures for today across our target leagues.
    Uses 1 API call per league.
    """
    if league_ids is None:
        league_ids = list(LEAGUE_MAP.values())

    today = date.today().isoformat()
    all_fixtures = []

    for league_id in league_ids:
        data = _request("fixtures", {
            "league": league_id,
            "date": today,
            "season": date.today().year if date.today().month >= 7 else date.today().year - 1,
            "timezone": "Europe/London",
        })
        time.sleep(6.5)  # Free tier: max 10 requests/minute

        if data and data.get("response"):
            for fixture in data["response"]:
                all_fixtures.append({
                    "fixture_id": fixture["fixture"]["id"],
                    "date": fixture["fixture"]["date"],
                    "status": fixture["fixture"]["status"]["short"],
                    "league_id": league_id,
                    "league_name": fixture["league"]["name"],
                    "league_code": ID_TO_CODE.get(league_id, ""),
                    "home_team": fixture["teams"]["home"]["name"],
                    "home_team_id": fixture["teams"]["home"]["id"],
                    "away_team": fixture["teams"]["away"]["name"],
                    "away_team_id": fixture["teams"]["away"]["id"],
                    "home_goals": fixture["goals"]["home"],
                    "away_goals": fixture["goals"]["away"],
                })

    return all_fixtures


def get_fixture_lineups(fixture_id: int) -> dict | None:
    """Get confirmed lineups for a specific fixture (usually available ~1h before kickoff)"""
    data = _request("fixtures/lineups", {"fixture": fixture_id})

    if data and data.get("response"):
        lineups = {}
        for team_lineup in data["response"]:
            team_name = team_lineup["team"]["name"]
            lineups[team_name] = {
                "formation": team_lineup.get("formation", ""),
                "starting_xi": [
                    {"name": p["player"]["name"], "number": p["player"]["number"],
                     "pos": p["player"]["pos"]}
                    for p in team_lineup.get("startXI", [])
                ],
                "substitutes": [
                    {"name": p["player"]["name"], "number": p["player"]["number"]}
                    for p in team_lineup.get("substitutes", [])
                ],
            }
        return lineups
    return None


def get_fixture_result(fixture_id: int) -> dict | None:
    """Get the final result and stats for a completed fixture"""
    data = _request("fixtures", {"id": fixture_id})

    if data and data.get("response"):
        f = data["response"][0]
        return {
            "fixture_id": fixture_id,
            "status": f["fixture"]["status"]["short"],
            "home_goals": f["goals"]["home"],
            "away_goals": f["goals"]["away"],
            "ht_home": f["score"]["halftime"]["home"],
            "ht_away": f["score"]["halftime"]["away"],
        }
    return None


def get_fixtures_by_date(target_date: str, league_ids: list[int] = None) -> list[dict]:
    """Get fixtures for a specific date (YYYY-MM-DD format)"""
    if league_ids is None:
        league_ids = list(LEAGUE_MAP.values())

    all_fixtures = []

    for league_id in league_ids:
        data = _request("fixtures", {
            "league": league_id,
            "date": target_date,
            "timezone": "Europe/London",
        })

        if data and data.get("response"):
            for fixture in data["response"]:
                all_fixtures.append({
                    "fixture_id": fixture["fixture"]["id"],
                    "date": fixture["fixture"]["date"],
                    "status": fixture["fixture"]["status"]["short"],
                    "league_id": league_id,
                    "league_name": fixture["league"]["name"],
                    "league_code": ID_TO_CODE.get(league_id, ""),
                    "home_team": fixture["teams"]["home"]["name"],
                    "home_team_id": fixture["teams"]["home"]["id"],
                    "away_team": fixture["teams"]["away"]["name"],
                    "away_team_id": fixture["teams"]["away"]["id"],
                    "home_goals": fixture["goals"]["home"],
                    "away_goals": fixture["goals"]["away"],
                })

    return all_fixtures


def check_quota() -> dict | None:
    """Check how many API calls we've used today"""
    data = _request("status")
    if data and data.get("response"):
        return {
            "requests_today": data["response"]["requests"]["current"],
            "requests_limit": data["response"]["requests"]["limit_day"],
        }
    return None


if __name__ == "__main__":
    # Quick test
    quota = check_quota()
    if quota:
        print(f"API quota: {quota['requests_today']}/{quota['requests_limit']} requests used today")

    fixtures = get_todays_fixtures()
    if fixtures:
        print(f"\nToday's matches: {len(fixtures)}")
        for f in fixtures[:5]:
            print(f"  {f['league_name']}: {f['home_team']} vs {f['away_team']} ({f['status']})")
    else:
        print("No fixtures found (check API key or no matches today)")
