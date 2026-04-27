"""
OddsIntel — Model v9: xG Proxy + ELO + All Improvements
Adds expected goals approximation from shots on target data.

xG proxy: Based on shot location models, shots on target convert at ~0.30 rate.
Better proxy: xG = 0.10 * shots_off_target + 0.32 * shots_on_target
This is a crude xG but adds information our model didn't have before.

Also includes: ELO, isotonic calibration, Poisson ensemble, selectivity, FL bias correction.
"""

import sys
import json
import joblib
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from scipy.stats import poisson
from sklearn.calibration import CalibratedClassifierCV
from xgboost import XGBClassifier, XGBRegressor
from rich.console import Console
from rich.table import Table

console = Console()

ENGINE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
MODELS_DIR = ENGINE_DIR / "data" / "models" / "soccer"
RESULTS_DIR = ENGINE_DIR / "data" / "model_results"
MODELS_DIR.mkdir(parents=True, exist_ok=True)


def compute_elo(df, k_factor=30, home_adv=100):
    """ELO with season regression, goal diff multiplier"""
    elo = {}
    records = []
    current_season = None

    for _, row in df.sort_values("Date").iterrows():
        if row["season"] != current_season:
            current_season = row["season"]
            for team in elo:
                elo[team] = elo[team] * 0.67 + 1500 * 0.33

        home, away = row["HomeTeam"], row["AwayTeam"]
        if home not in elo: elo[home] = 1500
        if away not in elo: elo[away] = 1500

        home_elo, away_elo = elo[home], elo[away]
        exp_home = 1 / (1 + 10 ** (-(home_elo - away_elo + home_adv) / 400))

        records.append({"home_elo": home_elo, "away_elo": away_elo,
                        "elo_diff": home_elo - away_elo, "home_elo_exp": exp_home})

        actual = 1.0 if row["FTR"] == "H" else 0.0 if row["FTR"] == "A" else 0.5
        gd_mult = max(1.0, np.sqrt(abs(row["FTHG"] - row["FTAG"])))
        delta = k_factor * gd_mult * (actual - exp_home)
        elo[home] += delta
        elo[away] -= delta

    return pd.DataFrame(records)


