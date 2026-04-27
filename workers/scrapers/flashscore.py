"""
OddsIntel — Flashscore Fixture Scraper
Gets today's matches, live scores, and results from Flashscore.
Free, no API key needed.

Note: Flashscore uses dynamic JS rendering. We use their mobile API
endpoints which return JSON directly.
"""

import requests
import json
import re
from datetime import date, datetime
from rich.console import Console

console = Console()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
    "Accept": "application/json",
    "Referer": "https://www.flashscore.com/",
}

# Flashscore country/league IDs for our target leagues
# These may need updating if Flashscore changes their internal IDs
LEAGUE_CONFIG = {
    "england-premier-league": {"tier": 1, "fd_code": "E0"},
    "england-championship": {"tier": 2, "fd_code": "E1"},
    "england-league-one": {"tier": 3, "fd_code": "E2"},
    "england-league-two": {"tier": 4, "fd_code": "E3"},
    "spain-laliga": {"tier": 1, "fd_code": "SP1"},
    "spain-laliga2": {"tier": 2, "fd_code": "SP2"},
    "germany-bundesliga": {"tier": 1, "fd_code": "D1"},
    "germany-2-bundesliga": {"tier": 2, "fd_code": "D2"},
    "italy-serie-a": {"tier": 1, "fd_code": "I1"},
    "italy-serie-b": {"tier": 2, "fd_code": "I2"},
    "france-ligue-1": {"tier": 1, "fd_code": "F1"},
    "france-ligue-2": {"tier": 2, "fd_code": "F2"},
    "netherlands-eredivisie": {"tier": 1, "fd_code": "N1"},
    "turkey-super-lig": {"tier": 1, "fd_code": "T1"},
    "greece-super-league": {"tier": 1, "fd_code": "G1"},
    "scotland-premiership": {"tier": 1, "fd_code": "SC0"},
}


def get_todays_matches_from_flashscore() -> list[dict]:
    """
    Scrape today's fixtures from Flashscore.
    Uses their data endpoint that returns structured text data.
    """
    today_str = date.today().strftime("%Y%m%d")

    # Flashscore's summary endpoint returns all matches for a day
    url = f"https://d.flashscore.com/x/feed/f_1_0_1_{today_str}_"

    try:
        resp = requests.get(url, headers={
            **HEADERS,
            "x-fsign": "SW9D1eZo",  # May need updating
        }, timeout=15)

        if resp.status_code != 200:
            console.print(f"[yellow]Flashscore returned {resp.status_code} — trying alternative...[/yellow]")
            return _get_matches_alternative()

        return _parse_flashscore_data(resp.text)

    except Exception as e:
        console.print(f"[red]Flashscore error: {e}[/red]")
        return _get_matches_alternative()


def _get_matches_alternative() -> list[dict]:
    """
    Alternative: scrape from sofascore API which is more stable.
    """
    today_str = date.today().isoformat()

    try:
        resp = requests.get(
            f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{today_str}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )

        if resp.status_code != 200:
            console.print(f"[yellow]Sofascore returned {resp.status_code}[/yellow]")
            return []

        data = resp.json()
        events = data.get("events", [])
        matches = []

        for event in events:
            tournament = event.get("tournament", {})
            tournament_name = tournament.get("name", "")
            category = tournament.get("category", {}).get("name", "")

            # Only include football (not futsal, beach soccer, etc.)
            if event.get("sport", {}).get("name") != "Football":
                continue

            home = event.get("homeTeam", {})
            away = event.get("awayTeam", {})

            status_code = event.get("status", {}).get("code", 0)
            status_map = {0: "NS", 6: "FT", 7: "FT", 31: "1H", 41: "HT", 51: "2H"}
            status = status_map.get(status_code, str(status_code))

            home_score = event.get("homeScore", {}).get("current")
            away_score = event.get("awayScore", {}).get("current")

            matches.append({
                "event_id": event.get("id"),
                "date": datetime.fromtimestamp(event.get("startTimestamp", 0)).isoformat(),
                "status": status,
                "league_name": tournament_name,
                "country": category,
                "home_team": home.get("name", ""),
                "home_team_id": home.get("id"),
                "away_team": away.get("name", ""),
                "away_team_id": away.get("id"),
                "home_goals": home_score,
                "away_goals": away_score,
                "source": "sofascore",
            })

        return matches

    except Exception as e:
        console.print(f"[red]Sofascore error: {e}[/red]")
        return []


def _parse_flashscore_data(raw_text: str) -> list[dict]:
    """Parse Flashscore's proprietary text format"""
    # Flashscore uses a custom delimiter-based format
    # This is fragile and may need updating
    matches = []

    # Split by match delimiter
    parts = raw_text.split("~AA÷")

    for part in parts:
        if not part.strip():
            continue

        try:
            fields = {}
            for item in part.split("¬"):
                if "÷" in item:
                    key, val = item.split("÷", 1)
                    fields[key] = val

            if "AE" in fields and "AF" in fields:
                matches.append({
                    "event_id": fields.get("AA", ""),
                    "home_team": fields.get("AE", ""),
                    "away_team": fields.get("AF", ""),
                    "home_goals": fields.get("AG"),
                    "away_goals": fields.get("AH"),
                    "status": fields.get("AB", "NS"),
                    "league_name": fields.get("ZA", ""),
                    "source": "flashscore",
                })
        except (ValueError, KeyError):
            continue

    return matches


def get_match_result(event_id: str) -> dict | None:
    """Get the final result for a specific match"""
    # For now, use the main scraper and filter
    matches = get_todays_matches_from_flashscore()
    for m in matches:
        if str(m.get("event_id")) == str(event_id):
            return m
    return None


if __name__ == "__main__":
    console.print("[bold]Flashscore/Sofascore Match Scraper Test[/bold]\n")

    matches = get_todays_matches_from_flashscore()
    console.print(f"Found {len(matches)} matches today\n")

    # Group by league
    leagues = {}
    for m in matches:
        league = f"{m.get('country', '')} - {m['league_name']}"
        if league not in leagues:
            leagues[league] = []
        leagues[league].append(m)

    for league, league_matches in sorted(leagues.items()):
        console.print(f"[cyan]{league}[/cyan] ({len(league_matches)} matches)")
        for m in league_matches[:3]:
            score = f"{m['home_goals']}-{m['away_goals']}" if m['home_goals'] is not None else "vs"
            console.print(f"  {m['home_team']} {score} {m['away_team']} ({m['status']})")
        if len(league_matches) > 3:
            console.print(f"  ... and {len(league_matches) - 3} more")
