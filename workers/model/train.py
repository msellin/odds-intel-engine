"""
OddsIntel — Model Training
Trains prediction models for:
  1. Match result (1X2) — XGBoost classifier
  2. Over/Under 2.5 goals — XGBoost classifier
  3. BTTS (Both Teams To Score) — XGBoost classifier

No AI/LLM needed. Pure machine learning on historical data.
"""

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from sklearn.calibration import CalibratedClassifierCV
from xgboost import XGBClassifier
from rich.console import Console
from rich.table import Table

console = Console()

MODELS_DIR = Path(__file__).parent.parent.parent / "data" / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Features used by the model
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


def train_result_model(features_df: pd.DataFrame, targets_df: pd.DataFrame):
    """Train 1X2 match result prediction model"""
    console.print("\n[bold cyan]Training 1X2 Result Model[/bold cyan]")

    X = features_df[FEATURE_COLS].copy()
    y = targets_df["result"].map({"H": 0, "D": 1, "A": 2}).copy()

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

    # Use calibration for better probability estimates
    calibrated = CalibratedClassifierCV(final_model, cv=5, method="isotonic")
    calibrated.fit(X, y)

    # Save model
    model_path = MODELS_DIR / "result_model.pkl"
    joblib.dump(calibrated, model_path)
    console.print(f"  Saved to: {model_path}")

    # Feature importance
    final_model.fit(X, y, verbose=False)
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

    return calibrated


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

    calibrated = CalibratedClassifierCV(final_model, cv=5, method="isotonic")
    calibrated.fit(X, y)

    model_path = MODELS_DIR / "over25_model.pkl"
    joblib.dump(calibrated, model_path)
    console.print(f"  Saved to: {model_path}")

    return calibrated


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

    calibrated = CalibratedClassifierCV(final_model, cv=5, method="isotonic")
    calibrated.fit(X, y)

    model_path = MODELS_DIR / "btts_model.pkl"
    joblib.dump(calibrated, model_path)
    console.print(f"  Saved to: {model_path}")

    return calibrated


def train_all(features_df: pd.DataFrame, targets_df: pd.DataFrame):
    """Train all three models"""
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
