"""
OddsIntel — Model Iteration Framework
Tests each model improvement systematically and logs results.

BASELINE (v0): XGBoost on form stats, 3% min edge → -11% ROI (NO-GO)

Improvements to test:
  v1: Fix calibration (isotonic regression on probabilities)
  v2: Be more selective (10%+ edge only, high-confidence only)
  v3: Focus on O/U 2.5 market only (2-way is simpler than 3-way 1X2)
  v4: Add ELO ratings as features
  v5: Use Poisson regression for goal prediction instead of XGBoost classification
  v6: Only bet on specific profitable leagues/tiers
  v7: Combine best elements from above
"""

import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.linear_model import LogisticRegression
from xgboost import XGBClassifier
from scipy.stats import poisson
from rich.console import Console
from rich.table import Table

console = Console()

ENGINE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
RESULTS_DIR = ENGINE_DIR / "data" / "model_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def load_data():
    """Load pre-computed features and targets"""
    features = pd.read_csv(PROCESSED_DIR / "features_fast.csv")
    targets = pd.read_csv(PROCESSED_DIR / "targets_fast.csv", parse_dates=["Date"])
    return features, targets


def implied_prob(odds):
    if pd.isna(odds) or odds <= 1.0:
        return 0.0
    return 1.0 / odds


def evaluate_bets(bets_df, stake=10.0):
    """Calculate key metrics from a bets dataframe"""
    if len(bets_df) == 0:
        return {"total_bets": 0, "roi": 0, "hit_rate": 0}

    total = len(bets_df)
    wins = (bets_df["result"] == "W").sum()
    total_pnl = bets_df["pnl"].sum()
    roi = total_pnl / (total * stake) * 100

    # Losing streak
    streak = max_streak = 0
    for r in bets_df["result"]:
        streak = streak + 1 if r == "L" else 0
        max_streak = max(max_streak, streak)

    return {
        "total_bets": total,
        "wins": int(wins),
        "losses": total - int(wins),
        "hit_rate": wins / total,
        "total_pnl": total_pnl,
        "roi": roi,
        "avg_edge": bets_df["edge"].mean(),
        "avg_odds": bets_df["odds"].mean(),
        "max_losing_streak": max_streak,
    }


def log_iteration(version, description, results, test_season):
    """Save iteration results to JSON log"""
    log_path = RESULTS_DIR / "iterations.json"

    if log_path.exists():
        with open(log_path) as f:
            log = json.load(f)
    else:
        log = []

    entry = {
        "version": version,
        "description": description,
        "test_season": test_season,
        "timestamp": datetime.now().isoformat(),
        **results,
    }
    log.append(entry)

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2, default=str)

    return entry


def print_results(version, description, results):
    """Pretty-print iteration results"""
    color = "green" if results["roi"] > 0 else "red" if results["roi"] < -2 else "yellow"

    t = Table(title=f"{version}: {description}")
    t.add_column("Metric", style="cyan")
    t.add_column("Value", style=color)
    t.add_row("Bets", str(results["total_bets"]))
    t.add_row("Hit rate", f"{results['hit_rate']:.1%}")
    t.add_row("ROI", f"{results['roi']:+.2f}%")
    t.add_row("P&L", f"EUR {results['total_pnl']:+,.2f}")
    t.add_row("Avg edge", f"{results['avg_edge']:.1%}")
    t.add_row("Avg odds", f"{results['avg_odds']:.2f}")
    t.add_row("Max losing streak", str(results["max_losing_streak"]))
    console.print(t)


# ============================================================
# ITERATION v1: Fix Calibration
# The model is overconfident by 10-15%. Apply isotonic calibration.
# ============================================================

