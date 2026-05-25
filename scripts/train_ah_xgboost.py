"""AH-XGBOOST — train a dedicated XGBoost classifier for Asian Handicap.

The current AH pricing uses Dixon-Coles + Poisson joint goal matrix. This
script trains an XGBoost head that learns from settled matches directly,
using MFV features. Output is a candidate bundle (not activated by default).

Training cohort:
  - All settled matches (status='finished') with score + MFV row
  - Pick the "main" AH line per match (handicap_line whose home_odds at
    the closing snapshot is closest to 2.00 — i.e. closest to fair-odds)
  - One row per match (home-covered = label)

Label rule (handicap H from home perspective, score margin = home - away):
  - whole line (H integer):   home covers if margin > -H; push if ==
                              (pushes dropped from training)
  - half line (H = x.5):      home covers if margin > -H (no push)
  - quarter (x.25 / x.75):    half-win/half-loss treated as 0.5 label
                              (XGBoost handles as a regression-like target
                              via Brier-style binary cross-entropy)

Features: MATCH_LEVEL_FEATURES from train_b_ml3.py + the AH line itself
+ the implied prob from the AH home/away odds at t-6h.

Output: data/models/ah_xgb/v_YYYYMMDD/{ah_xgb.pkl, scaler.pkl,
        feature_cols.pkl, threshold.json, model_type.txt}

Run: python3 scripts/train_ah_xgboost.py [--version v_YYYYMMDD] [--dry-run]
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from datetime import date as _date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from rich.console import Console
from sklearn.metrics import roc_auc_score, log_loss
from sklearn.model_selection import StratifiedKFold

from workers.api_clients.db import execute_query

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "ah_xgb"

# Features taken from B-ML3 v2.2 schema; AH-specific additions below
MATCH_FEATURES = [
    "bookmaker_disagreement",
    "elo_diff",
    "form_ppg_home",
    "form_ppg_away",
    "lineup_confirmed",
    "rest_days_home",
    "rest_days_away",
    "fixture_importance",
    "league_position_home",
    "odds_drift_home_at_t6h",
    "steam_move_at_t6h",
    "form_momentum_home",
    "form_momentum_away",
    "pinnacle_ah_line_at_t6h",
    "pinnacle_ah_line_move",
    # 1X2 ensemble priors that AH should leverage
    "ensemble_prob_home",
    "ensemble_prob_away",
]
# AH-specific (computed at load time):
#   handicap_line             — the line we're pricing
#   ah_home_implied           — best book home AH implied prob
#   ah_line_deviation         — handicap_line - pinnacle_ah_line_at_t6h (how
#                                 different the chosen line is vs the sharp one)


def _ah_label(margin: int, line: float) -> float | None:
    """Return label for the AH bet (home covers) given final margin + line.

    handicap line H is from the home perspective: negative = home gives goals.
    Returns 0.5 for half-win/half-loss (quarter lines). None for pushes.
    """
    # The "spread" = how many goals home must win by for the bet to win.
    spread = -line
    floor_s = math.floor(spread)
    frac = spread - floor_s
    if frac < 0.01:  # whole
        if margin > spread:
            return 1.0
        if margin < spread:
            return 0.0
        return None  # push
    if abs(frac - 0.5) < 0.01:  # half — no push
        return 1.0 if margin > spread else 0.0
    if frac < 0.5:  # x.25: half-loss when margin == floor_s
        if margin >= floor_s + 1:
            return 1.0
        if margin == floor_s:
            return 0.5
        return 0.0
    # x.75: half-win when margin == floor_s + 1
    if margin >= floor_s + 2:
        return 1.0
    if margin == floor_s + 1:
        return 0.5
    return 0.0


def _load_training_rows() -> pd.DataFrame:
    """One row per match: the main AH line + label + features."""
    console.print("[bold]Loading AH training cohort...[/bold]")
    sql = """
        WITH closing_ah AS (
            SELECT DISTINCT ON (os.match_id, os.handicap_line)
                os.match_id, os.handicap_line,
                os.odds AS home_odds,
                os.timestamp,
                ABS(os.odds - 2.00) AS dist_to_fair
            FROM odds_snapshots os
            WHERE os.market = 'asian_handicap'
              AND os.selection = 'home'
              AND os.is_live = FALSE
              AND os.minutes_to_kickoff IS NOT NULL
              AND os.minutes_to_kickoff BETWEEN -30 AND 720
              AND os.odds BETWEEN 1.40 AND 3.00
              AND os.handicap_line IS NOT NULL
            ORDER BY os.match_id, os.handicap_line, os.minutes_to_kickoff ASC
        ),
        main_line AS (
            SELECT DISTINCT ON (match_id) match_id, handicap_line, home_odds
            FROM closing_ah
            ORDER BY match_id, dist_to_fair ASC
        )
        SELECT
            m.id AS match_id, m.score_home, m.score_away,
            ml.handicap_line, ml.home_odds,
            mfv.bookmaker_disagreement, mfv.elo_diff,
            mfv.form_ppg_home, mfv.form_ppg_away,
            mfv.lineup_confirmed,
            mfv.rest_days_home, mfv.rest_days_away,
            mfv.fixture_importance, mfv.league_position_home,
            mfv.odds_drift_home_at_t6h, mfv.steam_move_at_t6h,
            mfv.form_momentum_home, mfv.form_momentum_away,
            mfv.pinnacle_ah_line_at_t6h, mfv.pinnacle_ah_line_move,
            mfv.ensemble_prob_home, mfv.ensemble_prob_away
        FROM matches m
        JOIN main_line ml ON ml.match_id = m.id
        JOIN match_feature_vectors mfv ON mfv.match_id = m.id
        WHERE m.score_home IS NOT NULL
          AND m.score_away IS NOT NULL
          AND m.date >= '2026-05-01'
    """
    rows = execute_query(sql)
    df = pd.DataFrame(rows)
    console.print(f"  Loaded {len(df):,} match rows")

    # Label
    labels = []
    for _, r in df.iterrows():
        margin = int(r["score_home"] - r["score_away"])
        lab = _ah_label(margin, float(r["handicap_line"]))
        labels.append(lab)
    df["y"] = labels
    kept = df[df["y"].notna()].copy()
    console.print(f"  After dropping pushes: {len(kept):,} rows")
    # Drop rows where AH-essential features are missing. ensemble_prob_*
    # is sparsely backfilled (~23% coverage) — fill with median in feature
    # matrix builder rather than dropping. elo_diff is dense (>99%).
    kept = kept.dropna(subset=["elo_diff"])
    console.print(f"  After dropping NaN elo_diff: {len(kept):,} rows")
    return kept


def _build_feature_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    df = df.copy()
    df["ah_home_implied"] = 1.0 / df["home_odds"].astype(float)
    df["handicap_line"] = df["handicap_line"].astype(float)
    df["ah_line_deviation"] = df["handicap_line"] - df["pinnacle_ah_line_at_t6h"].astype(float).fillna(df["handicap_line"])
    feature_cols = MATCH_FEATURES + ["handicap_line", "ah_home_implied", "ah_line_deviation"]
    # Numeric coercion
    X = df[feature_cols].apply(pd.to_numeric, errors="coerce")
    # bool features
    if "lineup_confirmed" in X.columns:
        X["lineup_confirmed"] = X["lineup_confirmed"].fillna(False).astype(int)
    if "steam_move_at_t6h" in X.columns:
        X["steam_move_at_t6h"] = X["steam_move_at_t6h"].fillna(False).astype(int)
    X = X.fillna(X.median(numeric_only=True))
    y = df["y"].astype(float)
    return X, y, feature_cols


def _train(X: pd.DataFrame, y: pd.Series, feature_cols: list[str]) -> tuple:
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    # XGBoost handles half-labels naturally via binary:logistic with float y
    # — internally it's a weighted binary cross-entropy.
    auc_scores, ll_scores = [], []
    y_bin = (y > 0.5).astype(int)  # for stratification only
    for fold, (tr, te) in enumerate(skf.split(X, y_bin)):
        clf = xgb.XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            objective="binary:logistic", eval_metric="logloss",
            random_state=42 + fold, n_jobs=-1,
        )
        # XGBoost binary classification needs int labels; quantize half-wins to 1
        # for fitting then use predict_proba's continuous prob as the score.
        y_train_int = (y.iloc[tr] > 0.4).astype(int)
        clf.fit(X.iloc[tr], y_train_int)
        prob = clf.predict_proba(X.iloc[te])[:, 1]
        auc_scores.append(roc_auc_score((y.iloc[te] > 0.5).astype(int), prob))
        ll_scores.append(log_loss((y.iloc[te] > 0.5).astype(int), prob, labels=[0, 1]))

    # Fit final model on full data
    y_full_int = (y > 0.4).astype(int)
    final = xgb.XGBClassifier(
        n_estimators=200, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        objective="binary:logistic", eval_metric="logloss",
        random_state=42, n_jobs=-1,
    )
    final.fit(X, y_full_int)

    metrics = {
        "cv_auc_mean": float(np.mean(auc_scores)),
        "cv_auc_std": float(np.std(auc_scores)),
        "cv_logloss_mean": float(np.mean(ll_scores)),
        "n_train": int(len(X)),
        "n_features": int(len(feature_cols)),
        "feature_importance": dict(zip(feature_cols, final.feature_importances_.tolist())),
    }
    console.print(f"\n[bold]CV AUC: {metrics['cv_auc_mean']:.4f} ± {metrics['cv_auc_std']:.4f}[/bold]")
    console.print(f"  Log loss: {metrics['cv_logloss_mean']:.4f}")
    console.print(f"  Top-5 feature importance:")
    top = sorted(metrics["feature_importance"].items(), key=lambda x: x[1], reverse=True)[:5]
    for k, v in top:
        console.print(f"    {k}: {v:.4f}")
    return final, metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=f"v_{_date.today().strftime('%Y%m%d')}")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    df = _load_training_rows()
    if len(df) < 1000:
        console.print(f"[red]Only {len(df)} training rows — need ≥1,000. Aborting.[/red]")
        sys.exit(1)
    X, y, feature_cols = _build_feature_matrix(df)
    console.print(f"\n[bold]Feature matrix: {X.shape[0]:,} rows × {X.shape[1]} features[/bold]")
    model, metrics = _train(X, y, feature_cols)
    if args.dry_run:
        console.print("\n[yellow]--dry-run: not saving bundle[/yellow]")
        return
    out_dir = MODELS_DIR / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / "ah_xgb.pkl")
    joblib.dump(None, out_dir / "scaler.pkl")  # no scaler for XGBoost
    joblib.dump(feature_cols, out_dir / "feature_cols.pkl")
    with open(out_dir / "model_type.txt", "w") as f:
        f.write("xgboost")
    with open(out_dir / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    console.print(f"\n[bold green]✓ AH-XGBOOST bundle saved to {out_dir}[/bold green]")
    console.print("  Activation: not auto-wired. To use, load via "
                  "joblib.load(out_dir/'ah_xgb.pkl') and replace _ah_model_prob "
                  "in daily_pipeline_v2.py behind an env-gate.")


if __name__ == "__main__":
    main()
