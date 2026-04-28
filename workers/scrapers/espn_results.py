"""
OddsIntel — ESPN Match Results Scraper
Fetches finished match results from ESPN's public API.
No API key needed. One call per league per date.

Usage:
    from workers.scrapers.espn_results import get_finished_matches_espn
    results = get_finished_matches_espn("2026-04-27")
"""

import requests
from datetime import date
from rich.console import Console

console = Console()

# ESPN league slugs for our target leagues
# Format: "sport/league" → espn_slug
ESPN_LEAGUES = {
    # Tier 1
    "eng.1": "England / Premier League",
    "esp.1": "Spain / LaLiga",
    "ger.1": "Germany / Bundesliga",
    "ita.1": "Italy / Serie A",
    "fra.1": "France / Ligue 1",
    "ned.1": "Netherlands / Eredivisie",
    "tur.1": "Turkey / Super Lig",
    "gre.1": "Greece / Super League",
    "sco.1": "Scotland / Premiership",
    "por.1": "Portugal / Primeira Liga",
    # Tier 2
    "eng.2": "England / Championship",
    "esp.2": "Spain / LaLiga2",
    "ger.2": "Germany / 2. Bundesliga",
    "ita.2": "Italy / Serie B",
    "fra.2": "France / Ligue 2",
    # Tier 3
    "eng.3": "England / League One",
    "eng.4": "England / League Two",
    # Additional
    "bel.1": "Belgium / Jupiler Pro League",
    "den.1": "Denmark / Superliga",
    "nor.1": "Norway / Eliteserien",
    "swe.1": "Sweden / Allsvenskan",
    "fin.1": "Finland / Veikkausliiga",
    "aut.1": "Austria / Bundesliga",
    "sui.1": "Switzerland / Super League",
    "pol.1": "Poland / Ekstraklasa",
    "cze.1": "Czech Republic / First League",
    "rou.1": "Romania / Liga 1",
    "ser.1": "Serbia / SuperLiga",
    "cro.1": "Croatia / HNL",
    "hun.1": "Hungary / NB I",
    "bul.1": "Bulgaria / First League",
    "ukr.1": "Ukraine / Premier League",
    "rus.1": "Russia / Premier League",
    "arg.1": "Argentina / Liga Profesional",
    "bra.1": "Brazil / Serie A",
    "mex.1": "Mexico / Liga MX",
    "usa.1": "USA / MLS",
    "chn.1": "China / Super League",
    "jpn.1": "Japan / J1 League",
    "kor.1": "South Korea / K League 1",
    "aus.1": "Australia / A-League",
    "sgp.1": "Singapore / Premier League",
    # Smaller European leagues
    "isl.1": "Iceland / Úrvalsdeild",
    "lat.1": "Latvia / Virsliga",
    "ltu.1": "Lithuania / A Lyga",
    "est.1": "Estonia / Meistriliiga",
    "cyp.1": "Cyprus / First Division",
    "svk.1": "Slovakia / Super Liga",
    "svn.1": "Slovenia / PrvaLiga",
    "bih.1": "Bosnia / Premijer Liga",
    "mkd.1": "North Macedonia / First League",
    "mne.1": "Montenegro / First League",
    "alb.1": "Albania / Superliga",
    "geo.1": "Georgia / Erovnuli Liga",
    "kaz.1": "Kazakhstan / Premier League",
    "uzb.1": "Uzbekistan / Super League",
    # Asian
    "kor.1": "South Korea / K League 1",
    "kor.2": "South Korea / K League 2",
    "tha.1": "Thailand / Thai League",
    "idn.1": "Indonesia / Liga 1",
    "mys.1": "Malaysia / Super League",
    "vnm.1": "Vietnam / V.League 1",
    # Americas
    "col.1": "Colombia / Liga BetPlay",
    "chl.1": "Chile / Primera División",
    "per.1": "Peru / Liga 1",
    "ecu.1": "Ecuador / Liga Pro",
    "par.1": "Paraguay / División Profesional",
    "uru.1": "Uruguay / Primera División",
    "ven.1": "Venezuela / Liga FUTVE",
    "crc.1": "Costa Rica / Primera División",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def get_finished_matches_espn(target_date: str = None) -> list[dict]:
    """
    Fetch finished match results from ESPN for all configured leagues.
    target_date: YYYY-MM-DD format. Defaults to today.
    Returns list of dicts compatible with settlement pipeline.
    """
    if not target_date:
        target_date = date.today().isoformat()

    date_param = target_date.replace("-", "")
    all_matches = []

    for slug, league_label in ESPN_LEAGUES.items():
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={date_param}"
            resp = requests.get(url, headers=HEADERS, timeout=10)

            if resp.status_code != 200:
                continue

            data = resp.json()
            events = data.get("events", [])

            for event in events:
                competitions = event.get("competitions", [])
                if not competitions:
                    continue

                comp = competitions[0]
                competitors = comp.get("competitors", [])
                if len(competitors) < 2:
                    continue

                status_type = comp.get("status", {}).get("type", {})
                is_finished = status_type.get("completed", False)

                # Identify home/away
                home = away = None
                for c in competitors:
                    if c.get("homeAway") == "home":
                        home = c
                    else:
                        away = c

                if not home or not away:
                    continue

                home_score = home.get("score")
                away_score = away.get("score")

                match = {
                    "event_id": event.get("id"),
                    "home_team": home.get("team", {}).get("displayName", ""),
                    "away_team": away.get("team", {}).get("displayName", ""),
                    "home_goals": int(home_score) if home_score is not None and is_finished else None,
                    "away_goals": int(away_score) if away_score is not None and is_finished else None,
                    "status": "FT" if is_finished else status_type.get("name", "NS"),
                    "league_name": league_label.split(" / ")[-1],
                    "country": league_label.split(" / ")[0],
                    "source": "espn",
                }
                all_matches.append(match)

        except Exception as e:
            console.print(f"  [yellow]ESPN {slug}: {e}[/yellow]")
            continue

    return all_matches


if __name__ == "__main__":
    console.print("[bold]ESPN Match Results Test[/bold]\n")

    today = date.today().isoformat()
    matches = get_finished_matches_espn(today)
    finished = [m for m in matches if m["status"] == "FT"]

    console.print(f"Found {len(matches)} total matches, {len(finished)} finished\n")

    for m in finished[:20]:
        console.print(
            f"  {m['country']} - {m['league_name']}: "
            f"{m['home_team']} {m['home_goals']}-{m['away_goals']} {m['away_team']}"
        )
    if len(finished) > 20:
        console.print(f"  ... and {len(finished) - 20} more")
