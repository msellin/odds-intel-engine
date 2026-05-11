"""
OddsIntel — Model Training
Trains prediction models for:
  1. Match result (1X2) — XGBoost classifier → result_1x2.pkl
  2. Over/Under 2.5 goals — XGBoost classifier → over_under.pkl
  3. BTTS (Both Teams To Score) — XGBoost classifier → btts.pkl

Output paths match what `xgboost_ensemble.py:_load_models()` reads, so the
trained model can be activated by setting `MODEL_VERSION=<version>` in env.

  data/models/soccer/<version>/
    feature_cols.pkl    — list of column names the model expects
    result_1x2.pkl      — 3-class home/draw/away
    over_under.pkl      — binary over 2.5
    btts.pkl            — binary both-teams-to-score
    home_goals.pkl      — (TODO) Poisson regression
    away_goals.pkl      — (TODO) Poisson regression

NOTE: home_goals + away_goals regression models are not trained here; the
production loader still falls back to v9a_202425 for those when missing.
Adding them is tracked in unified-ml-pipeline-tasks.md (Stage 1c).

Column names match match_feature_vectors exactly. Callers must provide:
  features_df: columns from FEATURE_COLS (allow NaN — dropped per model)
  targets_df:  match_outcome (H/D/A), over_25 (bool), btts (bool)
               btts = score_home > 0 AND score_away > 0 — compute at load time
"""

import argparse
import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from xgboost import XGBClassifier, XGBRegressor
from rich.console import Console
from rich.table import Table

console = Console()


# Columns where missingness is informative (not just "we don't have the data
# yet"). For these we add a `<col>_missing` indicator alongside per-league mean
# imputation, so the model can learn from the *pattern* of missingness — e.g.
# H2H is missing for newly-promoted pairings, opening odds are missing for
# pre-Q2-2026 matches the engine wasn't watching.
INFORMATIVE_MISSING_COLS = [
    "h2h_win_pct",
    "opening_implied_home", "opening_implied_draw", "opening_implied_away",
    "bookmaker_disagreement",
    "referee_cards_avg", "referee_home_win_pct", "referee_over25_pct",
    # Pinnacle (v11+) — coverage is sparse so the indicator carries the bulk
    # of the signal early on, with the imputed value useful where present.
    "pinnacle_implied_home", "pinnacle_implied_draw", "pinnacle_implied_away",
    # OU market features (v14+) — Pinnacle OU 2.5 coverage ~22%; BTTS multi-book
    # covers ~30%; disagreement follows the same sparse-but-informative pattern.
    "pinnacle_implied_over25", "pinnacle_implied_under25",
    "ou25_bookmaker_disagreement", "market_implied_btts_yes",
]


def _impute_features(features_df: pd.DataFrame, league_col: pd.Series | None) -> tuple[pd.DataFrame, list[str]]:
    """Per-league mean imputation + indicator columns for INFORMATIVE_MISSING_COLS.

    Stage 2a — replaces the prior `X.notna().all(axis=1)` row-drop, which was
    losing ~30-40% of rows because H2H is structurally missing for promoted
    teams. Strategy: per-league mean for numeric, fall back to global mean for
    leagues with no observations of that feature; add `<col>_missing` flag
    alongside informative columns so the model can split on missingness.

    Returns (imputed_df, augmented_feature_cols).
    """
    df = features_df.copy()

    # Indicator columns first — must compute BEFORE imputation overwrites NaNs.
    for col in INFORMATIVE_MISSING_COLS:
        if col in df.columns:
            df[f"{col}_missing"] = df[col].isna().astype(int)

    augmented_cols = list(df.columns)

    # Per-league mean for the original numeric columns (skip indicators).
    if league_col is not None and not league_col.empty:
        for col in features_df.columns:
            if df[col].isna().any():
                league_means = df.groupby(league_col)[col].transform("mean")
                df[col] = df[col].fillna(league_means)

    # Global mean as final fallback for any leagues without observations.
    for col in features_df.columns:
        if df[col].isna().any():
            df[col] = df[col].fillna(df[col].mean())

    # Last-resort 0 fill for fully-empty columns (no observations at all). Rare,
    # but stops downstream `np.isnan` checks in xgboost from blowing up.
    df = df.fillna(0.0)

    return df, augmented_cols


