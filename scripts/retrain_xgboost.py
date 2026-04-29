"""
Retrain result_1x2 and over_under XGBoost models.

Loads pre-built features_v9.csv + targets_v9.csv (95,847 rows) and retrains
only the two broken classifiers (home_goals.pkl and away_goals.pkl are fine).

Saves directly to data/models/soccer/v9a_202425/ — replaces the corrupted files.

Uses all available data (no train/test split) for the production model.

Usage:
  python scripts/retrain_xgboost.py
"""

import sys
import joblib
import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.calibration import CalibratedClassifierCV
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
console = Console()

ENGINE_DIR = Path(__file__).parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
MODELS_DIR = ENGINE_DIR / "data" / "models" / "soccer" / "v9a_202425"


def main():
    console.print("[bold green]═══ Retrain XGBoost Classifiers (v9a_202425) ═══[/bold green]\n")

    # Load pre-built features
    console.print("[cyan]Loading features_v9.csv and targets_v9.csv...[/cyan]")
    features_path = PROCESSED_DIR / "features_v9.csv"
    targets_path = PROCESSED_DIR / "targets_v9.csv"

    if not features_path.exists():
        console.print(f"[red]Missing: {features_path}[/red]")
        sys.exit(1)

    features = pd.read_csv(features_path)
    targets = pd.read_csv(targets_path)
    console.print(f"  {len(features):,} rows × {len(features.columns)} feature columns")

    # Load existing feature_cols to keep the same column order
    feature_cols_path = MODELS_DIR / "feature_cols.pkl"
    if feature_cols_path.exists():
        with open(feature_cols_path, "rb") as f:
            import pickle
            feature_cols = pickle.load(f)
        console.print(f"  Using existing feature_cols ({len(feature_cols)} columns)")
    else:
        # Derive from the CSV (same logic as model_v9_xg.py)
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
        console.print(f"  Derived feature_cols ({len(feature_cols)} columns)")

    # Prepare training data — use all rows with complete features
    available_cols = [c for c in feature_cols if c in features.columns]
    if len(available_cols) < len(feature_cols):
        missing = set(feature_cols) - set(available_cols)
        console.print(f"  [yellow]Missing columns in CSV: {missing}[/yellow]")

    X = features[available_cols].fillna(0)
    console.print(f"  Training on {len(X):,} rows")

    # Targets
    y_result = targets["result"].map({"H": 0, "D": 1, "A": 2})
    valid_result = y_result.notna()
    console.print(f"  Result target: {valid_result.sum():,} valid rows")

    y_over = targets["over_25"]
    valid_over = y_over.notna()
    console.print(f"  Over/under target: {valid_over.sum():,} valid rows")

    # ── Train result_1x2 (CalibratedClassifierCV wrapping XGBClassifier) ──
    console.print("\n[cyan]Training result_1x2 classifier...[/cyan]")
    console.print("  XGBClassifier(n_estimators=200, max_depth=5, lr=0.03, multi:softprob)")
    console.print("  CalibratedClassifierCV(cv=5, method='isotonic') — this takes ~2 min")

    X_result = X[valid_result]
    y_result_clean = y_result[valid_result]

    result_base = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        objective="multi:softprob", num_class=3,
        random_state=42, verbosity=0,
        device="cpu",
    )
    result_model = CalibratedClassifierCV(result_base, cv=5, method="isotonic")
    result_model.fit(X_result, y_result_clean)

    result_path = MODELS_DIR / "result_1x2.pkl"
    joblib.dump(result_model, result_path)
    size_mb = result_path.stat().st_size / 1e6
    console.print(f"  [green]Saved result_1x2.pkl ({size_mb:.1f} MB)[/green]")

    # Quick sanity check
    proba = result_model.predict_proba(X_result.head(5))
    console.print(f"  Sample probabilities (first 5 rows): {proba.round(2).tolist()}")

    # ── Train over_under (CalibratedClassifierCV wrapping XGBClassifier) ──
    console.print("\n[cyan]Training over_under classifier...[/cyan]")
    console.print("  XGBClassifier(n_estimators=200, max_depth=5, lr=0.03, binary:logistic)")
    console.print("  CalibratedClassifierCV(cv=5, method='isotonic') — this takes ~1 min")

    X_over = X[valid_over]
    y_over_clean = y_over[valid_over].astype(int)

    over_base = XGBClassifier(
        n_estimators=200, max_depth=5, learning_rate=0.03,
        subsample=0.8, colsample_bytree=0.7,
        objective="binary:logistic",
        random_state=42, verbosity=0,
        device="cpu",
    )
    over_model = CalibratedClassifierCV(over_base, cv=5, method="isotonic")
    over_model.fit(X_over, y_over_clean)

    over_path = MODELS_DIR / "over_under.pkl"
    joblib.dump(over_model, over_path)
    size_mb = over_path.stat().st_size / 1e6
    console.print(f"  [green]Saved over_under.pkl ({size_mb:.1f} MB)[/green]")

    # Quick sanity check
    ou_proba = over_model.predict_proba(X_over.head(5))
    console.print(f"  Sample over25 probs (first 5 rows): {ou_proba[:, 1].round(2).tolist()}")

    # ── Verify models load correctly ──
    console.print("\n[cyan]Verifying both models load correctly...[/cyan]")
    for name in ["result_1x2.pkl", "over_under.pkl", "home_goals.pkl", "away_goals.pkl"]:
        path = MODELS_DIR / name
        try:
            m = joblib.load(path)
            console.print(f"  [green]✓ {name} loads OK ({type(m).__name__})[/green]")
        except Exception as e:
            console.print(f"  [red]✗ {name} failed: {e}[/red]")

    console.print(f"\n[bold green]Done! Models saved to {MODELS_DIR}[/bold green]")
    console.print("[green]Run the pipeline — XGBoost ensemble will now activate for Tier A teams.[/green]")


if __name__ == "__main__":
    main()
