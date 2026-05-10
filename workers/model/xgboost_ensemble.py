"""
OddsIntel — XGBoost Ensemble for Live Pipeline

Loads saved XGBoost models and computes predictions for Tier A teams.

Two schemas supported, switched at runtime by inspecting the loaded
`feature_cols.pkl`:

  * **Kaggle schema** (v9*) — features `home_elo`, `h_*`, `a_*`, `xg_diff`,
    `form_diff`, `tier`. Looked up from `features_v9.csv` keyed by team name.
  * **MFV schema** (v10+) — features `elo_home`, `form_ppg_home`,
    `goals_for_avg_home`, etc. Plus Stage-2a `<col>_missing` indicators.
    Pulled from `match_feature_vectors` keyed by `match_id`.

The schema check is a cheap sentinel: presence of `"elo_home"` in
`feature_cols` is the new schema, else legacy. Both paths feed into the
same downstream prediction logic (1X2 / OU / goal regressors).

Blends 50/50 with Poisson predictions to form the ensemble.

Architecture:
  - Poisson: thinks in goals (exp_home, exp_away → scoreline PMF)
  - XGBoost: thinks in features → P(home), P(draw), P(away), P(over 2.5)
  - Ensemble: blend → better calibrated than either alone
  - Model disagreement: abs(xgb_prob - poisson_prob) → uncertainty signal

Only works for Tier A teams. Tier B/C falls back to Poisson-only.
"""

import os
import joblib
import pandas as pd
from pathlib import Path

ENGINE_DIR = Path(__file__).parent.parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
MODELS_DIR = ENGINE_DIR / "data" / "models" / "soccer"

# Production default — trained on the Kaggle CSV cohort (2022-25).
DEFAULT_MODEL_VERSION = "v9a_202425"

# `MODEL_VERSION` is read from env at module import. Setting it on Railway
# lets ops flip the active model without code changes; the harness exposes
# this through the `model_version` column on `predictions` / `simulated_bets`
# so post-hoc evaluation can compare versions on overlapping settled matches.
MODEL_VERSION = os.environ.get("MODEL_VERSION", DEFAULT_MODEL_VERSION)

# Cache loaded models and feature data (loaded once per pipeline run)
_model_cache = {}
_feature_cache = {}


def _load_models() -> dict:
    """Load saved XGBoost models from disk. Cached after first call.

    ML-BUNDLE-STORAGE: if the bundle dir doesn't exist locally, attempt to
    pull it from Supabase Storage first (`workers/model/storage.py`). This
    is what makes ephemeral Railway deploys safe: a fresh container with
    `MODEL_VERSION=v_20260517` set won't have the bundle on its filesystem,
    so we hydrate from Storage on the first prediction. Cached for the
    container's lifetime after that. Falls back to empty (Poisson-only)
    if the version is in neither place — caller logs a warning."""
    if _model_cache:
        return _model_cache

    model_path = MODELS_DIR / MODEL_VERSION
    if not model_path.exists() or not (model_path / "feature_cols.pkl").exists():
        # Lazy import — Storage is only reached when the cache is cold AND
        # the bundle isn't on local disk (i.e. once per container lifetime
        # in production).
        try:
            from workers.model.storage import ensure_local_bundle
            present = ensure_local_bundle(MODEL_VERSION, MODELS_DIR)
        except Exception as e:
            from rich.console import Console
            Console().print(
                f"[yellow]Bundle {MODEL_VERSION} not local and Storage hydration "
                f"failed: {e} — falling back to Poisson-only.[/yellow]"
            )
            return {}
        if not present:
            from rich.console import Console
            Console().print(
                f"[yellow]Bundle {MODEL_VERSION} not in local disk OR Supabase "
                f"Storage — falling back to Poisson-only.[/yellow]"
            )
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


# Stage 2a indicator columns. When loading a v10+ bundle, feature_cols.pkl
# contains the augmented list (FEATURE_COLS + these as <col>_missing). We must
# recompute the indicators at inference from the raw MFV row — the model
# learned to split on the indicator alongside the imputed value.
_INFORMATIVE_MISSING_COLS = (
    "h2h_win_pct",
    "opening_implied_home", "opening_implied_draw", "opening_implied_away",
    "bookmaker_disagreement",
    "referee_cards_avg", "referee_home_win_pct", "referee_over25_pct",
)