def _prepare_xy(features_df: pd.DataFrame, target: pd.Series, league_col: pd.Series | None):
    """Apply imputation, drop only rows where the *target* is NaN.

    Row-drop on features is removed in Stage 2a — we keep every row with a
    valid target and impute its feature gaps. Returns (X, y, augmented_feature_cols).
    """
    # Use whatever columns the caller supplied — caller controls the base set
    # (FEATURE_COLS for v10, FEATURE_COLS + PINNACLE_FEATURE_COLS for v11+).
    X = features_df.copy()
    n_input = len(X)

    X_imp, augmented_cols = _impute_features(X, league_col)

    target_valid = target.notna()
    X_clean = X_imp[target_valid]
    y_clean = target[target_valid]

    n_dropped = n_input - len(X_clean)
    if n_dropped:
        console.print(f"  [dim]Dropped {n_dropped} rows for missing target ({n_dropped/n_input:.1%}).[/dim]")
    return X_clean, y_clean, augmented_cols

# Output to data/models/soccer/<version>/ — the same root xgboost_ensemble.py
# loads from. Versioned subdir lets us train v10 without overwriting v9a.
DEFAULT_OUTPUT_ROOT = Path(__file__).parent.parent.parent / "data" / "models" / "soccer"

# Features used by the model — must match match_feature_vectors column names exactly
FEATURE_COLS = [
    # ELO
    "elo_home", "elo_away", "elo_diff",
    # Form
    "form_ppg_home", "form_ppg_away",
    # Goals
    "goals_for_avg_home", "goals_for_avg_away",
    "goals_against_avg_home", "goals_against_avg_away",
    # Position / standings
    "league_position_home", "league_position_away",
    "points_to_relegation_home", "points_to_relegation_away",
    "points_to_title_home", "points_to_title_away",
    # H2H
    "h2h_win_pct",
    # Rest
    "rest_days_home", "rest_days_away",
    # Injuries / news
    "injury_count_home", "injury_count_away",
    # Match context
    "fixture_importance",
    # Referee
    "referee_cards_avg", "referee_home_win_pct", "referee_over25_pct",
    # Market
    "opening_implied_home", "opening_implied_draw", "opening_implied_away",
    "bookmaker_disagreement",
    # League
    "league_tier",
]


def train_result_model(features_df: pd.DataFrame, targets_df: pd.DataFrame, output_dir: Path | None = None):
    """Train 1X2 match result prediction model"""
    console.print("\n[bold cyan]Training 1X2 Result Model[/bold cyan]")

    league_col = targets_df["league_tier"] if "league_tier" in targets_df else features_df.get("league_tier")
    # MFV stores match_outcome as 'home'/'draw'/'away' (lowercase); legacy callers
    # may still pass 'H'/'D'/'A'. Map both.
    outcome_map = {"home": 0, "draw": 1, "away": 2, "H": 0, "D": 1, "A": 2}
    y = targets_df["match_outcome"].map(outcome_map)
    X, y, augmented_cols = _prepare_xy(features_df, y, league_col)

    console.print(f"Training samples: {len(X):,}")

    # Time-series split (don't leak future data)
    tscv = TimeSeriesSplit(n_splits=5)

    accuracies = []
    log_losses = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        model = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="multi:softprob",
            num_class=3,
            eval_metric="mlogloss",
            random_state=42,
            verbosity=0,
        )

        model.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  verbose=False)

        preds = model.predict(X_val)
        proba = model.predict_proba(X_val)

        acc = accuracy_score(y_val, preds)
        ll = log_loss(y_val, proba)
        accuracies.append(acc)
        log_losses.append(ll)

        console.print(f"  Fold {fold+1}: accuracy={acc:.3f}, log_loss={ll:.4f}")

    console.print(f"\n  [green]Mean accuracy: {np.mean(accuracies):.3f}[/green]")
    console.print(f"  [green]Mean log_loss: {np.mean(log_losses):.4f}[/green]")

    # Train final model on all data with calibration
    final_model = XGBClassifier(
        n_estimators=200,
        max_depth=6,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        random_state=42,
        verbosity=0,
    )

    # XGBoost multi:softprob already outputs calibrated probabilities.
    # Platt scaling is applied at inference — no double-calibration here.
    final_model.fit(X, y, verbose=False)

    out_dir = output_dir or DEFAULT_OUTPUT_ROOT / "untagged"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "result_1x2.pkl"
    joblib.dump(final_model, model_path)
    console.print(f"  Saved to: {model_path}")

    # Feature importance
    importance = pd.Series(
        final_model.feature_importances_,
        index=augmented_cols
    ).sort_values(ascending=False)

    imp_table = Table(title="Top 10 Features (1X2 Model)")
    imp_table.add_column("Feature", style="cyan")
    imp_table.add_column("Importance", style="green", justify="right")

    for feat, imp in importance.head(10).items():
        imp_table.add_row(feat, f"{imp:.4f}")

    console.print(imp_table)

    return final_model


