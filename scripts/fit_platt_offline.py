"""
Offline Platt calibration fit for a saved model bundle.

Production `scripts/fit_platt.py` reads from the `predictions` table — only
works for models that have been deployed and have settled DB predictions.
A freshly-trained model bundle (e.g. `v10_pre_shadow`) doesn't have those
yet, so we can't measure or fit calibration through the normal path.

This script does it offline:
  1. Load the model bundle from data/models/soccer/<version>/
  2. Time-split MFV: first 80% as "train" (just used to align with how the
     model was trained), last 20% as held-out scoring set
  3. Score the bundle on the held-out slice
  4. Compute raw Brier per market
  5. Fit Platt sigmoid α, β to (raw_prob, outcome) pairs
  6. Apply, recompute Brier
  7. Save coefficients to data/models/soccer/<version>/platt.pkl

The bundle's platt.pkl is read by xgboost_ensemble at inference time when
present. Production `model_calibration` table can ingest these via a
follow-up.

Usage:
    python3 scripts/fit_platt_offline.py --version v10_pre_shadow
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.metrics import brier_score_loss

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import execute_query
from workers.model.train import FEATURE_COLS, _impute_features

MODELS_ROOT = Path(__file__).parent.parent / "data" / "models" / "soccer"


def _platt(p, a, b):
    z = np.clip(a * p + b, -30, 30)
    return 1.0 / (1.0 + np.exp(-z))


def _fit_platt(p, y):
    """Minimise negative log-likelihood; return (a, b)."""
    def nll(params):
        a, b = params
        q = _platt(p, a, b)
        q = np.clip(q, 1e-12, 1 - 1e-12)
        return -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))
    res = minimize(nll, x0=[1.0, 0.0], method="Nelder-Mead")
    return float(res.x[0]), float(res.x[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True,
                    help="Model version subdir under data/models/soccer/")
    ap.add_argument("--holdout-frac", type=float, default=0.2,
                    help="Tail fraction of time-sorted MFV used as holdout")
    args = ap.parse_args()

    bundle_dir = MODELS_ROOT / args.version
    if not bundle_dir.exists():
        print(f"ERROR: bundle not found at {bundle_dir}", file=sys.stderr)
        sys.exit(1)

    # Load bundle
    feature_cols = joblib.load(bundle_dir / "feature_cols.pkl")
    result_model = joblib.load(bundle_dir / "result_1x2.pkl")
    over_model = joblib.load(bundle_dir / "over_under.pkl")
    btts_model = joblib.load(bundle_dir / "btts.pkl")

    # Pull MFV with outcomes
    rows = execute_query("""
        SELECT mfv.*, m.score_home, m.score_away
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        WHERE mfv.match_outcome IS NOT NULL AND mfv.match_date IS NOT NULL
        ORDER BY mfv.match_date ASC
    """, ())
    df = pd.DataFrame(rows)
    print(f"Loaded {len(df):,} settled MFV rows.")

    # Coerce + impute (match training-time logic)
    for c in FEATURE_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    X_imp, augmented_cols = _impute_features(df[FEATURE_COLS], df.get("league_tier"))

    # Holdout = tail
    n = len(df)
    cut = int(n * (1 - args.holdout_frac))
    X_hold = X_imp.iloc[cut:][feature_cols] if all(c in X_imp.columns for c in feature_cols) \
             else X_imp.iloc[cut:]
    df_hold = df.iloc[cut:].reset_index(drop=True)
    print(f"Holdout: {len(X_hold):,} rows  ({df_hold['match_date'].min()} → {df_hold['match_date'].max()})")

    # Targets
    y_home = (df_hold["match_outcome"] == "home").astype(int).values
    y_draw = (df_hold["match_outcome"] == "draw").astype(int).values
    y_away = (df_hold["match_outcome"] == "away").astype(int).values
    y_over = (
        pd.to_numeric(df_hold["score_home"], errors="coerce")
        + pd.to_numeric(df_hold["score_away"], errors="coerce")
        > 2.5
    ).astype(int).values
    y_btts = (
        (pd.to_numeric(df_hold["score_home"], errors="coerce") > 0)
        & (pd.to_numeric(df_hold["score_away"], errors="coerce") > 0)
    ).astype(int).values

    # Predict
    proba_1x2 = result_model.predict_proba(X_hold)
    classes = list(result_model.classes_)
    home_idx, draw_idx, away_idx = classes.index(0), classes.index(1), classes.index(2)
    p_home = proba_1x2[:, home_idx]
    p_draw = proba_1x2[:, draw_idx]
    p_away = proba_1x2[:, away_idx]
    p_over = over_model.predict_proba(X_hold)[:, 1]
    p_btts = btts_model.predict_proba(X_hold)[:, 1]

    # Fit Platt per market + report Brier delta
    platt_params: dict[str, tuple[float, float]] = {}
    print(f"\n{'Market':<12}{'raw Brier':>12}{'cal Brier':>12}{'a':>10}{'b':>10}")
    print("-" * 56)
    for name, p, y in (
        ("1x2_home", p_home, y_home),
        ("1x2_draw", p_draw, y_draw),
        ("1x2_away", p_away, y_away),
        ("over_25",  p_over, y_over),
        ("btts_yes", p_btts, y_btts),
    ):
        raw_brier = brier_score_loss(y, p)
        a, b = _fit_platt(p, y)
        cal_brier = brier_score_loss(y, _platt(p, a, b))
        print(f"{name:<12}{raw_brier:>12.4f}{cal_brier:>12.4f}{a:>10.3f}{b:>10.3f}")
        platt_params[name] = (a, b)

    # Persist alongside the bundle
    out_path = bundle_dir / "platt.pkl"
    joblib.dump(platt_params, out_path)
    print(f"\nSaved Platt coefficients to {out_path}")
    print("\nNote: production calibrate_prob() in workers/model/improvements.py reads")
    print("from the model_calibration table, not platt.pkl. Wiring the table read")
    print("to be model_version-aware is a follow-up — for now this file documents")
    print("v10's calibration delta vs raw probabilities.")


if __name__ == "__main__":
    main()
