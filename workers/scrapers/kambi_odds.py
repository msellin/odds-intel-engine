"""
OddsIntel — Kambi Odds Scraper
Fetches real odds from Kambi-powered bookmakers (Unibet, Paf, Betsafe).
Free, no API key, no rate limit issues at reasonable polling.

Kambi powers: Unibet, Betsafe, Paf, 888sport, LeoVegas, and more.
The API returns odds in millioddss (1750 = 1.75 decimal odds).
"""

import requests
import time
from datetime import datetime
from rich.console import Console

console = Console()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Kambi operator codes
OPERATORS = {
    "unibet": "ub",
    "paf": "paf",
    # "betsafe": "betsafe",  # Rate limited more aggressively
}

# League path mapping (Kambi path → our football-data code)
LEAGUE_MAP = {
    "England / Premier League": {"fd_code": "E0", "tier": 1},
    "England / Championship": {"fd_code": "E1", "tier": 2},
    "England / League One": {"fd_code": "E2", "tier": 3},
    "England / League Two": {"fd_code": "E3", "tier": 4},
    "Spain / La Liga": {"fd_code": "SP1", "tier": 1},
    "Spain / La Liga 2": {"fd_code": "SP2", "tier": 2},
    "Germany / Bundesliga": {"fd_code": "D1", "tier": 1},
    "Germany / 2. Bundesliga": {"fd_code": "D2", "tier": 2},
    "Italy / Serie A": {"fd_code": "I1", "tier": 1},
    "Italy / Serie B": {"fd_code": "I2", "tier": 2},
    "France / Ligue 1": {"fd_code": "F1", "tier": 1},
    "France / Ligue 2": {"fd_code": "F2", "tier": 2},
    "Netherlands / Eredivisie": {"fd_code": "N1", "tier": 1},
    "Turkey / Süper Lig": {"fd_code": "T1", "tier": 1},
    "Greece / Super League": {"fd_code": "G1", "tier": 1},
    "Scotland / Premiership": {"fd_code": "SC0", "tier": 1},
    "Portugal / Primeira Liga": {"fd_code": "P1", "tier": 1},
    "Belgium / Jupiler Pro League": {"fd_code": "B1", "tier": 1},
    "Sweden / Allsvenskan": {"fd_code": "SE1", "tier": 1},
    "Denmark / Superligaen": {"fd_code": "DK1", "tier": 1},
    "Estonia / Esiliiga": {"fd_code": "EST1", "tier": 2},
    "Estonia / Esiliiga B": {"fd_code": "EST2", "tier": 3},
}


def fetch_odds(operator: str = "ub") -> list[dict]:
    """
    Fetch all football events with odds from a Kambi operator.
    Returns structured match data with 1X2 and Over/Under odds.
    """
    url = f"https://eu-offering-api.kambicdn.com/offering/v2018/{operator}/listView/football.json"

    try:
        resp = requests.get(url, headers=HEADERS,
                           params={"lang": "en_GB", "market": "EE"},
                           timeout=15)

        if resp.status_code != 200:
            console.print(f"[red]Kambi API error: {resp.status_code}[/red]")
            return []

        data = resp.json()
        events = data.get("events", [])

        matches = []
        for event in events:
            match = _parse_event(event, operator)
            if match:
                matches.append(match)

        return matches

    except Exception as e:
        console.print(f"[red]Kambi error: {e}[/red]")
        return []


def _parse_event(event: dict, operator: str) -> dict | None:
    """Parse a single Kambi event into our format"""
    event_data = event.get("event", {})

    name = event_data.get("name", "")
    if " - " not in name:
        return None

    home_team, away_team = name.split(" - ", 1)

    # Build league path
    path_parts = [p.get("englishName", p.get("name", ""))
                  for p in event_data.get("path", [])]
    # Skip "Football" prefix
    league_path = " / ".join(path_parts[1:]) if len(path_parts) > 1 else ""

    # Skip esports
    if "Esports" in league_path or "esports" in league_path:
        return None

    # Map to our league codes
    league_info = LEAGUE_MAP.get(league_path, {})

    start_time = event_data.get("start", "")

    # Extract odds from betOffers
    odds_1x2 = {}
    odds_ou = {}

    for offer in event.get("betOffers", []):
        offer_type = offer.get("betOfferType", {}).get("name", "")

        if offer_type == "Match":
            # 1X2 odds
            for outcome in offer.get("outcomes", []):
                label = outcome.get("label", "")
                raw_odds = outcome.get("odds", 0)
                decimal_odds = raw_odds / 1000 if raw_odds > 0 else 0

                if label == "1":
                    odds_1x2["home"] = decimal_odds
                elif label == "X":
                    odds_1x2["draw"] = decimal_odds
                elif label == "2":
                    odds_1x2["away"] = decimal_odds

        elif offer_type == "Over/Under":
            # Over/Under odds
            line = offer.get("outcomes", [{}])[0].get("line", 0) / 1000 if offer.get("outcomes") else 0

            if abs(line - 2.5) < 0.01:  # Only 2.5 line
                for outcome in offer.get("outcomes", []):
                    label = outcome.get("label", "")
                    raw_odds = outcome.get("odds", 0)
                    decimal_odds = raw_odds / 1000 if raw_odds > 0 else 0

                    if label == "Over":
                        odds_ou["over_25"] = decimal_odds
                    elif label == "Under":
                        odds_ou["under_25"] = decimal_odds

    # Only include if we have at least 1X2 odds
    if not odds_1x2:
        return None

    return {
        "home_team": home_team.strip(),
        "away_team": away_team.strip(),
        "start_time": start_time,
        "league_path": league_path,
        "league_code": league_info.get("fd_code", ""),
        "tier": league_info.get("tier", 0),
        "operator": operator,
        "odds_home": odds_1x2.get("home", 0),
        "odds_draw": odds_1x2.get("draw", 0),
        "odds_away": odds_1x2.get("away", 0),
        "odds_over_25": odds_ou.get("over_25", 0),
        "odds_under_25": odds_ou.get("under_25", 0),
        "scraped_at": datetime.now().isoformat(),
    }


