"""
OddsIntel — Bot Profile Optimizer
Finds optimal bot configurations by grid-searching parameters against
historical match data (612K+ matches from two datasets).

Approach:
  1. Load both datasets:
     - football-data.co.uk (133K matches, 1X2 + O/U odds, 2005-2025)
     - beat_the_bookie (479K matches, 1X2 only, 2005-2015)
  2. Build rolling features per team, train XGBoost models
  3. Generate model probabilities for every match/market
  4. Grid-search bot parameter combinations (edge, odds, market, tier, region)
  5. Walk-forward validation across seasons to avoid overfitting
  6. Rank by risk-adjusted ROI with confidence intervals

Usage:
  python scripts/bot_optimizer.py                  # Full run
  python scripts/bot_optimizer.py --dataset btb    # Beat-the-bookie only
  python scripts/bot_optimizer.py --dataset fd     # Football-data only
  python scripts/bot_optimizer.py --top 30         # Show top 30 configs
"""

import sys
import argparse
import itertools
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
from xgboost import XGBClassifier

warnings.filterwarnings("ignore", category=FutureWarning)

console = Console()
DATA_DIR = Path(__file__).parent.parent / "data"
PROCESSED_DIR = DATA_DIR / "processed"
RAW_DIR = DATA_DIR / "raw"

STAKE = 10.0


# ═══════════════════════════════════════════════════════════════════════════
# DATASET LOADERS
# ═══════════════════════════════════════════════════════════════════════════

def load_football_data() -> pd.DataFrame:
    """Load football-data.co.uk all_matches.csv (133K, 1X2 + O/U)"""
    path = PROCESSED_DIR / "all_matches.csv"
    if not path.exists():
        console.print("[red]all_matches.csv not found — run import_historical.py first[/red]")
        return pd.DataFrame()

    df = pd.read_csv(path, parse_dates=["Date"], low_memory=False)
    df = df.dropna(subset=["FTHG", "FTAG", "FTR"])
    df["FTHG"] = df["FTHG"].astype(int)
    df["FTAG"] = df["FTAG"].astype(int)
    df["total_goals"] = df["FTHG"] + df["FTAG"]
    df["over_25"] = (df["total_goals"] > 2).astype(int)

    # Extract year for walk-forward splits
    df["year"] = df["Date"].dt.year

    console.print(f"[green]Football-data: {len(df):,} matches, "
                  f"{df['league_name'].nunique()} leagues, "
                  f"{df['season'].nunique()} seasons[/green]")
    return df


def load_beat_the_bookie() -> pd.DataFrame:
    """Load beat_the_bookie closing_odds.csv (479K, 1X2 only)"""
    path = RAW_DIR / "beat_the_bookie" / "closing_odds.csv"
    if not path.exists():
        console.print("[red]closing_odds.csv not found[/red]")
        return pd.DataFrame()

    df = pd.read_csv(path)
    df["match_date"] = pd.to_datetime(df["match_date"])
    df = df.dropna(subset=["home_score", "away_score", "avg_odds_home_win",
                           "avg_odds_draw", "avg_odds_away_win"])
    df["home_score"] = df["home_score"].astype(int)
    df["away_score"] = df["away_score"].astype(int)
    df["total_goals"] = df["home_score"] + df["away_score"]
    df["over_25"] = (df["total_goals"] > 2).astype(int)

    # Result
    df["FTR"] = np.where(df["home_score"] > df["away_score"], "H",
                np.where(df["home_score"] < df["away_score"], "A", "D"))

    # Parse country from league string "Country: League Name"
    df["country"] = df["league"].str.split(":").str[0].str.strip()
    df["league_name"] = df["league"].str.split(":").str[1].str.strip()

    # Rough tier assignment
    df["tier"] = 1  # default
    tier2_keywords = ["championship", "segunda", "serie b", "ligue 2", "2. bundesliga",
                      "eerste", "league one", "division 1", "second", "2nd"]
    tier3_keywords = ["league two", "third", "national", "division 2", "3rd"]
    for kw in tier2_keywords:
        df.loc[df["league_name"].str.lower().str.contains(kw, na=False), "tier"] = 2
    for kw in tier3_keywords:
        df.loc[df["league_name"].str.lower().str.contains(kw, na=False), "tier"] = 3

    # Skip friendlies
    df = df[~df["league"].str.contains("Friendly|Club Friendly", case=False, na=False)]

    df["year"] = df["match_date"].dt.year

    console.print(f"[green]Beat-the-bookie: {len(df):,} matches, "
                  f"{df['league'].nunique()} leagues, "
                  f"{df['country'].nunique()} countries[/green]")
    return df


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE ENGINEERING
# ═══════════════════════════════════════════════════════════════════════════

