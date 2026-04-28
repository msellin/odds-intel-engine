"""
OddsIntel — BetExplorer Odds Scraper
Fetches 1X2 and Over/Under odds from BetExplorer's internal Ajax API.

BetExplorer covers 1000+ leagues with 15-20 bookmakers per match.
Key value: fills gaps where Kambi + SofaScore have no coverage
(e.g. Singapore, South Korea, Scotland lower divisions).

No Playwright needed — uses requests + BeautifulSoup on Ajax endpoints.

Rate: ~1s delay between requests to be polite.
"""

import re
import time
import requests
from datetime import datetime, date
from bs4 import BeautifulSoup
from rich.console import Console

console = Console()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json",
    "Accept-Language": "en-GB,en;q=0.9",
}

AJAX_HEADERS = {
    **HEADERS,
    "X-Requested-With": "XMLHttpRequest",
}

BASE_URL = "https://www.betexplorer.com"

# BetExplorer URL slug → our league code + tier
# Format: (country_slug, league_slug) → {fd_code, tier}
LEAGUE_MAP = {
    # England
    ("england", "premier-league"): {"fd_code": "E0", "tier": 1},
    ("england", "championship"): {"fd_code": "E1", "tier": 2},
    ("england", "league-one"): {"fd_code": "E2", "tier": 3},
    ("england", "league-two"): {"fd_code": "E3", "tier": 4},
    # Spain
    ("spain", "laliga"): {"fd_code": "SP1", "tier": 1},
    ("spain", "laliga2"): {"fd_code": "SP2", "tier": 2},
    # Germany
    ("germany", "bundesliga"): {"fd_code": "D1", "tier": 1},
    ("germany", "2-bundesliga"): {"fd_code": "D2", "tier": 2},
    # Italy
    ("italy", "serie-a"): {"fd_code": "I1", "tier": 1},
    ("italy", "serie-b"): {"fd_code": "I2", "tier": 2},
    # France
    ("france", "ligue-1"): {"fd_code": "F1", "tier": 1},
    ("france", "ligue-2"): {"fd_code": "F2", "tier": 2},
    # Netherlands
    ("netherlands", "eredivisie"): {"fd_code": "N1", "tier": 1},
    # Turkey
    ("turkey", "super-lig"): {"fd_code": "T1", "tier": 1},
    ("turkey", "1-lig"): {"fd_code": "T2", "tier": 2},
    # Greece
    ("greece", "super-league"): {"fd_code": "G1", "tier": 1},
    # Scotland — all divisions (strong backtest signal)
    ("scotland", "premiership"): {"fd_code": "SC0", "tier": 1},
    ("scotland", "championship"): {"fd_code": "SC1", "tier": 2},
    ("scotland", "league-one"): {"fd_code": "SC2", "tier": 3},
    ("scotland", "league-two"): {"fd_code": "SC3", "tier": 4},
    # Portugal
    ("portugal", "liga-portugal"): {"fd_code": "P1", "tier": 1},
    ("portugal", "liga-portugal-2"): {"fd_code": "P2", "tier": 2},
    # Belgium
    ("belgium", "jupiler-pro-league"): {"fd_code": "B1", "tier": 1},
    # Sweden
    ("sweden", "allsvenskan"): {"fd_code": "SE1", "tier": 1},
    # Denmark
    ("denmark", "superligaen"): {"fd_code": "DK1", "tier": 1},
    # Norway
    ("norway", "eliteserien"): {"fd_code": "NOR1", "tier": 1},
    ("norway", "obos-ligaen"): {"fd_code": "NOR2", "tier": 2},
    # Poland
    ("poland", "ekstraklasa"): {"fd_code": "PL1", "tier": 1},
    # Austria — all divisions (backtest signal)
    ("austria", "bundesliga"): {"fd_code": "AT1", "tier": 1},
    ("austria", "2-liga"): {"fd_code": "AT2", "tier": 2},
    # Switzerland
    ("switzerland", "super-league"): {"fd_code": "SW1", "tier": 1},
    # Czech Republic
    ("czech-republic", "fortuna-liga"): {"fd_code": "CZ1", "tier": 1},
    # Croatia
    ("croatia", "hnl"): {"fd_code": "CR1", "tier": 1},
    # Romania
    ("romania", "liga-i"): {"fd_code": "RO1", "tier": 1},
    ("romania", "superliga"): {"fd_code": "RO1", "tier": 1},
    # Serbia
    ("serbia", "super-liga"): {"fd_code": "SER1", "tier": 1},
    # Hungary
    ("hungary", "nb-i"): {"fd_code": "HUN1", "tier": 1},
    # Ireland — (backtest signal)
    ("ireland", "premier-division"): {"fd_code": "IRL1", "tier": 1},
    ("ireland", "first-division"): {"fd_code": "IRL2", "tier": 2},
    # Singapore — highest ROI signal (+27.5%)
    ("singapore", "premier-league"): {"fd_code": "SIN1", "tier": 1},
    # South Korea
    ("south-korea", "k-league-1"): {"fd_code": "KOR1", "tier": 1},
    ("south-korea", "k-league-2"): {"fd_code": "KOR2", "tier": 2},
    # Estonia
    ("estonia", "meistriliiga"): {"fd_code": "EST0", "tier": 1},
    ("estonia", "esiliiga"): {"fd_code": "EST1", "tier": 2},
    # Iceland
    ("iceland", "urvalsdeild"): {"fd_code": "ICE1", "tier": 1},
    # Latvia
    ("latvia", "virsliga"): {"fd_code": "LAT1", "tier": 1},
    # Cyprus
    ("cyprus", "1st-division"): {"fd_code": "CY1", "tier": 1},
    # Georgia
    ("georgia", "erovnuli-liga"): {"fd_code": "GEO1", "tier": 1},
    # Ukraine
    ("ukraine", "premier-league"): {"fd_code": "UA1", "tier": 1},
}