def _build_row_from_mfv(match_id: str, feature_cols: list, tier: int) -> dict | None:
    """v10+ inference path. Pulls the row directly from match_feature_vectors
    by match_id. Returns None if the row doesn't exist yet — caller falls
    back to Poisson. Pre-KO rows are written by MFV-LIVE-BUILD inside
    `run_morning`; finished-match rows by the nightly settlement ETL."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT * FROM match_feature_vectors WHERE match_id = %s LIMIT 1",
            (match_id,),
        )
    except Exception:
        return None
    if not rows:
        return None

    raw = rows[0]
    row: dict = {}
    for col in feature_cols:
        if col == "tier":
            row[col] = tier
            continue
        if col.endswith("_missing"):
            base = col[:-len("_missing")]
            row[col] = 1 if (raw.get(base) is None) else 0
            continue
        v = raw.get(col)
        if v is None:
            # Mean-fill at inference is approximate — the model was trained on
            # per-league means, but at predict time we don't have the league
            # mean handy. Zero-fill is what feature_cols.pkl-aware builders
            # have always done; the indicator column carries the real signal.
            row[col] = 0.0
        else:
            try:
                row[col] = float(v)
            except (TypeError, ValueError):
                row[col] = 0.0
    return row


def _build_row_from_legacy_cache(home_team: str, away_team: str,
                                  tier: int, feature_cols: list) -> dict | None:
    """v9* (Kaggle schema) inference path. Looks up the latest cached feature
    vectors for each team from features_v9.csv and assembles a row in the
    exact column order the model expects."""
    features_data = _load_feature_data()
    if not features_data:
        return None

    home_feats = features_data.get(f"{home_team}_home")
    away_feats = features_data.get(f"{away_team}_away")
    if not home_feats or not away_feats:
        return None

    row = {}
    home_elo = home_feats.get("elo", 1500)
    away_elo = away_feats.get("elo", 1500)
    row["home_elo"] = home_elo
    row["away_elo"] = away_elo
    row["elo_diff"] = home_elo - away_elo
    row["home_elo_exp"] = 1 / (1 + 10 ** (-(home_elo - away_elo + 100) / 400))

    for col in feature_cols:
        if col.startswith("h_"):
            row[col] = home_feats.get(col, 0.0)
        elif col.startswith("a_"):
            row[col] = away_feats.get(col, 0.0)

    row["xg_diff"] = row.get("h_xg_for_avg", 0) - row.get("a_xg_for_avg", 0)
    row["form_diff"] = row.get("h_ppg", 1.3) - row.get("a_ppg", 1.3)
    row["overperf_diff"] = row.get("h_overperf_avg", 0) - row.get("a_overperf_avg", 0)
    row["tier"] = tier
    return row


def _is_mfv_schema(feature_cols) -> bool:
    """Sentinel test. v10+ FEATURE_COLS lead with `elo_home`; v9* uses
    `home_elo` (legacy Kaggle convention)."""
    cols = list(feature_cols) if not isinstance(feature_cols, list) else feature_cols
    return "elo_home" in cols and "home_elo" not in cols


def get_xgboost_prediction(home_team: str, away_team: str,
                           tier: int = 1,
                           match_id: str | None = None) -> dict | None:
    """
    Get XGBoost prediction for a match using saved models.

    `match_id` is required for v10+ (MFV-schema) models — the inference row
    is fetched from `match_feature_vectors` by id. For v9* (Kaggle-schema)
    models the lookup falls back to the legacy `features_v9.csv` cache keyed
    by `home_team` / `away_team`.

    Returns dict with probabilities, or None if features unavailable.
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

    feature_cols = models["feature_cols"]

    if _is_mfv_schema(feature_cols):
        # v10+ path — fetch the raw MFV row by match_id.
        if not match_id:
            return None
        row = _build_row_from_mfv(match_id, feature_cols, tier)
    else:
        # v9* legacy path — feature lookup by team name.
        row = _build_row_from_legacy_cache(home_team, away_team, tier, feature_cols)

    if row is None:
        return None

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


def load_blend_weight(tier: int | None = None) -> float:
    """
    Load the learned Poisson/XGBoost blend weight from model_calibration.

    With `tier`, prefers the tier-specific row `blend_weight_1x2_t{tier}`
    (ML-BLEND-DYNAMIC) and falls back to the global `blend_weight_1x2` if
    no tier row exists. Both fall back to 0.5 if neither row is present.

    Per-tier values are loaded once per (process, tier) pair and cached.
    """
    cache_key = f"t{tier}" if tier is not None else "global"
    if cache_key in _blend_weight_cache:
        return _blend_weight_cache[cache_key]

    try:
        import sys
        from pathlib import Path as _Path
        sys.path.insert(0, str(_Path(__file__).parent.parent.parent))
        from workers.api_clients.db import execute_query

        # Try tier-specific row first
        if tier is not None:
            rows = execute_query(
                """
                SELECT platt_a FROM model_calibration
                WHERE market = %s
                ORDER BY fitted_at DESC LIMIT 1
                """,
                [f"blend_weight_1x2_t{tier}"],
            )
            if rows:
                w = float(rows[0]["platt_a"])
                _blend_weight_cache[cache_key] = w
                return w
            # else fall through to global

        rows = execute_query(
            """
            SELECT platt_a FROM model_calibration
            WHERE market = 'blend_weight_1x2'
            ORDER BY fitted_at DESC LIMIT 1
            """,
            [],
        )
        w = float(rows[0]["platt_a"]) if rows else 0.5
    except Exception:
        w = 0.5

    _blend_weight_cache[cache_key] = w
    return w


_blend_weight_cache: dict[str, float] = {}


def ensemble_prediction(poisson_pred: dict, xgb_pred: dict,
                        poisson_weight: float | None = None,
                        tier: int | None = None) -> dict:
    """
    Blend Poisson and XGBoost predictions.

    poisson_weight: if None, loads from model_calibration (learned via
    scripts/fit_blend_weights.py). With `tier`, prefers a tier-specific
    weight (ML-BLEND-DYNAMIC). Falls back to 0.5 if no learned value.
    Pass an explicit float to override.

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
    if poisson_weight is None:
        poisson_weight = load_blend_weight(tier=tier)
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
