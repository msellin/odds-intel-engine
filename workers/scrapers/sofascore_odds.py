"""
OddsIntel — SofaScore Odds Scraper
Fetches 1X2 and Over/Under odds from SofaScore's internal API.

SofaScore covers virtually all leagues worldwide including lower tiers —
this is the primary odds source for Greek, Turkish, Scandinavian,
Eastern European, and other smaller leagues.

Rate: ~0.5s per request. For 150 target events ≈ 75s total.
"""

import requests
import time
from datetime import datetime, date
from rich.console import Console

console = Console()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}

# SofaScore (country, tournament_name) → (fd_code, tier)
# country = category.name from SofaScore API
# tournament_name = tournament.name from SofaScore API (may be partial match)
LEAGUE_MAP = {
    # England
    ("England", "Premier League"): {"fd_code": "E0", "tier": 1},
    ("England", "Championship"): {"fd_code": "E1", "tier": 2},
    ("England", "League One"): {"fd_code": "E2", "tier": 3},
    ("England", "League Two"): {"fd_code": "E3", "tier": 4},
    # Spain
    ("Spain", "LaLiga"): {"fd_code": "SP1", "tier": 1},
    ("Spain", "La Liga"): {"fd_code": "SP1", "tier": 1},
    ("Spain", "LaLiga2"): {"fd_code": "SP2", "tier": 2},
    ("Spain", "La Liga 2"): {"fd_code": "SP2", "tier": 2},
    # Germany
    ("Germany", "Bundesliga"): {"fd_code": "D1", "tier": 1},
    ("Germany", "2. Bundesliga"): {"fd_code": "D2", "tier": 2},
    # Italy
    ("Italy", "Serie A"): {"fd_code": "I1", "tier": 1},
    ("Italy", "Serie B"): {"fd_code": "I2", "tier": 2},
    # France
    ("France", "Ligue 1"): {"fd_code": "F1", "tier": 1},
    ("France", "Ligue 2"): {"fd_code": "F2", "tier": 2},
    # Netherlands
    ("Netherlands", "Eredivisie"): {"fd_code": "N1", "tier": 1},
    # Turkey
    ("Turkey", "Süper Lig"): {"fd_code": "T1", "tier": 1},
    ("Turkey", "Trendyol Süper Lig"): {"fd_code": "T1", "tier": 1},
    ("Turkey", "1. Lig"): {"fd_code": "T2", "tier": 2},
    # Greece
    ("Greece", "Super League"): {"fd_code": "G1", "tier": 1},
    ("Greece", "Stoiximan Super League"): {"fd_code": "G1", "tier": 1},
    ("Greece", "Super League, Relegation Round"): {"fd_code": "G1", "tier": 1},
    ("Greece", "Stoiximan Super League, Relegation Round"): {"fd_code": "G1", "tier": 1},
    # Scotland
    ("Scotland", "Premiership"): {"fd_code": "SC0", "tier": 1},
    # Portugal (sponsor name variants)
    ("Portugal", "Primeira Liga"): {"fd_code": "P1", "tier": 1},
    ("Portugal", "Liga Portugal"): {"fd_code": "P1", "tier": 1},
    ("Portugal", "Liga Portugal Betclic"): {"fd_code": "P1", "tier": 1},
    ("Portugal", "Liga Portugal 2"): {"fd_code": "P2", "tier": 2},
    ("Portugal", "Liga Portugal Betclic 2"): {"fd_code": "P2", "tier": 2},
    # Belgium
    ("Belgium", "Jupiler Pro League"): {"fd_code": "B1", "tier": 1},
    ("Belgium", "Pro League"): {"fd_code": "B1", "tier": 1},
    # Sweden
    ("Sweden", "Allsvenskan"): {"fd_code": "SE1", "tier": 1},
    # Denmark
    ("Denmark", "Superligaen"): {"fd_code": "DK1", "tier": 1},
    # Estonia
    ("Estonia", "Esiliiga"): {"fd_code": "EST1", "tier": 2},
    ("Estonia", "Meistriliiga"): {"fd_code": "EST0", "tier": 1},
    # Norway
    ("Norway", "Eliteserien"): {"fd_code": "NOR1", "tier": 1},
    ("Norway", "OBOS-ligaen"): {"fd_code": "NOR2", "tier": 2},
    ("Norway", "1st Division"): {"fd_code": "NOR2", "tier": 2},
    # Poland
    ("Poland", "Ekstraklasa"): {"fd_code": "PL1", "tier": 1},
    ("Poland", "I liga"): {"fd_code": "PL2", "tier": 2},
    # Croatia
    ("Croatia", "HNL"): {"fd_code": "CR1", "tier": 1},
    ("Croatia", "Supersport HNL"): {"fd_code": "CR1", "tier": 1},
    # Romania
    ("Romania", "Liga I"): {"fd_code": "RO1", "tier": 1},
    ("Romania", "SuperLiga"): {"fd_code": "RO1", "tier": 1},
    # Serbia (sponsor name variants)
    ("Serbia", "Super liga"): {"fd_code": "SER1", "tier": 1},
    ("Serbia", "Mozzart Bet Superliga"): {"fd_code": "SER1", "tier": 1},
    # Ukraine
    ("Ukraine", "Premier League"): {"fd_code": "UA1", "tier": 1},
    ("Ukraine", "UPL"): {"fd_code": "UA1", "tier": 1},
    # Hungary
    ("Hungary", "OTP Bank Liga"): {"fd_code": "HUN1", "tier": 1},
    ("Hungary", "NB I"): {"fd_code": "HUN1", "tier": 1},
    # Czech Republic
    ("Czech Republic", "Fortuna:Liga"): {"fd_code": "CZ1", "tier": 1},
    ("Czechia", "Fortuna:Liga"): {"fd_code": "CZ1", "tier": 1},
    # Austria
    ("Austria", "Bundesliga"): {"fd_code": "AT1", "tier": 1},
    ("Austria", "Admiral Bundesliga"): {"fd_code": "AT1", "tier": 1},
    # Switzerland
    ("Switzerland", "Super League"): {"fd_code": "SW1", "tier": 1},
    # Iceland
    ("Iceland", "Úrvalsdeild"): {"fd_code": "ICE1", "tier": 1},
    # Latvia
    ("Latvia", "Virsliga"): {"fd_code": "LAT1", "tier": 1},
    # Cyprus
    ("Cyprus", "First Division"): {"fd_code": "CY1", "tier": 1},
    ("Cyprus", "1. Division"): {"fd_code": "CY1", "tier": 1},
    # Georgia
    ("Georgia", "Erovnuli Liga"): {"fd_code": "GEO1", "tier": 1},
}


