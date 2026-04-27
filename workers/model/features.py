"""
OddsIntel — Feature Engineering
Computes all features needed for the prediction model from raw match data.
No AI needed — just SQL-like operations on pandas DataFrames.
"""

import pandas as pd
import numpy as np
from pathlib import Path

# Features used by the prediction model
FEATURE_COLS = [
    # Home form
    "home_form_win_pct", "home_form_ppg", "home_form_goals_scored",
    "home_form_goals_conceded", "home_form_goal_diff",
    "home_form_over25_pct", "home_form_btts_pct", "home_form_clean_sheet_pct",
    # Home at home
    "home_venue_win_pct", "home_venue_goals_scored",
    "home_venue_goals_conceded", "home_venue_over25_pct",
    # Away form
    "away_form_win_pct", "away_form_ppg", "away_form_goals_scored",
    "away_form_goals_conceded", "away_form_goal_diff",
    "away_form_over25_pct", "away_form_btts_pct", "away_form_clean_sheet_pct",
    # Away at away
    "away_venue_win_pct", "away_venue_goals_scored",
    "away_venue_goals_conceded", "away_venue_over25_pct",
    # H2H
    "h2h_home_win_pct", "h2h_avg_goals", "h2h_over25_pct",
    "h2h_btts_pct", "h2h_matches",
    # Position
    "home_position_norm", "away_position_norm", "position_diff",
    "home_pts_to_relegation", "away_pts_to_relegation",
    "home_in_relegation", "away_in_relegation",
    # Rest
    "home_rest_days", "away_rest_days", "rest_advantage",
    # League
    "league_tier",
]


def team_form(df: pd.DataFrame, team: str, before_date: pd.Timestamp,
              n: int = 10, venue: str = "all") -> dict:
    """
    Calculate team form over last N matches before a given date.

    Args:
        df: All matches dataframe
        team: Team name
        before_date: Only consider matches before this date
        n: Number of matches to look back
        venue: 'home', 'away', or 'all'
    """
    # Get matches involving this team before the date
    mask = (
        ((df["HomeTeam"] == team) | (df["AwayTeam"] == team)) &
        (df["Date"] < before_date)
    )

    if venue == "home":
        mask = (df["HomeTeam"] == team) & (df["Date"] < before_date)
    elif venue == "away":
        mask = (df["AwayTeam"] == team) & (df["Date"] < before_date)

    matches = df[mask].sort_values("Date", ascending=False).head(n)

    if len(matches) == 0:
        return _empty_form()

    # Calculate stats
    wins = 0
    draws = 0
    losses = 0
    goals_scored = 0
    goals_conceded = 0
    clean_sheets = 0
    over_25_count = 0
    btts_count = 0

    for _, match in matches.iterrows():
        is_home = match["HomeTeam"] == team
        gs = match["FTHG"] if is_home else match["FTAG"]
        gc = match["FTAG"] if is_home else match["FTHG"]

        goals_scored += gs
        goals_conceded += gc

        if gs > gc:
            wins += 1
        elif gs == gc:
            draws += 1
        else:
            losses += 1

        if gc == 0:
            clean_sheets += 1

        if match["total_goals"] > 2.5:
            over_25_count += 1

        if gs > 0 and gc > 0:
            btts_count += 1

    n_matches = len(matches)

    return {
        "matches_played": n_matches,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "win_pct": wins / n_matches,
        "draw_pct": draws / n_matches,
        "loss_pct": losses / n_matches,
        "goals_scored_avg": goals_scored / n_matches,
        "goals_conceded_avg": goals_conceded / n_matches,
        "goal_diff_avg": (goals_scored - goals_conceded) / n_matches,
        "clean_sheet_pct": clean_sheets / n_matches,
        "over_25_pct": over_25_count / n_matches,
        "btts_pct": btts_count / n_matches,
        "points_per_game": (wins * 3 + draws) / n_matches,
    }


