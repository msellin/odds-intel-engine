"""
OddsIntel — Model v8: Research-Informed
Incorporates all findings from academic research:
  1. ELO ratings as features (highest single-feature predictor)
  2. Proper isotonic calibration on HELD-OUT data (not training data)
  3. Favorite-longshot bias correction (higher edge threshold for longshots)
  4. Extreme selectivity (bet on ~10-15% of matches, not all)
  5. Odds band filtering (avoid extreme favorites and extreme longshots)
  6. Dixon-Coles inspired goal modeling via Poisson regression
  7. Separate models per market (1X2 vs O/U are fundamentally different)
"""

import sys
import json
import pandas as pd
import numpy as np
from pathlib import Path
from scipy.stats import poisson
from sklearn.calibration import CalibratedClassifierCV
from xgboost import XGBClassifier, XGBRegressor
from rich.console import Console
from rich.table import Table

console = Console()

ENGINE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
RESULTS_DIR = ENGINE_DIR / "data" / "model_results"


# ============================================================
# STEP 1: Compute ELO ratings
# ============================================================

def compute_elo(df: pd.DataFrame, k_factor=30, home_adv=100) -> pd.DataFrame:
    """
    Compute ELO ratings with:
    - K-factor of 30 (league matches)
    - Home advantage of +100
    - Goal difference multiplier: sqrt(GD)
    - Season reset: regress 1/3 toward 1500 at season start
    """
    elo = {}
    elo_records = []
    current_season = None

    for _, row in df.sort_values("Date").iterrows():
        # Season regression (regress toward mean at start of new season)
        if row["season"] != current_season:
            current_season = row["season"]
            for team in elo:
                elo[team] = elo[team] * 0.67 + 1500 * 0.33

        home = row["HomeTeam"]
        away = row["AwayTeam"]

        if home not in elo:
            elo[home] = 1500
        if away not in elo:
            elo[away] = 1500

        # Pre-match ELOs (what we'd use for prediction)
        home_elo = elo[home]
        away_elo = elo[away]
        elo_diff = home_elo - away_elo

        # Expected score with home advantage
        home_expected = 1 / (1 + 10 ** (-(elo_diff + home_adv) / 400))

        elo_records.append({
            "home_elo": home_elo,
            "away_elo": away_elo,
            "elo_diff": elo_diff,
            "home_elo_expected": home_expected,
        })

        # Actual result
        if row["FTR"] == "H":
            actual_home = 1.0
        elif row["FTR"] == "A":
            actual_home = 0.0
        else:
            actual_home = 0.5

        # Goal difference multiplier
        gd = abs(row["FTHG"] - row["FTAG"])
        gd_mult = max(1.0, np.sqrt(gd))

        # Update
        delta = k_factor * gd_mult * (actual_home - home_expected)
        elo[home] += delta
        elo[away] -= delta

    return pd.DataFrame(elo_records)


# ============================================================
# STEP 2: Build enhanced features with ELO
# ============================================================