def build_features_v9(df):
    """Build features with xG proxy + ELO + rolling stats"""
    console.print("[yellow]Step 1/4: Computing ELO...[/yellow]")
    elo_df = compute_elo(df)
    df["home_elo"] = elo_df["home_elo"].values
    df["away_elo"] = elo_df["away_elo"].values
    df["elo_diff"] = elo_df["elo_diff"].values
    df["home_elo_exp"] = elo_df["home_elo_exp"].values

    console.print("[yellow]Step 2/4: Computing xG proxy...[/yellow]")
    # xG proxy from shots data
    # xG ≈ 0.10 * (shots - shots_on_target) + 0.32 * shots_on_target
    # Simplifies to: xG ≈ 0.10 * shots + 0.22 * shots_on_target
    df["xg_proxy_home"] = np.where(
        df["HS"].notna() & df["HST"].notna(),
        0.10 * df["HS"] + 0.22 * df["HST"],
        np.nan
    )
    df["xg_proxy_away"] = np.where(
        df["AS"].notna() & df["AST"].notna(),
        0.10 * df["AS"] + 0.22 * df["AST"],
        np.nan
    )
    df["xg_proxy_total"] = df["xg_proxy_home"] + df["xg_proxy_away"]
    df["xg_proxy_diff"] = df["xg_proxy_home"] - df["xg_proxy_away"]

    # Over/under performance: actual goals vs xG proxy (regression to mean indicator)
    df["home_overperformance"] = df["FTHG"] - df["xg_proxy_home"]
    df["away_overperformance"] = df["FTAG"] - df["xg_proxy_away"]

    console.print("[yellow]Step 3/4: Computing rolling stats...[/yellow]")

    # Create long format
    home = df[["Date", "HomeTeam", "FTHG", "FTAG", "FTR", "total_goals", "over_25", "btts",
               "xg_proxy_home", "xg_proxy_away", "home_overperformance",
               "HS", "HST", "HC"]].copy()
    home["team"] = home["HomeTeam"]
    home["venue"] = "home"
    home["gs"] = home["FTHG"]
    home["gc"] = home["FTAG"]
    home["xg_for"] = home["xg_proxy_home"]
    home["xg_against"] = home["xg_proxy_away"]
    home["overperf"] = home["home_overperformance"]
    home["shots"] = home["HS"]
    home["sot"] = home["HST"]
    home["corners"] = home["HC"]
    home["win"] = (home["FTR"] == "H").astype(float)
    home["pts"] = home["win"] * 3 + (home["FTR"] == "D").astype(float)
    home["cs"] = (home["FTAG"] == 0).astype(float)

    away = df[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "total_goals", "over_25", "btts",
               "xg_proxy_home", "xg_proxy_away", "away_overperformance",
               "AS", "AST", "AC"]].copy()
    away["team"] = away["AwayTeam"]
    away["venue"] = "away"
    away["gs"] = away["FTAG"]
    away["gc"] = away["FTHG"]
    away["xg_for"] = away["xg_proxy_away"]
    away["xg_against"] = away["xg_proxy_home"]
    away["overperf"] = away["away_overperformance"]
    away["shots"] = away["AS"]
    away["sot"] = away["AST"]
    away["corners"] = away["AC"]
    away["win"] = (away["FTR"] == "A").astype(float)
    away["pts"] = away["win"] * 3 + (away["FTR"] == "D").astype(float)
    away["cs"] = (away["FTHG"] == 0).astype(float)

    long = pd.concat([home, away], ignore_index=True).sort_values("Date")

    # Rolling stats per team
    n = 10
    all_rolling = []
    for team_name, g in long.groupby("team"):
        g = g.sort_values("Date")
        s = pd.DataFrame(index=g.index)
        s["team"] = team_name
        s["Date"] = g["Date"]
        s["venue"] = g["venue"]

        for col, name in [("win", "win_pct"), ("pts", "ppg"), ("gs", "gs_avg"),
                          ("gc", "gc_avg"), ("cs", "cs_pct"),
                          ("over_25", "over25_pct"), ("btts", "btts_pct"),
                          ("xg_for", "xg_for_avg"), ("xg_against", "xg_against_avg"),
                          ("overperf", "overperf_avg"),
                          ("sot", "sot_avg"), ("shots", "shots_avg"), ("corners", "corners_avg")]:
            s[name] = g[col].shift(1).rolling(n, min_periods=3).mean()

        s["xg_diff_avg"] = s["xg_for_avg"] - s["xg_against_avg"]
        s["gd_avg"] = s["gs_avg"] - s["gc_avg"]
        all_rolling.append(s)

    rolling = pd.concat(all_rolling, ignore_index=True)

    console.print("[yellow]Step 4/4: Merging features...[/yellow]")

    # Merge to match level
    home_r = rolling[rolling["venue"] == "home"].drop(columns=["venue"])
    away_r = rolling[rolling["venue"] == "away"].drop(columns=["venue"])

    feat_cols_rolling = [c for c in home_r.columns if c not in ["team", "Date"]]

    home_r = home_r.rename(columns={c: f"h_{c}" for c in feat_cols_rolling})
    away_r = away_r.rename(columns={c: f"a_{c}" for c in feat_cols_rolling})

    features = df[["Date", "HomeTeam", "AwayTeam", "league_code", "league_name", "season", "tier",
                    "home_elo", "away_elo", "elo_diff", "home_elo_exp"]].copy()

    features = features.merge(home_r, left_on=["HomeTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")
    features = features.merge(away_r, left_on=["AwayTeam", "Date"], right_on=["team", "Date"], how="left").drop(columns=["team"], errors="ignore")

    # Differential features
    features["xg_diff"] = features["h_xg_for_avg"].fillna(0) - features["a_xg_for_avg"].fillna(0)
    features["form_diff"] = features["h_ppg"].fillna(1.3) - features["a_ppg"].fillna(1.3)
    features["overperf_diff"] = features["h_overperf_avg"].fillna(0) - features["a_overperf_avg"].fillna(0)

    # Targets
    targets = df[["Date", "HomeTeam", "AwayTeam", "FTR", "FTHG", "FTAG",
                   "total_goals", "over_25", "btts", "league_code", "league_name", "season", "tier"]].copy()
    targets.rename(columns={"HomeTeam": "home_team", "AwayTeam": "away_team",
                            "FTR": "result", "league_name": "league"}, inplace=True)
    for col in ["AvgH", "AvgD", "AvgA", "Avg>2.5", "Avg<2.5", "B365H", "B365D", "B365A",
                "PSH", "PSD", "PSA"]:
        if col in df.columns:
            targets[col] = df[col].values

    # Feature columns
    feature_cols = [
        "home_elo", "away_elo", "elo_diff", "home_elo_exp",
        "h_win_pct", "h_ppg", "h_gs_avg", "h_gc_avg", "h_cs_pct",
        "h_over25_pct", "h_btts_pct",
        "h_xg_for_avg", "h_xg_against_avg", "h_xg_diff_avg", "h_overperf_avg",
        "h_sot_avg", "h_shots_avg", "h_corners_avg",
        "a_win_pct", "a_ppg", "a_gs_avg", "a_gc_avg", "a_cs_pct",
        "a_over25_pct", "a_btts_pct",
        "a_xg_for_avg", "a_xg_against_avg", "a_xg_diff_avg", "a_overperf_avg",
        "a_sot_avg", "a_shots_avg", "a_corners_avg",
        "xg_diff", "form_diff", "overperf_diff",
        "tier",
    ]

    # Filter valid
    valid = features[feature_cols].notna().all(axis=1)
    console.print(f"[green]Features: {valid.sum():,} valid matches, {len(feature_cols)} features (incl. xG proxy + ELO)[/green]")

    return features[valid][feature_cols].reset_index(drop=True), targets[valid].reset_index(drop=True), feature_cols


def run_backtest(features, targets, feature_cols, test_season, version, description,
                 min_edge_fav=0.05, min_edge_long=0.08, tier_filter=None):
    """Run backtest with model versioning"""
    train_mask = targets["season"] != test_season
    test_mask = targets["season"] == test_season

    if tier_filter:
        test_mask = test_mask & targets["tier"].isin(tier_filter)

    X_train = features[train_mask]
    X_test = features[test_mask].reset_index(drop=True)
    targets_test = targets[test_mask].reset_index(drop=True)

    if len(X_test) == 0:
        console.print(f"[red]No test data for {test_season}[/red]")
        return None

    tier_desc = f" (tiers {tier_filter})" if tier_filter else ""
    console.print(f"\n[bold green]═══ {version}: {test_season}{tier_desc} ═══[/bold green]")
    console.print(f"Train: {len(X_train):,} | Test: {len(X_test):,}")

    # Train models
    # Poisson goal models
    hg_model = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=0.03,
                            subsample=0.8, colsample_bytree=0.7,
                            objective="count:poisson", random_state=42, verbosity=0)
    hg_model.fit(X_train, targets[train_mask]["FTHG"])

    ag_model = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=0.03,
                            subsample=0.8, colsample_bytree=0.7,
                            objective="count:poisson", random_state=42, verbosity=0)
    ag_model.fit(X_train, targets[train_mask]["FTAG"])

    exp_h = np.clip(hg_model.predict(X_test), 0.2, 4.0)
    exp_a = np.clip(ag_model.predict(X_test), 0.2, 4.0)

    # Calibrated 1X2 classifier
    y_result = targets[train_mask]["result"].map({"H": 0, "D": 1, "A": 2})
    result_base = XGBClassifier(n_estimators=200, max_depth=5, learning_rate=0.03,
                                subsample=0.8, colsample_bytree=0.7,
                                objective="multi:softprob", num_class=3,
                                random_state=42, verbosity=0)
    result_model = CalibratedClassifierCV(result_base, cv=5, method="isotonic")
    result_model.fit(X_train, y_result)
    result_proba = result_model.predict_proba(X_test)

    # Calibrated O/U classifier
    y_over = targets[train_mask]["over_25"]
    over_base = XGBClassifier(n_estimators=200, max_depth=5, learning_rate=0.03,
                              subsample=0.8, colsample_bytree=0.7,
                              objective="binary:logistic", random_state=42, verbosity=0)
    over_model = CalibratedClassifierCV(over_base, cv=5, method="isotonic")
    over_model.fit(X_train, y_over)
    over_proba = over_model.predict_proba(X_test)[:, 1]

    # Poisson-derived probabilities
    poisson_over = np.zeros(len(X_test))
    poisson_h = np.zeros(len(X_test))
    poisson_a = np.zeros(len(X_test))

    for i in range(len(X_test)):
        eh, ea = exp_h[i], exp_a[i]
        for h in range(8):
            for a in range(8):
                p = poisson.pmf(h, eh) * poisson.pmf(a, ea)
                if h + a > 2: poisson_over[i] += p
                if h > a: poisson_h[i] += p
                elif h < a: poisson_a[i] += p

    # Ensemble
    ens_over = 0.5 * over_proba + 0.5 * poisson_over
    ens_h = 0.5 * result_proba[:, 0] + 0.5 * poisson_h
    ens_a = 0.5 * result_proba[:, 2] + 0.5 * poisson_a

    # Save models
    model_path = MODELS_DIR / f"{version}_{test_season.replace('-','')}"
    model_path.mkdir(parents=True, exist_ok=True)
    joblib.dump(hg_model, model_path / "home_goals.pkl")
    joblib.dump(ag_model, model_path / "away_goals.pkl")
    joblib.dump(result_model, model_path / "result_1x2.pkl")
    joblib.dump(over_model, model_path / "over_under.pkl")
    joblib.dump(feature_cols, model_path / "feature_cols.pkl")

    # Simulate bets
    bets = []
    stake = 10.0

    for i, row in targets_test.iterrows():
        match = f"{row['home_team']} vs {row['away_team']}"

        # Over 2.5
        odds = row.get("Avg>2.5")
        if pd.notna(odds) and odds > 1.0:
            mp, ip = ens_over[i], 1/odds
            edge = mp - ip
            if edge >= 0.05 and 1.55 <= odds <= 2.60 and mp >= 0.48:
                won = row["over_25"] == 1
                bets.append({"match": match, "market": "O/U", "sel": "Over 2.5",
                             "odds": odds, "mp": mp, "ip": ip, "edge": edge,
                             "W": won, "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"], "date": row["Date"]})

        # Under 2.5
        odds = row.get("Avg<2.5")
        if pd.notna(odds) and odds > 1.0:
            mp, ip = 1-ens_over[i], 1/odds
            edge = mp - ip
            if edge >= 0.05 and 1.55 <= odds <= 2.60 and mp >= 0.48:
                won = row["over_25"] == 0
                bets.append({"match": match, "market": "O/U", "sel": "Under 2.5",
                             "odds": odds, "mp": mp, "ip": ip, "edge": edge,
                             "W": won, "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"], "date": row["Date"]})

        # Home win
        odds = row.get("AvgH") or row.get("B365H")
        if pd.notna(odds) and odds > 1.0:
            mp, ip = ens_h[i], 1/odds
            edge = mp - ip
            me = min_edge_fav if odds < 2.0 else min_edge_long
            if edge >= me and 1.30 <= odds <= 3.50 and mp >= 0.38:
                won = row["result"] == "H"
                bets.append({"match": match, "market": "1X2", "sel": "Home",
                             "odds": odds, "mp": mp, "ip": ip, "edge": edge,
                             "W": won, "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"], "date": row["Date"]})

        # Away win
        odds = row.get("AvgA") or row.get("B365A")
        if pd.notna(odds) and odds > 1.0:
            mp, ip = ens_a[i], 1/odds
            edge = mp - ip
            me = 0.07 if odds < 2.5 else 0.10
            if edge >= me and 1.80 <= odds <= 4.00 and mp >= 0.30:
                won = row["result"] == "A"
                bets.append({"match": match, "market": "1X2", "sel": "Away",
                             "odds": odds, "mp": mp, "ip": ip, "edge": edge,
                             "W": won, "pnl": (odds-1)*stake if won else -stake,
                             "league": row["league"], "tier": row["tier"], "date": row["Date"]})

    if not bets:
        console.print("[red]No bets.[/red]")
        return None

    bets_df = pd.DataFrame(bets)
    total = len(bets_df)
    wins = bets_df["W"].sum()
    total_pnl = bets_df["pnl"].sum()
    roi = total_pnl / (total * stake) * 100

    streak = max_streak = 0
    for w in bets_df["W"]:
        streak = 0 if w else streak + 1
        max_streak = max(max_streak, streak)

    color = "green" if roi > 0 else "red"

    t = Table(title=f"{version} Results: {test_season}{tier_desc}")
    t.add_column("Metric", style="cyan")
    t.add_column("Value", style=color)
    t.add_row("Bets", str(total))
    t.add_row("Hit rate", f"{wins/total:.1%}")
    t.add_row("ROI", f"{roi:+.2f}%")
    t.add_row("P&L", f"EUR {total_pnl:+,.2f}")
    t.add_row("Avg edge", f"{bets_df['edge'].mean():.1%}")
    t.add_row("Avg odds", f"{bets_df['odds'].mean():.2f}")
    t.add_row("Max losing streak", str(max_streak))
    console.print(t)

    # By market
    for market in bets_df["market"].unique():
        m = bets_df[bets_df["market"] == market]
        m_roi = m["pnl"].sum() / (len(m) * stake) * 100
        c = "green" if m_roi > 0 else "red"
        console.print(f"  {market}: {len(m)} bets, {m['W'].mean():.1%} hit, [{c}]{m_roi:+.1f}% ROI[/{c}]")

    # By tier
    for tier in sorted(bets_df["tier"].unique()):
        tb = bets_df[bets_df["tier"] == tier]
        t_roi = tb["pnl"].sum() / (len(tb) * stake) * 100
        c = "green" if t_roi > 0 else "red"
        name = {1: "Top", 2: "2nd", 3: "3rd", 4: "4th"}.get(tier, f"T{tier}")
        console.print(f"  {name}: {len(tb)} bets, {tb['W'].mean():.1%} hit, [{c}]{t_roi:+.1f}% ROI[/{c}]")

    # Calibration
    console.print("  Calibration:")
    for lo, hi in [(0.3, 0.45), (0.45, 0.55), (0.55, 0.70)]:
        b = bets_df[(bets_df["mp"] >= lo) & (bets_df["mp"] < hi)]
        if len(b) >= 10:
            pred, actual = b["mp"].mean(), b["W"].mean()
            diff = actual - pred
            c = "green" if abs(diff) < 0.03 else "yellow" if abs(diff) < 0.07 else "red"
            console.print(f"    {pred:.0%} predicted → {actual:.0%} actual ([{c}]{diff:+.0%}[/{c}], n={len(b)})")

    # Save results
    bets_df.to_csv(RESULTS_DIR / f"{version}_{test_season.replace('-','')}.csv", index=False)

    # Log
    log_path = RESULTS_DIR / "iterations.json"
    log = json.load(open(log_path)) if log_path.exists() else []
    log.append({
        "version": version, "description": description,
        "test_season": test_season, "tier_filter": str(tier_filter),
        "timestamp": datetime.now().isoformat(),
        "total_bets": total, "wins": int(wins), "hit_rate": float(wins/total),
        "roi": roi, "total_pnl": float(total_pnl),
        "avg_edge": float(bets_df["edge"].mean()),
        "avg_odds": float(bets_df["odds"].mean()),
        "max_losing_streak": int(max_streak),
    })
    json.dump(log, open(log_path, "w"), indent=2, default=str)

    verdict = "GO" if roi > 2 and total >= 100 else "PROMISING" if roi > 0 else "CLOSE" if roi > -3 else "NO-GO"
    vc = {"GO": "green", "PROMISING": "yellow", "CLOSE": "yellow", "NO-GO": "red"}[verdict]
    console.print(f"  [bold {vc}]VERDICT: {verdict}[/bold {vc}]")

    return bets_df