def build_rolling_features(dates, home_teams, away_teams, home_goals, away_goals,
                           results, league_keys, n=10):
    """
    Build rolling features for any dataset.
    Returns feature DataFrame aligned with input rows.
    """
    n_matches = len(dates)

    # Build per-team history in long format
    records = []
    for i in range(n_matches):
        dt = dates[i]
        # Home entry
        records.append({
            "idx": i, "date": dt, "team": home_teams[i], "venue": "home",
            "goals_scored": home_goals[i], "goals_conceded": away_goals[i],
            "won": 1 if results[i] == "H" else 0,
            "draw": 1 if results[i] == "D" else 0,
            "lost": 1 if results[i] == "A" else 0,
            "clean_sheet": 1 if away_goals[i] == 0 else 0,
            "league_key": league_keys[i],
        })
        # Away entry
        records.append({
            "idx": i, "date": dt, "team": away_teams[i], "venue": "away",
            "goals_scored": away_goals[i], "goals_conceded": home_goals[i],
            "won": 1 if results[i] == "A" else 0,
            "draw": 1 if results[i] == "D" else 0,
            "lost": 1 if results[i] == "H" else 0,
            "clean_sheet": 1 if home_goals[i] == 0 else 0,
            "league_key": league_keys[i],
        })

    long = pd.DataFrame(records)
    long = long.sort_values("date").reset_index(drop=True)

    # Compute rolling stats per team
    feature_dict = {}  # idx -> {feature_name: value}

    for team_name, group in long.groupby("team"):
        g = group.sort_values("date")
        idxs = g["idx"].values
        venues = g["venue"].values

        gs = g["goals_scored"].values.astype(float)
        gc = g["goals_conceded"].values.astype(float)
        won = g["won"].values.astype(float)
        draw = g["draw"].values.astype(float)
        cs = g["clean_sheet"].values.astype(float)
        points = (won * 3 + draw)

        for j in range(len(g)):
            # Look back at previous matches (not including current)
            start = max(0, j - n)
            window = slice(start, j)

            if j < 3:  # need min 3 matches
                continue

            count = j - start
            prefix = "home" if venues[j] == "home" else "away"
            match_idx = idxs[j]

            if match_idx not in feature_dict:
                feature_dict[match_idx] = {}

            feature_dict[match_idx][f"{prefix}_form_goals_scored"] = np.mean(gs[window])
            feature_dict[match_idx][f"{prefix}_form_goals_conceded"] = np.mean(gc[window])
            feature_dict[match_idx][f"{prefix}_form_win_pct"] = np.mean(won[window])
            feature_dict[match_idx][f"{prefix}_form_ppg"] = np.mean(points[window])
            feature_dict[match_idx][f"{prefix}_form_clean_sheet_pct"] = np.mean(cs[window])
            feature_dict[match_idx][f"{prefix}_form_goal_diff"] = (
                np.mean(gs[window]) - np.mean(gc[window])
            )

    features_df = pd.DataFrame.from_dict(feature_dict, orient="index")
    features_df.index.name = "match_idx"

    # Fill missing with neutral values
    feature_cols = [
        "home_form_goals_scored", "home_form_goals_conceded",
        "home_form_win_pct", "home_form_ppg",
        "home_form_clean_sheet_pct", "home_form_goal_diff",
        "away_form_goals_scored", "away_form_goals_conceded",
        "away_form_win_pct", "away_form_ppg",
        "away_form_clean_sheet_pct", "away_form_goal_diff",
    ]

    for col in feature_cols:
        if col not in features_df.columns:
            features_df[col] = np.nan

    return features_df[feature_cols], feature_cols