def train_over25_model(features_df: pd.DataFrame, targets_df: pd.DataFrame, output_dir: Path | None = None):
    """Train Over/Under 2.5 goals prediction model"""
    console.print("\n[bold cyan]Training Over/Under 2.5 Model[/bold cyan]")

    league_col = targets_df["league_tier"] if "league_tier" in targets_df else features_df.get("league_tier")
    X, y, _ = _prepare_xy(features_df, targets_df["over_25"], league_col)

    console.print(f"Training samples: {len(X):,}")
    console.print(f"Over 2.5 rate: {y.mean():.1%}")

    tscv = TimeSeriesSplit(n_splits=5)
    accuracies = []
    brier_scores = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        model = XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="binary:logistic",
            eval_metric="logloss",
            random_state=42,
            verbosity=0,
        )

        model.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  verbose=False)

        preds = model.predict(X_val)
        proba = model.predict_proba(X_val)[:, 1]

        acc = accuracy_score(y_val, preds)
        brier = brier_score_loss(y_val, proba)
        accuracies.append(acc)
        brier_scores.append(brier)

        console.print(f"  Fold {fold+1}: accuracy={acc:.3f}, brier={brier:.4f}")

    console.print(f"\n  [green]Mean accuracy: {np.mean(accuracies):.3f}[/green]")
    console.print(f"  [green]Mean brier score: {np.mean(brier_scores):.4f}[/green]")

    # Final calibrated model
    final_model = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
        verbosity=0,
    )

    final_model.fit(X, y, verbose=False)

    out_dir = output_dir or DEFAULT_OUTPUT_ROOT / "untagged"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "over_under.pkl"
    joblib.dump(final_model, model_path)
    console.print(f"  Saved to: {model_path}")

    return final_model


def train_btts_model(features_df: pd.DataFrame, targets_df: pd.DataFrame, output_dir: Path | None = None):
    """Train BTTS (Both Teams To Score) prediction model"""
    console.print("\n[bold cyan]Training BTTS Model[/bold cyan]")

    league_col = targets_df["league_tier"] if "league_tier" in targets_df else features_df.get("league_tier")
    X, y, _ = _prepare_xy(features_df, targets_df["btts"], league_col)

    console.print(f"Training samples: {len(X):,}")
    console.print(f"BTTS rate: {y.mean():.1%}")

    tscv = TimeSeriesSplit(n_splits=5)
    accuracies = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]

        model = XGBClassifier(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="binary:logistic",
            eval_metric="logloss",
            random_state=42,
            verbosity=0,
        )

        model.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  verbose=False)

        preds = model.predict(X_val)
        acc = accuracy_score(y_val, preds)
        accuracies.append(acc)
        console.print(f"  Fold {fold+1}: accuracy={acc:.3f}")

    console.print(f"\n  [green]Mean accuracy: {np.mean(accuracies):.3f}[/green]")

    final_model = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="binary:logistic", eval_metric="logloss",
        random_state=42, verbosity=0,
    )

    final_model.fit(X, y, verbose=False)

    out_dir = output_dir or DEFAULT_OUTPUT_ROOT / "untagged"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "btts.pkl"
    joblib.dump(final_model, model_path)
    console.print(f"  Saved to: {model_path}")

    return final_model