def _parse_1x2_odds(html: str) -> list[dict]:
    """Parse 1X2 odds from BetExplorer Ajax response HTML."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr[data-bid]")
    bookmakers = []
    for row in rows:
        name_el = row.select_one("a.in-bookmaker-logo-link")
        name = name_el.get_text(strip=True) if name_el else "unknown"
        odds_tds = row.select("td[data-odd]")
        if len(odds_tds) != 3:
            continue
        bookmakers.append({
            "bookmaker": name.lower().replace(" ", ""),
            "odds_home": float(odds_tds[0]["data-odd"]),
            "odds_draw": float(odds_tds[1]["data-odd"]),
            "odds_away": float(odds_tds[2]["data-odd"]),
        })
    return bookmakers


def _parse_ou_odds(html: str) -> dict[str, list[dict]]:
    """
    Parse Over/Under odds from BetExplorer Ajax response HTML.
    Returns dict keyed by line (e.g. "2.5") → list of bookmaker odds.
    """
    soup = BeautifulSoup(html, "html.parser")
    lines = {}

    for thead in soup.select("thead"):
        classes = thead.get("class", [])
        # Extract line from class like "thead-collapse-2.50"
        line_val = None
        for cls in classes:
            if cls.startswith("thead-collapse-"):
                line_val = cls.replace("thead-collapse-", "")
                break
        if not line_val:
            continue

        tbody = thead.find_next_sibling("tbody")
        if not tbody:
            continue

        bookmakers = []
        for row in tbody.select("tr[data-bid]"):
            name_el = row.select_one("a.in-bookmaker-logo-link")
            name = name_el.get_text(strip=True) if name_el else "unknown"
            odds_tds = row.select("td[data-odd]")
            if len(odds_tds) != 2:
                continue
            bookmakers.append({
                "bookmaker": name.lower().replace(" ", ""),
                "over": float(odds_tds[0]["data-odd"]),
                "under": float(odds_tds[1]["data-odd"]),
            })

        if bookmakers:
            lines[line_val] = bookmakers

    return lines


def fetch_match_odds(match_id: str, referer_url: str = "") -> dict:
    """
    Fetch 1X2 and O/U odds for a single BetExplorer match.
    Returns dict with best odds across all bookmakers.
    """
    headers = {**AJAX_HEADERS}
    if referer_url:
        headers["Referer"] = referer_url

    result = {
        "odds_home": 0, "odds_draw": 0, "odds_away": 0,
        "odds_over_25": 0, "odds_under_25": 0,
        "bookmaker_count": 0,
    }

    # 1X2 odds
    try:
        resp = requests.get(
            f"{BASE_URL}/match-odds/{match_id}/1/1x2/odds/?lang=en",
            headers=headers, timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            bookmakers = _parse_1x2_odds(data.get("odds", ""))
            if bookmakers:
                result["odds_home"] = max(b["odds_home"] for b in bookmakers)
                result["odds_draw"] = max(b["odds_draw"] for b in bookmakers)
                result["odds_away"] = max(b["odds_away"] for b in bookmakers)
                result["bookmaker_count"] = len(bookmakers)
                result["all_1x2"] = bookmakers
    except Exception as e:
        console.print(f"  [yellow]1X2 odds error for {match_id}: {e}[/yellow]")

    time.sleep(0.3)

    # O/U odds
    try:
        resp = requests.get(
            f"{BASE_URL}/match-odds/{match_id}/1/ou/odds/?lang=en",
            headers=headers, timeout=15
        )
        if resp.status_code == 200:
            data = resp.json()
            ou_lines = _parse_ou_odds(data.get("odds", ""))

            # Extract best odds for each line we care about
            for line_str, line_key in [
                ("0.50", "05"), ("1.50", "15"), ("2.50", "25"),
                ("3.50", "35"), ("4.50", "45"),
            ]:
                if line_str in ou_lines:
                    bks = ou_lines[line_str]
                    result[f"odds_over_{line_key}"] = max(b["over"] for b in bks)
                    result[f"odds_under_{line_key}"] = max(b["under"] for b in bks)

            # Ensure standard O/U 2.5 is set
            if "2.50" in ou_lines:
                bks = ou_lines["2.50"]
                result["odds_over_25"] = max(b["over"] for b in bks)
                result["odds_under_25"] = max(b["under"] for b in bks)

    except Exception as e:
        console.print(f"  [yellow]O/U odds error for {match_id}: {e}[/yellow]")

    return result


def fetch_league_matches(
    country: str, league: str, mode: str = "upcoming"
) -> list[dict]:
    """
    Fetch matches from a BetExplorer league page.

    Args:
        country: BetExplorer country slug (e.g. "singapore")
        league: BetExplorer league slug (e.g. "premier-league")
        mode: "upcoming" for next fixtures, "results" for completed matches

    Returns list of dicts with match_id, home_team, away_team, score (if finished).
    """
    suffix = "" if mode == "upcoming" else "results/"
    url = f"{BASE_URL}/football/{country}/{league}/{suffix}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            console.print(f"  [yellow]Rate limited on {url}, waiting 15s...[/yellow]")
            time.sleep(15)
            resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            console.print(f"  [red]HTTP {resp.status_code} for {url}[/red]")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        pattern = re.compile(
            rf"/football/{re.escape(country)}/{re.escape(league)}/([^/]+)/([A-Za-z0-9]+)/"
        )

        matches = {}
        for link in soup.find_all("a", href=pattern):
            m = pattern.search(link["href"])
            if not m:
                continue
            slug, match_id = m.group(1), m.group(2)
            text = link.get_text(strip=True)

            if match_id in matches:
                # Update with additional info
                if ":" in text and "score" not in matches[match_id]:
                    matches[match_id]["score"] = text
                continue

            # Parse teams from slug (e.g. "arsenal-newcastle-utd")
            # We'll get better names from the link text
            entry = {
                "match_id": match_id,
                "slug": slug,
                "url": link["href"],
            }

            if "-" in text and ":" not in text:
                # Team names like "Geylang-Lion City Sailors"
                # BetExplorer uses single hyphen between teams
                parts = text.split("-", 1)
                if len(parts) == 2:
                    entry["home_team"] = parts[0].strip()
                    entry["away_team"] = parts[1].strip()
            elif ":" in text:
                entry["score"] = text

            matches[match_id] = entry

        # Merge scores into match entries
        result = []
        for match_id, entry in matches.items():
            if "home_team" not in entry:
                # Try parsing from slug
                slug_parts = entry["slug"].rsplit("-", 1)
                if len(slug_parts) >= 1:
                    entry["home_team"] = entry["slug"].replace("-", " ").title()
                    entry["away_team"] = ""

            result.append(entry)

        return result

    except Exception as e:
        console.print(f"  [red]Error fetching {url}: {e}[/red]")
        return []


def fetch_league_odds(
    country: str,
    league: str,
    mode: str = "upcoming",
    delay: float = 1.0,
    max_matches: int = 0,
) -> list[dict]:
    """
    Fetch all matches + odds for a league.
    Returns list of match dicts in the same format as Kambi/SofaScore scrapers.
    """
    league_key = (country, league)
    league_info = LEAGUE_MAP.get(league_key, {})

    matches = fetch_league_matches(country, league, mode=mode)
    if not matches:
        return []

    console.print(
        f"  {country}/{league}: {len(matches)} matches found ({mode})"
    )

    if max_matches > 0:
        matches = matches[:max_matches]

    results = []
    for i, match in enumerate(matches):
        match_id = match["match_id"]
        home = match.get("home_team", "Unknown")
        away = match.get("away_team", "Unknown")

        if not home or not away:
            continue

        referer = f"{BASE_URL}{match['url']}" if match.get("url") else ""
        odds = fetch_match_odds(match_id, referer_url=referer)

        if not odds.get("odds_home"):
            continue

        result = {
            "home_team": home,
            "away_team": away,
            "start_time": "",  # BetExplorer doesn't always show time on results pages
            "league_path": f"{country.title()} / {league.replace('-', ' ').title()}",
            "league_code": league_info.get("fd_code", ""),
            "tier": league_info.get("tier", 0),
            "operator": "betexplorer",
            "bookmaker": "betexplorer",
            # 1X2 best odds
            "odds_home": odds["odds_home"],
            "odds_draw": odds["odds_draw"],
            "odds_away": odds["odds_away"],
            # O/U best odds
            "odds_over_05": odds.get("odds_over_05", 0),
            "odds_under_05": odds.get("odds_under_05", 0),
            "odds_over_15": odds.get("odds_over_15", 0),
            "odds_under_15": odds.get("odds_under_15", 0),
            "odds_over_25": odds.get("odds_over_25", 0),
            "odds_under_25": odds.get("odds_under_25", 0),
            "odds_over_35": odds.get("odds_over_35", 0),
            "odds_under_35": odds.get("odds_under_35", 0),
            "odds_over_45": odds.get("odds_over_45", 0),
            "odds_under_45": odds.get("odds_under_45", 0),
            "ou_lines": {
                k: v for k, v in odds.items()
                if k.startswith("odds_over_") or k.startswith("odds_under_")
            },
            # BetExplorer metadata
            "betexplorer_match_id": match_id,
            "bookmaker_count": odds.get("bookmaker_count", 0),
            "score": match.get("score", ""),
            "scraped_at": datetime.now().isoformat(),
        }
        results.append(result)

        if (i + 1) % 10 == 0:
            console.print(f"    ... {i + 1}/{len(matches)} processed")

        time.sleep(delay)

    console.print(
        f"  BetExplorer {country}/{league}: "
        f"{len(results)} matches with odds out of {len(matches)} total"
    )
    return results


def fetch_all_target_leagues(
    mode: str = "upcoming",
    delay: float = 1.0,
    max_per_league: int = 0,
) -> list[dict]:
    """
    Fetch odds from all target leagues in LEAGUE_MAP.
    Returns unified list compatible with _merge_odds_sources().
    """
    all_matches = []

    for (country, league), info in LEAGUE_MAP.items():
        console.print(f"\n  [cyan]Fetching {country}/{league}...[/cyan]")
        try:
            matches = fetch_league_odds(
                country, league, mode=mode,
                delay=delay, max_matches=max_per_league,
            )
            all_matches.extend(matches)
        except Exception as e:
            console.print(f"  [red]Error for {country}/{league}: {e}[/red]")

        time.sleep(2)  # Pause between leagues

    console.print(
        f"\n  [bold]BetExplorer total: {len(all_matches)} matches with odds[/bold]"
    )
    return all_matches


# Leagues where Kambi + SofaScore have no/weak coverage — only these
# are fetched in the daily pipeline to avoid unnecessary load.
GAP_LEAGUES = [
    ("singapore", "premier-league"),
    ("south-korea", "k-league-1"),
    ("south-korea", "k-league-2"),
    ("scotland", "championship"),
    ("scotland", "league-one"),
    ("scotland", "league-two"),
    ("austria", "2-liga"),
    ("ireland", "premier-division"),
    ("ireland", "first-division"),
    ("iceland", "urvalsdeild"),
    ("georgia", "erovnuli-liga"),
    ("cyprus", "1st-division"),
    ("latvia", "virsliga"),
    ("estonia", "meistriliiga"),
    ("estonia", "esiliiga"),
]


def fetch_gap_leagues_odds(delay: float = 0.5) -> list[dict]:
    """
    Fetch odds only for leagues that Kambi + SofaScore don't cover well.
    Used by the daily pipeline to keep run time reasonable.
    """
    all_matches = []

    for i, (country, league) in enumerate(GAP_LEAGUES):
        console.print(f"  [cyan]{country}/{league}...[/cyan]")
        try:
            matches = fetch_league_odds(
                country, league, mode="upcoming", delay=delay,
            )
            all_matches.extend(matches)
        except Exception as e:
            console.print(f"  [red]Error for {country}/{league}: {e}[/red]")

        # Brief pause between leagues to avoid 429s
        if (i + 1) % 5 == 0:
            time.sleep(4)
        else:
            time.sleep(2)

    console.print(
        f"  [bold]BetExplorer gap leagues: {len(all_matches)} matches with odds[/bold]"
    )
    return all_matches


def fetch_upcoming_odds(delay: float = 1.0) -> list[dict]:
    """Fetch odds for upcoming/today's matches across all target leagues."""
    return fetch_all_target_leagues(mode="upcoming", delay=delay)


