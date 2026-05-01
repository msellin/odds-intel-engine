"""
OddsIntel — XGBoost Ensemble for Live Pipeline

Loads saved v9 XGBoost models and computes predictions for Tier A teams
by looking up their most recent feature vectors from features_v9.csv.

Blends 50/50 with Poisson predictions to form the ensemble.

Architecture:
  - Poisson: thinks in goals (exp_home, exp_away → scoreline PMF)
  - XGBoost: thinks in features (36 rolling stats → P(home), P(draw), P(away), P(over 2.5))
  - Ensemble: 50/50 blend of both → better calibrated than either alone
  - Model disagreement: abs(xgb_prob - poisson_prob) → uncertainty signal

Only works for Tier A teams (those in features_v9.csv / targets_v9.csv).
Tier B/C teams fall back to Poisson-only (no change from current behavior).
"""

import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.stats import poisson

ENGINE_DIR = Path(__file__).parent.parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
MODELS_DIR = ENGINE_DIR / "data" / "models" / "soccer"

# Use v9a_202425 — trained on all leagues, most recent season
MODEL_VERSION = "v9a_202425"

# Cache loaded models and feature data (loaded once per pipeline run)
_model_cache = {}
_feature_cache = {}


def _load_models() -> dict:
    """Load saved XGBoost models from disk. Cached after first call."""
    if _model_cache:
        return _model_cache

    model_path = MODELS_DIR / MODEL_VERSION
    if not model_path.exists():
        return {}

    try:
        _model_cache["feature_cols"] = joblib.load(model_path / "feature_cols.pkl")
        _model_cache["result_1x2"] = joblib.load(model_path / "result_1x2.pkl")
        _model_cache["over_under"] = joblib.load(model_path / "over_under.pkl")
        _model_cache["home_goals"] = joblib.load(model_path / "home_goals.pkl")
        _model_cache["away_goals"] = joblib.load(model_path / "away_goals.pkl")
    except Exception:
        _model_cache.clear()
        return {}

    return _model_cache


def _load_feature_data() -> dict:
    """
    Load features_v9.csv + targets_v9.csv and build a lookup of
    most recent feature vector per team. Cached after first call.

    Returns dict:
      {team_name: {feature_name: value, ...}} — last known features
    """
    if _feature_cache:
        return _feature_cache

    features_path = PROCESSED_DIR / "features_v9.csv"
    targets_path = PROCESSED_DIR / "targets_v9.csv"

    if not features_path.exists() or not targets_path.exists():
        return {}

    try:
        features = pd.read_csv(features_path)
        targets = pd.read_csv(targets_path)

        if len(features) != len(targets):
            return {}

        # Combine for team lookup
        features["home_team"] = targets["home_team"]
        features["away_team"] = targets["away_team"]
        features["Date"] = targets["Date"]
        features["tier"] = targets["tier"]

        # Sort by date so groupby().last() gives most recent
        features_sorted = features.sort_values("Date")

        home_cols = [c for c in features.columns if c.startswith("h_")]
        away_cols = [c for c in features.columns if c.startswith("a_")]

        # Vectorized: get last row per home team (much faster than iterrows)
        last_home = features_sorted.dropna(subset=["h_win_pct"]).groupby("home_team").last()
        for team, row in last_home.iterrows():
            _feature_cache[f"{team}_home"] = {
                c: row[c] for c in home_cols if pd.notna(row.get(c))
            }
            _feature_cache[f"{team}_home"]["elo"] = row.get("home_elo", 1500)

        # Vectorized: get last row per away team
        last_away = features_sorted.dropna(subset=["a_win_pct"]).groupby("away_team").last()
        for team, row in last_away.iterrows():
            _feature_cache[f"{team}_away"] = {
                c: row[c] for c in away_cols if pd.notna(row.get(c))
            }
            _feature_cache[f"{team}_away"]["elo"] = row.get("away_elo", 1500)

    except Exception:
        _feature_cache.clear()
        return {}

    return _feature_cache