def _empty_form() -> dict:
    """Return empty form dict when no data available"""
    return {
        "matches_played": 0,
        "wins": 0, "draws": 0, "losses": 0,
        "win_pct": 0.0, "draw_pct": 0.0, "loss_pct": 0.0,
        "goals_scored_avg": 0.0, "goals_conceded_avg": 0.0,
        "goal_diff_avg": 0.0, "clean_sheet_pct": 0.0,
        "over_25_pct": 0.0, "btts_pct": 0.0,
        "points_per_game": 0.0,
    }


def head_to_head(df: pd.DataFrame, team_a: str, team_b: str,
                 before_date: pd.Timestamp, n: int = 10) -> dict:
    """Calculate head-to-head record between two teams"""
    mask = (
        (((df["HomeTeam"] == team_a) & (df["AwayTeam"] == team_b)) |
         ((df["HomeTeam"] == team_b) & (df["AwayTeam"] == team_a))) &
        (df["Date"] < before_date)
    )
    matches = df[mask].sort_values("Date", ascending=False).head(n)

    if len(matches) == 0:
        return {
            "matches_played": 0,
            "team_a_wins": 0, "draws": 0, "team_b_wins": 0,
            "avg_total_goals": 0.0, "over_25_pct": 0.0, "btts_pct": 0.0,
        }

    a_wins = 0
    b_wins = 0
    draws = 0

    for _, match in matches.iterrows():
        if match["HomeTeam"] == team_a:
            if match["FTR"] == "H":
                a_wins += 1
            elif match["FTR"] == "A":
                b_wins += 1
            else:
                draws += 1
        else:  # team_b is home
            if match["FTR"] == "H":
                b_wins += 1
            elif match["FTR"] == "A":
                a_wins += 1
            else:
                draws += 1

    n_matches = len(matches)

    return {
        "matches_played": n_matches,
        "team_a_wins": a_wins,
        "draws": draws,
        "team_b_wins": b_wins,
        "team_a_win_pct": a_wins / n_matches,
        "avg_total_goals": matches["total_goals"].mean(),
        "over_25_pct": matches["over_25"].mean(),
        "btts_pct": matches["btts"].mean(),
    }


def league_position(df: pd.DataFrame, team: str, league_code: str,
                    season: str, before_date: pd.Timestamp) -> dict:
    """
    Calculate league table position at a given point in the season.
    Returns position, points, and context (relegation/title/europe).
    """
    season_matches = df[
        (df["league_code"] == league_code) &
        (df["season"] == season) &
        (df["Date"] < before_date)
    ]

    if len(season_matches) == 0:
        return {"position": 0, "points": 0, "played": 0,
                "goal_diff": 0, "pts_to_relegation": 0, "pts_to_top": 0}

    # Build table
    teams_stats = {}
    for _, match in season_matches.iterrows():
        home = match["HomeTeam"]
        away = match["AwayTeam"]

        for t in [home, away]:
            if t not in teams_stats:
                teams_stats[t] = {"points": 0, "played": 0, "gd": 0}

        teams_stats[home]["played"] += 1
        teams_stats[away]["played"] += 1
        teams_stats[home]["gd"] += match["FTHG"] - match["FTAG"]
        teams_stats[away]["gd"] += match["FTAG"] - match["FTHG"]

        if match["FTR"] == "H":
            teams_stats[home]["points"] += 3
        elif match["FTR"] == "A":
            teams_stats[away]["points"] += 3
        else:
            teams_stats[home]["points"] += 1
            teams_stats[away]["points"] += 1

    # Sort by points, then goal difference
    table = sorted(teams_stats.items(),
                   key=lambda x: (x[1]["points"], x[1]["gd"]),
                   reverse=True)

    n_teams = len(table)
    team_pos = 0
    team_pts = 0
    team_gd = 0
    team_played = 0

    for i, (t, stats) in enumerate(table):
        if t == team:
            team_pos = i + 1
            team_pts = stats["points"]
            team_gd = stats["gd"]
            team_played = stats["played"]
            break

    # Relegation zone (bottom 3 for most leagues)
    relegation_line = n_teams - 2 if n_teams > 5 else n_teams
    relegation_pts = table[relegation_line - 1][1]["points"] if len(table) >= relegation_line else 0

    top_pts = table[0][1]["points"] if table else 0

    return {
        "position": team_pos,
        "points": team_pts,
        "played": team_played,
        "goal_diff": team_gd,
        "total_teams": n_teams,
        "pts_to_relegation": team_pts - relegation_pts,
        "pts_to_top": top_pts - team_pts,
        "in_relegation_zone": team_pos > (n_teams - 3),
        "normalized_position": team_pos / n_teams if n_teams > 0 else 0.5,
    }


