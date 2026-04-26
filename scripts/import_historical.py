"""
OddsIntel — Historical Data Import
Downloads match results + closing odds from football-data.co.uk
Covers 20+ years across major European leagues.

This is Phase 0: no API keys needed, no accounts, just free CSV data.
"""

import os
import sys
import time
import pandas as pd
import requests
from pathlib import Path
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table

console = Console()

# Project paths
ENGINE_DIR = Path(__file__).parent.parent
RAW_DIR = ENGINE_DIR / "data" / "raw"
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"

RAW_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# football-data.co.uk league codes and readable names
LEAGUES = {
    # Top 5 European leagues
    "E0": {"name": "Premier League", "country": "England", "tier": 1},
    "E1": {"name": "Championship", "country": "England", "tier": 2},
    "E2": {"name": "League One", "country": "England", "tier": 3},
    "E3": {"name": "League Two", "country": "England", "tier": 4},
    "SP1": {"name": "La Liga", "country": "Spain", "tier": 1},
    "SP2": {"name": "Segunda Division", "country": "Spain", "tier": 2},
    "D1": {"name": "Bundesliga", "country": "Germany", "tier": 1},
    "D2": {"name": "2. Bundesliga", "country": "Germany", "tier": 2},
    "I1": {"name": "Serie A", "country": "Italy", "tier": 1},
    "I2": {"name": "Serie B", "country": "Italy", "tier": 2},
    "F1": {"name": "Ligue 1", "country": "France", "tier": 1},
    "F2": {"name": "Ligue 2", "country": "France", "tier": 2},
    # Additional leagues
    "N1": {"name": "Eredivisie", "country": "Netherlands", "tier": 1},
    "B1": {"name": "Jupiler Pro League", "country": "Belgium", "tier": 1},
    "P1": {"name": "Liga Portugal", "country": "Portugal", "tier": 1},
    "T1": {"name": "Super Lig", "country": "Turkey", "tier": 1},
    "G1": {"name": "Super League", "country": "Greece", "tier": 1},
    "SC0": {"name": "Premiership", "country": "Scotland", "tier": 1},
}

# Seasons to download (football-data.co.uk format: 2324 = 2023-24)
# Going back to 2005-06 for most leagues
SEASONS = [
    "0506", "0607", "0708", "0809", "0910",
    "1011", "1112", "1213", "1314", "1415",
    "1516", "1617", "1718", "1819", "1920",
    "2021", "2122", "2223", "2324", "2425",
]

# Columns we want to keep (not all exist in every CSV)
CORE_COLUMNS = [
    "Div", "Date", "Time", "HomeTeam", "AwayTeam",
    "FTHG", "FTAG", "FTR",  # Full-time: home goals, away goals, result
    "HTHG", "HTAG", "HTR",  # Half-time
    "HS", "AS",              # Shots
    "HST", "AST",            # Shots on target
    "HF", "AF",              # Fouls
    "HC", "AC",              # Corners
    "HY", "AY",              # Yellow cards
    "HR", "AR",              # Red cards
]

# Odds columns (various bookmakers)
ODDS_COLUMNS = [
    # Bet365
    "B365H", "B365D", "B365A",
    "B365>2.5", "B365<2.5",
    # Pinnacle (the sharp benchmark)
    "PSH", "PSD", "PSA",
    "P>2.5", "P<2.5",
    # Market average
    "AvgH", "AvgD", "AvgA",
    "Avg>2.5", "Avg<2.5",
    # Max odds
    "MaxH", "MaxD", "MaxA",
    "Max>2.5", "Max<2.5",
    # Bet365 Asian Handicap
    "BbAHh", "BbAH>2.5", "BbAH<2.5",
    "AHh",  # Asian handicap line
    # Additional bookmakers
    "BWH", "BWD", "BWA",    # Bet&Win
    "IWH", "IWD", "IWA",    # Interwetten
    "WHH", "WHD", "WHA",    # William Hill
    "VCH", "VCD", "VCA",    # VC Bet
]


def season_to_readable(season_code: str) -> str:
    """Convert '2324' to '2023-24'"""
    start = int("20" + season_code[:2])
    end = season_code[2:]
    return f"{start}-{end}"


def download_csv(league_code: str, season: str) -> pd.DataFrame | None:
    """Download a single CSV from football-data.co.uk"""
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{league_code}.csv"

    csv_path = RAW_DIR / f"{league_code}_{season}.csv"

    # Use cached file if exists
    if csv_path.exists():
        try:
            df = pd.read_csv(csv_path, encoding="utf-8", on_bad_lines="skip")
            if len(df) > 0:
                return df
        except Exception:
            pass

    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            # Save raw file
            csv_path.write_bytes(response.content)

            try:
                df = pd.read_csv(csv_path, encoding="utf-8", on_bad_lines="skip")
                if len(df) > 0:
                    return df
            except Exception:
                # Try different encoding
                try:
                    df = pd.read_csv(csv_path, encoding="latin-1", on_bad_lines="skip")
                    if len(df) > 0:
                        return df
                except Exception:
                    return None
        return None
    except requests.RequestException:
        return None