def fetch_all_operators() -> list[dict]:
    """
    Fetch odds from all operators and merge.
    Returns best odds across operators for each match.
    """
    all_matches = {}

    for op_name, op_code in OPERATORS.items():
        console.print(f"  Fetching from {op_name}...")
        matches = fetch_odds(op_code)

        for m in matches:
            key = f"{m['home_team']}_{m['away_team']}_{m['start_time'][:10]}"

            if key not in all_matches:
                all_matches[key] = {
                    **m,
                    "operators": {},
                }

            # Store per-operator odds
            all_matches[key]["operators"][op_name] = {
                "home": m["odds_home"],
                "draw": m["odds_draw"],
                "away": m["odds_away"],
                "over_25": m["odds_over_25"],
                "under_25": m["odds_under_25"],
            }

            # Update best odds
            if m["odds_home"] > all_matches[key]["odds_home"]:
                all_matches[key]["odds_home"] = m["odds_home"]
            if m["odds_draw"] > all_matches[key]["odds_draw"]:
                all_matches[key]["odds_draw"] = m["odds_draw"]
            if m["odds_away"] > all_matches[key]["odds_away"]:
                all_matches[key]["odds_away"] = m["odds_away"]
            if m["odds_over_25"] > all_matches[key]["odds_over_25"]:
                all_matches[key]["odds_over_25"] = m["odds_over_25"]
            if m["odds_under_25"] > all_matches[key]["odds_under_25"]:
                all_matches[key]["odds_under_25"] = m["odds_under_25"]

        time.sleep(2)  # Be polite between operators

    return list(all_matches.values())


def get_target_league_matches() -> list[dict]:
    """Get only matches from our target leagues with odds"""
    all_matches = fetch_all_operators()

    # Filter to mapped leagues only
    target = [m for m in all_matches if m.get("league_code")]
    other = [m for m in all_matches if not m.get("league_code")]

    console.print(f"\n  Target league matches: {len(target)}")
    console.print(f"  Other leagues (not tracked): {len(other)}")

    return target


if __name__ == "__main__":
    from rich.table import Table

    console.print("[bold]OddsIntel — Kambi Odds Scraper Test[/bold]\n")

    matches = fetch_all_operators()
    console.print(f"\nTotal matches with odds: {len(matches)}")

    # Show target league matches
    target = [m for m in matches if m.get("league_code")]

    t = Table(title=f"Target League Matches ({len(target)})")
    t.add_column("League", style="cyan")
    t.add_column("Match")
    t.add_column("1", justify="right", style="green")
    t.add_column("X", justify="right", style="yellow")
    t.add_column("2", justify="right", style="red")
    t.add_column("O2.5", justify="right")
    t.add_column("U2.5", justify="right")
    t.add_column("Kickoff")

    for m in sorted(target, key=lambda x: x["start_time"]):
        t.add_row(
            m["league_path"][:30],
            f"{m['home_team'][:15]} vs {m['away_team'][:15]}",
            f"{m['odds_home']:.2f}" if m['odds_home'] else "-",
            f"{m['odds_draw']:.2f}" if m['odds_draw'] else "-",
            f"{m['odds_away']:.2f}" if m['odds_away'] else "-",
            f"{m['odds_over_25']:.2f}" if m['odds_over_25'] else "-",
            f"{m['odds_under_25']:.2f}" if m['odds_under_25'] else "-",
            m["start_time"][11:16] if len(m["start_time"]) > 11 else "",
        )

    console.print(t)

    # Show all leagues available
    leagues = set(m["league_path"] for m in matches if m["league_path"])
    console.print(f"\nAll available leagues: {len(leagues)}")
    for l in sorted(leagues):
        count = sum(1 for m in matches if m["league_path"] == l)
        mapped = "✓" if l in LEAGUE_MAP else " "
        console.print(f"  {mapped} {l} ({count})")
