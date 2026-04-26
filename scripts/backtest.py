"""
OddsIntel — Backtesting Engine
Tests if the prediction model would have been profitable on historical data.

Simulates placing bets on matches where the model found "value"
(model probability > bookmaker implied probability + minimum edge).

This is the GO/NO-GO validation before building the actual product.
"""

import sys
import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.model.features import build_feature_matrix, FEATURE_COLS

console = Console()

MODELS_DIR = Path(__file__).parent.parent / "data" / "models"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"


def implied_probability(odds: float) -> float:
    """Convert decimal odds to implied probability"""
    if pd.isna(odds) or odds <= 1.0:
        return 0.0
    return 1.0 / odds


def run_backtest(
    features_df: pd.DataFrame,
    targets_df: pd.DataFrame,
    min_edge: float = 0.03,
    stake: float = 10.0,
    start_bankroll: float = 1000.0,
    test_season: str = "2024-25",
):
    """
    Run a backtest on a specific season using models trained on prior data.

    Args:
        min_edge: Minimum edge (model prob - implied prob) to place a bet
        stake: Flat stake per bet in EUR
        start_bankroll: Starting bankroll
        test_season: Season to backtest on
    """
    console.print(f"\n[bold green]═══ OddsIntel Backtest: {test_season} ═══[/bold green]")
    console.print(f"Min edge: {min_edge:.0%} | Flat stake: EUR {stake:.0f} | Bankroll: EUR {start_bankroll:.0f}\n")

    # Split into train and test
    train_mask = targets_df["season"] != test_season
    test_mask = targets_df["season"] == test_season

    X_train = features_df[train_mask][FEATURE_COLS]
    y_train_result = targets_df[train_mask]["result"].map({"H": 0, "D": 1, "A": 2})
    y_train_over25 = targets_df[train_mask]["over_25"]
    y_train_btts = targets_df[train_mask]["btts"]

    X_test = features_df[test_mask][FEATURE_COLS]
    targets_test = targets_df[test_mask].copy()

    # Drop NaN rows
    valid_train = X_train.notna().all(axis=1)
    X_train = X_train[valid_train]
    y_train_result = y_train_result[valid_train]
    y_train_over25 = y_train_over25[valid_train]
    y_train_btts = y_train_btts[valid_train]

    valid_test = X_test.notna().all(axis=1)
    X_test = X_test[valid_test]
    targets_test = targets_test[valid_test]

    console.print(f"Train set: {len(X_train):,} matches")
    console.print(f"Test set: {len(X_test):,} matches")

    if len(X_test) == 0:
        console.print(f"[red]No test data for season {test_season}[/red]")
        return

    # Train models on historical data only
    from sklearn.calibration import CalibratedClassifierCV
    from xgboost import XGBClassifier

    console.print("\n[yellow]Training models on pre-test data...[/yellow]")

    # 1X2 model
    result_xgb = XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="multi:softprob", num_class=3,
        random_state=42, verbosity=0,
    )
    result_model = CalibratedClassifierCV(result_xgb, cv=5, method="isotonic")
    result_model.fit(X_train, y_train_result)

    # Over 2.5 model
    over25_xgb = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="binary:logistic", random_state=42, verbosity=0,
    )
    over25_model = CalibratedClassifierCV(over25_xgb, cv=5, method="isotonic")
    over25_model.fit(X_train, y_train_over25)

    # BTTS model
    btts_xgb = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="binary:logistic", random_state=42, verbosity=0,
    )
    btts_model = CalibratedClassifierCV(btts_xgb, cv=5, method="isotonic")
    btts_model.fit(X_train, y_train_btts)

    console.print("[green]Models trained.[/green]\n")

    # Run predictions on test set
    result_proba = result_model.predict_proba(X_test)  # [home, draw, away]
    over25_proba = over25_model.predict_proba(X_test)[:, 1]
    btts_proba = btts_model.predict_proba(X_test)[:, 1]

    # Simulate betting
    bets = []
    bankroll = start_bankroll

    for i, (idx, row) in enumerate(targets_test.iterrows()):
        match_info = f"{row['home_team']} vs {row['away_team']}"

        # --- 1X2 BETS ---
        # Home win
        home_odds = row.get("avg_home_odds")
        if pd.notna(home_odds) and home_odds > 1.0:
            model_prob = result_proba[i][0]
            implied = implied_probability(home_odds)
            edge = model_prob - implied

            if edge >= min_edge:
                won = row["result"] == "H"
                pnl = (home_odds - 1) * stake if won else -stake
                bankroll += pnl
                bets.append({
                    "match": match_info, "date": row["date"],
                    "league": row["league"], "tier": row["tier"],
                    "market": "1X2", "selection": "Home",
                    "odds": home_odds, "model_prob": model_prob,
                    "implied_prob": implied, "edge": edge,
                    "result": "W" if won else "L",
                    "pnl": pnl, "bankroll": bankroll,
                })

        # Draw
        draw_odds = row.get("avg_draw_odds")
        if pd.notna(draw_odds) and draw_odds > 1.0:
            model_prob = result_proba[i][1]
            implied = implied_probability(draw_odds)
            edge = model_prob - implied

            if edge >= min_edge:
                won = row["result"] == "D"
                pnl = (draw_odds - 1) * stake if won else -stake
                bankroll += pnl
                bets.append({
                    "match": match_info, "date": row["date"],
                    "league": row["league"], "tier": row["tier"],
                    "market": "1X2", "selection": "Draw",
                    "odds": draw_odds, "model_prob": model_prob,
                    "implied_prob": implied, "edge": edge,
                    "result": "W" if won else "L",
                    "pnl": pnl, "bankroll": bankroll,
                })

        # Away win
        away_odds = row.get("avg_away_odds")
        if pd.notna(away_odds) and away_odds > 1.0:
            model_prob = result_proba[i][2]
            implied = implied_probability(away_odds)
            edge = model_prob - implied

            if edge >= min_edge:
                won = row["result"] == "A"
                pnl = (away_odds - 1) * stake if won else -stake
                bankroll += pnl
                bets.append({
                    "match": match_info, "date": row["date"],
                    "league": row["league"], "tier": row["tier"],
                    "market": "O/U 2.5", "selection": "Over 2.5",
                    "odds": away_odds, "model_prob": model_prob,
                    "implied_prob": implied, "edge": edge,
                    "result": "W" if won else "L",
                    "pnl": pnl, "bankroll": bankroll,
                })

        # --- OVER 2.5 BET ---
        over_odds = row.get("avg_over25_odds")
        if pd.notna(over_odds) and over_odds > 1.0:
            model_prob = over25_proba[i]
            implied = implied_probability(over_odds)
            edge = model_prob - implied

            if edge >= min_edge:
                won = row["over_25"] == 1
                pnl = (over_odds - 1) * stake if won else -stake
                bankroll += pnl
                bets.append({
                    "match": match_info, "date": row["date"],
                    "league": row["league"], "tier": row["tier"],
                    "market": "O/U 2.5", "selection": "Over 2.5",
                    "odds": over_odds, "model_prob": model_prob,
                    "implied_prob": implied, "edge": edge,
                    "result": "W" if won else "L",
                    "pnl": pnl, "bankroll": bankroll,
                })

        # Under 2.5
        under_odds = row.get("avg_under25_odds")
        if pd.notna(under_odds) and under_odds > 1.0:
            model_prob = 1 - over25_proba[i]
            implied = implied_probability(under_odds)
            edge = model_prob - implied

            if edge >= min_edge:
                won = row["over_25"] == 0
                pnl = (under_odds - 1) * stake if won else -stake
                bankroll += pnl
                bets.append({
                    "match": match_info, "date": row["date"],
                    "league": row["league"], "tier": row["tier"],
                    "market": "O/U 2.5", "selection": "Under 2.5",
                    "odds": under_odds, "model_prob": model_prob,
                    "implied_prob": implied, "edge": edge,
                    "result": "W" if won else "L",
                    "pnl": pnl, "bankroll": bankroll,
                })

    # --- RESULTS ---
    if not bets:
        console.print("[red]No value bets found with the current edge threshold.[/red]")
        return

    bets_df = pd.DataFrame(bets)

    # Overall stats
    total_bets = len(bets_df)
    wins = len(bets_df[bets_df["result"] == "W"])
    losses = len(bets_df[bets_df["result"] == "L"])
    hit_rate = wins / total_bets
    total_staked = total_bets * stake
    total_pnl = bets_df["pnl"].sum()
    roi = total_pnl / total_staked * 100
    final_bankroll = start_bankroll + total_pnl

    # Results table
    results = Table(title=f"Backtest Results: {test_season}")
    results.add_column("Metric", style="cyan")
    results.add_column("Value", style="green")

    results.add_row("Total bets", str(total_bets))
    results.add_row("Wins", f"{wins} ({hit_rate:.1%})")
    results.add_row("Losses", str(losses))
    results.add_row("Total staked", f"EUR {total_staked:,.0f}")
    results.add_row("Total P&L", f"EUR {total_pnl:+,.2f}")
    results.add_row("ROI", f"{roi:+.2f}%")
    results.add_row("Starting bankroll", f"EUR {start_bankroll:,.0f}")
    results.add_row("Final bankroll", f"EUR {final_bankroll:,.2f}")
    results.add_row("Avg edge on bets", f"{bets_df['edge'].mean():.1%}")
    results.add_row("Avg odds", f"{bets_df['odds'].mean():.2f}")

    # Longest losing streak
    streak = 0
    max_streak = 0
    for r in bets_df["result"]:
        if r == "L":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    results.add_row("Longest losing streak", str(max_streak))

    console.print(results)

    # Breakdown by market
    market_table = Table(title="Results by Market")
    market_table.add_column("Market", style="cyan")
    market_table.add_column("Bets", justify="right")
    market_table.add_column("Hit Rate", justify="right")
    market_table.add_column("P&L", justify="right")
    market_table.add_column("ROI", justify="right")

    for market in bets_df["market"].unique():
        m = bets_df[bets_df["market"] == market]
        m_wins = len(m[m["result"] == "W"])
        m_pnl = m["pnl"].sum()
        m_roi = m_pnl / (len(m) * stake) * 100

        color = "green" if m_pnl > 0 else "red"
        market_table.add_row(
            market,
            str(len(m)),
            f"{m_wins/len(m):.1%}",
            f"[{color}]EUR {m_pnl:+,.2f}[/{color}]",
            f"[{color}]{m_roi:+.1f}%[/{color}]",
        )

    console.print(market_table)

    # Breakdown by league tier
    tier_table = Table(title="Results by League Tier")
    tier_table.add_column("Tier", style="cyan")
    tier_table.add_column("Bets", justify="right")
    tier_table.add_column("Hit Rate", justify="right")
    tier_table.add_column("P&L", justify="right")
    tier_table.add_column("ROI", justify="right")

    for tier in sorted(bets_df["tier"].unique()):
        t = bets_df[bets_df["tier"] == tier]
        t_wins = len(t[t["result"] == "W"])
        t_pnl = t["pnl"].sum()
        t_roi = t_pnl / (len(t) * stake) * 100

        tier_name = {1: "Top division", 2: "Second division",
                     3: "Third division", 4: "Fourth division"}.get(tier, f"Tier {tier}")
        color = "green" if t_pnl > 0 else "red"
        tier_table.add_row(
            tier_name,
            str(len(t)),
            f"{t_wins/len(t):.1%}",
            f"[{color}]EUR {t_pnl:+,.2f}[/{color}]",
            f"[{color}]{t_roi:+.1f}%[/{color}]",
        )

    console.print(tier_table)

    # Calibration check
    console.print("\n[bold cyan]Calibration Check[/bold cyan]")
    bins = [(0.0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.0)]

    cal_table = Table(title="Model Calibration (Predicted Prob vs Actual Win Rate)")
    cal_table.add_column("Predicted Range", style="cyan")
    cal_table.add_column("Bets", justify="right")
    cal_table.add_column("Predicted Avg", justify="right")
    cal_table.add_column("Actual Win Rate", justify="right")
    cal_table.add_column("Calibration", justify="right")

    for lo, hi in bins:
        bin_bets = bets_df[(bets_df["model_prob"] >= lo) & (bets_df["model_prob"] < hi)]
        if len(bin_bets) > 0:
            pred_avg = bin_bets["model_prob"].mean()
            actual = len(bin_bets[bin_bets["result"] == "W"]) / len(bin_bets)
            diff = actual - pred_avg

            color = "green" if abs(diff) < 0.05 else "yellow" if abs(diff) < 0.10 else "red"
            cal_table.add_row(
                f"{lo:.0%} - {hi:.0%}",
                str(len(bin_bets)),
                f"{pred_avg:.1%}",
                f"{actual:.1%}",
                f"[{color}]{diff:+.1%}[/{color}]",
            )

    console.print(cal_table)

    # Save results
    output_path = PROCESSED_DIR / f"backtest_{test_season.replace('-', '')}.csv"
    bets_df.to_csv(output_path, index=False)
    console.print(f"\n[green]Full bet log saved to: {output_path}[/green]")

    # GO/NO-GO verdict
    console.print("\n[bold]═══ VERDICT ═══[/bold]")
    if roi > 2.0 and total_bets >= 100:
        console.print("[bold green]GO — Model shows positive ROI with sufficient volume[/bold green]")
    elif roi > 0 and total_bets >= 100:
        console.print("[bold yellow]CAUTIOUS GO — Positive but edge is thin. Consider tuning.[/bold yellow]")
    elif total_bets < 100:
        console.print("[bold yellow]INSUFFICIENT DATA — Need more bets for statistical significance[/bold yellow]")
    else:
        console.print("[bold red]NO-GO — Model is not profitable. Review features and approach.[/bold red]")

    return bets_df


if __name__ == "__main__":
    console.print("[bold]OddsIntel Backtest[/bold]\n")

    # Load data
    data_path = PROCESSED_DIR / "all_matches.csv"
    if not data_path.exists():
        console.print("[red]No data found. Run import_historical.py first.[/red]")
        sys.exit(1)

    console.print("[yellow]Loading match data...[/yellow]")
    df = pd.read_csv(data_path, parse_dates=["Date"])

    console.print("[yellow]Building feature matrix (this takes a while)...[/yellow]")
    features_df, targets_df = build_feature_matrix(df)

    # Save feature matrix for reuse
    features_df.to_csv(PROCESSED_DIR / "features.csv", index=False)
    targets_df.to_csv(PROCESSED_DIR / "targets.csv", index=False)
    console.print(f"[green]Feature matrix saved ({len(features_df):,} matches)[/green]")

    # Run backtest on the most recent complete season
    # Try 2024-25 first, fall back to 2023-24
    for season in ["2024-25", "2023-24", "2022-23"]:
        if season in targets_df["season"].values:
            run_backtest(features_df, targets_df, test_season=season)
            break
