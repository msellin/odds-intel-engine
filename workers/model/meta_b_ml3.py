"""B-ML3 v2.1 meta-model inference (2026-05-25).

Loads the trained meta-model bundle and scores a bet candidate's likelihood
of beating the closing line. Used by daily_pipeline_v2 as a post-edge-gate
filter — only candidates scoring above the threshold get stored.

Bundle layout (see scripts/train_b_ml3.py):
  data/models/meta/<version>/
    b_ml3.pkl          — sklearn LogisticRegression
    scaler.pkl         — StandardScaler
    feature_cols.pkl   — list[str] of feature column order
    threshold.json     — chosen threshold + CV metrics

Env vars (read at module import):
  META_B_ML3_VERSION    — bundle version tag (default "v_20260525_v21")
  META_B_ML3_THRESHOLD  — firing threshold (default 0.475)
  META_B_ML3_ENABLED    — "true" to gate placement on score (default "false";
                           when false, scores are still computed + returned for
                           logging, but the decision is always "fire")

Inference cost: <5ms per scoring call (logistic regression on 30 features
plus one Postgres MFV lookup; mostly the lookup).
"""
from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from workers.api_clients.db import execute_query

DEFAULT_VERSION = "v_20260525_v21"
META_B_ML3_VERSION = os.environ.get("META_B_ML3_VERSION", DEFAULT_VERSION)
META_B_ML3_THRESHOLD = float(os.environ.get("META_B_ML3_THRESHOLD", "0.475"))
META_B_ML3_ENABLED = os.environ.get("META_B_ML3_ENABLED", "false").lower() in ("true", "1", "yes")

_BUNDLE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "meta"
_cache: dict = {}


def _load_bundle(version: str) -> Optional[dict]:
    """Load (and cache) a meta-model bundle by version. Returns None if missing."""
    if version in _cache:
        return _cache[version]

    bp = _BUNDLE_DIR / version
    if not bp.exists() or not (bp / "b_ml3.pkl").exists():
        # Future enhancement: pull from Supabase Storage via storage.ensure_local_bundle
        # for now we expect the bundle to be on disk locally OR mirrored to Railway via repo.
        _cache[version] = None
        return None
    try:
        bundle = {
            "model": joblib.load(bp / "b_ml3.pkl"),
            "scaler": joblib.load(bp / "scaler.pkl"),
            "feature_cols": joblib.load(bp / "feature_cols.pkl"),
            "threshold": json.loads((bp / "threshold.json").read_text()).get("chosen_threshold", 0.5),
        }
        _cache[version] = bundle
        return bundle
    except Exception:
        _cache[version] = None
        return None


