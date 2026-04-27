"""
OddsIntel — Understat xG Scraper
Downloads expected goals (xG) data from understat.com for top 5 European leagues.
Free, no API key needed. Just scraping JSON embedded in their web pages.

Covers: Premier League, La Liga, Bundesliga, Serie A, Ligue 1
Seasons: 2014-15 to 2024-25
"""

import json
import time
import re
import pandas as pd
import requests
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

console = Console()

ENGINE_DIR = Path(__file__).parent.parent
RAW_DIR = ENGINE_DIR / "data" / "raw" / "understat"
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"

RAW_DIR.mkdir(parents=True, exist_ok=True)

# Understat league names and our football-data.co.uk codes
LEAGUES = {
    "EPL": {"name": "Premier League", "fd_code": "E0"},
    "La_liga": {"name": "La Liga", "fd_code": "SP1"},
    "Bundesliga": {"name": "Bundesliga", "fd_code": "D1"},
    "Serie_A": {"name": "Serie A", "fd_code": "I1"},
    "Ligue_1": {"name": "Ligue 1", "fd_code": "F1"},
}

# Understat uses calendar years for seasons: 2024 = 2024-25 season
SEASONS = list(range(2014, 2025))  # 2014 to 2024 (= 2014-15 to 2024-25)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def extract_json_data(html: str, var_name: str) -> list | dict | None:
    """Extract JSON data from Understat's embedded JavaScript variables"""
    # Understat embeds data as: var datesData = JSON.parse('...')
    pattern = rf"var\s+{var_name}\s*=\s*JSON\.parse\('(.+?)'\)"
    match = re.search(pattern, html)

    if not match:
        return None

    # The JSON string has escaped characters
    json_str = match.group(1)
    # Decode unicode escapes
    json_str = json_str.encode().decode("unicode_escape")

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


def scrape_league_season(league: str, season: int) -> pd.DataFrame | None:
    """Scrape all match xG data for a league/season from Understat"""

    cache_path = RAW_DIR / f"{league}_{season}.json"

    # Use cached data if available
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                data = json.load(f)
            if data:
                return pd.DataFrame(data)
        except Exception:
            pass

    url = f"https://understat.com/league/{league}/{season}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None

        # Extract match data from the page
        data = extract_json_data(resp.text, "datesData")

        if not data:
            return None

        # Cache the raw data
        with open(cache_path, "w") as f:
            json.dump(data, f)

        return pd.DataFrame(data)

    except Exception as e:
        console.print(f"[red]Error scraping {league} {season}: {e}[/red]")
        return None


def process_match_data(df: pd.DataFrame, league: str, season: int) -> pd.DataFrame:
    """Process raw Understat match data into clean format"""
    if df is None or len(df) == 0:
        return pd.DataFrame()

    rows = []
    for _, match in df.iterrows():
        try:
            home_team = match.get("h", {})
            away_team = match.get("a", {})

            if isinstance(home_team, str):
                # Sometimes the data structure varies
                continue

            row = {
                "date": match.get("datetime", "")[:10],
                "home_team": home_team.get("title", ""),
                "away_team": away_team.get("title", ""),
                "home_goals": int(home_team.get("goals", 0)),
                "away_goals": int(away_team.get("goals", 0)),
                "xg_home": float(home_team.get("xG", 0)),
                "xg_away": float(away_team.get("xG", 0)),
                "league": league,
                "season": f"{season}-{str(season+1)[-2:]}",
                "understat_id": match.get("id", ""),
                "is_result": match.get("isResult", False),
            }

            # Only include completed matches
            if row["is_result"] or (row["home_goals"] > 0 or row["away_goals"] > 0):
                rows.append(row)
        except (TypeError, ValueError, KeyError):
            continue

    return pd.DataFrame(rows)


def scrape_all():
    """Scrape xG data for all leagues and seasons"""
    console.print("\n[bold green]OddsIntel — Understat xG Scraper[/bold green]")
    console.print("Downloading expected goals data from understat.com\n")

    all_data = []

    total = len(LEAGUES) * len(SEASONS)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Scraping...", total=total)

        for league, info in LEAGUES.items():
            for season in SEASONS:
                season_str = f"{season}-{str(season+1)[-2:]}"
                progress.update(task, description=f"{info['name']} {season_str}")

                raw_df = scrape_league_season(league, season)

                if raw_df is not None and len(raw_df) > 0:
                    processed = process_match_data(raw_df, league, season)
                    if len(processed) > 0:
                        processed["fd_code"] = info["fd_code"]
                        processed["league_name"] = info["name"]
                        all_data.append(processed)

                progress.advance(task)
                time.sleep(1.0)  # Be polite to the server

    if not all_data:
        console.print("[red]No xG data downloaded.[/red]")
        return

    combined = pd.concat(all_data, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values("date").reset_index(drop=True)

    # Compute derived xG features
    combined["xg_total"] = combined["xg_home"] + combined["xg_away"]
    combined["xg_diff"] = combined["xg_home"] - combined["xg_away"]

    # Save
    output_path = PROCESSED_DIR / "xg_data.csv"
    combined.to_csv(output_path, index=False)

    # Summary
    console.print(f"\n[bold green]xG Scrape Complete![/bold green]\n")

    summary = Table(title="xG Data Summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", style="green")
    summary.add_row("Total matches", f"{len(combined):,}")
    summary.add_row("Date range", f"{combined['date'].min().strftime('%Y-%m-%d')} to {combined['date'].max().strftime('%Y-%m-%d')}")
    summary.add_row("Leagues", str(combined["league_name"].nunique()))
    summary.add_row("Seasons", str(combined["season"].nunique()))
    summary.add_row("Avg xG per match", f"{combined['xg_total'].mean():.2f}")
    summary.add_row("Output file", str(output_path))
    console.print(summary)

    # Per-league breakdown
    league_table = Table(title="xG by League")
    league_table.add_column("League", style="cyan")
    league_table.add_column("Matches", justify="right")
    league_table.add_column("Avg xG Home", justify="right")
    league_table.add_column("Avg xG Away", justify="right")
    league_table.add_column("Avg xG Total", justify="right")

    for league_name in sorted(combined["league_name"].unique()):
        ld = combined[combined["league_name"] == league_name]
        league_table.add_row(
            league_name,
            f"{len(ld):,}",
            f"{ld['xg_home'].mean():.2f}",
            f"{ld['xg_away'].mean():.2f}",
            f"{ld['xg_total'].mean():.2f}",
        )

    console.print(league_table)

    return combined


if __name__ == "__main__":
    scrape_all()