# ═══════════════════════════════════════════════════════════════════════════
# MODEL TRAINING + PREDICTION GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def generate_predictions_fd(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate predictions for football-data dataset using walk-forward.
    Returns DataFrame with one row per possible bet.
    """
    console.print("\n[bold cyan]═══ Football-Data: Building predictions ═══[/bold cyan]")

    # Build features using the fast vectorized approach from backtest_fast
    from backtest_fast import build_features_fast
    features_df, targets_df, feature_cols = build_features_fast(df)

    # Add country column (not included by backtest_fast)
    if "country" not in targets_df.columns:
        # Align with the valid rows from build_features_fast
        targets_df["country"] = df.loc[targets_df.index, "country"].values if "country" in df.columns else "Unknown"

    # Add extra odds columns for fallback
    for col in ["B365H", "B365D", "B365A", "B365>2.5", "B365<2.5",
                "PSH", "PSD", "PSA", "P>2.5", "P<2.5",
                "BWH", "BWD", "BWA"]:
        if col in df.columns and col not in targets_df.columns:
            targets_df[col] = df.loc[targets_df.index, col].values

    all_bets = []
    seasons = sorted(targets_df["season"].unique())

    # Walk-forward: train on all prior seasons, predict next
    for i, test_season in enumerate(seasons):
        if i < 2:  # need at least 2 seasons to train
            continue

        train_mask = targets_df["season"].isin(seasons[:i])
        test_mask = targets_df["season"] == test_season

        X_train = features_df[train_mask]
        X_test = features_df[test_mask].reset_index(drop=True)
        targets_train = targets_df[train_mask]
        targets_test = targets_df[test_mask].reset_index(drop=True)

        if len(X_test) == 0 or len(X_train) < 500:
            continue

        console.print(f"  Season {test_season}: train={len(X_train):,} test={len(X_test):,}", end=" ")

        # 1X2 model
        y_result = targets_train["result"].map({"H": 0, "D": 1, "A": 2})
        model_1x2 = XGBClassifier(
            n_estimators=150, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            objective="multi:softprob", num_class=3,
            random_state=42, verbosity=0,
        )
        model_1x2.fit(X_train, y_result)
        proba_1x2 = model_1x2.predict_proba(X_test)

        # O/U model
        y_over = targets_train["over_25"]
        model_ou = XGBClassifier(
            n_estimators=150, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            objective="binary:logistic", random_state=42, verbosity=0,
        )
        model_ou.fit(X_train, y_over)
        proba_ou = model_ou.predict_proba(X_test)[:, 1]

        bet_count = 0
        for j, row in targets_test.iterrows():
            base = {
                "date": row["Date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "league": row["league"],
                "country": row.get("country", row.get("league", "Unknown")),
                "tier": row["tier"],
                "season": test_season,
                "source": "fd",
            }

            candidates = []

            def _pick_odds(*cols):
                """Return first non-NaN odds value from the given columns."""
                for c in cols:
                    v = row.get(c)
                    if pd.notna(v) and v > 1.0:
                        return v
                return None

            # 1X2 Home
            odds = _pick_odds("AvgH", "B365H", "PSH", "BWH")
            if odds:
                mp = float(proba_1x2[j][0])
                ip = 1.0 / odds
                candidates.append({**base, "market": "1x2", "selection": "home",
                                   "odds": odds, "model_prob": mp, "implied_prob": ip,
                                   "edge": mp - ip, "won": row["result"] == "H"})

            # 1X2 Draw
            odds = _pick_odds("AvgD", "B365D", "PSD", "BWD")
            if odds:
                mp = float(proba_1x2[j][1])
                ip = 1.0 / odds
                candidates.append({**base, "market": "1x2", "selection": "draw",
                                   "odds": odds, "model_prob": mp, "implied_prob": ip,
                                   "edge": mp - ip, "won": row["result"] == "D"})

            # 1X2 Away
            odds = _pick_odds("AvgA", "B365A", "PSA", "BWA")
            if odds:
                mp = float(proba_1x2[j][2])
                ip = 1.0 / odds
                candidates.append({**base, "market": "1x2", "selection": "away",
                                   "odds": odds, "model_prob": mp, "implied_prob": ip,
                                   "edge": mp - ip, "won": row["result"] == "A"})

            # Over 2.5
            odds = _pick_odds("Avg>2.5", "B365>2.5", "P>2.5")
            if odds:
                mp = float(proba_ou[j])
                ip = 1.0 / odds
                candidates.append({**base, "market": "ou", "selection": "over",
                                   "odds": odds, "model_prob": mp, "implied_prob": ip,
                                   "edge": mp - ip, "won": row["over_25"] == 1})

            # Under 2.5
            odds = _pick_odds("Avg<2.5", "B365<2.5", "P<2.5")
            if odds:
                mp = float(1 - proba_ou[j])
                ip = 1.0 / odds
                candidates.append({**base, "market": "ou", "selection": "under",
                                   "odds": odds, "model_prob": mp, "implied_prob": ip,
                                   "edge": mp - ip, "won": row["over_25"] == 0})

            all_bets.extend(candidates)
            bet_count += len(candidates)

        console.print(f"→ {bet_count:,} potential bets")

    result = pd.DataFrame(all_bets)
    console.print(f"[green]Football-data total: {len(result):,} potential bets[/green]")
    return result


def generate_predictions_btb(df: pd.DataFrame) -> pd.DataFrame:
    """
    Generate predictions for beat_the_bookie dataset using walk-forward.
    1X2 only (no O/U odds in this dataset).
    """
    console.print("\n[bold cyan]═══ Beat-the-Bookie: Building predictions ═══[/bold cyan]")

    # Sort by date
    df = df.sort_values("match_date").reset_index(drop=True)

    # Build features
    console.print("  Building rolling features for 450K+ matches...")
    dates = df["match_date"].values
    home_teams = df["home_team"].values
    away_teams = df["away_team"].values
    home_goals = df["home_score"].values
    away_goals = df["away_score"].values
    results = df["FTR"].values
    league_keys = df["league"].values

    features_df, feature_cols = build_rolling_features(
        dates, home_teams, away_teams, home_goals, away_goals,
        results, league_keys, n=10
    )

    # Align features with original df
    df["tier_val"] = df["tier"]
    features_df = features_df.reindex(range(len(df)))

    # Add tier as feature
    features_df["league_tier"] = df["tier"].values
    feature_cols = feature_cols + ["league_tier"]

    # Position proxy
    features_df["position_diff"] = (
        features_df["home_form_ppg"].fillna(1.3) - features_df["away_form_ppg"].fillna(1.3)
    )
    feature_cols = feature_cols + ["position_diff"]

    # Drop rows without features
    valid_mask = features_df[feature_cols].notna().all(axis=1)
    console.print(f"  Valid matches with features: {valid_mask.sum():,} / {len(df):,}")

    all_bets = []
    years = sorted(df["year"].unique())

    # Walk-forward by year: train on all prior years, predict next
    for i, test_year in enumerate(years):
        if i < 2:
            continue

        train_years = years[:i]
        train_mask = df["year"].isin(train_years) & valid_mask
        test_mask = (df["year"] == test_year) & valid_mask

        X_train = features_df.loc[train_mask, feature_cols]
        X_test = features_df.loc[test_mask, feature_cols].reset_index(drop=True)
        y_train = df.loc[train_mask, "FTR"].map({"H": 0, "D": 1, "A": 2})
        test_rows = df.loc[test_mask].reset_index(drop=True)

        if len(X_test) == 0 or len(X_train) < 1000:
            continue

        console.print(f"  Year {test_year}: train={len(X_train):,} test={len(X_test):,}", end=" ")

        model = XGBClassifier(
            n_estimators=150, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            objective="multi:softprob", num_class=3,
            random_state=42, verbosity=0,
        )
        model.fit(X_train, y_train)
        proba = model.predict_proba(X_test)

        bet_count = 0
        for j in range(len(test_rows)):
            row = test_rows.iloc[j]
            base = {
                "date": row["match_date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "league": row["league_name"],
                "country": row["country"],
                "tier": row["tier"],
                "season": f"{test_year}",
                "source": "btb",
            }

            # Home
            odds = row["avg_odds_home_win"]
            if odds > 1.0:
                mp = float(proba[j][0])
                ip = 1.0 / odds
                all_bets.append({**base, "market": "1x2", "selection": "home",
                                "odds": odds, "model_prob": mp, "implied_prob": ip,
                                "edge": mp - ip, "won": row["FTR"] == "H"})

            # Draw
            odds = row["avg_odds_draw"]
            if odds > 1.0:
                mp = float(proba[j][1])
                ip = 1.0 / odds
                all_bets.append({**base, "market": "1x2", "selection": "draw",
                                "odds": odds, "model_prob": mp, "implied_prob": ip,
                                "edge": mp - ip, "won": row["FTR"] == "D"})

            # Away
            odds = row["avg_odds_away_win"]
            if odds > 1.0:
                mp = float(proba[j][2])
                ip = 1.0 / odds
                all_bets.append({**base, "market": "1x2", "selection": "away",
                                "odds": odds, "model_prob": mp, "implied_prob": ip,
                                "edge": mp - ip, "won": row["FTR"] == "A"})

            bet_count += 3

        console.print(f"→ {bet_count:,} potential bets")

    result = pd.DataFrame(all_bets)
    console.print(f"[green]Beat-the-bookie total: {len(result):,} potential bets[/green]")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# GRID SEARCH
# ═══════════════════════════════════════════════════════════════════════════

# Parameter space
PARAM_GRID = {
    "min_edge": [0.03, 0.05, 0.07, 0.10, 0.13, 0.15, 0.20],
    "odds_min": [1.20, 1.40, 1.60, 2.00, 2.50, 3.00],
    "odds_max": [2.50, 3.00, 3.50, 4.00, 5.00, 8.00, 15.00],
    "market": ["1x2_all", "1x2_home", "1x2_away", "1x2_draw",
               "1x2_home_away", "ou_all", "ou_over", "ou_under", "all"],
    "min_prob": [0.20, 0.25, 0.30, 0.35, 0.40, 0.50],
    "tier": ["all", "t1", "t2+", "t1t2"],
    "region": ["all", "europe_top5", "europe_other", "south_america",
               "asia", "scandinavia", "british_isles"],
}

EUROPE_TOP5 = {"England", "Spain", "Germany", "Italy", "France"}
SCANDINAVIA = {"Sweden", "Norway", "Denmark", "Finland", "Iceland"}
BRITISH_ISLES = {"England", "Scotland", "Ireland", "Wales", "Northern Ireland"}
SOUTH_AMERICA = {"Argentina", "Brazil", "Chile", "Colombia", "Paraguay", "Uruguay",
                 "Peru", "Bolivia", "Ecuador", "Venezuela"}
ASIA = {"Japan", "South Korea", "China", "Australia", "Indonesia", "Thailand",
        "Vietnam", "Singapore", "Malaysia", "India"}


def filter_bets(bets_df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Apply a bot configuration filter to the full bets DataFrame."""
    df = bets_df

    # Edge
    df = df[df["edge"] >= config["min_edge"]]

    # Odds range
    df = df[(df["odds"] >= config["odds_min"]) & (df["odds"] <= config["odds_max"])]

    # Min probability
    df = df[df["model_prob"] >= config["min_prob"]]

    # Market
    mkt = config["market"]
    if mkt == "1x2_all":
        df = df[df["market"] == "1x2"]
    elif mkt == "1x2_home":
        df = df[(df["market"] == "1x2") & (df["selection"] == "home")]
    elif mkt == "1x2_away":
        df = df[(df["market"] == "1x2") & (df["selection"] == "away")]
    elif mkt == "1x2_draw":
        df = df[(df["market"] == "1x2") & (df["selection"] == "draw")]
    elif mkt == "1x2_home_away":
        df = df[(df["market"] == "1x2") & (df["selection"].isin(["home", "away"]))]
    elif mkt == "ou_all":
        df = df[df["market"] == "ou"]
    elif mkt == "ou_over":
        df = df[(df["market"] == "ou") & (df["selection"] == "over")]
    elif mkt == "ou_under":
        df = df[(df["market"] == "ou") & (df["selection"] == "under")]
    # "all" = no filter

    # Tier
    tier = config["tier"]
    if tier == "t1":
        df = df[df["tier"] == 1]
    elif tier == "t2+":
        df = df[df["tier"] >= 2]
    elif tier == "t1t2":
        df = df[df["tier"].isin([1, 2])]

    # Region
    region = config["region"]
    if region == "europe_top5":
        df = df[df["country"].isin(EUROPE_TOP5)]
    elif region == "europe_other":
        df = df[(~df["country"].isin(EUROPE_TOP5)) &
                (~df["country"].isin(SOUTH_AMERICA)) &
                (~df["country"].isin(ASIA))]
    elif region == "south_america":
        df = df[df["country"].isin(SOUTH_AMERICA)]
    elif region == "asia":
        df = df[df["country"].isin(ASIA)]
    elif region == "scandinavia":
        df = df[df["country"].isin(SCANDINAVIA)]
    elif region == "british_isles":
        df = df[df["country"].isin(BRITISH_ISLES)]

    return df