def _train_goals_regressor(features_df: pd.DataFrame, targets_df: pd.DataFrame,
                            target_col: str, output_dir: Path | None,
                            label: str, output_name: str):
    """Shared core for home/away goals Poisson regression.

    XGBoost's `count:poisson` objective predicts λ for the side, used by
    `xgboost_ensemble.py:_predict_goals` to derive goals + the over-line
    probabilities. Both v9a_202425 and v10+ ship these so the bundle is
    self-contained.
    """
    console.print(f"\n[bold cyan]Training {label} Poisson regressor[/bold cyan]")

    league_col = targets_df["league_tier"] if "league_tier" in targets_df else features_df.get("league_tier")
    X, y, _ = _prepare_xy(features_df, targets_df[target_col].astype(float), league_col)

    console.print(f"Training samples: {len(X):,}")
    console.print(f"Mean {target_col}: {y.mean():.2f}")

    tscv = TimeSeriesSplit(n_splits=5)
    rmses = []
    poisson_devs = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]
        m = XGBRegressor(
            n_estimators=200,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="count:poisson",
            eval_metric="poisson-nloglik",
            random_state=42,
            verbosity=0,
        )
        m.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
        preds = np.clip(m.predict(X_val), 1e-6, None)
        rmse = float(np.sqrt(np.mean((preds - y_val.values) ** 2)))
        # Poisson deviance: 2 * sum(y*log(y/mu) - (y - mu)). Lower is better.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(y_val.values > 0, y_val.values * np.log(y_val.values / preds), 0.0)
        dev = float(2 * np.mean(ratio - (y_val.values - preds)))
        rmses.append(rmse)
        poisson_devs.append(dev)
        console.print(f"  Fold {fold+1}: rmse={rmse:.3f}, poisson_dev={dev:.4f}")

    console.print(f"\n  [green]Mean rmse: {np.mean(rmses):.3f}[/green]")
    console.print(f"  [green]Mean poisson_dev: {np.mean(poisson_devs):.4f}[/green]")

    final = XGBRegressor(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="count:poisson",
        eval_metric="poisson-nloglik",
        random_state=42,
        verbosity=0,
    )
    final.fit(X, y, verbose=False)

    out_dir = output_dir or DEFAULT_OUTPUT_ROOT / "untagged"
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / output_name
    joblib.dump(final, model_path)
    console.print(f"  Saved to: {model_path}")
    return final


def train_home_goals_model(features_df: pd.DataFrame, targets_df: pd.DataFrame, output_dir: Path | None = None):
    return _train_goals_regressor(features_df, targets_df, "score_home", output_dir,
                                   "Home Goals", "home_goals.pkl")


def train_away_goals_model(features_df: pd.DataFrame, targets_df: pd.DataFrame, output_dir: Path | None = None):
    return _train_goals_regressor(features_df, targets_df, "score_away", output_dir,
                                   "Away Goals", "away_goals.pkl")


PINNACLE_FEATURE_COLS = ["pinnacle_implied_home", "pinnacle_implied_draw", "pinnacle_implied_away"]

OU_MARKET_FEATURE_COLS = [
    "pinnacle_implied_over25", "pinnacle_implied_under25",
    "ou25_bookmaker_disagreement", "market_implied_btts_yes",
]


def _load_pinnacle_features() -> pd.DataFrame:
    """Per-match Pinnacle pre-match 1X2 implied probabilities.

    Looked up directly from odds_snapshots — MFV's `market_implied_*` is a
    multi-bookmaker consensus; Pinnacle is the sharp book and worth a
    dedicated lookup. Coverage is thin (~5% of finished matches as of
    2026-05-10) — Stage 2a's `_missing` indicator handles the rest.

    Takes the LATEST pre-kickoff Pinnacle snapshot per (match_id, selection),
    then implied prob = 1/odds. Overround is left in deliberately — its size
    itself is informative (Pinnacle widens its margin on uncertain matches).
    """
    from workers.api_clients.supabase_client import execute_query

    sql = """
    WITH latest AS (
        SELECT DISTINCT ON (os.match_id, os.selection)
               os.match_id, os.selection, os.odds
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.bookmaker = 'Pinnacle'
          AND os.market = '1x2'
          AND os.is_live = false
          AND os.timestamp < m.date
        ORDER BY os.match_id, os.selection, os.timestamp DESC
    )
    SELECT match_id,
           MAX(CASE WHEN selection = 'home' THEN 1.0/odds END) AS pinnacle_implied_home,
           MAX(CASE WHEN selection = 'draw' THEN 1.0/odds END) AS pinnacle_implied_draw,
           MAX(CASE WHEN selection = 'away' THEN 1.0/odds END) AS pinnacle_implied_away
    FROM latest
    GROUP BY match_id
    """
    rows = execute_query(sql, ())
    return pd.DataFrame(rows)