def get_xgboost_prediction(home_team: str, away_team: str,
                           tier: int = 1) -> dict | None:
    """
    Get XGBoost prediction for a match using saved models + cached features.

    Returns dict with probabilities, or None if teams not in feature data.
    {
        "xgb_home_prob": float,
        "xgb_draw_prob": float,
        "xgb_away_prob": float,
        "xgb_over25_prob": float,
        "xgb_exp_home": float,
        "xgb_exp_away": float,
    }
    """
    models = _load_models()
    if not models:
        return None

    features_data = _load_feature_data()
    if not features_data:
        return None

    # Look up latest feature vectors for both teams
    home_feats = features_data.get(f"{home_team}_home")
    away_feats = features_data.get(f"{away_team}_away")

    if not home_feats or not away_feats:
        return None

    feature_cols = models["feature_cols"]

    # Build the feature vector in the exact order the model expects
    row = {}

    # ELO features
    home_elo = home_feats.get("elo", 1500)
    away_elo = away_feats.get("elo", 1500)
    row["home_elo"] = home_elo
    row["away_elo"] = away_elo
    row["elo_diff"] = home_elo - away_elo
    row["home_elo_exp"] = 1 / (1 + 10 ** (-(home_elo - away_elo + 100) / 400))

    # Home team rolling stats (h_ prefix)
    for col in feature_cols:
        if col.startswith("h_"):
            row[col] = home_feats.get(col, 0.0)
        elif col.startswith("a_"):
            row[col] = away_feats.get(col, 0.0)

    # Differential features
    row["xg_diff"] = row.get("h_xg_for_avg", 0) - row.get("a_xg_for_avg", 0)
    row["form_diff"] = row.get("h_ppg", 1.3) - row.get("a_ppg", 1.3)
    row["overperf_diff"] = row.get("h_overperf_avg", 0) - row.get("a_overperf_avg", 0)
    row["tier"] = tier

    # Build DataFrame in correct column order
    try:
        X = pd.DataFrame([row])[feature_cols]
        X = X.fillna(0)
    except KeyError:
        return None

    try:
        # 1X2 classifier
        result_model = models["result_1x2"]
        probs_1x2 = result_model.predict_proba(X)[0]
        # Classes are typically [A, D, H] or [0, 1, 2] — check model classes
        classes = list(result_model.classes_)

        if "H" in classes:
            home_prob = probs_1x2[classes.index("H")]
            draw_prob = probs_1x2[classes.index("D")]
            away_prob = probs_1x2[classes.index("A")]
        else:
            # Assume order: away=0, draw=1, home=2
            home_prob = probs_1x2[2] if len(probs_1x2) > 2 else probs_1x2[0]
            draw_prob = probs_1x2[1] if len(probs_1x2) > 1 else 0.3
            away_prob = probs_1x2[0]

        # O/U classifier
        over_model = models["over_under"]
        probs_ou = over_model.predict_proba(X)[0]
        ou_classes = list(over_model.classes_)
        if True in ou_classes or 1 in ou_classes:
            over25_prob = probs_ou[ou_classes.index(True)] if True in ou_classes else probs_ou[ou_classes.index(1)]
        else:
            over25_prob = probs_ou[-1]  # assume last class is "over"

        # Poisson goal regressors (for expected goals)
        hg_model = models["home_goals"]
        ag_model = models["away_goals"]
        exp_home = max(0.1, float(hg_model.predict(X)[0]))
        exp_away = max(0.1, float(ag_model.predict(X)[0]))

        return {
            "xgb_home_prob": float(home_prob),
            "xgb_draw_prob": float(draw_prob),
            "xgb_away_prob": float(away_prob),
            "xgb_over25_prob": float(over25_prob),
            "xgb_exp_home": exp_home,
            "xgb_exp_away": exp_away,
        }

    except Exception:
        return None


def ensemble_prediction(poisson_pred: dict, xgb_pred: dict,
                        poisson_weight: float = 0.5) -> dict:
    """
    Blend Poisson and XGBoost predictions 50/50.

    Returns merged prediction dict with:
      - Blended probabilities for ALL markets (1x2, over/under, BTTS)
      - Model disagreement (abs difference between models on 1x2 home)
      - Both models' individual outputs for tracking

    Market coverage:
      - 1x2 (home/draw/away): blended Poisson + XGBoost
      - over/under 2.5: blended Poisson + XGBoost (XGBoost has an O/U model)
      - over/under 1.5, 3.5, BTTS: Poisson-only (XGBoost not trained on these)
        These are passed through directly so the pipeline stores them too.
        If you add a new market to the pipeline's storage loop (daily_pipeline_v2.py),
        you MUST ensure the corresponding prob key is produced here.
    """
    xw = 1.0 - poisson_weight
    pw = poisson_weight

    blended = {
        # --- 1x2: blended ---
        "home_prob": pw * poisson_pred["home_prob"] + xw * xgb_pred["xgb_home_prob"],
        "draw_prob": pw * poisson_pred["draw_prob"] + xw * xgb_pred["xgb_draw_prob"],
        "away_prob": pw * poisson_pred["away_prob"] + xw * xgb_pred["xgb_away_prob"],

        # --- Over/Under 2.5: blended (XGBoost has an O/U 2.5 model) ---
        "over_25_prob": pw * poisson_pred["over_25_prob"] + xw * xgb_pred["xgb_over25_prob"],
        "under_25_prob": pw * poisson_pred.get("under_25_prob", 1 - poisson_pred["over_25_prob"]) + xw * (1 - xgb_pred["xgb_over25_prob"]),

        # --- Over/Under 1.5 and 3.5: Poisson-only (XGBoost not trained on these lines) ---
        "over_15_prob": poisson_pred.get("over_15_prob", 0.0),
        "under_15_prob": poisson_pred.get("under_15_prob", 1.0),
        "over_35_prob": poisson_pred.get("over_35_prob", 0.0),
        "under_35_prob": poisson_pred.get("under_35_prob", 1.0),

        # --- BTTS: Poisson-only (derived from Dixon-Coles joint goal distribution) ---
        "btts_yes_prob": poisson_pred.get("btts_yes_prob", 0.0),
        "btts_no_prob": poisson_pred.get("btts_no_prob", 1.0),

        # Expected goals (average of both)
        "exp_home": (poisson_pred["exp_home"] + xgb_pred["xgb_exp_home"]) / 2,
        "exp_away": (poisson_pred["exp_away"] + xgb_pred["xgb_exp_away"]) / 2,

        # Keep data tier from Poisson
        "data_tier": poisson_pred.get("data_tier", "A"),

        # Model disagreement — key uncertainty signal (1x2 home delta)
        "model_disagreement": round(abs(
            poisson_pred["home_prob"] - xgb_pred["xgb_home_prob"]
        ), 4),

        # Flag that this was an ensemble prediction
        "ensemble": True,

        # Individual model outputs (for tracking/debugging)
        "poisson_home_prob": poisson_pred["home_prob"],
        "xgb_home_prob": xgb_pred["xgb_home_prob"],
    }

    return blended