def evaluate_config(filtered_df: pd.DataFrame) -> dict | None:
    """Evaluate a filtered set of bets. Returns metrics or None if insufficient data."""
    n = len(filtered_df)
    if n < 50:
        return None

    wins = filtered_df["won"].sum()
    pnl_series = np.where(
        filtered_df["won"].values,
        (filtered_df["odds"].values - 1) * STAKE,
        -STAKE
    )
    total_pnl = pnl_series.sum()
    roi = total_pnl / (n * STAKE)

    # Hit rate
    hit_rate = wins / n

    # Sharpe-like ratio (monthly returns)
    filtered_df = filtered_df.copy()
    filtered_df["pnl"] = pnl_series
    filtered_df["month"] = pd.to_datetime(filtered_df["date"]).dt.to_period("M")
    monthly = filtered_df.groupby("month")["pnl"].sum()
    sharpe = (monthly.mean() / monthly.std()) if monthly.std() > 0 else 0

    # Max drawdown
    cumulative = np.cumsum(pnl_series)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    max_drawdown = drawdown.max()

    # Avg bets per year
    years = pd.to_datetime(filtered_df["date"]).dt.year.nunique()
    bets_per_year = n / max(years, 1)

    # Longest losing streak
    streak = max_streak = 0
    for w in filtered_df["won"].values:
        streak = 0 if w else streak + 1
        max_streak = max(max_streak, streak)

    # Consistency: % of seasons profitable
    filtered_df["season_key"] = filtered_df["season"]
    season_pnl = filtered_df.groupby("season_key")["pnl"].sum()
    profitable_seasons = (season_pnl > 0).mean() if len(season_pnl) > 0 else 0

    # ROI confidence interval (bootstrap-like approximation)
    roi_se = np.std(pnl_series) / (np.sqrt(n) * STAKE)
    roi_lower = roi - 1.96 * roi_se
    roi_upper = roi + 1.96 * roi_se

    return {
        "n_bets": n,
        "wins": int(wins),
        "hit_rate": hit_rate,
        "total_pnl": total_pnl,
        "roi": roi,
        "roi_lower": roi_lower,
        "roi_upper": roi_upper,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "max_losing_streak": max_streak,
        "bets_per_year": bets_per_year,
        "profitable_seasons_pct": profitable_seasons,
        "avg_odds": filtered_df["odds"].mean(),
        "avg_edge": filtered_df["edge"].mean(),
        "n_seasons": len(season_pnl),
    }