def _frac_to_decimal(frac: str) -> float:
    """Convert fractional odds string '7/4' to decimal 2.75"""
    try:
        num, den = frac.split("/")
        return round(int(num) / int(den) + 1, 4)
    except Exception:
        return 0.0


def _get_league_info(country: str, tournament: str) -> dict:
    """Look up league metadata. Tries exact key, then partial tournament name match."""
    key = (country, tournament)
    if key in LEAGUE_MAP:
        return LEAGUE_MAP[key]

    # Partial match on tournament name (handles subtitle variants like ", Relegation Round")
    for (c, t), info in LEAGUE_MAP.items():
        if c == country and (tournament.startswith(t) or t.startswith(tournament)):
            return info

    return {}


def fetch_event_odds(event_id: int) -> dict:
    """
    Fetch odds for a single SofaScore event.
    Returns dict with odds_home, odds_draw, odds_away, odds_over_25, odds_under_25.
    """
    url = f"https://api.sofascore.com/api/v1/event/{event_id}/odds/1/all"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return {}

        data = resp.json()
        result = {}

        for market in data.get("markets", []):
            group = market.get("marketGroup", "")
            period = market.get("marketPeriod", "")
            choice_group = market.get("choiceGroup", "")

            # Full-time 1X2
            if group == "1X2" and period == "Full-time":
                for choice in market.get("choices", []):
                    name = choice.get("name", "")
                    frac = choice.get("fractionalValue", "")
                    dec = _frac_to_decimal(frac) if frac else 0.0
                    if name == "1":
                        result["odds_home"] = dec
                    elif name == "X":
                        result["odds_draw"] = dec
                    elif name == "2":
                        result["odds_away"] = dec

            # Over/Under 2.5 goals
            elif group == "Match goals" and choice_group == "2.5":
                for choice in market.get("choices", []):
                    name = choice.get("name", "")
                    frac = choice.get("fractionalValue", "")
                    dec = _frac_to_decimal(frac) if frac else 0.0
                    if name == "Over":
                        result["odds_over_25"] = dec
                    elif name == "Under":
                        result["odds_under_25"] = dec

        return result

    except Exception:
        return {}