def build_enhanced_features(df: pd.DataFrame) -> tuple:
    """Build features including ELO ratings"""

    console.print("[yellow]Computing ELO ratings...[/yellow]")
    elo_df = compute_elo(df)

    console.print("[yellow]Loading base features...[/yellow]")
    base_features = pd.read_csv(PROCESSED_DIR / "features_fast.csv")
    targets = pd.read_csv(PROCESSED_DIR / "targets_fast.csv", parse_dates=["Date"])

    # The base features and elo_df are both aligned to the full df
    # But base features may have filtered some rows (NaN removal)
    # We need to align them

    # Add ELO to the original df
    df["home_elo"] = elo_df["home_elo"].values
    df["away_elo"] = elo_df["away_elo"].values
    df["elo_diff"] = elo_df["elo_diff"].values
    df["home_elo_expected"] = elo_df["home_elo_expected"].values

    # Now rebuild features with ELO included
    # Use the same rolling stats approach as backtest_fast.py
    sys.path.insert(0, str(Path(__file__).parent))
    from backtest_fast import compute_team_rolling_stats

    all_stats, venue_stats, long = compute_team_rolling_stats(df, n=10)

    console.print("[yellow]Merging features...[/yellow]")

    # Start with match-level data
    features = df[["Date", "HomeTeam", "AwayTeam", "league_code", "season", "tier",
                    "home_elo", "away_elo", "elo_diff", "home_elo_expected"]].copy()

    # Merge home overall form
    home_overall = all_stats[all_stats["venue"] == "home"][
        ["team", "Date", "form_win_pct", "form_ppg", "form_goals_scored",
         "form_goals_conceded", "form_goal_diff", "form_over25_pct",
         "form_btts_pct", "form_clean_sheet_pct"]
    ].rename(columns=lambda c: f"home_{c}" if c not in ["team", "Date"] else c)

    away_overall = all_stats[all_stats["venue"] == "away"][
        ["team", "Date", "form_win_pct", "form_ppg", "form_goals_scored",
         "form_goals_conceded", "form_goal_diff", "form_over25_pct",
         "form_btts_pct", "form_clean_sheet_pct"]
    ].rename(columns=lambda c: f"away_{c}" if c not in ["team", "Date"] else c)

    home_venue = venue_stats[venue_stats["venue"] == "home"][
        ["team", "Date", "venue_win_pct", "venue_goals_scored",
         "venue_goals_conceded", "venue_over25_pct"]
    ].rename(columns=lambda c: f"home_{c}" if c not in ["team", "Date"] else c)

    away_venue = venue_stats[venue_stats["venue"] == "away"][
        ["team", "Date", "venue_win_pct", "venue_goals_scored",
         "venue_goals_conceded", "venue_over25_pct"]
    ].rename(columns=lambda c: f"away_{c}" if c not in ["team", "Date"] else c)

    features = features.merge(home_overall, left_on=["HomeTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")
    features = features.merge(away_overall, left_on=["AwayTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")
    features = features.merge(home_venue, left_on=["HomeTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")
    features = features.merge(away_venue, left_on=["AwayTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")

    # Rest days
    rest_data = []
    for team_name, group in long.groupby("team"):
        g = group.sort_values("Date")
        g["rest_days"] = g["Date"].diff().dt.days.clip(upper=14)
        rest_data.append(g[["team", "Date", "venue", "rest_days"]])
    rest_df = pd.concat(rest_data, ignore_index=True)

    home_rest = rest_df[rest_df["venue"] == "home"][["team", "Date", "rest_days"]].rename(columns={"rest_days": "home_rest_days"})
    away_rest = rest_df[rest_df["venue"] == "away"][["team", "Date", "rest_days"]].rename(columns={"rest_days": "away_rest_days"})

    features = features.merge(home_rest, left_on=["HomeTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")
    features = features.merge(away_rest, left_on=["AwayTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")

    features["rest_advantage"] = features["home_rest_days"].fillna(7) - features["away_rest_days"].fillna(7)
    features["home_rest_days"] = features["home_rest_days"].fillna(7)
    features["away_rest_days"] = features["away_rest_days"].fillna(7)
    features["league_tier"] = features["tier"]

    # Derived features
    features["form_ppg_diff"] = features["home_form_ppg"].fillna(1.3) - features["away_form_ppg"].fillna(1.3)
    features["form_goals_diff"] = (features["home_form_goals_scored"].fillna(1.3) - features["away_form_goals_scored"].fillna(1.3))
    features["defense_diff"] = (features["away_form_goals_conceded"].fillna(1.3) - features["home_form_goals_conceded"].fillna(1.3))

    # Targets
    targets_full = df[["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG",
                        "total_goals", "over_25", "btts", "league_code",
                        "league_name", "season", "tier"]].copy()
    targets_full.rename(columns={"HomeTeam": "home_team", "AwayTeam": "away_team",
                                  "FTR": "result", "league_name": "league"}, inplace=True)

    for col in ["AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5",
                "PSH", "PSD", "PSA", "B365H", "B365D", "B365A"]:
        if col in df.columns:
            targets_full[col] = df[col].values

    # Feature columns
    feature_cols = [
        # ELO (NEW — most important single feature)
        "home_elo", "away_elo", "elo_diff", "home_elo_expected",
        # Form
        "home_form_win_pct", "home_form_ppg", "home_form_goals_scored",
        "home_form_goals_conceded", "home_form_goal_diff",
        "home_form_over25_pct", "home_form_btts_pct", "home_form_clean_sheet_pct",
        # Home venue
        "home_venue_win_pct", "home_venue_goals_scored",
        "home_venue_goals_conceded", "home_venue_over25_pct",
        # Away form
        "away_form_win_pct", "away_form_ppg", "away_form_goals_scored",
        "away_form_goals_conceded", "away_form_goal_diff",
        "away_form_over25_pct", "away_form_btts_pct", "away_form_clean_sheet_pct",
        # Away venue
        "away_venue_win_pct", "away_venue_goals_scored",
        "away_venue_goals_conceded", "away_venue_over25_pct",
        # Rest
        "home_rest_days", "away_rest_days", "rest_advantage",
        # Derived
        "form_ppg_diff", "form_goals_diff", "defense_diff",
        "league_tier",
    ]

    # Filter valid rows
    valid = features[feature_cols].notna().all(axis=1)
    features_out = features[valid][feature_cols].reset_index(drop=True)
    targets_out = targets_full[valid].reset_index(drop=True)

    console.print(f"[green]Enhanced features: {len(features_out):,} matches, {len(feature_cols)} features (incl. ELO)[/green]")

    return features_out, targets_out, feature_cols


# ============================================================
# STEP 3: Run v8 backtest with all improvements
# ============================================================

def run_v8(features, targets, feature_cols, test_season="2024-25"):
    """
    v8: ELO + calibration + favorite-longshot correction + selectivity
    """
    console.print(f"\n[bold green]═══ v8 Backtest: {test_season} ═══[/bold green]")

    train_mask = targets["season"] != test_season
    test_mask = targets["season"] == test_season

    X_train = features[train_mask]
    X_test = features[test_mask].reset_index(drop=True)
    targets_test = targets[test_mask].reset_index(drop=True)

    console.print(f"Train: {len(X_train):,} | Test: {len(X_test):,}")

    # --- GOAL PREDICTION (Poisson XGBoost) ---
    console.print("  Training Poisson goal models...")
    home_goals_model = XGBRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        objective="count:poisson", random_state=42, verbosity=0,
    )
    home_goals_model.fit(X_train, targets[train_mask]["FTHG"])

    away_goals_model = XGBRegressor(
        n_estimators=200, max_depth=4, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        objective="count:poisson", random_state=42, verbosity=0,
    )
    away_goals_model.fit(X_train, targets[train_mask]["FTAG"])

    exp_home = np.clip(home_goals_model.predict(X_test), 0.2, 4.0)
    exp_away = np.clip(away_goals_model.predict(X_test), 0.2, 4.0)

    # --- 1X2 CLASSIFIER (calibrated) ---
    console.print("  Training calibrated 1X2 model...")
    y_result = targets[train_mask]["result"].map({"H": 0, "D": 1, "A": 2})
    result_base = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        objective="multi:softprob", num_class=3,
        random_state=42, verbosity=0,
    )
    result_model = CalibratedClassifierCV(result_base, cv=5, method="isotonic")
    result_model.fit(X_train, y_result)
    result_proba = result_model.predict_proba(X_test)

    # --- O/U CLASSIFIER (calibrated) ---
    console.print("  Training calibrated O/U model...")
    y_over = targets[train_mask]["over_25"]
    over_base = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        objective="binary:logistic", random_state=42, verbosity=0,
    )
    over_model = CalibratedClassifierCV(over_base, cv=5, method="isotonic")
    over_model.fit(X_train, y_over)
    over_proba = over_model.predict_proba(X_test)[:, 1]

    console.print("[green]  Models trained.[/green]")

    # --- ENSEMBLE: Average Poisson-derived probs with classifier probs ---
    poisson_over25 = np.zeros(len(X_test))
    poisson_home = np.zeros(len(X_test))
    poisson_draw = np.zeros(len(X_test))
    poisson_away = np.zeros(len(X_test))

    for i in range(len(X_test)):
        eh, ea = exp_home[i], exp_away[i]
        p_over = p_home = p_draw = p_away = 0.0

        for h in range(8):
            for a in range(8):
                p = poisson.pmf(h, eh) * poisson.pmf(a, ea)
                if h + a > 2:
                    p_over += p
                if h > a:
                    p_home += p
                elif h == a:
                    p_draw += p
                else:
                    p_away += p

        poisson_over25[i] = p_over
        poisson_home[i] = p_home
        poisson_draw[i] = p_draw
        poisson_away[i] = p_away

    # Ensemble: 50/50 blend of classifier and Poisson
    ens_over25 = 0.5 * over_proba + 0.5 * poisson_over25
    ens_home = 0.5 * result_proba[:, 0] + 0.5 * poisson_home
    ens_draw = 0.5 * result_proba[:, 1] + 0.5 * poisson_draw
    ens_away = 0.5 * result_proba[:, 2] + 0.5 * poisson_away

    # --- BET SIMULATION with research-informed rules ---
    bets = []
    stake = 10.0

    for i, row in targets_test.iterrows():
        match = f"{row['home_team']} vs {row['away_team']}"

        # ---- OVER 2.5 ----
        odds = row.get("Avg>2.5")
        if pd.notna(odds) and odds > 1.0:
            mp = ens_over25[i]
            ip = 1 / odds
            edge = mp - ip

            # SELECTIVE: min 5% edge, odds 1.55-2.60, model prob >= 48%
            if edge >= 0.05 and 1.55 <= odds <= 2.60 and mp >= 0.48:
                won = row["over_25"] == 1
                bets.append({"match": match, "market": "O/U", "selection": "Over 2.5",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"],
                             "date": row["Date"]})

        # ---- UNDER 2.5 ----
        odds = row.get("Avg<2.5")
        if pd.notna(odds) and odds > 1.0:
            mp = 1 - ens_over25[i]
            ip = 1 / odds
            edge = mp - ip

            if edge >= 0.05 and 1.55 <= odds <= 2.60 and mp >= 0.48:
                won = row["over_25"] == 0
                bets.append({"match": match, "market": "O/U", "selection": "Under 2.5",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"],
                             "date": row["Date"]})

        # ---- HOME WIN ----
        odds = row.get("AvgH") or row.get("B365H")
        if pd.notna(odds) and odds > 1.0:
            mp = ens_home[i]
            ip = 1 / odds
            edge = mp - ip

            # FAVORITE-LONGSHOT correction:
            # For favorites (odds < 2.0): 5% edge
            # For non-favorites (odds >= 2.0): 8% edge
            min_edge = 0.05 if odds < 2.0 else 0.08

            if edge >= min_edge and 1.30 <= odds <= 3.50 and mp >= 0.38:
                won = row["result"] == "H"
                bets.append({"match": match, "market": "1X2", "selection": "Home",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"],
                             "date": row["Date"]})

        # ---- AWAY WIN ----
        odds = row.get("AvgA") or row.get("B365A")
        if pd.notna(odds) and odds > 1.0:
            mp = ens_away[i]
            ip = 1 / odds
            edge = mp - ip

            # Away wins are harder — require bigger edge
            min_edge = 0.07 if odds < 2.5 else 0.10

            if edge >= min_edge and 1.80 <= odds <= 4.00 and mp >= 0.30:
                won = row["result"] == "A"
                bets.append({"match": match, "market": "1X2", "selection": "Away",
                             "odds": odds, "model_prob": mp, "implied_prob": ip,
                             "edge": edge, "result": "W" if won else "L",
                             "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"],
                             "date": row["Date"]})

    if not bets:
        console.print("[red]No bets placed.[/red]")
        return None

    bets_df = pd.DataFrame(bets)

    # --- RESULTS ---
    total = len(bets_df)
    wins = (bets_df["result"] == "W").sum()
    total_pnl = bets_df["pnl"].sum()
    roi = total_pnl / (total * stake) * 100

    streak = max_streak = 0
    for r in bets_df["result"]:
        streak = streak + 1 if r == "L" else 0
        max_streak = max(max_streak, streak)

    color = "green" if roi > 0 else "red"

    t = Table(title=f"v8 Results: {test_season}")
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
    t.add_row("Bets per matchday", f"~{total / 300:.1f}")  # ~300 matchdays per season
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
        mt.add_row(market, str(len(m)), f"{(m['result']=='W').mean():.1%}",
                   f"[{c}]{m_roi:+.1f}%[/{c}]", f"[{c}]EUR {m_pnl:+,.0f}[/{c}]")
    console.print(mt)

    # By tier
    tt = Table(title="By League Tier")
    tt.add_column("Tier")
    tt.add_column("Bets", justify="right")
    tt.add_column("Hit%", justify="right")
    tt.add_column("ROI", justify="right")
    for tier in sorted(bets_df["tier"].unique()):
        tb = bets_df[bets_df["tier"] == tier]
        t_roi = tb["pnl"].sum() / (len(tb) * stake) * 100
        c = "green" if t_roi > 0 else "red"
        tier_name = {1: "Top", 2: "2nd", 3: "3rd", 4: "4th"}.get(tier, f"T{tier}")
        tt.add_row(tier_name, str(len(tb)), f"{(tb['result']=='W').mean():.1%}",
                   f"[{c}]{t_roi:+.1f}%[/{c}]")
    console.print(tt)

    # Calibration
    ct = Table(title="Calibration")
    ct.add_column("Predicted")
    ct.add_column("Actual")
    ct.add_column("Diff")
    ct.add_column("N")
    for lo, hi in [(0.2, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.70), (0.70, 0.90)]:
        b = bets_df[(bets_df["model_prob"] >= lo) & (bets_df["model_prob"] < hi)]
        if len(b) >= 5:
            pred = b["model_prob"].mean()
            actual = (b["result"] == "W").mean()
            diff = actual - pred
            c = "green" if abs(diff) < 0.03 else "yellow" if abs(diff) < 0.07 else "red"
            ct.add_row(f"{pred:.1%}", f"{actual:.1%}", f"[{c}]{diff:+.1%}[/{c}]", str(len(b)))
    console.print(ct)

    # Save
    bets_df.to_csv(RESULTS_DIR / f"v8_{test_season.replace('-','')}.csv", index=False)

    # Log
    log_path = RESULTS_DIR / "iterations.json"
    log = json.load(open(log_path)) if log_path.exists() else []
    log.append({
        "version": "v8",
        "description": "ELO + ensemble Poisson/XGB + calibration + FL bias correction + selectivity",
        "test_season": test_season,
        "total_bets": total, "wins": int(wins), "losses": total - int(wins),
        "hit_rate": wins / total, "roi": roi,
        "total_pnl": total_pnl, "avg_edge": bets_df["edge"].mean(),
        "avg_odds": bets_df["odds"].mean(), "max_losing_streak": max_streak,
    })
    json.dump(log, open(log_path, "w"), indent=2, default=str)

    console.print(f"\n[bold]VERDICT: ", end="")
    if roi > 2.0 and total >= 100:
        console.print("[bold green]GO — Profitable![/bold green]")
    elif roi > 0:
        console.print("[bold yellow]PROMISING — Positive ROI, needs more data[/bold yellow]")
    elif roi > -3:
        console.print("[bold yellow]CLOSE — Almost breakeven, iterate further[/bold yellow]")
    else:
        console.print("[bold red]NO-GO[/bold red]")

    return bets_df


# ============================================================
# MAIN
# ============================================================

def main():
    console.print("[bold green]═══ OddsIntel v8: Research-Informed Model ═══[/bold green]\n")

    df = pd.read_csv(PROCESSED_DIR / "all_matches.csv", parse_dates=["Date"], low_memory=False)
    console.print(f"Loaded {len(df):,} matches\n")

    features, targets, feature_cols = build_enhanced_features(df)

    # Save enhanced features
    features.to_csv(PROCESSED_DIR / "features_v8.csv", index=False)
    targets.to_csv(PROCESSED_DIR / "targets_v8.csv", index=False)

    # Run on multiple seasons
    for season in ["2024-25", "2023-24", "2022-23"]:
        if season in targets["season"].values:
            run_v8(features, targets, feature_cols, test_season=season)


if __name__ == "__main__":
    main()