def _build_feature_row(mfv: dict, selection: str, ensemble_prob: float, opening_implied: float,
                       time_to_kickoff_h: float, league_tier: int, feature_cols: list) -> Optional[np.ndarray]:
    """Construct the feature row in the order the model expects. Mirrors
    `scripts/train_b_ml3.py::_build_feature_matrix` — keep both in sync.

    mfv is a dict-shaped row from match_feature_vectors. selection is "home"
    | "draw" | "away".
    """
    row = {}

    # Selection-aware (pivoted from per-side MFV columns)
    row["edge_proxy"] = (ensemble_prob or 0) - (opening_implied or 0)
    row["ensemble_prob"] = ensemble_prob or 0
    row["opening_implied"] = opening_implied or 0
    row["pinnacle_line_move"] = mfv.get(f"pinnacle_line_move_{selection}_at_t6h")
    row["sharp_consensus"] = mfv.get(f"sharp_consensus_{selection}_at_t6h")
    row["odds_volatility"] = mfv.get(f"odds_volatility_{selection}_at_t6h")

    # Match-level — replicate from MFV
    for col in ("bookmaker_disagreement", "elo_diff", "form_ppg_home", "form_ppg_away",
                "lineup_confirmed", "rest_days_home", "rest_days_away",
                "fixture_importance", "league_position_home",
                "odds_drift_home_at_t6h", "steam_move_at_t6h",
                "form_momentum_home", "form_momentum_away"):
        row[col] = mfv.get(col)

    row["time_to_kickoff_h"] = time_to_kickoff_h
    row["league_tier"] = league_tier

    # Selection one-hot (drop_first="home" → only draw + away columns)
    row["selection_draw"] = 1 if selection == "draw" else 0
    row["selection_away"] = 1 if selection == "away" else 0

    # Missing-indicators (per training script's THIN_FEATURES_FOR_INDICATORS)
    thin = ["bookmaker_disagreement", "fixture_importance", "league_position_home",
            "rest_days_home", "rest_days_away", "pinnacle_line_move",
            "sharp_consensus", "odds_volatility", "odds_drift_home_at_t6h"]
    for c in thin:
        row[f"{c}_missing"] = 1 if row.get(c) is None else 0

    # Fill NaN with 0 (training used median; in production we just zero — close
    # enough since the missing indicator captures the absence signal).
    df = pd.DataFrame([row])
    for col in df.columns:
        if df[col].dtype == bool:
            df[col] = df[col].astype(int)
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.fillna(0)

    # Align to feature_cols order; if a column is unexpectedly missing, zero it.
    try:
        return df[feature_cols].to_numpy().astype(float)
    except KeyError:
        # Fallback: rebuild with zeros for missing cols
        out = np.zeros((1, len(feature_cols)))
        for i, c in enumerate(feature_cols):
            if c in df.columns:
                out[0, i] = float(df[c].iloc[0])
        return out


_mfv_cache: dict = {}


def _get_mfv_row(match_id: str) -> Optional[dict]:
    """Fetch MFV row by match_id with a small in-process cache."""
    if match_id in _mfv_cache:
        return _mfv_cache[match_id]
    rows = execute_query("""
        SELECT mfv.*, l.tier AS league_tier, m.date AS match_kickoff
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE mfv.match_id = %s
        LIMIT 1
    """, (match_id,))
    out = dict(rows[0]) if rows else None
    _mfv_cache[match_id] = out
    return out


def score_bet(match_id: str, selection: str, ensemble_prob: float,
              opening_implied: Optional[float] = None,
              now=None) -> Optional[float]:
    """Score a single bet candidate. Returns probability in [0, 1] that this
    bet beats the closing line, or None if scoring is unavailable (bundle
    missing, MFV missing, etc.).

    Callers should treat None as 'no signal' — i.e. fall through to the
    existing bot-rule decision rather than auto-reject.
    """
    bundle = _load_bundle(META_B_ML3_VERSION)
    if bundle is None:
        return None
    mfv = _get_mfv_row(match_id)
    if mfv is None:
        return None
    if opening_implied is None:
        opening_implied = mfv.get(f"opening_implied_{selection}")

    # time_to_kickoff_h: hours from now until match.date
    from datetime import datetime, timezone
    _now = now or datetime.now(timezone.utc)
    kickoff = mfv.get("match_kickoff")
    if kickoff is None:
        ttk = 24.0  # default if unknown
    else:
        # match_kickoff may be naive or tz-aware depending on driver
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        ttk = max(0.0, (kickoff - _now).total_seconds() / 3600.0)

    league_tier = int(mfv.get("league_tier") or 4)

    X = _build_feature_row(mfv, selection, ensemble_prob, opening_implied, ttk,
                            league_tier, bundle["feature_cols"])
    if X is None:
        return None
    try:
        X_scaled = bundle["scaler"].transform(X)
        score = float(bundle["model"].predict_proba(X_scaled)[0, 1])
        return score
    except Exception:
        return None


def should_fire(score: Optional[float]) -> bool:
    """Returns True if the bet should be placed. Honors META_B_ML3_ENABLED:
       - when disabled → always True (passive logging only)
       - when enabled and score is None → True (no-signal fall-through)
       - when enabled and score ≥ threshold → True
       - when enabled and score < threshold → False (drop the bet)
    """
    if not META_B_ML3_ENABLED:
        return True
    if score is None:
        return True
    return score >= META_B_ML3_THRESHOLD