def fetch_historical_odds(
    country: str,
    league: str,
    delay: float = 1.0,
    max_matches: int = 0,
) -> list[dict]:
    """Fetch historical (results) odds for a single league."""
    return fetch_league_odds(
        country, league, mode="results",
        delay=delay, max_matches=max_matches,
    )


if __name__ == "__main__":
    from rich.table import Table

    console.print("[bold]OddsIntel — BetExplorer Odds Scraper Test[/bold]\n")

    # Test with Singapore (our gap league)
    console.print("[cyan]Testing Singapore Premier League (results)...[/cyan]")
    matches = fetch_league_odds("singapore", "premier-league", mode="results", max_matches=5)

    if matches:
        t = Table(title=f"BetExplorer Singapore ({len(matches)} matches)")
        t.add_column("Match")
        t.add_column("Score", style="yellow")
        t.add_column("1", justify="right", style="green")
        t.add_column("X", justify="right")
        t.add_column("2", justify="right", style="red")
        t.add_column("O2.5", justify="right")
        t.add_column("U2.5", justify="right")
        t.add_column("Bkms", justify="right", style="cyan")

        for m in matches:
            t.add_row(
                f"{m['home_team'][:18]} vs {m['away_team'][:18]}",
                m.get("score", ""),
                f"{m['odds_home']:.2f}" if m["odds_home"] else "-",
                f"{m['odds_draw']:.2f}" if m["odds_draw"] else "-",
                f"{m['odds_away']:.2f}" if m["odds_away"] else "-",
                f"{m['odds_over_25']:.2f}" if m["odds_over_25"] else "-",
                f"{m['odds_under_25']:.2f}" if m["odds_under_25"] else "-",
                str(m.get("bookmaker_count", 0)),
            )

        console.print(t)
    else:
        console.print("[yellow]No matches found[/yellow]")

    # Test upcoming
    console.print("\n[cyan]Testing Scotland League Two (upcoming)...[/cyan]")
    upcoming = fetch_league_odds("scotland", "league-two", mode="upcoming", max_matches=5)

    if upcoming:
        t2 = Table(title=f"BetExplorer Scotland League Two ({len(upcoming)} upcoming)")
        t2.add_column("Match")
        t2.add_column("1", justify="right", style="green")
        t2.add_column("X", justify="right")
        t2.add_column("2", justify="right", style="red")
        t2.add_column("O2.5", justify="right")
        t2.add_column("U2.5", justify="right")

        for m in upcoming:
            t2.add_row(
                f"{m['home_team'][:18]} vs {m['away_team'][:18]}",
                f"{m['odds_home']:.2f}" if m["odds_home"] else "-",
                f"{m['odds_draw']:.2f}" if m["odds_draw"] else "-",
                f"{m['odds_away']:.2f}" if m["odds_away"] else "-",
                f"{m['odds_over_25']:.2f}" if m["odds_over_25"] else "-",
                f"{m['odds_under_25']:.2f}" if m["odds_under_25"] else "-",
            )

        console.print(t2)
    else:
        console.print("[yellow]No upcoming matches found[/yellow]")