def clean_dataframe(df: pd.DataFrame, league_code: str, season: str) -> pd.DataFrame:
    """Clean and standardize a raw CSV dataframe"""
    if df is None or len(df) == 0:
        return pd.DataFrame()

    # Drop rows where essential fields are missing
    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"], how="any")

    if len(df) == 0:
        return pd.DataFrame()

    # Keep only columns that exist
    all_wanted = CORE_COLUMNS + ODDS_COLUMNS
    existing_cols = [c for c in all_wanted if c in df.columns]
    df = df[existing_cols].copy()

    # Add metadata
    df["league_code"] = league_code
    df["league_name"] = LEAGUES[league_code]["name"]
    df["country"] = LEAGUES[league_code]["country"]
    df["tier"] = LEAGUES[league_code]["tier"]
    df["season"] = season_to_readable(season)

    # Parse date - try multiple formats
    if "Date" in df.columns:
        for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"]:
            try:
                df["Date"] = pd.to_datetime(df["Date"], format=fmt, dayfirst=True)
                break
            except (ValueError, TypeError):
                continue

        # Fallback
        if not pd.api.types.is_datetime64_any_dtype(df["Date"]):
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")

    # Convert numeric columns
    numeric_cols = ["FTHG", "FTAG", "HTHG", "HTAG", "HS", "AS", "HST", "AST",
                    "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR"]
    numeric_cols += [c for c in ODDS_COLUMNS if c in df.columns]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Compute derived fields
    df["total_goals"] = df["FTHG"] + df["FTAG"]
    df["over_25"] = (df["total_goals"] > 2.5).astype(int)
    df["over_15"] = (df["total_goals"] > 1.5).astype(int)
    df["btts"] = ((df["FTHG"] > 0) & (df["FTAG"] > 0)).astype(int)

    # Implied probabilities from Pinnacle (sharp benchmark)
    if all(c in df.columns for c in ["PSH", "PSD", "PSA"]):
        margin = (1/df["PSH"] + 1/df["PSD"] + 1/df["PSA"])
        df["pinnacle_home_prob"] = (1/df["PSH"]) / margin
        df["pinnacle_draw_prob"] = (1/df["PSD"]) / margin
        df["pinnacle_away_prob"] = (1/df["PSA"]) / margin

    # Implied probabilities from market average
    if all(c in df.columns for c in ["AvgH", "AvgD", "AvgA"]):
        margin = (1/df["AvgH"] + 1/df["AvgD"] + 1/df["AvgA"])
        df["avg_home_prob"] = (1/df["AvgH"]) / margin
        df["avg_draw_prob"] = (1/df["AvgD"]) / margin
        df["avg_away_prob"] = (1/df["AvgA"]) / margin

    return df


def download_all():
    """Download and process all historical data"""
    console.print("\n[bold green]OddsIntel — Historical Data Import[/bold green]")
    console.print("Downloading match data from football-data.co.uk\n")

    all_data = []
    failed = []
    skipped = 0

    total_tasks = len(LEAGUES) * len(SEASONS)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        console=console,
    ) as progress:
        task = progress.add_task("Downloading...", total=total_tasks)

        for league_code, league_info in LEAGUES.items():
            for season in SEASONS:
                season_readable = season_to_readable(season)
                progress.update(task, description=f"{league_info['name']} {season_readable}")

                df = download_csv(league_code, season)

                if df is not None and len(df) > 0:
                    cleaned = clean_dataframe(df, league_code, season)
                    if len(cleaned) > 0:
                        all_data.append(cleaned)
                    else:
                        skipped += 1
                else:
                    failed.append(f"{league_code} {season_readable}")
                    skipped += 1

                progress.advance(task)

                # Be polite to the server
                time.sleep(0.3)

    if not all_data:
        console.print("[red]No data downloaded. Check your internet connection.[/red]")
        return

    # Combine all data
    console.print("\n[yellow]Combining all data...[/yellow]")
    combined = pd.concat(all_data, ignore_index=True)

    # Sort by date
    combined = combined.sort_values("Date", ascending=True).reset_index(drop=True)

    # Save combined dataset
    output_path = PROCESSED_DIR / "all_matches.csv"
    combined.to_csv(output_path, index=False)

    # Print summary
    console.print(f"\n[bold green]Download complete![/bold green]\n")

    summary = Table(title="OddsIntel Data Summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", style="green")

    summary.add_row("Total matches", f"{len(combined):,}")
    summary.add_row("Date range", f"{combined['Date'].min().strftime('%Y-%m-%d')} to {combined['Date'].max().strftime('%Y-%m-%d')}")
    summary.add_row("Leagues", str(combined["league_name"].nunique()))
    summary.add_row("Seasons", str(combined["season"].nunique()))
    summary.add_row("Teams", str(pd.concat([combined["HomeTeam"], combined["AwayTeam"]]).nunique()))
    summary.add_row("Matches with Pinnacle odds", f"{combined['PSH'].notna().sum():,}")
    summary.add_row("Matches with Bet365 odds", f"{combined['B365H'].notna().sum():,}")
    summary.add_row("Matches with avg odds", f"{combined['AvgH'].notna().sum():,}")
    summary.add_row("Failed downloads", str(len(failed)))
    summary.add_row("Output file", str(output_path))
    summary.add_row("File size", f"{output_path.stat().st_size / 1024 / 1024:.1f} MB")

    console.print(summary)

    # League breakdown
    league_summary = Table(title="\nMatches per League")
    league_summary.add_column("League", style="cyan")
    league_summary.add_column("Country", style="white")
    league_summary.add_column("Matches", style="green", justify="right")
    league_summary.add_column("Seasons", style="yellow", justify="right")
    league_summary.add_column("Avg Goals/Game", style="magenta", justify="right")
    league_summary.add_column("Over 2.5 %", style="blue", justify="right")

    for league_name in sorted(combined["league_name"].unique()):
        league_data = combined[combined["league_name"] == league_name]
        league_summary.add_row(
            league_name,
            league_data["country"].iloc[0],
            f"{len(league_data):,}",
            str(league_data["season"].nunique()),
            f"{league_data['total_goals'].mean():.2f}",
            f"{league_data['over_25'].mean() * 100:.1f}%",
        )

    console.print(league_summary)

    return combined


if __name__ == "__main__":
    download_all()
