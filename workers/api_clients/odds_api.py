"""
OddsIntel — The Odds API Client
Fetches live odds from 40+ bookmakers.

Free tier: 500 requests/month (~16/day).
Docs: https://the-odds-api.com/liveapi/guides/v4/
"""

import os
import requests
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"

# Sport keys for football leagues
# The Odds API groups by sport, not individual leagues
SPORT_KEYS = {
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "La Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie A",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_efl_champ": "Championship",
    "soccer_netherlands_eredivisie": "Eredivisie",
    "soccer_portugal_primeira_liga": "Liga Portugal",
    "soccer_turkey_super_league": "Super Lig",
    "soccer_greece_super_league": "Super League Greece",
    "soccer_belgium_first_div": "Jupiler Pro League",
    "soccer_germany_bundesliga2": "2. Bundesliga",
    "soccer_spain_segunda_division": "Segunda Division",
    "soccer_italy_serie_b": "Serie B",
    "soccer_france_ligue_two": "Ligue 2",
    "soccer_england_league1": "League One",
    "soccer_england_league2": "League Two",
}

# Bookmakers we care about most
PRIORITY_BOOKMAKERS = [
    "pinnacle",      # Sharpest odds (benchmark)
    "bet365",        # Most popular
    "unibet_eu",     # Good European coverage
    "betfair_ex_eu", # Exchange odds
    "williamhill",   # Traditional
    "betway",
    "1xbet",
]


def _request(endpoint: str, params: dict = None) -> dict | None:
    """Make a request to The Odds API"""
    if not API_KEY:
        print("WARNING: ODDS_API_KEY not set. Set it in .env")
        return None

    base_params = {"apiKey": API_KEY}
    if params:
        base_params.update(params)

    try:
        resp = requests.get(
            f"{BASE_URL}/{endpoint}",
            params=base_params,
            timeout=15,
        )

        # Track remaining quota
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")

        if resp.status_code == 200:
            data = resp.json()
            print(f"  [Odds API] Quota: {used} used, {remaining} remaining")
            return data
        elif resp.status_code == 401:
            print("Invalid API key")
            return None
        elif resp.status_code == 422:
            print(f"Invalid params: {resp.text[:200]}")
            return None
        elif resp.status_code == 429:
            print("Rate limit exceeded!")
            return None
        else:
            print(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None

    except requests.RequestException as e:
        print(f"Request error: {e}")
        return None


def get_odds(sport_key: str, markets: str = "h2h,totals",
             regions: str = "eu", odds_format: str = "decimal") -> list[dict]:
    """
    Get odds for all upcoming matches in a sport/league.

    Each call costs 1 request from quota.
    Markets: h2h (1X2), totals (over/under), spreads
    """
    data = _request(f"sports/{sport_key}/odds", {
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
    })

    if not data:
        return []

    matches = []
    for event in data:
        match = {
            "event_id": event["id"],
            "sport": event["sport_key"],
            "commence_time": event["commence_time"],
            "home_team": event["home_team"],
            "away_team": event["away_team"],
            "bookmakers": {},
        }

        for bookmaker in event.get("bookmakers", []):
            bk_key = bookmaker["key"]
            bk_data = {"name": bookmaker["title"], "markets": {}}

            for market in bookmaker.get("markets", []):
                market_key = market["key"]
                outcomes = {}

                for outcome in market.get("outcomes", []):
                    name = outcome["name"]
                    price = outcome["price"]
                    point = outcome.get("point")

                    if market_key == "h2h":
                        if name == match["home_team"]:
                            outcomes["home"] = price
                        elif name == match["away_team"]:
                            outcomes["away"] = price
                        elif name == "Draw":
                            outcomes["draw"] = price
                    elif market_key == "totals":
                        if name == "Over":
                            outcomes["over"] = price
                            outcomes["line"] = point
                        elif name == "Under":
                            outcomes["under"] = price

                bk_data["markets"][market_key] = outcomes

            match["bookmakers"][bk_key] = bk_data

        matches.append(match)

    return matches


def get_all_league_odds(sport_keys: list[str] = None) -> list[dict]:
    """
    Get odds for all our target leagues.
    Each league = 1 API call. Be mindful of quota (500/month free).
    """
    if sport_keys is None:
        sport_keys = list(SPORT_KEYS.keys())

    all_matches = []
    for sport_key in sport_keys:
        league_name = SPORT_KEYS.get(sport_key, sport_key)
        print(f"Fetching odds for {league_name}...")
        matches = get_odds(sport_key)
        for m in matches:
            m["league_name"] = league_name
        all_matches.extend(matches)

    return all_matches


def extract_best_odds(match: dict) -> dict:
    """Extract the best available odds across all bookmakers for a match"""
    best = {
        "home": 0, "home_bk": "",
        "draw": 0, "draw_bk": "",
        "away": 0, "away_bk": "",
        "over_25": 0, "over_25_bk": "",
        "under_25": 0, "under_25_bk": "",
        "pinnacle_home": None, "pinnacle_draw": None, "pinnacle_away": None,
    }

    for bk_key, bk_data in match.get("bookmakers", {}).items():
        h2h = bk_data.get("markets", {}).get("h2h", {})
        totals = bk_data.get("markets", {}).get("totals", {})

        # Best 1X2
        if h2h.get("home", 0) > best["home"]:
            best["home"] = h2h["home"]
            best["home_bk"] = bk_data["name"]
        if h2h.get("draw", 0) > best["draw"]:
            best["draw"] = h2h["draw"]
            best["draw_bk"] = bk_data["name"]
        if h2h.get("away", 0) > best["away"]:
            best["away"] = h2h["away"]
            best["away_bk"] = bk_data["name"]

        # Best totals (only 2.5 line)
        if totals.get("line") == 2.5:
            if totals.get("over", 0) > best["over_25"]:
                best["over_25"] = totals["over"]
                best["over_25_bk"] = bk_data["name"]
            if totals.get("under", 0) > best["under_25"]:
                best["under_25"] = totals["under"]
                best["under_25_bk"] = bk_data["name"]

        # Track Pinnacle specifically (sharp benchmark)
        if bk_key == "pinnacle":
            best["pinnacle_home"] = h2h.get("home")
            best["pinnacle_draw"] = h2h.get("draw")
            best["pinnacle_away"] = h2h.get("away")

    return best


def get_available_sports() -> list[str]:
    """List all available sports (useful for discovering sport keys)"""
    data = _request("sports")
    if data:
        return [s["key"] for s in data if s.get("active")]
    return []


if __name__ == "__main__":
    # Quick test
    sports = get_available_sports()
    soccer_sports = [s for s in sports if "soccer" in s]
    print(f"Available soccer leagues: {len(soccer_sports)}")
    for s in soccer_sports:
        print(f"  {s}")

    # Test with one league
    if soccer_sports:
        print(f"\nFetching EPL odds...")
        matches = get_odds("soccer_epl")
        print(f"Found {len(matches)} matches with odds")
        if matches:
            m = matches[0]
            print(f"\nExample: {m['home_team']} vs {m['away_team']}")
            best = extract_best_odds(m)
            print(f"  Best Home: {best['home']} ({best['home_bk']})")
            print(f"  Best Draw: {best['draw']} ({best['draw_bk']})")
            print(f"  Best Away: {best['away']} ({best['away_bk']})")
            if best['pinnacle_home']:
                print(f"  Pinnacle: {best['pinnacle_home']} / {best['pinnacle_draw']} / {best['pinnacle_away']}")