def _load_ou_market_features() -> pd.DataFrame:
    """Per-match OU 2.5 and BTTS market features from odds_snapshots.

    Three separate queries merged in Python (one combined CTE can timeout on
    the full historical dataset):
      1. Pinnacle OU 2.5 implied over/under — latest pre-KO snapshot, with
         overround guard (1/over + 1/under < 1.10) to drop the 2.4% bad pairs.
      2. OU 2.5 bookmaker disagreement — max-min implied_over across distinct
         books (blacklist-filtered: api-football, api-football-live, William Hill).
      3. Market implied BTTS yes — avg 1/yes_odds across distinct bookmakers.

    All columns are NaN for matches without the relevant data — Stage 2a
    `_missing` indicators handle the gaps.
    """
    from workers.api_clients.supabase_client import execute_query

    pin_sql = """
    WITH latest AS (
        SELECT DISTINCT ON (os.match_id, os.selection)
               os.match_id, os.selection, os.odds
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.bookmaker = 'Pinnacle'
          AND os.market = 'over_under_25'
          AND os.is_live = false
          AND os.timestamp < m.date
        ORDER BY os.match_id, os.selection, os.timestamp DESC
    )
    SELECT match_id,
           1.0 / MAX(CASE WHEN selection = 'over'  THEN odds END) AS pinnacle_implied_over25,
           1.0 / MAX(CASE WHEN selection = 'under' THEN odds END) AS pinnacle_implied_under25
    FROM latest
    GROUP BY match_id
    HAVING COUNT(DISTINCT selection) = 2
       AND (1.0 / MAX(CASE WHEN selection = 'over'  THEN odds END)
          + 1.0 / MAX(CASE WHEN selection = 'under' THEN odds END)) < 1.10
    """

    disagree_sql = """
    WITH latest_per_book AS (
        SELECT DISTINCT ON (os.match_id, os.bookmaker)
               os.match_id, 1.0/os.odds AS implied_over
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.market = 'over_under_25'
          AND os.selection = 'over'
          AND os.is_live = false
          AND os.timestamp < m.date
          AND os.bookmaker NOT IN ('api-football', 'api-football-live', 'William Hill')
        ORDER BY os.match_id, os.bookmaker, os.timestamp DESC
    )
    SELECT match_id,
           ROUND((MAX(implied_over) - MIN(implied_over))::numeric, 4) AS ou25_bookmaker_disagreement
    FROM latest_per_book
    GROUP BY match_id
    HAVING COUNT(*) >= 2
    """

    btts_sql = """
    WITH latest_per_book AS (
        SELECT DISTINCT ON (os.match_id, os.bookmaker)
               os.match_id, 1.0/os.odds AS implied_yes
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE os.market = 'btts'
          AND os.selection = 'yes'
          AND os.is_live = false
          AND os.timestamp < m.date
        ORDER BY os.match_id, os.bookmaker, os.timestamp DESC
    )
    SELECT match_id,
           ROUND(AVG(implied_yes)::numeric, 4) AS market_implied_btts_yes
    FROM latest_per_book
    GROUP BY match_id
    """

    dfs = []
    for sql, label in [(pin_sql, "Pinnacle OU 2.5"), (disagree_sql, "OU 2.5 disagree"), (btts_sql, "BTTS yes")]:
        rows = execute_query(sql, ())
        if rows:
            dfs.append(pd.DataFrame(rows).set_index("match_id"))
            console.print(f"  [dim]OU market features — {label}: {len(rows):,} matches[/dim]")

    if not dfs:
        return pd.DataFrame()

    result = dfs[0]
    for other in dfs[1:]:
        result = result.join(other, how="outer")
    return result.reset_index()