def v1_calibrated_model(features, targets, test_season="2024-25", min_edge=0.03):
    """v1: Properly calibrate probabilities using isotonic regression"""

    train_mask = targets["season"] != test_season
    test_mask = targets["season"] == test_season

    feature_cols = [c for c in features.columns if c not in ["Date", "HomeTeam", "AwayTeam"]]

    X_train = features[train_mask][feature_cols]
    X_test = features[test_mask][feature_cols]
    targets_test = targets[test_mask].reset_index(drop=True)
    X_test = X_test.reset_index(drop=True)

    # Over 2.5 model with proper calibration
    y_train = targets[train_mask]["over_25"]

    # Train base model
    base = XGBClassifier(
        n_estimators=150, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="binary:logistic", random_state=42, verbosity=0,
    )

    # Calibrate with isotonic regression (fixes overconfidence)
    calibrated = CalibratedClassifierCV(base, cv=5, method="isotonic")
    calibrated.fit(X_train, y_train)

    over_proba = calibrated.predict_proba(X_test)[:, 1]

    # 1X2 with calibration
    y_result = targets[train_mask]["result"].map({"H": 0, "D": 1, "A": 2})
    result_base = XGBClassifier(
        n_estimators=150, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="multi:softprob", num_class=3,
        random_state=42, verbosity=0,
    )
    result_cal = CalibratedClassifierCV(result_base, cv=5, method="isotonic")
    result_cal.fit(X_train, y_result)
    result_proba = result_cal.predict_proba(X_test)

    # Bet simulation
    bets = []
    stake = 10.0

    for i, row in targets_test.iterrows():
        # Over 2.5
        odds = row.get("Avg>2.5")
        if pd.notna(odds) and odds > 1.0:
            mp = over_proba[i]
            ip = implied_prob(odds)
            edge = mp - ip
            if edge >= min_edge:
                won = row["over_25"] == 1
                bets.append({"market": "O/U", "selection": "Over 2.5",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

        # Under 2.5
        odds = row.get("Avg<2.5")
        if pd.notna(odds) and odds > 1.0:
            mp = 1 - over_proba[i]
            ip = implied_prob(odds)
            edge = mp - ip
            if edge >= min_edge:
                won = row["over_25"] == 0
                bets.append({"market": "O/U", "selection": "Under 2.5",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

        # 1X2 Home
        odds = row.get("AvgH") or row.get("B365H")
        if pd.notna(odds) and odds > 1.0:
            mp = result_proba[i][0]
            ip = implied_prob(odds)
            edge = mp - ip
            if edge >= min_edge:
                won = row["result"] == "H"
                bets.append({"market": "1X2", "selection": "Home",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

        # 1X2 Away
        odds = row.get("AvgA") or row.get("B365A")
        if pd.notna(odds) and odds > 1.0:
            mp = result_proba[i][2]
            ip = implied_prob(odds)
            edge = mp - ip
            if edge >= min_edge:
                won = row["result"] == "A"
                bets.append({"market": "1X2", "selection": "Away",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

    return pd.DataFrame(bets)


# ============================================================
# ITERATION v2: Extreme Selectivity
# Only bet when edge is very high AND odds are in sweet spot
# ============================================================

def v2_selective(features, targets, test_season="2024-25"):
    """v2: Only bet on extreme value — high edge, reasonable odds"""

    bets_all = v1_calibrated_model(features, targets, test_season, min_edge=0.01)

    if len(bets_all) == 0:
        return pd.DataFrame()

    # Filters for selectivity:
    # 1. Edge must be >= 8% (was 3%)
    # 2. Odds must be between 1.5 and 3.5 (avoid extreme longshots and heavy favorites)
    # 3. Model probability must be >= 40% (need reasonable confidence)
    selective = bets_all[
        (bets_all["edge"] >= 0.08) &
        (bets_all["odds"] >= 1.50) &
        (bets_all["odds"] <= 3.50) &
        (bets_all["model_prob"] >= 0.40)
    ].copy()

    return selective


# ============================================================
# ITERATION v3: O/U Only + Calibrated + Selective
# 1X2 is a 3-way market (harder). Focus purely on Over/Under.
# ============================================================

def v3_ou_only(features, targets, test_season="2024-25"):
    """v3: Only Over/Under 2.5 bets, calibrated, selective"""

    bets_all = v1_calibrated_model(features, targets, test_season, min_edge=0.01)

    if len(bets_all) == 0:
        return pd.DataFrame()

    # Only O/U market, selective
    ou_bets = bets_all[
        (bets_all["market"] == "O/U") &
        (bets_all["edge"] >= 0.05) &
        (bets_all["odds"] >= 1.60) &
        (bets_all["odds"] <= 2.50) &
        (bets_all["model_prob"] >= 0.45)
    ].copy()

    return ou_bets


# ============================================================
# ITERATION v4: Add ELO Ratings
# Compute ELO ratings for all teams, use as additional features.
# ============================================================

def compute_elo_ratings(df: pd.DataFrame, k=20, home_advantage=100):
    """Compute ELO ratings for all teams over time"""
    elo = {}  # team -> current ELO
    match_elos = []  # ELO at time of each match

    for _, row in df.sort_values("Date").iterrows():
        home = row["HomeTeam"]
        away = row["AwayTeam"]

        # Initialize new teams
        if home not in elo:
            elo[home] = 1500
        if away not in elo:
            elo[away] = 1500

        home_elo = elo[home] + home_advantage
        away_elo = elo[away]

        # Expected scores
        exp_home = 1 / (1 + 10 ** ((away_elo - home_elo) / 400))
        exp_away = 1 - exp_home

        # Actual scores
        if row["FTR"] == "H":
            actual_home, actual_away = 1, 0
        elif row["FTR"] == "A":
            actual_home, actual_away = 0, 1
        else:
            actual_home, actual_away = 0.5, 0.5

        # Store pre-match ELOs
        match_elos.append({
            "home_elo": elo[home],
            "away_elo": elo[away],
            "elo_diff": elo[home] - elo[away],
            "home_expected": exp_home,
        })

        # Update ELOs (using goal difference as multiplier)
        goal_diff = abs(row["FTHG"] - row["FTAG"])
        gd_mult = max(1, np.log(goal_diff + 1))

        elo[home] += k * gd_mult * (actual_home - exp_home)
        elo[away] += k * gd_mult * (actual_away - exp_away)

    return pd.DataFrame(match_elos)


def v4_with_elo(features, targets, test_season="2024-25"):
    """v4: Add ELO ratings to features"""

    # Load raw data to compute ELOs
    df = pd.read_csv(PROCESSED_DIR / "all_matches.csv", parse_dates=["Date"], low_memory=False)

    console.print("  Computing ELO ratings...")
    elo_df = compute_elo_ratings(df)

    # We need to align ELO data with our feature matrix
    # The features_fast.csv was built from the same df, same order after filtering
    # But we need to be careful about alignment

    # Rebuild features with ELO
    # For speed, just add ELO columns to existing features
    if len(elo_df) != len(df):
        console.print("[red]ELO length mismatch[/red]")
        return pd.DataFrame()

    df["home_elo"] = elo_df["home_elo"].values
    df["away_elo"] = elo_df["away_elo"].values
    df["elo_diff"] = elo_df["elo_diff"].values

    # Rebuild features with ELO included
    from scripts.backtest_fast import build_features_fast

    # Add ELO to the dataframe before feature building
    features_with_elo, targets_new, feature_cols = build_features_fast(df)

    # Add ELO features
    # Need to align - use the valid mask from build_features_fast
    # This is tricky, so let's take a simpler approach:
    # Just add ELO to the existing features by matching on date+teams

    # Actually, let's just merge ELO into our existing features
    # Since both are aligned to the same underlying df

    # For now, fall back to v2 selective approach
    # (ELO integration needs more careful alignment work)
    console.print("  [yellow]ELO integration needs alignment work — using v2 as base[/yellow]")
    return v2_selective(features, targets, test_season)


# ============================================================
# ITERATION v5: Poisson Goal Model
# Instead of classifying outcomes, predict goal counts.
# This is how most successful football models work.
# ============================================================

def v5_poisson(features, targets, test_season="2024-25"):
    """v5: Poisson regression to predict goals, derive probabilities"""

    feature_cols = [c for c in features.columns]

    train_mask = targets["season"] != test_season
    test_mask = targets["season"] == test_season

    X_train = features[train_mask]
    X_test = features[test_mask].reset_index(drop=True)
    targets_train = targets[train_mask]
    targets_test = targets[test_mask].reset_index(drop=True)

    # Train goal prediction models (regression, not classification)
    from xgboost import XGBRegressor

    # Home goals model
    home_goals_model = XGBRegressor(
        n_estimators=150, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="count:poisson",  # Poisson regression
        random_state=42, verbosity=0,
    )
    home_goals_model.fit(X_train, targets_train["FTHG"])

    # Away goals model
    away_goals_model = XGBRegressor(
        n_estimators=150, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="count:poisson",
        random_state=42, verbosity=0,
    )
    away_goals_model.fit(X_train, targets_train["FTAG"])

    # Predict expected goals
    exp_home = home_goals_model.predict(X_test)
    exp_away = away_goals_model.predict(X_test)

    # Derive probabilities from Poisson distributions
    bets = []
    stake = 10.0

    for i, row in targets_test.iterrows():
        eh = max(0.1, exp_home[i])  # Clamp to avoid zero
        ea = max(0.1, exp_away[i])

        # P(Over 2.5) = 1 - P(0+0) - P(1+0) - P(0+1) - P(1+1) - P(2+0) - P(0+2)
        p_over_25 = 0.0
        p_under_25 = 0.0
        for h in range(10):
            for a in range(10):
                p = poisson.pmf(h, eh) * poisson.pmf(a, ea)
                if h + a > 2:
                    p_over_25 += p
                else:
                    p_under_25 += p

        # P(Home win), P(Draw), P(Away win)
        p_home = 0.0
        p_draw = 0.0
        p_away = 0.0
        for h in range(10):
            for a in range(10):
                p = poisson.pmf(h, eh) * poisson.pmf(a, ea)
                if h > a:
                    p_home += p
                elif h == a:
                    p_draw += p
                else:
                    p_away += p

        # P(BTTS) = 1 - P(home=0) - P(away=0) + P(both=0)
        p_btts = 1 - poisson.pmf(0, eh) - poisson.pmf(0, ea) + poisson.pmf(0, eh) * poisson.pmf(0, ea)

        # Check for value — Over 2.5
        odds = row.get("Avg>2.5")
        if pd.notna(odds) and odds > 1.0:
            ip = implied_prob(odds)
            edge = p_over_25 - ip
            if edge >= 0.05 and 1.50 <= odds <= 2.80 and p_over_25 >= 0.45:
                won = row["over_25"] == 1
                bets.append({"market": "O/U", "selection": "Over 2.5",
                             "odds": odds, "model_prob": p_over_25, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

        # Under 2.5
        odds = row.get("Avg<2.5")
        if pd.notna(odds) and odds > 1.0:
            ip = implied_prob(odds)
            edge = p_under_25 - ip
            if edge >= 0.05 and 1.50 <= odds <= 2.80 and p_under_25 >= 0.45:
                won = row["over_25"] == 0
                bets.append({"market": "O/U", "selection": "Under 2.5",
                             "odds": odds, "model_prob": p_under_25, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

        # Home win (selective: only clear favorites or underdogs with value)
        odds = row.get("AvgH") or row.get("B365H")
        if pd.notna(odds) and odds > 1.0:
            ip = implied_prob(odds)
            edge = p_home - ip
            if edge >= 0.07 and 1.40 <= odds <= 3.00 and p_home >= 0.40:
                won = row["result"] == "H"
                bets.append({"market": "1X2", "selection": "Home",
                             "odds": odds, "model_prob": p_home, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

        # Away win
        odds = row.get("AvgA") or row.get("B365A")
        if pd.notna(odds) and odds > 1.0:
            ip = implied_prob(odds)
            edge = p_away - ip
            if edge >= 0.07 and 1.80 <= odds <= 4.50 and p_away >= 0.30:
                won = row["result"] == "A"
                bets.append({"market": "1X2", "selection": "Away",
                             "odds": odds, "model_prob": p_away, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"]})

    return pd.DataFrame(bets)


# ============================================================
# ITERATION v6: League-Specific (Only Softer Markets)
# Only bet on 2nd division + lower tier leagues
# ============================================================

def v6_soft_leagues_only(features, targets, test_season="2024-25"):
    """v6: Only bet on tier 2+ leagues (softer markets)"""

    bets_all = v1_calibrated_model(features, targets, test_season, min_edge=0.01)

    if len(bets_all) == 0:
        return pd.DataFrame()

    # Only lower divisions
    soft = bets_all[
        (bets_all["tier"] >= 2) &
        (bets_all["edge"] >= 0.05) &
        (bets_all["odds"] >= 1.50) &
        (bets_all["odds"] <= 3.00) &
        (bets_all["model_prob"] >= 0.42)
    ].copy()

    return soft


# ============================================================
# ITERATION v7: Best of Everything
# Poisson model + selective + O/U focus + reasonable odds band
# ============================================================

def v7_combined_best(features, targets, test_season="2024-25"):
    """v7: Poisson model, very selective, focused markets"""

    # Use Poisson model but with even tighter filters
    bets = v5_poisson(features, targets, test_season)

    if len(bets) == 0:
        return pd.DataFrame()

    # Additional filters: only keep highest-confidence bets
    best = bets[
        (bets["edge"] >= 0.08) &
        (bets["model_prob"] >= 0.50) &
        (bets["odds"] >= 1.60) &
        (bets["odds"] <= 2.60)
    ].copy()

    return best


# ============================================================
# MAIN: Run all iterations and compare
# ============================================================

def main():
    console.print("[bold green]═══ OddsIntel Model Iterations ═══[/bold green]\n")

    features, targets = load_data()
    console.print(f"Data loaded: {len(features):,} matches\n")

    test_seasons = ["2024-25", "2023-24"]

    iterations = [
        ("v1", "Calibrated XGBoost (3% edge)", v1_calibrated_model),
        ("v2", "Extreme selectivity (8% edge, odds 1.5-3.5)", v2_selective),
        ("v3", "O/U only + calibrated + selective", v3_ou_only),
        ("v5", "Poisson goal model (selective)", v5_poisson),
        ("v6", "Soft leagues only (tier 2+)", v6_soft_leagues_only),
        ("v7", "Poisson + very selective + tight odds", v7_combined_best),
    ]

    all_results = []

    for version, description, func in iterations:
        for season in test_seasons:
            console.print(f"\n[bold yellow]Running {version}: {description} — {season}[/bold yellow]")
            try:
                bets = func(features, targets, test_season=season)
                results = evaluate_bets(bets)
                print_results(f"{version} ({season})", description, results)
                log_iteration(version, description, results, season)
                all_results.append({"version": version, "season": season, **results})
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")
                import traceback
                traceback.print_exc()

    # Summary comparison
    console.print("\n\n[bold green]═══ COMPARISON TABLE ═══[/bold green]\n")

    summary = Table(title="All Iterations Compared")
    summary.add_column("Version", style="cyan")
    summary.add_column("Season")
    summary.add_column("Bets", justify="right")
    summary.add_column("Hit%", justify="right")
    summary.add_column("ROI", justify="right")
    summary.add_column("P&L", justify="right")
    summary.add_column("Avg Edge", justify="right")

    for r in all_results:
        if r["total_bets"] == 0:
            continue
        color = "green" if r["roi"] > 0 else "red" if r["roi"] < -2 else "yellow"
        summary.add_row(
            r["version"],
            r["season"],
            str(r["total_bets"]),
            f"{r['hit_rate']:.1%}",
            f"[{color}]{r['roi']:+.1f}%[/{color}]",
            f"[{color}]EUR {r['total_pnl']:+,.0f}[/{color}]",
            f"{r['avg_edge']:.1%}",
        )

    console.print(summary)

    # Save summary
    pd.DataFrame(all_results).to_csv(RESULTS_DIR / "comparison.csv", index=False)
    console.print(f"\n[green]Results saved to {RESULTS_DIR}[/green]")


if __name__ == "__main__":
    main()
