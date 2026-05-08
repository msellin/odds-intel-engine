"""
OddsIntel — Model Training
Trains prediction models for:
  1. Match result (1X2) — XGBoost classifier
  2. Over/Under 2.5 goals — XGBoost classifier
  3. BTTS (Both Teams To Score) — XGBoost classifier

No AI/LLM needed. Pure machine learning on historical data.

Column names match match_feature_vectors exactly. Callers must provide:
  features_df: columns from FEATURE_COLS (allow NaN — dropped per model)
  targets_df:  match_outcome (H/D/A), over_25 (bool), btts (bool)
               btts = score_home > 0 AND score_away > 0 — compute at load time
"""

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from xgboost import XGBClassifier
from rich.console import Console
from rich.table import Table

console = Console()

MODELS_DIR = Path(__file__).parent.parent.parent / "data" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

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


def train_result_model(features_df: pd.DataFrame, targets_df: pd.DataFrame):
    """Train 1X2 match result prediction model"""
    console.print("\n[bold cyan]Training 1X2 Result Model[/bold cyan]")

    X = features_df[FEATURE_COLS].copy()
    y = targets_df["match_outcome"].map({"H": 0, "D": 1, "A": 2}).copy()

    # Drop rows with missing values
    valid = X.notna().all(axis=1) & y.notna()
    X = X[valid]
    y = y[valid]

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

    model_path = MODELS_DIR / "result_model.pkl"
    joblib.dump(final_model, model_path)
    console.print(f"  Saved to: {model_path}")

    # Feature importance
    importance = pd.Series(
        final_model.feature_importances_,
        index=FEATURE_COLS
    ).sort_values(ascending=False)

    imp_table = Table(title="Top 10 Features (1X2 Model)")
    imp_table.add_column("Feature", style="cyan")
    imp_table.add_column("Importance", style="green", justify="right")

    for feat, imp in importance.head(10).items():
        imp_table.add_row(feat, f"{imp:.4f}")

    console.print(imp_table)

    return final_model


def train_over25_model(features_df: pd.DataFrame, targets_df: pd.DataFrame):
    """Train Over/Under 2.5 goals prediction model"""
    console.print("\n[bold cyan]Training Over/Under 2.5 Model[/bold cyan]")

    X = features_df[FEATURE_COLS].copy()
    y = targets_df["over_25"].copy()

    valid = X.notna().all(axis=1) & y.notna()
    X = X[valid]
    y = y[valid]

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

    model_path = MODELS_DIR / "over25_model.pkl"
    joblib.dump(final_model, model_path)
    console.print(f"  Saved to: {model_path}")

    return final_model


def train_btts_model(features_df: pd.DataFrame, targets_df: pd.DataFrame):
    """Train BTTS (Both Teams To Score) prediction model"""
    console.print("\n[bold cyan]Training BTTS Model[/bold cyan]")

    X = features_df[FEATURE_COLS].copy()
    y = targets_df["btts"].copy()

    valid = X.notna().all(axis=1) & y.notna()
    X = X[valid]
    y = y[valid]

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

    model_path = MODELS_DIR / "btts_model.pkl"
    joblib.dump(final_model, model_path)
    console.print(f"  Saved to: {model_path}")

    return final_model


def load_training_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load match_feature_vectors rows with completed outcomes from the DB.

    Returns (features_df, targets_df) sorted by match_date ascending.
    btts is derived from match scores joined from the matches table.
    Requires env DB credentials — same as the rest of the pipeline.
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

    features_df = df[FEATURE_COLS].copy()
    targets_df = df[["match_outcome", "over_25", "btts"]].copy()

    console.print(f"Loaded {len(df):,} completed matches for training")
    return features_df, targets_df


def train_all(features_df: pd.DataFrame | None = None, targets_df: pd.DataFrame | None = None):
    """Train all three models. If called with no args, loads data from DB automatically."""
    if features_df is None or targets_df is None:
        features_df, targets_df = load_training_data()

    console.print("\n[bold green]═══ OddsIntel Model Training ═══[/bold green]")

    result_model = train_result_model(features_df, targets_df)
    over25_model = train_over25_model(features_df, targets_df)
    btts_model = train_btts_model(features_df, targets_df)

    console.print("\n[bold green]All models trained and saved![/bold green]")

    return {
        "result": result_model,
        "over25": over25_model,
        "btts": btts_model,
    }


if __name__ == "__main__":
    train_all()