def run_grid_search(bets_df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """
    Vectorized grid search over all parameter combinations.
    Pre-computes boolean masks for each filter dimension, then combines them.
    """
    console.print("\n[bold cyan]═══ Grid Search: Finding optimal bot profiles ═══[/bold cyan]")

    # Pre-filter: only consider bets with positive edge at the loosest threshold
    min_edge_floor = min(PARAM_GRID["min_edge"])
    df = bets_df[bets_df["edge"] >= min_edge_floor].copy().reset_index(drop=True)
    console.print(f"Candidate bets (edge >= {min_edge_floor:.0%}): {len(df):,}")

    has_ou = (df["market"] == "ou").any()
    n = len(df)

    # Pre-extract arrays for speed
    edge_arr = df["edge"].values
    odds_arr = df["odds"].values
    prob_arr = df["model_prob"].values
    won_arr = df["won"].values.astype(bool)
    pnl_arr = np.where(won_arr, (odds_arr - 1) * STAKE, -STAKE)
    market_arr = df["market"].values
    selection_arr = df["selection"].values
    tier_arr = df["tier"].values
    country_arr = df["country"].values
    date_arr = pd.to_datetime(df["date"])
    season_arr = df["season"].values
    month_arr = date_arr.dt.to_period("M")
    year_arr = date_arr.dt.year

    # Pre-compute masks for each dimension value
    console.print("  Pre-computing filter masks...")

    edge_masks = {v: edge_arr >= v for v in PARAM_GRID["min_edge"]}
    odds_min_masks = {v: odds_arr >= v for v in PARAM_GRID["odds_min"]}
    odds_max_masks = {v: odds_arr <= v for v in PARAM_GRID["odds_max"]}
    prob_masks = {v: prob_arr >= v for v in PARAM_GRID["min_prob"]}

    market_masks = {
        "1x2_all": market_arr == "1x2",
        "1x2_home": (market_arr == "1x2") & (selection_arr == "home"),
        "1x2_away": (market_arr == "1x2") & (selection_arr == "away"),
        "1x2_draw": (market_arr == "1x2") & (selection_arr == "draw"),
        "1x2_home_away": (market_arr == "1x2") & np.isin(selection_arr, ["home", "away"]),
        "ou_all": market_arr == "ou",
        "ou_over": (market_arr == "ou") & (selection_arr == "over"),
        "ou_under": (market_arr == "ou") & (selection_arr == "under"),
        "all": np.ones(n, dtype=bool),
    }

    tier_masks = {
        "all": np.ones(n, dtype=bool),
        "t1": tier_arr == 1,
        "t2+": tier_arr >= 2,
        "t1t2": np.isin(tier_arr, [1, 2]),
    }

    region_masks = {
        "all": np.ones(n, dtype=bool),
        "europe_top5": np.isin(country_arr, list(EUROPE_TOP5)),
        "europe_other": (~np.isin(country_arr, list(EUROPE_TOP5)) &
                         ~np.isin(country_arr, list(SOUTH_AMERICA)) &
                         ~np.isin(country_arr, list(ASIA))),
        "south_america": np.isin(country_arr, list(SOUTH_AMERICA)),
        "asia": np.isin(country_arr, list(ASIA)),
        "scandinavia": np.isin(country_arr, list(SCANDINAVIA)),
        "british_isles": np.isin(country_arr, list(BRITISH_ISLES)),
    }

    # Build valid combos
    keys = list(PARAM_GRID.keys())
    all_combos = list(itertools.product(*[PARAM_GRID[k] for k in keys]))

    valid_combos = []
    for combo in all_combos:
        config = dict(zip(keys, combo))
        if config["odds_min"] >= config["odds_max"]:
            continue
        if not has_ou and config["market"] in ("ou_all", "ou_over", "ou_under"):
            continue
        valid_combos.append(config)

    console.print(f"  Testing {len(valid_combos):,} parameter combinations...")

    # Vectorized evaluation
    results = []
    batch_size = 10000
    total = len(valid_combos)

    for batch_start in range(0, total, batch_size):
        batch_end = min(batch_start + batch_size, total)
        if batch_start % 50000 == 0:
            console.print(f"    Progress: {batch_start:,}/{total:,} ({batch_start/total:.0%})")

        for config in valid_combos[batch_start:batch_end]:
            # Combine masks
            mask = (
                edge_masks[config["min_edge"]] &
                odds_min_masks[config["odds_min"]] &
                odds_max_masks[config["odds_max"]] &
                prob_masks[config["min_prob"]] &
                market_masks[config["market"]] &
                tier_masks[config["tier"]] &
                region_masks[config["region"]]
            )

            count = mask.sum()
            if count < 50:
                continue

            # Fast metrics
            m_pnl = pnl_arr[mask]
            m_won = won_arr[mask]
            m_odds = odds_arr[mask]
            m_edge = edge_arr[mask]
            total_pnl = m_pnl.sum()
            roi = total_pnl / (count * STAKE)
            hit_rate = m_won.sum() / count

            # ROI confidence interval
            roi_se = np.std(m_pnl) / (np.sqrt(count) * STAKE)
            roi_lower = roi - 1.96 * roi_se
            roi_upper = roi + 1.96 * roi_se

            # Bets per year
            m_years = year_arr[mask]
            n_years = max(m_years.max() - m_years.min(), 1)
            bets_per_year = count / n_years

            results.append({
                **config,
                "n_bets": int(count),
                "wins": int(m_won.sum()),
                "hit_rate": hit_rate,
                "total_pnl": total_pnl,
                "roi": roi,
                "roi_lower": roi_lower,
                "roi_upper": roi_upper,
                "bets_per_year": bets_per_year,
                "avg_odds": m_odds.mean(),
                "avg_edge": m_edge.mean(),
            })

    console.print(f"    Progress: {total:,}/{total:,} (100%)")

    results_df = pd.DataFrame(results)
    console.print(f"\n[green]Viable configs (50+ bets): {len(results_df):,}[/green]")

    if results_df.empty:
        console.print("[red]No viable configurations found![/red]")
        return results_df

    # Rank by composite score: ROI weighted by volume and statistical significance
    results_df["volume_score"] = np.clip(results_df["bets_per_year"] / 100, 0.3, 1.0)
    # Bonus for roi_lower > 0 (statistically significant)
    results_df["sig_bonus"] = np.where(results_df["roi_lower"] > 0, 1.5, 1.0)
    results_df["composite_score"] = (
        results_df["roi"]
        * results_df["volume_score"]
        * results_df["sig_bonus"]
    )

    results_df = results_df.sort_values("composite_score", ascending=False)

    # Display top N
    display_results(results_df.head(top_n))

    # Save full results
    out_path = PROCESSED_DIR / "bot_optimizer_results.csv"
    results_df.to_csv(out_path, index=False)
    console.print(f"\n[dim]Full results saved to {out_path}[/dim]")

    # Also display some interesting slices
    display_best_per_market(results_df)
    display_best_per_region(results_df)

    return results_df


def display_results(df: pd.DataFrame):
    """Display top bot configurations as a rich table."""
    t = Table(title="Top Bot Configurations (ranked by composite score)")
    t.add_column("#", style="dim", justify="right")
    t.add_column("Market")
    t.add_column("Edge", justify="right")
    t.add_column("Odds Range")
    t.add_column("Min Prob", justify="right")
    t.add_column("Tier")
    t.add_column("Region")
    t.add_column("Bets", justify="right")
    t.add_column("Bets/yr", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("ROI 95% CI", justify="right")
    t.add_column("P&L", justify="right")
    for rank, (_, row) in enumerate(df.iterrows(), 1):
        roi_color = "green" if row["roi"] > 0 else "red"
        ci_color = "green" if row["roi_lower"] > 0 else "yellow" if row["roi"] > 0 else "red"

        t.add_row(
            str(rank),
            row["market"],
            f"{row['min_edge']:.0%}",
            f"{row['odds_min']:.1f}–{row['odds_max']:.1f}",
            f"{row['min_prob']:.0%}",
            row["tier"],
            row["region"],
            f"{row['n_bets']:,}",
            f"{row['bets_per_year']:.0f}",
            f"{row['hit_rate']:.1%}",
            f"[{roi_color}]{row['roi']:+.1%}[/{roi_color}]",
            f"[{ci_color}]{row['roi_lower']:+.1%} to {row['roi_upper']:+.1%}[/{ci_color}]",
            f"[{roi_color}]{row['total_pnl']:+,.0f}[/{roi_color}]",
        )

    console.print(t)


def display_best_per_market(df: pd.DataFrame):
    """Show best config per market type."""
    t = Table(title="Best Config Per Market")
    t.add_column("Market")
    t.add_column("Edge")
    t.add_column("Odds")
    t.add_column("Tier")
    t.add_column("Region")
    t.add_column("ROI", justify="right")
    t.add_column("Bets", justify="right")
    for market in df["market"].unique():
        best = df[df["market"] == market].iloc[0]
        c = "green" if best["roi"] > 0 else "red"
        t.add_row(
            market,
            f"{best['min_edge']:.0%}",
            f"{best['odds_min']:.1f}–{best['odds_max']:.1f}",
            best["tier"],
            best["region"],
            f"[{c}]{best['roi']:+.1%}[/{c}]",
            f"{best['n_bets']:,}",
        )

    console.print(t)


def display_best_per_region(df: pd.DataFrame):
    """Show best config per region."""
    t = Table(title="Best Config Per Region")
    t.add_column("Region")
    t.add_column("Market")
    t.add_column("Edge")
    t.add_column("Odds")
    t.add_column("ROI", justify="right")
    t.add_column("Bets", justify="right")
    for region in df["region"].unique():
        region_df = df[df["region"] == region]
        if region_df.empty:
            continue
        best = region_df.iloc[0]
        c = "green" if best["roi"] > 0 else "red"
        t.add_row(
            region,
            best["market"],
            f"{best['min_edge']:.0%}",
            f"{best['odds_min']:.1f}–{best['odds_max']:.1f}",
            f"[{c}]{best['roi']:+.1%}[/{c}]",
            f"{best['n_bets']:,}",
        )

    console.print(t)


def generate_bot_configs(results_df: pd.DataFrame, top_n: int = 5):
    """Generate ready-to-paste BOTS_CONFIG entries from top results."""
    console.print(f"\n[bold cyan]═══ Suggested Bot Configurations (top {top_n}) ═══[/bold cyan]")

    # Pick diverse configs: best overall + best per key market
    selected = []
    seen_markets = set()

    for _, row in results_df.iterrows():
        if len(selected) >= top_n:
            break

        # Skip if we already have a very similar config
        key = (row["market"], row["tier"], row["region"])
        if key in seen_markets:
            continue

        # Skip if ROI lower bound is negative (not statistically significant)
        if row["roi_lower"] < -0.02:
            continue

        seen_markets.add(key)
        selected.append(row)

    for i, row in enumerate(selected):
        name = f"bot_opt_{i+1}_{row['market']}_{row['region']}"
        name = name.replace(" ", "_").lower()

        markets_list = []
        if "1x2" in row["market"]:
            markets_list.append('"1x2"')
        if "ou" in row["market"] or row["market"] == "all":
            markets_list.append('"ou"')

        tier_filter = "None"
        if row["tier"] == "t1":
            tier_filter = "[1]"
        elif row["tier"] == "t2+":
            tier_filter = "[2, 3, 4]"
        elif row["tier"] == "t1t2":
            tier_filter = "[1, 2]"

        console.print(f"""
[bold yellow]"{name}"[/bold yellow]: {{
    "description": "Optimizer-found: {row['market']} {row['region']} — "
                   "ROI {row['roi']:+.1%}, {row['n_bets']:,} bets",
    "markets": [{', '.join(markets_list)}],
    "tier_filter": {tier_filter},
    "edge_thresholds": {{
        1: {{"1x2_fav": {row['min_edge']:.2f}, "1x2_long": {row['min_edge']:.2f}, "ou": {row['min_edge']:.2f}}},
        2: {{"1x2_fav": {row['min_edge']:.2f}, "1x2_long": {row['min_edge']:.2f}, "ou": {row['min_edge']:.2f}}},
    }},
    "odds_range": ({row['odds_min']:.2f}, {row['odds_max']:.2f}),
    "min_prob": {row['min_prob']:.2f},
    # ROI: {row['roi']:+.1%} | CI: [{row['roi_lower']:+.1%}, {row['roi_upper']:+.1%}]
    # Hit rate: {row['hit_rate']:.1%} | Bets/year: {row['bets_per_year']:.0f}
}},""")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="OddsIntel Bot Profile Optimizer")
    parser.add_argument("--dataset", choices=["all", "fd", "btb"], default="all",
                        help="Which dataset to use (default: all)")
    parser.add_argument("--top", type=int, default=20,
                        help="Number of top configs to display (default: 20)")
    parser.add_argument("--cache", action="store_true",
                        help="Use cached predictions if available")
    args = parser.parse_args()

    console.print("[bold]OddsIntel Bot Profile Optimizer[/bold]")
    console.print(f"Using dataset: {args.dataset}\n")

    cache_path = PROCESSED_DIR / "optimizer_all_bets.parquet"

    if args.cache and cache_path.exists():
        console.print(f"[yellow]Loading cached predictions from {cache_path}[/yellow]")
        all_bets = pd.read_parquet(cache_path)
    else:
        all_bets_parts = []

        if args.dataset in ("all", "fd"):
            fd_df = load_football_data()
            if not fd_df.empty:
                fd_bets = generate_predictions_fd(fd_df)
                all_bets_parts.append(fd_bets)

        if args.dataset in ("all", "btb"):
            btb_df = load_beat_the_bookie()
            if not btb_df.empty:
                btb_bets = generate_predictions_btb(btb_df)
                all_bets_parts.append(btb_bets)

        if not all_bets_parts:
            console.print("[red]No data loaded![/red]")
            sys.exit(1)

        all_bets = pd.concat(all_bets_parts, ignore_index=True)

        # Cache for re-runs
        all_bets.to_parquet(cache_path)
        console.print(f"[dim]Predictions cached to {cache_path}[/dim]")

    console.print(f"\n[bold]Total potential bets: {len(all_bets):,}[/bold]")
    console.print(f"  Sources: {all_bets['source'].value_counts().to_dict()}")
    console.print(f"  Markets: {all_bets['market'].value_counts().to_dict()}")
    console.print(f"  Date range: {all_bets['date'].min()} to {all_bets['date'].max()}")

    # Run grid search
    results_df = run_grid_search(all_bets, top_n=args.top)

    if not results_df.empty:
        generate_bot_configs(results_df, top_n=5)


if __name__ == "__main__":
    main()
