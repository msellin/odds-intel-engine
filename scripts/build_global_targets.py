"""
Build targets_global.csv from global_matches_with_elo.parquet.

Extends prediction coverage beyond the 18 football-data.co.uk leagues
to all countries where Kambi/Sofascore now provides odds.

Usage:
    python scripts/build_global_targets.py
"""

import pandas as pd
import numpy as np
from pathlib import Path

ENGINE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"


# Map: parquet competition name → (league_code, league_display, tier)
# Countries already covered by targets_v9.csv are still included here
# so we capture additional teams / seasons the football-data set misses.
COMPETITION_MAP = {
    "norway":   ("NOR1", "Norwegian Eliteserien",        1),
    "sweden":   ("SE1",  "Swedish Allsvenskan",          1),
    "poland":   ("PL1",  "Polish Ekstraklasa",           1),
    "romania":  ("RO1",  "Romanian Liga I",              1),
    "serbia":   ("SER1", "Serbian Super Liga",           1),
    "ukraine":  ("UA1",  "Ukrainian Premier League",     1),
    "turkey":   ("T1",   "Turkish Super Lig",            1),
    "greece":   ("G1",   "Greek Super League",           1),
    "croatia":  ("CR1",  "Croatian 1. HNL",              1),
    "denmark":  ("DK1",  "Danish Superligaen",           1),
    "iceland":  ("ICE1", "Icelandic Úrvalsdeild",        1),
    "hungary":  ("HUN1", "Hungarian NB I",               1),
    "bulgaria": ("BUL1", "Bulgarian First League",       1),
    "cyprus":   ("CY1",  "Cypriot 1st Division",         1),
    "georgia":  ("GEO1", "Georgian Erovnuli Liga",       1),
    "latvia":   ("LAT1", "Latvian Virsliga",             1),
    "portugal": ("P1",   "Primeira Liga",                1),
}

# Minimum season year — drop ancient data that doesn't represent modern teams
MIN_YEAR = 2015


def derive_season(date: pd.Timestamp) -> str:
    """Convert a date to a season string (e.g., 2023 → '2023-24')."""
    year = date.year
    month = date.month
    # Seasons in these leagues mostly run Apr–Nov (summer) or Aug–May (winter)
    # Use a simple calendar-year label for summer leagues and Aug-start for winter
    if month >= 7:
        return f"{year}-{str(year + 1)[-2:]}"
    else:
        return f"{year - 1}-{str(year)[-2:]}"


def main():
    print("Loading global_matches_with_elo.parquet …")
    df = pd.read_parquet(PROCESSED_DIR / "global_matches_with_elo.parquet")
    print(f"  {len(df):,} total rows loaded")

    # Filter to target competitions only
    target_comps = list(COMPETITION_MAP.keys())
    df = df[df["competition"].isin(target_comps)].copy()
    print(f"  {len(df):,} rows in target competitions")

    # Ensure date is datetime and filter from MIN_YEAR
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.year >= MIN_YEAR].copy()
    print(f"  {len(df):,} rows from {MIN_YEAR} onwards")

    # Drop rows with missing goals (postponed / unplayed)
    df = df.dropna(subset=["gh", "ga"]).copy()
    df["gh"] = df["gh"].astype(int)
    df["ga"] = df["ga"].astype(int)
    print(f"  {len(df):,} rows with valid goals")

    # Rename columns to targets_v9 format
    df = df.rename(columns={
        "home":  "home_team",
        "away":  "away_team",
        "date":  "Date",
        "gh":    "FTHG",
        "ga":    "FTAG",
    })

    # Derived columns
    df["result"] = df.apply(
        lambda r: "H" if r["FTHG"] > r["FTAG"] else ("A" if r["FTAG"] > r["FTHG"] else "D"),
        axis=1
    )
    df["total_goals"] = df["FTHG"] + df["FTAG"]
    df["over_25"] = (df["total_goals"] > 2.5).astype(bool)
    df["btts"] = ((df["FTHG"] > 0) & (df["FTAG"] > 0)).astype(bool)

    # League metadata from COMPETITION_MAP
    df["league_code"] = df["competition"].map(lambda c: COMPETITION_MAP[c][0])
    df["league"]      = df["competition"].map(lambda c: COMPETITION_MAP[c][1])
    df["tier"]        = df["competition"].map(lambda c: COMPETITION_MAP[c][2])

    # Season
    df["season"] = df["Date"].apply(derive_season)

    # Odds columns — not available for these leagues, set to NaN
    for col in ["AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5",
                "B365H", "B365D", "B365A", "PSH", "PSD", "PSA"]:
        df[col] = np.nan

    # Select and order columns to match targets_v9.csv schema
    out_cols = [
        "Date", "home_team", "away_team", "result", "FTHG", "FTAG",
        "total_goals", "over_25", "btts", "league_code", "league",
        "season", "tier", "AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5",
        "B365H", "B365D", "B365A", "PSH", "PSD", "PSA",
    ]
    df = df[out_cols].sort_values("Date").reset_index(drop=True)

    out_path = PROCESSED_DIR / "targets_global.csv"
    df.to_csv(out_path, index=False)
    print(f"\nSaved {len(df):,} rows → {out_path}")

    # Summary
    print("\nBreakdown by league:")
    summary = df.groupby(["competition" if "competition" in df.columns else "league_code", "league_code"]).agg(
        rows=("Date", "count"),
        teams=("home_team", lambda x: len(set(x) | set(df.loc[x.index, "away_team"]))),
        date_min=("Date", "min"),
        date_max=("Date", "max"),
    )
    for lc in df["league_code"].unique():
        sub = df[df["league_code"] == lc]
        teams = set(sub["home_team"]) | set(sub["away_team"])
        print(f"  {lc:8s}  {len(sub):5,} matches  {len(teams):3} teams  "
              f"{sub['Date'].min().date()} → {sub['Date'].max().date()}")


if __name__ == "__main__":
    main()