def days_since_last_match(df: pd.DataFrame, team: str,
                          match_date: pd.Timestamp) -> int:
    """Calculate rest days before a match"""
    prev_matches = df[
        ((df["HomeTeam"] == team) | (df["AwayTeam"] == team)) &
        (df["Date"] < match_date)
    ].sort_values("Date", ascending=False)

    if len(prev_matches) == 0:
        return 7  # default assumption

    last_match_date = prev_matches.iloc[0]["Date"]
    return (match_date - last_match_date).days


def build_match_features(df: pd.DataFrame, match_row: pd.Series) -> dict:
    """
    Build the complete feature vector for a single match.
    This is the main function called by the prediction model.
    """
    home = match_row["HomeTeam"]
    away = match_row["AwayTeam"]
    date = match_row["Date"]
    league = match_row["league_code"]
    season = match_row["season"]

    # Home team form
    home_all = team_form(df, home, date, n=10, venue="all")
    home_home = team_form(df, home, date, n=10, venue="home")

    # Away team form
    away_all = team_form(df, away, date, n=10, venue="all")
    away_away = team_form(df, away, date, n=10, venue="away")

    # Head to head
    h2h = head_to_head(df, home, away, date, n=10)

    # League position
    home_pos = league_position(df, home, league, season, date)
    away_pos = league_position(df, away, league, season, date)

    # Rest days
    home_rest = days_since_last_match(df, home, date)
    away_rest = days_since_last_match(df, away, date)

    features = {
        # Home team overall form
        "home_form_win_pct": home_all["win_pct"],
        "home_form_ppg": home_all["points_per_game"],
        "home_form_goals_scored": home_all["goals_scored_avg"],
        "home_form_goals_conceded": home_all["goals_conceded_avg"],
        "home_form_goal_diff": home_all["goal_diff_avg"],
        "home_form_over25_pct": home_all["over_25_pct"],
        "home_form_btts_pct": home_all["btts_pct"],
        "home_form_clean_sheet_pct": home_all["clean_sheet_pct"],

        # Home team at home specifically
        "home_venue_win_pct": home_home["win_pct"],
        "home_venue_goals_scored": home_home["goals_scored_avg"],
        "home_venue_goals_conceded": home_home["goals_conceded_avg"],
        "home_venue_over25_pct": home_home["over_25_pct"],

        # Away team overall form
        "away_form_win_pct": away_all["win_pct"],
        "away_form_ppg": away_all["points_per_game"],
        "away_form_goals_scored": away_all["goals_scored_avg"],
        "away_form_goals_conceded": away_all["goals_conceded_avg"],
        "away_form_goal_diff": away_all["goal_diff_avg"],
        "away_form_over25_pct": away_all["over_25_pct"],
        "away_form_btts_pct": away_all["btts_pct"],
        "away_form_clean_sheet_pct": away_all["clean_sheet_pct"],

        # Away team away specifically
        "away_venue_win_pct": away_away["win_pct"],
        "away_venue_goals_scored": away_away["goals_scored_avg"],
        "away_venue_goals_conceded": away_away["goals_conceded_avg"],
        "away_venue_over25_pct": away_away["over_25_pct"],

        # Head to head
        "h2h_home_win_pct": h2h["team_a_win_pct"] if h2h["matches_played"] > 0 else 0.33,
        "h2h_avg_goals": h2h["avg_total_goals"],
        "h2h_over25_pct": h2h["over_25_pct"],
        "h2h_btts_pct": h2h["btts_pct"],
        "h2h_matches": h2h["matches_played"],

        # League position
        "home_position_norm": home_pos["normalized_position"],
        "away_position_norm": away_pos["normalized_position"],
        "position_diff": home_pos["normalized_position"] - away_pos["normalized_position"],
        "home_pts_to_relegation": home_pos["pts_to_relegation"],
        "away_pts_to_relegation": away_pos["pts_to_relegation"],
        "home_in_relegation": int(home_pos["in_relegation_zone"]),
        "away_in_relegation": int(away_pos["in_relegation_zone"]),

        # Rest
        "home_rest_days": min(home_rest, 14),  # Cap at 14
        "away_rest_days": min(away_rest, 14),
        "rest_advantage": home_rest - away_rest,

        # League tier (proxy for market efficiency)
        "league_tier": match_row["tier"],
    }

    return features