def main():
    console.print("[bold green]═══ OddsIntel v9: xG Proxy + ELO ═══[/bold green]\n")

    df = pd.read_csv(PROCESSED_DIR / "all_matches.csv", parse_dates=["Date"], low_memory=False)
    console.print(f"Loaded {len(df):,} matches")

    features, targets, feature_cols = build_features_v9(df)

    # Save features
    features.to_csv(PROCESSED_DIR / "features_v9.csv", index=False)
    targets.to_csv(PROCESSED_DIR / "targets_v9.csv", index=False)

    # Run comprehensive backtests
    tests = [
        # All leagues
        ("v9a", "xG+ELO, all leagues", None),
        # Lower leagues only
        ("v9b", "xG+ELO, tier 2-4 only", [2, 3, 4]),
        # Tier 3-4 only (our best signal from v8)
        ("v9c", "xG+ELO, tier 3-4 only", [3, 4]),
        # Top leagues only (control - should be bad)
        ("v9d", "xG+ELO, tier 1 only", [1]),
    ]

    for version, desc, tier_filter in tests:
        for season in ["2024-25", "2023-24", "2022-23"]:
            if season in targets["season"].values:
                run_backtest(features, targets, feature_cols, season, version, desc,
                             tier_filter=tier_filter)

    console.print(f"\n[green]All models saved to {MODELS_DIR}[/green]")
    console.print(f"[green]All results saved to {RESULTS_DIR}[/green]")


if __name__ == "__main__":
    main()