def fetch_all_odds(events: list[dict], delay: float = 0.5) -> list[dict]:
    """
    Fetch odds for a list of fixture events (from sofascore fixture scraper).
    Filters to target leagues and returns results in the same format as kambi_odds.py.

    Args:
        events: list of dicts from get_todays_matches_from_flashscore()
        delay: seconds between API requests (be polite)
    """
    results = []
    skipped = 0
    today_str = date.today().isoformat()

    for event in events:
        country = event.get("country", "")
        league_name = event.get("league_name", "")
        event_id = event.get("event_id")

        if not event_id:
            skipped += 1
            continue

        league_info = _get_league_info(country, league_name)
        if not league_info:
            skipped += 1
            continue

        odds = fetch_event_odds(event_id)
        if not odds.get("odds_home"):
            skipped += 1
            continue

        result = {
            "home_team": event.get("home_team", ""),
            "away_team": event.get("away_team", ""),
            "start_time": event.get("date", datetime.now().isoformat()),
            "league_path": f"{country} / {league_name}",
            "league_code": league_info.get("fd_code", ""),
            "tier": league_info.get("tier", 0),
            "operator": "sofascore",
            "odds_home": odds.get("odds_home", 0),
            "odds_draw": odds.get("odds_draw", 0),
            "odds_away": odds.get("odds_away", 0),
            "odds_over_25": odds.get("odds_over_25", 0),
            "odds_under_25": odds.get("odds_under_25", 0),
            "scraped_at": datetime.now().isoformat(),
            # Extra context from sofascore
            "sofascore_event_id": event_id,
        }
        results.append(result)

        time.sleep(delay)

    console.print(f"  SofaScore odds: {len(results)} matches fetched, {skipped} skipped (no odds or unmapped league)")
    return results


if __name__ == "__main__":
    from rich.table import Table
    from workers.scrapers.flashscore import get_todays_matches_from_flashscore

    console.print("[bold]OddsIntel — SofaScore Odds Scraper Test[/bold]\n")

    fixtures = get_todays_matches_from_flashscore()
    console.print(f"Fixtures: {len(fixtures)} total from SofaScore")

    matches = fetch_all_odds(fixtures)
    console.print(f"\n[bold green]Total matches with odds: {len(matches)}[/bold green]\n")

    t = Table(title=f"SofaScore Odds ({len(matches)} matches)")
    t.add_column("League", style="cyan")
    t.add_column("Match")
    t.add_column("1", justify="right", style="green")
    t.add_column("X", justify="right", style="yellow")
    t.add_column("2", justify="right", style="red")
    t.add_column("O2.5", justify="right")
    t.add_column("U2.5", justify="right")

    for m in sorted(matches, key=lambda x: x["league_path"]):
        t.add_row(
            m["league_path"][:30],
            f"{m['home_team'][:15]} vs {m['away_team'][:15]}",
            f"{m['odds_home']:.2f}" if m["odds_home"] else "-",
            f"{m["odds_draw"]:.2f}" if m["odds_draw"] else "-",
            f"{m['odds_away']:.2f}" if m["odds_away"] else "-",
            f"{m['odds_over_25']:.2f}" if m["odds_over_25"] else "-",
            f"{m['odds_under_25']:.2f}" if m["odds_under_25"] else "-",
        )

    console.print(t)

    # Coverage by tier
    by_tier = {}
    for m in matches:
        t_val = m.get("tier", 0)
        by_tier[t_val] = by_tier.get(t_val, 0) + 1
    console.print("\nCoverage by tier:")
    for tier in sorted(by_tier):
        console.print(f"  Tier {tier}: {by_tier[tier]} matches")