def build_feature_matrix(df: pd.DataFrame, min_matches: int = 50) -> pd.DataFrame:
    """
    Build feature matrix for ALL matches in the dataset.
    Used for model training and backtesting.

    Args:
        df: All matches dataframe
        min_matches: Skip first N matches per league/season (not enough history)
    """
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
    from rich.console import Console

    console = Console()

    # Only process matches where we have enough prior data
    # Group by league and season, skip early matches
    feature_rows = []
    targets = []

    # Sort by date
    df = df.sort_values("Date").reset_index(drop=True)

    # Process each match
    total = len(df)
    console.print(f"\n[yellow]Building features for {total:,} matches...[/yellow]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console,
    ) as progress:
        task = progress.add_task("Computing features...", total=total)

        for idx, row in df.iterrows():
            progress.advance(task)

            # Skip if date is missing
            if pd.isna(row["Date"]):
                continue

            # Check if enough prior matches exist for this team in this league/season
            prior_home = df[
                (df["HomeTeam"] == row["HomeTeam"]) &
                (df["Date"] < row["Date"])
            ]
            prior_away = df[
                (df["AwayTeam"] == row["AwayTeam"]) &
                (df["Date"] < row["Date"])
            ]

            if len(prior_home) < 5 or len(prior_away) < 5:
                continue

            try:
                features = build_match_features(df, row)
                feature_rows.append(features)

                # Target variables
                targets.append({
                    "result": row["FTR"],  # H, D, A
                    "home_goals": row["FTHG"],
                    "away_goals": row["FTAG"],
                    "total_goals": row["total_goals"],
                    "over_25": row["over_25"],
                    "btts": row["btts"],
                    # Odds (for value detection in backtesting)
                    "pinnacle_home_odds": row.get("PSH"),
                    "pinnacle_draw_odds": row.get("PSD"),
                    "pinnacle_away_odds": row.get("PSA"),
                    "avg_home_odds": row.get("AvgH"),
                    "avg_draw_odds": row.get("AvgD"),
                    "avg_away_odds": row.get("AvgA"),
                    "avg_over25_odds": row.get("Avg>2.5"),
                    "avg_under25_odds": row.get("Avg<2.5"),
                    # Metadata
                    "date": row["Date"],
                    "home_team": row["HomeTeam"],
                    "away_team": row["AwayTeam"],
                    "league": row["league_name"],
                    "league_code": row["league_code"],
                    "season": row["season"],
                    "tier": row["tier"],
                })
            except Exception as e:
                continue

    features_df = pd.DataFrame(feature_rows)
    targets_df = pd.DataFrame(targets)

    console.print(f"\n[green]Feature matrix: {len(features_df):,} matches with {len(features_df.columns)} features[/green]")

    return features_df, targets_df