def load_training_data(include_pinnacle: bool = False,
                       include_ou_market: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load match_feature_vectors rows with completed outcomes from the DB.

    Returns (features_df, targets_df) sorted by match_date ascending.
    btts is derived from match scores joined from the matches table.

    `include_pinnacle=True` left-joins per-match Pinnacle pre-match 1X2
    implied probabilities. Caller must extend FEATURE_COLS with
    PINNACLE_FEATURE_COLS before training (used for v11+).

    `include_ou_market=True` left-joins Pinnacle OU 2.5 + multi-book BTTS
    features from odds_snapshots. Caller must extend with OU_MARKET_FEATURE_COLS
    (used for v14+).
    """
    from workers.api_clients.supabase_client import execute_query

    rows = execute_query(
        """
        SELECT
            mfv.*,
            m.score_home,
            m.score_away
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        WHERE mfv.match_outcome IS NOT NULL
          AND mfv.match_date IS NOT NULL
        ORDER BY mfv.match_date ASC
        """,
        (),
    )

    if not rows:
        raise ValueError("No completed matches in match_feature_vectors yet")

    df = pd.DataFrame(rows)

    # Derive btts from raw scores (not stored in MFV)
    df["btts"] = (
        (pd.to_numeric(df["score_home"], errors="coerce") > 0) &
        (pd.to_numeric(df["score_away"], errors="coerce") > 0)
    )

    if include_pinnacle:
        pin = _load_pinnacle_features()
        if not pin.empty:
            df = df.merge(pin, on="match_id", how="left")
            console.print(
                f"[dim]Joined Pinnacle features for {pin.shape[0]:,} matches "
                f"({pin.shape[0] / len(df) * 100:.1f}% coverage)[/dim]"
            )

    if include_ou_market:
        ou = _load_ou_market_features()
        if not ou.empty:
            df = df.merge(ou, on="match_id", how="left")
            console.print(
                f"[dim]Joined OU market features for {ou.shape[0]:,} matches "
                f"({ou.shape[0] / len(df) * 100:.1f}% coverage)[/dim]"
            )

    feature_cols = (FEATURE_COLS
                    + (PINNACLE_FEATURE_COLS if include_pinnacle else [])
                    + (OU_MARKET_FEATURE_COLS if include_ou_market else []))
    features_df = df[feature_cols].copy()
    # Postgres NUMERIC columns come back as decimal.Decimal which pandas can't
    # `.mean()` mixed with float NaNs. Coerce all features to float64 so the
    # imputation helper has a uniform dtype to work with.
    for col in feature_cols:
        features_df[col] = pd.to_numeric(features_df[col], errors="coerce")

    # Carry score_home/score_away through to targets so the goal regressors
    # (1c) can train on the same dataframe. league_tier is duplicated into
    # targets so the imputation helper can group on it without joining.
    target_cols = ["match_outcome", "over_25", "btts", "score_home", "score_away"]
    if "league_tier" in df.columns:
        target_cols.append("league_tier")
    targets_df = df[target_cols].copy()
    targets_df["score_home"] = pd.to_numeric(targets_df["score_home"], errors="coerce")
    targets_df["score_away"] = pd.to_numeric(targets_df["score_away"], errors="coerce")

    console.print(f"Loaded {len(df):,} completed matches for training")
    return features_df, targets_df


def train_all(version: str = "untagged",
              features_df: pd.DataFrame | None = None,
              targets_df: pd.DataFrame | None = None,
              output_root: Path | None = None,
              include_pinnacle: bool = False,
              include_ou_market: bool = False):
    """Train all three models. If called with no args, loads data from DB automatically.

    Writes to output_root/<version>/ — defaults to data/models/soccer/<version>/.
    Filenames match what xgboost_ensemble.py:_load_models() reads, so setting
    `MODEL_VERSION=<version>` activates the freshly trained set in production.

    `include_pinnacle=True` adds Pinnacle pre-match 1X2 implied probs as
    features (used for v11+ bundles). Coverage is sparse so the indicator
    columns from Stage 2a do most of the work.

    `include_ou_market=True` adds Pinnacle OU 2.5 + multi-book BTTS implied
    probs + OU 2.5 bookmaker disagreement (used for v14+ bundles).
    """
    if features_df is None or targets_df is None:
        features_df, targets_df = load_training_data(
            include_pinnacle=include_pinnacle,
            include_ou_market=include_ou_market,
        )

    output_dir = (output_root or DEFAULT_OUTPUT_ROOT) / version
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold green]═══ OddsIntel Model Training — version={version} ═══[/bold green]")
    console.print(f"Output: {output_dir}")

    result_model = train_result_model(features_df, targets_df, output_dir)
    over25_model = train_over25_model(features_df, targets_df, output_dir)
    btts_model = train_btts_model(features_df, targets_df, output_dir)

    # Stage 1c — goals regressors train inline so the version bundle is
    # self-contained. xgboost_ensemble.py loads home_goals/away_goals for the
    # Poisson side of its score-distribution head.
    home_goals_model = away_goals_model = None
    if {"score_home", "score_away"}.issubset(targets_df.columns):
        try:
            home_goals_model = train_home_goals_model(features_df, targets_df, output_dir)
            away_goals_model = train_away_goals_model(features_df, targets_df, output_dir)
        except Exception as e:
            console.print(f"[red]Goals regressors failed: {e}[/red]")

    # Dump feature column list — xgboost_ensemble.py loads this to align
    # incoming feature vectors at inference time. Source the base set from
    # the actual features_df columns (caller may have added Pinnacle), plus
    # the Stage-2a `_missing` indicators that _impute_features synthesises.
    base_cols = [c for c in features_df.columns if not c.endswith("_missing")]
    indicator_cols = [f"{c}_missing" for c in INFORMATIVE_MISSING_COLS if c in base_cols]
    augmented_feature_cols = base_cols + indicator_cols
    joblib.dump(augmented_feature_cols, output_dir / "feature_cols.pkl")
    console.print(f"  Saved: {output_dir / 'feature_cols.pkl'}")

    console.print(f"\n[bold green]✓ All models trained and saved to {output_dir}[/bold green]\n")

    # ML-BUNDLE-STORAGE — push the bundle to Supabase Storage + register a
    # row in `model_versions`. Without this, training on Railway leaves the
    # bundle on the container's ephemeral filesystem and it dies on the next
    # deploy. With this, every freshly-trained bundle is durable, downloadable
    # from any environment, and auditable through the registry table.
    try:
        from workers.model.storage import upload_bundle, register_version
        console.print("[cyan]Uploading bundle to Supabase Storage...[/cyan]")
        upload_bundle(version, output_dir)
        # Derive metadata from the in-memory training frame.
        win_start = win_end = None
        n_rows = int(len(features_df))
        try:
            if "match_date" in targets_df.columns:
                dates = targets_df["match_date"].dropna()
                if len(dates):
                    win_start = str(dates.min())[:10]
                    win_end = str(dates.max())[:10]
        except Exception:
            pass
        register_version(
            version,
            training_window_start=win_start,
            training_window_end=win_end,
            n_training_rows=n_rows,
            feature_cols=augmented_feature_cols,
            cv_metrics=None,  # TODO: thread per-market CV metrics through here once train_*_model returns them
            notes=f"Auto-uploaded by train.py train_all() (include_pinnacle={include_pinnacle}, include_ou_market={include_ou_market})",
        )
        console.print(f"[bold green]✓ Bundle {version} uploaded + registered in model_versions[/bold green]\n")
    except Exception as e:
        console.print(
            f"[yellow]⚠ Storage upload failed for {version}: {e}\n"
            f"  Bundle is on local disk only — Railway will lose it on next deploy.\n"
            f"  Run `python3 scripts/bootstrap_model_storage.py --only {version}` to retry.[/yellow]\n"
        )

    return {
        "result": result_model,
        "over25": over25_model,
        "btts": btts_model,
        "home_goals": home_goals_model,
        "away_goals": away_goals_model,
        "version": version,
        "output_dir": output_dir,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-pinnacle", action="store_true",
                        help="Add Pinnacle pre-match implied probs to FEATURE_COLS "
                             "(v11+ bundles). Coverage ~5pct; _missing indicators "
                             "carry most of the signal until coverage grows.")
    parser.add_argument("--include-ou-market", action="store_true",
                        help="Add Pinnacle OU 2.5 implied probs + OU 2.5 bookmaker "
                             "disagreement + market-implied BTTS yes to FEATURE_COLS "
                             "(v14+ bundles). Overround guard applied to Pinnacle rows.")
    parser.add_argument("--version", default="untagged",
                        help="Version tag — used as the subdir under data/models/soccer/. "
                             "Set MODEL_VERSION=<version> in env to activate.")
    args = parser.parse_args()
    train_all(
        version=args.version,
        include_pinnacle=args.include_pinnacle,
        include_ou_market=args.include_ou_market,
    )
