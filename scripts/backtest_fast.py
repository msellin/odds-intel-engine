"""
OddsIntel — Fast Backtest
Optimized version that uses vectorized rolling calculations instead of
per-match DataFrame lookups. 100x faster than the original.

Strategy: Pre-compute rolling stats per team, then join to matches.
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from rich.console import Console
from rich.table import Table
from sklearn.calibration import CalibratedClassifierCV
from xgboost import XGBClassifier

console = Console()

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


def compute_team_rolling_stats(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """
    Pre-compute rolling stats for every team at every point in time.
    Uses vectorized pandas operations — much faster than per-match lookups.
    """
    console.print(f"[yellow]Computing rolling stats (last {n} matches)...[/yellow]")

    all_rows = []

    # Create a "long" format: one row per team per match
    home = df[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
               "total_goals", "over_25", "btts", "league_code", "season", "tier"]].copy()
    home["team"] = home["HomeTeam"]
    home["venue"] = "home"
    home["goals_scored"] = home["FTHG"]
    home["goals_conceded"] = home["FTAG"]
    home["win"] = (home["FTR"] == "H").astype(int)
    home["draw"] = (home["FTR"] == "D").astype(int)
    home["loss"] = (home["FTR"] == "A").astype(int)
    home["clean_sheet"] = (home["FTAG"] == 0).astype(int)
    home["points"] = home["win"] * 3 + home["draw"]

    away = df[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
               "total_goals", "over_25", "btts", "league_code", "season", "tier"]].copy()
    away["team"] = away["AwayTeam"]
    away["venue"] = "away"
    away["goals_scored"] = away["FTAG"]
    away["goals_conceded"] = away["FTHG"]
    away["win"] = (away["FTR"] == "A").astype(int)
    away["draw"] = (away["FTR"] == "D").astype(int)
    away["loss"] = (away["FTR"] == "H").astype(int)
    away["clean_sheet"] = (away["FTHG"] == 0).astype(int)
    away["points"] = away["win"] * 3 + away["draw"]

    long = pd.concat([home, away], ignore_index=True)
    long = long.sort_values("Date").reset_index(drop=True)

    # Rolling stats per team (all venues)
    console.print("  Computing overall form...")
    for team_name, group in long.groupby("team"):
        g = group.sort_values("Date")

        # Shift by 1 to avoid leaking current match
        stats = pd.DataFrame(index=g.index)
        stats["team"] = team_name
        stats["Date"] = g["Date"]
        stats["venue"] = g["venue"]

        stats["form_win_pct"] = g["win"].shift(1).rolling(n, min_periods=3).mean()
        stats["form_ppg"] = g["points"].shift(1).rolling(n, min_periods=3).mean()
        stats["form_goals_scored"] = g["goals_scored"].shift(1).rolling(n, min_periods=3).mean()
        stats["form_goals_conceded"] = g["goals_conceded"].shift(1).rolling(n, min_periods=3).mean()
        stats["form_goal_diff"] = stats["form_goals_scored"] - stats["form_goals_conceded"]
        stats["form_over25_pct"] = g["over_25"].shift(1).rolling(n, min_periods=3).mean()
        stats["form_btts_pct"] = g["btts"].shift(1).rolling(n, min_periods=3).mean()
        stats["form_clean_sheet_pct"] = g["clean_sheet"].shift(1).rolling(n, min_periods=3).mean()

        all_rows.append(stats)

    all_stats = pd.concat(all_rows, ignore_index=True)

    # Venue-specific stats
    console.print("  Computing venue-specific form...")
    venue_rows = []
    for (team_name, venue), group in long.groupby(["team", "venue"]):
        g = group.sort_values("Date")

        stats = pd.DataFrame(index=g.index)
        stats["team"] = team_name
        stats["Date"] = g["Date"]
        stats["venue"] = venue

        stats["venue_win_pct"] = g["win"].shift(1).rolling(n, min_periods=3).mean()
        stats["venue_goals_scored"] = g["goals_scored"].shift(1).rolling(n, min_periods=3).mean()
        stats["venue_goals_conceded"] = g["goals_conceded"].shift(1).rolling(n, min_periods=3).mean()
        stats["venue_over25_pct"] = g["over_25"].shift(1).rolling(n, min_periods=3).mean()

        venue_rows.append(stats)

    venue_stats = pd.concat(venue_rows, ignore_index=True)

    return all_stats, venue_stats, long


def build_features_fast(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build feature matrix using vectorized operations.
    Returns (features_df, targets_df) aligned by index.
    """
    console.print("\n[bold cyan]Building Feature Matrix (Fast Mode)[/bold cyan]")

    # Compute rolling stats
    all_stats, venue_stats, long = compute_team_rolling_stats(df, n=10)

    # Now merge stats back to matches
    console.print("  Merging features to matches...")

    # Home team overall form
    home_overall = all_stats[all_stats["venue"] == "home"][
        ["team", "Date", "form_win_pct", "form_ppg", "form_goals_scored",
         "form_goals_conceded", "form_goal_diff", "form_over25_pct",
         "form_btts_pct", "form_clean_sheet_pct"]
    ].rename(columns=lambda c: f"home_{c}" if c not in ["team", "Date"] else c)

    # Away team overall form
    away_overall = all_stats[all_stats["venue"] == "away"][
        ["team", "Date", "form_win_pct", "form_ppg", "form_goals_scored",
         "form_goals_conceded", "form_goal_diff", "form_over25_pct",
         "form_btts_pct", "form_clean_sheet_pct"]
    ].rename(columns=lambda c: f"away_{c}" if c not in ["team", "Date"] else c)

    # Home team at home
    home_venue = venue_stats[venue_stats["venue"] == "home"][
        ["team", "Date", "venue_win_pct", "venue_goals_scored",
         "venue_goals_conceded", "venue_over25_pct"]
    ].rename(columns=lambda c: f"home_{c}" if c not in ["team", "Date"] else c)

    # Away team at away
    away_venue = venue_stats[venue_stats["venue"] == "away"][
        ["team", "Date", "venue_win_pct", "venue_goals_scored",
         "venue_goals_conceded", "venue_over25_pct"]
    ].rename(columns=lambda c: f"away_{c}" if c not in ["team", "Date"] else c)

    # Start building the feature matrix
    features = df[["Date", "HomeTeam", "AwayTeam", "league_code", "season", "tier"]].copy()

    # Merge home overall form
    features = features.merge(
        home_overall, left_on=["HomeTeam", "Date"], right_on=["team", "Date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    # Merge away overall form
    features = features.merge(
        away_overall, left_on=["AwayTeam", "Date"], right_on=["team", "Date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    # Merge home venue form
    features = features.merge(
        home_venue, left_on=["HomeTeam", "Date"], right_on=["team", "Date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    # Merge away venue form
    features = features.merge(
        away_venue, left_on=["AwayTeam", "Date"], right_on=["team", "Date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    # Rest days (vectorized)
    console.print("  Computing rest days...")
    rest_data = []
    for team_name, group in long.groupby("team"):
        g = group.sort_values("Date")
        g["rest_days"] = g["Date"].diff().dt.days.clip(upper=14)
        rest_data.append(g[["team", "Date", "venue", "rest_days"]])

    rest_df = pd.concat(rest_data, ignore_index=True)
    home_rest = rest_df[rest_df["venue"] == "home"][["team", "Date", "rest_days"]].rename(
        columns={"rest_days": "home_rest_days"})
    away_rest = rest_df[rest_df["venue"] == "away"][["team", "Date", "rest_days"]].rename(
        columns={"rest_days": "away_rest_days"})

    features = features.merge(
        home_rest, left_on=["HomeTeam", "Date"], right_on=["team", "Date"], how="left"
    ).drop(columns=["team"], errors="ignore")
    features = features.merge(
        away_rest, left_on=["AwayTeam", "Date"], right_on=["team", "Date"], how="left"
    ).drop(columns=["team"], errors="ignore")

    features["rest_advantage"] = features["home_rest_days"].fillna(7) - features["away_rest_days"].fillna(7)
    features["home_rest_days"] = features["home_rest_days"].fillna(7)
    features["away_rest_days"] = features["away_rest_days"].fillna(7)

    # League tier
    features["league_tier"] = features["tier"]

    # Simplified position proxy: use points-per-game differential
    features["position_diff"] = (
        features["home_form_ppg"].fillna(1.3) - features["away_form_ppg"].fillna(1.3)
    )

    # Targets
    targets = df[["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG",
                   "total_goals", "over_25", "btts", "league_code",
                   "league_name", "season", "tier"]].copy()
    targets.rename(columns={"HomeTeam": "home_team", "AwayTeam": "away_team",
                            "FTR": "result", "league_name": "league"}, inplace=True)

    # Add odds
    for col in ["AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5",
                "PSH", "PSD", "PSA", "B365H", "B365D", "B365A"]:
        if col in df.columns:
            targets[col] = df[col]

    # Feature columns for the model
    feature_cols = [
        "home_form_win_pct", "home_form_ppg", "home_form_goals_scored",
        "home_form_goals_conceded", "home_form_goal_diff",
        "home_form_over25_pct", "home_form_btts_pct", "home_form_clean_sheet_pct",
        "home_venue_win_pct", "home_venue_goals_scored",
        "home_venue_goals_conceded", "home_venue_over25_pct",
        "away_form_win_pct", "away_form_ppg", "away_form_goals_scored",
        "away_form_goals_conceded", "away_form_goal_diff",
        "away_form_over25_pct", "away_form_btts_pct", "away_form_clean_sheet_pct",
        "away_venue_win_pct", "away_venue_goals_scored",
        "away_venue_goals_conceded", "away_venue_over25_pct",
        "home_rest_days", "away_rest_days", "rest_advantage",
        "position_diff", "league_tier",
    ]

    # Drop rows where we don't have enough data
    valid = features[feature_cols].notna().all(axis=1)
    features = features[valid].reset_index(drop=True)
    targets = targets[valid].reset_index(drop=True)

    console.print(f"[green]Feature matrix: {len(features):,} matches, {len(feature_cols)} features[/green]")

    return features[feature_cols], targets, feature_cols


def implied_probability(odds: float) -> float:
    if pd.isna(odds) or odds <= 1.0:
        return 0.0
    return 1.0 / odds


def run_backtest(features_df, targets_df, feature_cols, test_season, min_edge=0.03, stake=10.0):
    """Run backtest on a single season"""
    console.print(f"\n[bold green]═══ Backtest: {test_season} | Min edge: {min_edge:.0%} ═══[/bold green]")

    train_mask = targets_df["season"] != test_season
    test_mask = targets_df["season"] == test_season

    X_train = features_df[train_mask]
    X_test = features_df[test_mask]
    targets_train = targets_df[train_mask]
    targets_test = targets_df[test_mask].reset_index(drop=True)
    X_test = X_test.reset_index(drop=True)

    if len(X_test) == 0:
        console.print(f"[red]No data for {test_season}[/red]")
        return None

    console.print(f"Train: {len(X_train):,} | Test: {len(X_test):,}")

    # Train 1X2 model
    y_result = targets_train["result"].map({"H": 0, "D": 1, "A": 2})
    result_model = XGBClassifier(
        n_estimators=150, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="multi:softprob", num_class=3,
        random_state=42, verbosity=0,
    )
    result_model.fit(X_train, y_result)
    result_proba = result_model.predict_proba(X_test)

    # Train Over 2.5 model
    y_over = targets_train["over_25"]
    over_model = XGBClassifier(
        n_estimators=150, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="binary:logistic", random_state=42, verbosity=0,
    )
    over_model.fit(X_train, y_over)
    over_proba = over_model.predict_proba(X_test)[:, 1]

    # Train BTTS model
    y_btts = targets_train["btts"]
    btts_model = XGBClassifier(
        n_estimators=150, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="binary:logistic", random_state=42, verbosity=0,
    )
    btts_model.fit(X_train, y_btts)

    console.print("[green]Models trained.[/green]")

    # Simulate betting
    bets = []
    bankroll = 1000.0

    for i, row in targets_test.iterrows():
        match_info = f"{row['home_team']} vs {row['away_team']}"

        bet_candidates = []

        # 1X2: Home
        odds = row.get("AvgH") or row.get("B365H")
        if pd.notna(odds) and odds > 1.0:
            mp = result_proba[i][0]
            ip = implied_probability(odds)
            edge = mp - ip
            if edge >= min_edge:
                bet_candidates.append(("1X2", "Home", odds, mp, ip, edge, row["result"] == "H"))

        # 1X2: Draw
        odds = row.get("AvgD") or row.get("B365D")
        if pd.notna(odds) and odds > 1.0:
            mp = result_proba[i][1]
            ip = implied_probability(odds)
            edge = mp - ip
            if edge >= min_edge:
                bet_candidates.append(("1X2", "Draw", odds, mp, ip, edge, row["result"] == "D"))

        # 1X2: Away
        odds = row.get("AvgA") or row.get("B365A")
        if pd.notna(odds) and odds > 1.0:
            mp = result_proba[i][2]
            ip = implied_probability(odds)
            edge = mp - ip
            if edge >= min_edge:
                bet_candidates.append(("1X2", "Away", odds, mp, ip, edge, row["result"] == "A"))

        # Over 2.5
        odds = row.get("Avg>2.5")
        if pd.notna(odds) and odds > 1.0:
            mp = over_proba[i]
            ip = implied_probability(odds)
            edge = mp - ip
            if edge >= min_edge:
                bet_candidates.append(("O/U", "Over 2.5", odds, mp, ip, edge, row["over_25"] == 1))

        # Under 2.5
        odds = row.get("Avg<2.5")
        if pd.notna(odds) and odds > 1.0:
            mp = 1 - over_proba[i]
            ip = implied_probability(odds)
            edge = mp - ip
            if edge >= min_edge:
                bet_candidates.append(("O/U", "Under 2.5", odds, mp, ip, edge, row["over_25"] == 0))

        for market, selection, odds, mp, ip, edge, won in bet_candidates:
            pnl = (odds - 1) * stake if won else -stake
            bankroll += pnl
            bets.append({
                "match": match_info, "date": row["Date"],
                "league": row["league"], "tier": row["tier"],
                "market": market, "selection": selection,
                "odds": odds, "model_prob": mp,
                "implied_prob": ip, "edge": edge,
                "result": "W" if won else "L",
                "pnl": pnl, "bankroll": bankroll,
            })

    if not bets:
        console.print("[red]No value bets found.[/red]")
        return None

    bets_df = pd.DataFrame(bets)

    # Display results
    total = len(bets_df)
    wins = (bets_df["result"] == "W").sum()
    total_pnl = bets_df["pnl"].sum()
    roi = total_pnl / (total * stake) * 100

    # Longest losing streak
    streak = max_streak = 0
    for r in bets_df["result"]:
        streak = streak + 1 if r == "L" else 0
        max_streak = max(max_streak, streak)

    color = "green" if roi > 0 else "red"

    t = Table(title=f"Results: {test_season}")
    t.add_column("Metric", style="cyan")
    t.add_column("Value", style=color)
    t.add_row("Total bets", str(total))
    t.add_row("Wins / Losses", f"{wins} / {total - wins}")
    t.add_row("Hit rate", f"{wins/total:.1%}")
    t.add_row("Total P&L", f"EUR {total_pnl:+,.2f}")
    t.add_row("ROI", f"{roi:+.2f}%")
    t.add_row("Avg edge", f"{bets_df['edge'].mean():.1%}")
    t.add_row("Avg odds", f"{bets_df['odds'].mean():.2f}")
    t.add_row("Max losing streak", str(max_streak))
    t.add_row("Final bankroll", f"EUR {bankroll:,.2f}")
    console.print(t)

    # By market
    mt = Table(title="By Market")
    mt.add_column("Market")
    mt.add_column("Bets", justify="right")
    mt.add_column("Hit%", justify="right")
    mt.add_column("ROI", justify="right")
    mt.add_column("P&L", justify="right")

    for market in bets_df["market"].unique():
        m = bets_df[bets_df["market"] == market]
        m_pnl = m["pnl"].sum()
        m_roi = m_pnl / (len(m) * stake) * 100
        c = "green" if m_pnl > 0 else "red"
        mt.add_row(market, str(len(m)),
                   f"{(m['result']=='W').mean():.1%}",
                   f"[{c}]{m_roi:+.1f}%[/{c}]",
                   f"[{c}]EUR {m_pnl:+,.0f}[/{c}]")
    console.print(mt)

    # By league tier
    lt = Table(title="By League Tier")
    lt.add_column("Tier")
    lt.add_column("Bets", justify="right")
    lt.add_column("Hit%", justify="right")
    lt.add_column("ROI", justify="right")
    lt.add_column("P&L", justify="right")

    for tier in sorted(bets_df["tier"].unique()):
        tier_bets = bets_df[bets_df["tier"] == tier]
        t_pnl = tier_bets["pnl"].sum()
        t_roi = t_pnl / (len(tier_bets) * stake) * 100
        c = "green" if t_pnl > 0 else "red"
        tier_name = {1: "Top", 2: "2nd div"}.get(tier, f"T{tier}")
        lt.add_row(tier_name, str(len(tier_bets)),
                   f"{(tier_bets['result']=='W').mean():.1%}",
                   f"[{c}]{t_roi:+.1f}%[/{c}]",
                   f"[{c}]EUR {t_pnl:+,.0f}[/{c}]")
    console.print(lt)

    # Calibration
    ct = Table(title="Calibration")
    ct.add_column("Predicted")
    ct.add_column("Actual")
    ct.add_column("Diff")
    ct.add_column("N")

    for lo, hi in [(0.2, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.85)]:
        bin_bets = bets_df[(bets_df["model_prob"] >= lo) & (bets_df["model_prob"] < hi)]
        if len(bin_bets) >= 5:
            pred = bin_bets["model_prob"].mean()
            actual = (bin_bets["result"] == "W").mean()
            diff = actual - pred
            c = "green" if abs(diff) < 0.05 else "yellow" if abs(diff) < 0.10 else "red"
            ct.add_row(f"{pred:.1%}", f"{actual:.1%}", f"[{c}]{diff:+.1%}[/{c}]", str(len(bin_bets)))
    console.print(ct)

    # Save
    out = PROCESSED_DIR / f"backtest_{test_season.replace('-','')}_fast.csv"
    bets_df.to_csv(out, index=False)

    # Verdict
    console.print(f"\n[bold]VERDICT: ", end="")
    if roi > 2.0 and total >= 100:
        console.print("[green]GO[/green]")
    elif roi > 0 and total >= 50:
        console.print("[yellow]CAUTIOUS — positive but thin[/yellow]")
    elif total < 50:
        console.print("[yellow]INSUFFICIENT DATA[/yellow]")
    else:
        console.print("[red]NO-GO[/red]")

    return bets_df


def main():
    console.print("[bold]OddsIntel Fast Backtest[/bold]\n")

    data_path = PROCESSED_DIR / "all_matches.csv"
    if not data_path.exists():
        console.print("[red]Run import_historical.py first.[/red]")
        sys.exit(1)

    df = pd.read_csv(data_path, parse_dates=["Date"], low_memory=False)
    console.print(f"Loaded {len(df):,} matches")

    # Build features (fast)
    features_df, targets_df, feature_cols = build_features_fast(df)

    # Save for reuse
    features_df.to_csv(PROCESSED_DIR / "features_fast.csv", index=False)
    targets_df.to_csv(PROCESSED_DIR / "targets_fast.csv", index=False)

    # Run backtests on multiple seasons
    for season in ["2024-25", "2023-24", "2022-23"]:
        if season in targets_df["season"].values:
            run_backtest(features_df, targets_df, feature_cols,
                         test_season=season, min_edge=0.03)

    # Also run with stricter edge
    console.print("\n\n[bold]═══ STRICT MODE (5% min edge) ═══[/bold]")
    for season in ["2024-25", "2023-24"]:
        if season in targets_df["season"].values:
            run_backtest(features_df, targets_df, feature_cols,
                         test_season=season, min_edge=0.05)


if __name__ == "__main__":
    main()
