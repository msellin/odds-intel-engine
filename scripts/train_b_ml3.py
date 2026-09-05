"""B-ML3 — train the Stage-3 meta-model (2026-05-24).

Binary classifier: P(pseudo_clv > 0) per (match × selection). Filters bot output
before placement — a bet is only fired when the meta-model believes it has a
positive expected CLV.

Feature list locked by META-FEATURE-DESIGN (MODEL_WHITEPAPER §3.4). Training
window filter: `match_date >= '2026-05-06'`. Each MFV row contributes 3 training
rows (home/draw/away selections), so the effective training set is ~3× the row
count.

Output bundle layout (mirrors data/models/soccer/<version>/ convention):
    data/models/meta/<version>/
        b_ml3.pkl           — sklearn LogisticRegression
        feature_cols.pkl    — list[str] of feature column order
        threshold.json      — {chosen_threshold, validation_auc, ece, n_train, n_holdout}
        coefficients.json   — feature coefficient inspection (drop |coef| < 0.05 next iter)

Usage:
    python3 scripts/train_b_ml3.py                       # default v_YYYYMMDD tag
    python3 scripts/train_b_ml3.py --version v_first
    python3 scripts/train_b_ml3.py --dry-run             # train but don't save

META-FEATURE-PRUNE (2026-09-05) adds three flags, all defaulting to the
historical behaviour:
    --feature-set  full | lean | core | micro   (drop near-zero-coef features)
    --missing-mode all | none | structural      (drop the data-coverage leak)
    --cutoff YYYY-MM-DD                          (hold out a real OOS window)

    python3 scripts/train_b_ml3.py --version v_EXP_lean_none \
        --feature-set lean --missing-mode none --cutoff 2026-08-01
"""
from __future__ import annotations
import sys, json, argparse
from pathlib import Path
from datetime import date as _date
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import joblib
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from sklearn.linear_model import LogisticRegression
import xgboost as xgb  # S6-P2 (2026-05-25): XGBoost meta-model option
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import calibration_curve

from workers.api_clients.db import execute_query

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "meta"

# Feature columns locked by MODEL_WHITEPAPER §3.4 (META-FEATURE-DESIGN 2026-05-24).
# Selection-aware features have _<sel> suffix and are pivoted at row-unpack time.
# Match-level features are repeated across all 3 selection rows for the same match.

# Match-level numeric features (same value for all 3 rows of a match).
#
# DATA-LEAK NOTE (2026-05-24, B-ML3 v1 training): the original
# odds_drift_home / steam_move MFV fields are derived from the LATEST snapshot
# at MFV-build time — on historical (settled) rows that IS the closing line,
# which is what pseudo_clv (the target) is computed against. So they leaked.
# Replaced by the *_at_t6h columns (migration 128) which use snapshots
# WHERE timestamp <= match.date - 6h, eliminating the leak. v2 trains on
# the _at_t6h variants. v1's odds_drift_home is excluded entirely.
MATCH_LEVEL_FEATURES = [
    "bookmaker_disagreement",
    "elo_diff",
    "form_ppg_home",
    "form_ppg_away",
    "lineup_confirmed",      # bool
    "rest_days_home",
    "rest_days_away",
    "fixture_importance",
    "league_position_home",
    # B-ML3 v2 (2026-05-24): leak-free market microstructure features.
    "odds_drift_home_at_t6h",
    "steam_move_at_t6h",              # bool
    # B-ML3 v2.1 (2026-05-25): form momentum from matches table
    "form_momentum_home",
    "form_momentum_away",
    # B-ML3 v2.2 (2026-05-25): Pinnacle AH cross-market signal (G fixed)
    "pinnacle_ah_line_at_t6h",
    "pinnacle_ah_line_move",
    # B-ML3 v3 (2026-05-25): MFV-V3 signal batch — backtest-validated lifts.
    "league_draw_rate_ytd",            # +11.6pp Q4 vs Q1 draw lift (LEAGUE-DRAW-YTD)
    "season_progress",                 # late vs early +7.7pp Over 2.5 (LEAGUE-SEASON-PHASE)
    "line_velocity",                   # REVERSE -6.6pp CLV-beat Q4 |v| (LINE-VELOCITY)
    "xg_overperf_home",                # regression-to-mean indicator (SIG-12)
    "xg_overperf_away",
    "league_clv_efficiency",           # 60d mean pseudo_clv per league
    "injury_severity_score_home",      # SEVERE×3 + MODERATE×1.5 + MINOR×0.5 + UNKNOWN×1
    "injury_severity_score_away",
    "team_avg_player_rating_home",     # AF player ratings (sparse ~5% coverage)
    "team_avg_player_rating_away",
]

# Selection-aware market features added in v2 — pivoted into per-selection rows.
SELECTION_AWARE_V2 = [
    "pinnacle_line_move",      # _<sel>_at_t6h in MFV
    "sharp_consensus",         # _<sel>_at_t6h
    "odds_volatility",         # _<sel>_at_t6h
]

# Selection-aware features: ensemble_prob_<sel> + opening_implied_<sel> + the
# computed edge proxy = (ensemble_prob_<sel> − opening_implied_<sel>).
# These get unpacked into per-selection rows during training-frame build.

# Categorical features one-hot-encoded post-build.
CATEGORICAL_FEATURES = ["selection_home", "selection_draw", "selection_away"]
# league_tier is also categorical but stored as int (1-4); we treat as ordinal numeric.


# ---------------------------------------------------------------------------
# META-FEATURE-PRUNE (2026-09-05) — reduced feature sets + missingness modes.
#
# Two defects motivated this, both read off v_PEEK_clvfix/coefficients.json:
#
#   (1) 29 of 44 features carry |coef| < 0.05 (injuries, lineups, form, xG,
#       player ratings, rest days, fixture importance, season progress,
#       league position). They add no signal and enlarge the overfitting
#       surface on a model whose CV folds already span AUC 0.587-0.785.
#       Note gotcha #26: several of them are 0.0% covered on SCHEDULED rows,
#       so they are structurally absent at serve time — a near-zero
#       coefficient there means "never present", not "no predictive value".
#
#   (2) 5 of the top 14 coefficients are `*_missing` indicator flags.
#       Missingness of the sharp-book features tracks league tier and book
#       coverage, so the model partly learns WHICH FIXTURES HAVE COMPLETE
#       DATA rather than which bets have edge. That is a coverage artefact,
#       and it moves whenever we add a book or backfill a column.
#
# `--feature-set full --missing-mode all` reproduces the historical behaviour
# exactly and stays the default; nothing on the production path changes.
# ---------------------------------------------------------------------------

# Always-present core (selection-aware market terms). Never pruned.
_ALWAYS = ["edge_proxy", "ensemble_prob", "opening_implied"]

FEATURE_SETS: dict[str, list[str] | None] = {
    # None = every feature the historical path builds (44 cols with indicators).
    "full": None,

    # Keep only what the baseline logistic actually leaned on: the features
    # whose |coef| >= 0.05 in v_PEEK_clvfix, excluding the _missing flags.
    "lean": _ALWAYS + [
        "pinnacle_line_move",
        "sharp_consensus",
        "odds_drift_home_at_t6h",
        "line_velocity",
        "pinnacle_ah_line_at_t6h",
        "elo_diff",
        "form_ppg_home",
    ],

    # Market microstructure only — no football/context features at all.
    # Tests directly whether the team-quality block contributes anything.
    "core": _ALWAYS + [
        "pinnacle_line_move",
        "sharp_consensus",
        "odds_volatility",
        "odds_drift_home_at_t6h",
        "steam_move_at_t6h",
        "line_velocity",
        "pinnacle_ah_line_at_t6h",
        "pinnacle_ah_line_move",
        "bookmaker_disagreement",
    ],

    # The four largest-magnitude market terms only. Deliberate extreme:
    # if this matches the 44-feature model, the other 40 are decoration.
    "micro": ["edge_proxy", "pinnacle_line_move",
              "odds_drift_home_at_t6h", "line_velocity"],
}

# Which columns get a companion `<col>_missing` indicator.
#   all        — historical behaviour (11 indicators)
#   none       — median-impute silently, no indicator at all
#   structural — only the indicators whose missingness is NOT sharp-book
#                coverage. rest_days comes from our own fixture history, so
#                its absence is a data-completeness fact about the team's
#                schedule rather than about which books quote the league.
_MISSING_ALL = [
    "bookmaker_disagreement", "fixture_importance",
    "league_position_home", "rest_days_home", "rest_days_away",
    "pinnacle_line_move", "sharp_consensus", "odds_volatility",
    "odds_drift_home_at_t6h",
    "pinnacle_ah_line_at_t6h", "pinnacle_ah_line_move",
]
MISSING_MODES: dict[str, list[str]] = {
    "all": _MISSING_ALL,
    "none": [],
    "structural": ["rest_days_home", "rest_days_away"],
}


def _load_training_data():
    """Load + unpivot MFV training data into per-(match × selection) rows."""
    console.print("[bold]Loading B-ML3 training data...[/bold]")
    rows = execute_query("""
        SELECT
          mfv.match_id, mfv.match_date,
          mfv.ensemble_prob_home, mfv.ensemble_prob_draw, mfv.ensemble_prob_away,
          mfv.opening_implied_home, mfv.opening_implied_draw, mfv.opening_implied_away,
          mfv.pseudo_clv_home, mfv.pseudo_clv_draw, mfv.pseudo_clv_away,
          mfv.bookmaker_disagreement,
          mfv.elo_diff,
          mfv.form_ppg_home, mfv.form_ppg_away,
          mfv.lineup_confirmed,
          mfv.rest_days_home, mfv.rest_days_away,
          mfv.fixture_importance,
          mfv.league_position_home,
          mfv.built_at,
          l.tier AS league_tier,
          m.date AS match_kickoff,
          -- B-ML3 v2 (2026-05-24): leak-free market microstructure features
          mfv.odds_drift_home_at_t6h,
          mfv.steam_move_at_t6h,
          mfv.pinnacle_line_move_home_at_t6h,
          mfv.pinnacle_line_move_draw_at_t6h,
          mfv.pinnacle_line_move_away_at_t6h,
          mfv.sharp_consensus_home_at_t6h,
          mfv.sharp_consensus_draw_at_t6h,
          mfv.sharp_consensus_away_at_t6h,
          mfv.odds_volatility_home_at_t6h,
          mfv.odds_volatility_draw_at_t6h,
          mfv.odds_volatility_away_at_t6h,
          -- B-ML3 v2.1: form_momentum (B) — backfilled 2026-05-25
          mfv.form_momentum_home,
          mfv.form_momentum_away,
          -- B-ML3 v2.2 (2026-05-25): Pinnacle AH main-line drift (G fixed)
          mfv.pinnacle_ah_line_at_t6h,
          mfv.pinnacle_ah_line_move,
          -- B-ML3 v3 (2026-05-25): MFV-V3 signal batch
          mfv.league_draw_rate_ytd,
          mfv.season_progress,
          mfv.line_velocity,
          mfv.xg_overperf_home,
          mfv.xg_overperf_away,
          mfv.league_clv_efficiency,
          mfv.injury_severity_score_home,
          mfv.injury_severity_score_away,
          mfv.team_avg_player_rating_home,
          mfv.team_avg_player_rating_away
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE mfv.match_date >= '2026-05-06'
          AND mfv.opening_implied_home IS NOT NULL
          AND mfv.pseudo_clv_home IS NOT NULL
          AND mfv.pseudo_clv_draw IS NOT NULL
          AND mfv.pseudo_clv_away IS NOT NULL
        ORDER BY mfv.match_date ASC
    """)
    df = pd.DataFrame(rows)
    console.print(f"  Loaded {len(df):,} matches in training window")

    # Unpivot: each match → 3 rows (home/draw/away). Selection-specific cols.
    long_rows = []
    for _, m in df.iterrows():
        for sel in ("home", "draw", "away"):
            ens = float(m[f"ensemble_prob_{sel}"]) if m[f"ensemble_prob_{sel}"] is not None else None
            imp = float(m[f"opening_implied_{sel}"]) if m[f"opening_implied_{sel}"] is not None else None
            clv = float(m[f"pseudo_clv_{sel}"]) if m[f"pseudo_clv_{sel}"] is not None else None
            if ens is None or imp is None or clv is None:
                continue
            # time_to_kickoff: hours from when MFV row built to match kickoff.
            ttk = None
            if m["built_at"] is not None and m["match_kickoff"] is not None:
                ttk = (m["match_kickoff"] - m["built_at"]).total_seconds() / 3600.0
            long_rows.append({
                "match_id": m["match_id"],
                "match_date": m["match_date"],
                "selection": sel,
                # Selection-aware
                "ensemble_prob": ens,
                "opening_implied": imp,
                "edge_proxy": ens - imp,
                # B-ML3 v2 selection-aware market microstructure (pivoted)
                "pinnacle_line_move": m.get(f"pinnacle_line_move_{sel}_at_t6h"),
                "sharp_consensus": m.get(f"sharp_consensus_{sel}_at_t6h"),
                "odds_volatility": m.get(f"odds_volatility_{sel}_at_t6h"),
                # Match-level (replicated)
                **{c: m[c] for c in MATCH_LEVEL_FEATURES},
                "time_to_kickoff_h": ttk,
                "league_tier": int(m["league_tier"]) if m["league_tier"] is not None else 4,
                # Target
                "y_clv_beat": 1 if clv > 0 else 0,
            })
    long_df = pd.DataFrame(long_rows)
    console.print(f"  Unpivoted to {len(long_df):,} (match × selection) training rows")
    console.print(f"  Base rate P(pseudo_clv > 0): {long_df['y_clv_beat'].mean():.3f}")
    return long_df


def _build_feature_matrix(long_df: pd.DataFrame,
                          feature_set: str = "full",
                          missing_mode: str = "all") -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Build X, y, feature_cols. Numerics imputed with median; bools cast to int.

    META-FEATURE-PRUNE (2026-09-05):
      feature_set  — key into FEATURE_SETS. "full" (default) keeps every column
                     and reproduces the historical bundle byte-for-byte.
      missing_mode — key into MISSING_MODES. "all" (default) is historical;
                     "none" imputes with the median and adds no indicator, so
                     the model cannot learn which fixtures have complete data.
    """
    # One-hot selection (drop one to avoid multicollinearity in logistic regression)
    sel_dummies = pd.get_dummies(long_df["selection"], prefix="selection", drop_first=True)

    feature_frame = pd.concat([
        long_df[[
            "edge_proxy", "ensemble_prob", "opening_implied",
            # v2 selection-aware (pivoted from _<sel>_at_t6h)
            "pinnacle_line_move", "sharp_consensus", "odds_volatility",
            *MATCH_LEVEL_FEATURES,
            "time_to_kickoff_h",
            "league_tier",
        ]].copy(),
        sel_dummies,
    ], axis=1)

    # Cast booleans to int and coerce all to float.
    for col in feature_frame.columns:
        if feature_frame[col].dtype == bool:
            feature_frame[col] = feature_frame[col].astype(int)
        feature_frame[col] = pd.to_numeric(feature_frame[col], errors="coerce")

    # Median imputation per column. Indicators for missingness on the thin features.
    # B-ML3 v2 (2026-05-24): the *_at_t6h features have ~38-56% coverage in MFV
    # (depends on Pinnacle / sharp / accessible book presence per match).
    # Their missingness is informative (matches without sharp-book coverage are
    # systematically different) so we add indicators for them too.
    #
    # META-FEATURE-PRUNE (2026-09-05): that "informative missingness" argument
    # is exactly the leak. Sharp-book presence tracks league tier and our own
    # ingestion coverage, not the bet's edge — so `missing_mode="none"` is
    # available to drop the indicators entirely.
    THIN_FEATURES_FOR_INDICATORS = MISSING_MODES[missing_mode]
    for col in THIN_FEATURES_FOR_INDICATORS:
        if col in feature_frame.columns:
            feature_frame[f"{col}_missing"] = feature_frame[col].isna().astype(int)

    feature_frame = feature_frame.fillna(feature_frame.median(numeric_only=True))
    # A column that is 100pct NULL leaves NaN after a median fill (the median of an
    # all-NaN column is NaN). Zero it so the fit cannot fail on an empty feature.
    feature_frame = feature_frame.fillna(0.0)

    # Prune to the requested feature set. Selection dummies and the retained
    # missingness indicators always survive the prune.
    keep = FEATURE_SETS[feature_set]
    if keep is not None:
        allowed = set(keep)
        allowed |= {f"{c}_missing" for c in THIN_FEATURES_FOR_INDICATORS if c in allowed}
        allowed |= set(sel_dummies.columns)
        dropped = [c for c in feature_frame.columns if c not in allowed]
        feature_frame = feature_frame[[c for c in feature_frame.columns if c in allowed]]
        console.print(f"  [cyan]feature-set={feature_set}: kept {feature_frame.shape[1]} cols, "
                      f"dropped {len(dropped)}[/cyan]")
    console.print(f"  [cyan]missing-mode={missing_mode}: "
                  f"{len(THIN_FEATURES_FOR_INDICATORS)} indicator column(s)[/cyan]")

    feature_cols = list(feature_frame.columns)
    X = feature_frame
    y = long_df["y_clv_beat"]
    return X, y, feature_cols


def _train_and_evaluate(X: pd.DataFrame, y: pd.Series, feature_cols: list[str],
                        model_type: str = "logistic"):
    """5-fold TimeSeriesSplit CV → final fit on all data. Returns model + metrics.

    S6-P2 (2026-05-25): supports `model_type="xgboost"` for non-linear meta-model.
    XGBoost can pick up feature interactions logistic can't (e.g.
    `lineup_confirmed × league_tier × form_ppg_diff`) without us having to
    enumerate them. Trade-off: less interpretable per-feature coefficients.
    """
    console.print(f"\n[bold]CV evaluation (TimeSeriesSplit, n_splits=5, model={model_type})[/bold]")

    tscv = TimeSeriesSplit(n_splits=5)
    cv_aucs = []
    cv_briers = []
    cv_log_losses = []
    for fold, (tr, va) in enumerate(tscv.split(X)):
        Xtr, Xva = X.iloc[tr], X.iloc[va]
        ytr, yva = y.iloc[tr], y.iloc[va]
        if model_type == "xgboost":
            # S6-P2: XGBoost doesn't need scaling. class_weight surrogate via
            # scale_pos_weight = neg / pos.
            scaler = None
            pos = max(int((ytr == 1).sum()), 1)
            neg = max(int((ytr == 0).sum()), 1)
            clf = xgb.XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.08,
                subsample=0.8, colsample_bytree=0.8,
                objective="binary:logistic", eval_metric="logloss",
                scale_pos_weight=neg / pos,
                random_state=42, verbosity=0, n_jobs=-1,
            )
            clf.fit(Xtr, ytr)
            proba = clf.predict_proba(Xva)[:, 1]
        else:
            scaler = StandardScaler()
            Xtr_s = scaler.fit_transform(Xtr)
            Xva_s = scaler.transform(Xva)
            clf = LogisticRegression(
                max_iter=1000, C=1.0, solver="lbfgs",
                class_weight="balanced",
            )
            clf.fit(Xtr_s, ytr)
            proba = clf.predict_proba(Xva_s)[:, 1]
        auc = roc_auc_score(yva, proba)
        brier = brier_score_loss(yva, proba)
        ll = log_loss(yva, proba)
        cv_aucs.append(auc)
        cv_briers.append(brier)
        cv_log_losses.append(ll)
        console.print(f"  Fold {fold+1}: AUC={auc:.4f}  Brier={brier:.4f}  LL={ll:.4f}  (n_train={len(tr):,}  n_val={len(va):,})")

    console.print(f"\n  [green]Mean AUC: {np.mean(cv_aucs):.4f} ± {np.std(cv_aucs):.4f}[/green]")
    console.print(f"  [green]Mean Brier: {np.mean(cv_briers):.4f}[/green]")
    console.print(f"  [green]Mean LL: {np.mean(cv_log_losses):.4f}[/green]")

    # Final model on all training data
    console.print("\n[bold]Final fit on all training data[/bold]")
    if model_type == "xgboost":
        scaler = None
        pos = max(int((y == 1).sum()), 1)
        neg = max(int((y == 0).sum()), 1)
        final_model = xgb.XGBClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.08,
            subsample=0.8, colsample_bytree=0.8,
            objective="binary:logistic", eval_metric="logloss",
            scale_pos_weight=neg / pos,
            random_state=42, verbosity=0, n_jobs=-1,
        )
        final_model.fit(X, y)
        # Feature importance instead of coefficients
        importances = dict(zip(feature_cols, final_model.feature_importances_))
        coefs = importances  # store under same key so threshold.json is consistent
        console.print("\n[bold]Feature importances (XGBoost gain, sorted)[/bold]")
        t = Table()
        for col in ("feature", "importance"):
            t.add_column(col)
        for feat, c in sorted(importances.items(), key=lambda kv: kv[1], reverse=True):
            t.add_row(feat, f"{c:.4f}")
        console.print(t)
    else:
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        final_model = LogisticRegression(
            max_iter=1000, C=1.0, solver="lbfgs",
            class_weight="balanced",
        )
        final_model.fit(X_scaled, y)
        coefs = dict(zip(feature_cols, final_model.coef_[0]))
        console.print("\n[bold]Feature coefficients (sorted by |coef|)[/bold]")
        t = Table()
        for col in ("feature", "coef", "|coef|"):
            t.add_column(col)
        for feat, c in sorted(coefs.items(), key=lambda kv: abs(kv[1]), reverse=True):
            t.add_row(feat, f"{c:+.4f}", f"{abs(c):.4f}")
        console.print(t)

    near_zero = [f for f, c in coefs.items() if abs(c) < 0.05]
    if near_zero:
        console.print(f"\n  [yellow]Near-zero coefficients (|coef|<0.05) — drop in v2: {near_zero}[/yellow]")

    return final_model, scaler, {
        "cv_auc_mean": float(np.mean(cv_aucs)),
        "cv_auc_std": float(np.std(cv_aucs)),
        "cv_brier_mean": float(np.mean(cv_briers)),
        "cv_brier_std": float(np.std(cv_briers)),
        "cv_log_loss_mean": float(np.mean(cv_log_losses)),
        "cv_folds_auc": [float(x) for x in cv_aucs],
        "n_training_rows": int(len(X)),
        "base_rate": float(y.mean()),
        "coefficients": {k: float(v) for k, v in coefs.items()},
    }


def _pick_threshold(model, scaler, X, y) -> dict:
    """Choose firing threshold by maximizing precision-at-volume on holdout.
    Default 0.5 if no clear winner. Returns threshold and metrics at chosen value.
    Scaler may be None (XGBoost path)."""
    X_eval = X if scaler is None else scaler.transform(X)
    proba = model.predict_proba(X_eval)[:, 1]
    # Sweep thresholds 0.30..0.70 in 0.025 steps
    best = {"threshold": 0.5, "score": -1e9, "metrics": {}}
    for t in np.arange(0.30, 0.71, 0.025):
        pred = (proba >= t).astype(int)
        n_fired = int(pred.sum())
        if n_fired == 0:
            continue
        precision = float((y[pred == 1] == 1).mean())
        # Score: precision × log(n_fired) — balances precision and volume
        score = precision * np.log(max(n_fired, 1))
        if score > best["score"]:
            best = {
                "threshold": float(t),
                "score": float(score),
                "metrics": {
                    "n_fired": n_fired,
                    "precision": precision,
                    "recall": float((pred[y == 1] == 1).mean()),
                },
            }
    return best


def _load_bets_mode_data(days: int = 60) -> pd.DataFrame:
    """B-ML3-BETS-MODE (2026-06-07): train on actual bot-fired bets, not all MFV rows.

    Root cause of the inverted signal: training on all 7K MFV rows teaches the
    model "what a match with positive pseudo_clv looks like" — but at inference
    time it scores bets that already passed edge filters, a completely different
    distribution. Training on the fired bets themselves fixes the mismatch.

    Label: clv_pinnacle > median(clv_pinnacle) — real Pinnacle closing-line CLV,
    relative threshold. Gives a proper ~50/50 split even though all active bots
    have positive CLV (absolute threshold 'clv > 0' is 84%+ positive = useless).

    Only active bots included. Only bets with clv_pinnacle populated (needs
    a Pinnacle closing snapshot after settlement). Joins to MFV for features.
    """
    console.print("[bold]Loading B-ML3 bets-mode training data (active bots + real CLV)...[/bold]")
    rows = execute_query(f"""
        SELECT
          sb.id as bet_id,
          sb.selection,
          -- META-MODEL-CLV-TARGET-2026-08-26: train on the DE-VIGGED Pinnacle
          -- CLV, not the raw one. The raw column carries Pinnacle's overround
          -- (measured +12.24 pct mean vs +5.39 pct de-vigged — a 6.85pp shift) and,
          -- until PIN-CLOSE-PRE-KO-FALLBACK-2026-08-26, could be sourced from
          -- an IN-PLAY tick on any match where is_closing was never marked.
          --
          -- Measured on the same 2,340 settled rows, sorting by each label:
          --     label   Q1     Q2      Q3     Q4     Q5    monotone  median split
          --     raw    +3.2  -13.0   -0.7  +15.0  +14.5     2/4      +12.4/-4.8
          --     devig  -1.9   -3.6   +2.9   +9.0  +12.5     4/4      +13.4/-5.8
          -- The raw label's Q1 being POSITIVE while Q2 is -13 pct is the tell: its
          -- extreme-negative values are in-play garbage, not bad bets.
          --
          -- COALESCE so rows the de-vig backfill could not compute (markets
          -- without a full Pinnacle complement) still contribute rather than
          -- silently shrinking an already-small bets-mode window.
          COALESCE(sb.clv_pinnacle_devig, sb.clv_pinnacle)::float as clv_pinnacle,
          sb.created_at,
          mfv.match_id,
          mfv.match_date,
          mfv.ensemble_prob_home, mfv.ensemble_prob_draw, mfv.ensemble_prob_away,
          mfv.opening_implied_home, mfv.opening_implied_draw, mfv.opening_implied_away,
          mfv.bookmaker_disagreement, mfv.elo_diff,
          mfv.form_ppg_home, mfv.form_ppg_away,
          mfv.lineup_confirmed, mfv.rest_days_home, mfv.rest_days_away,
          mfv.fixture_importance, mfv.league_position_home, mfv.built_at,
          mfv.odds_drift_home_at_t6h, mfv.steam_move_at_t6h,
          mfv.pinnacle_line_move_home_at_t6h, mfv.pinnacle_line_move_draw_at_t6h,
          mfv.pinnacle_line_move_away_at_t6h,
          mfv.sharp_consensus_home_at_t6h, mfv.sharp_consensus_draw_at_t6h,
          mfv.sharp_consensus_away_at_t6h,
          mfv.odds_volatility_home_at_t6h, mfv.odds_volatility_draw_at_t6h,
          mfv.odds_volatility_away_at_t6h,
          mfv.form_momentum_home, mfv.form_momentum_away,
          mfv.pinnacle_ah_line_at_t6h, mfv.pinnacle_ah_line_move,
          mfv.league_draw_rate_ytd, mfv.season_progress, mfv.line_velocity,
          mfv.xg_overperf_home, mfv.xg_overperf_away,
          mfv.league_clv_efficiency,
          mfv.injury_severity_score_home, mfv.injury_severity_score_away,
          mfv.team_avg_player_rating_home, mfv.team_avg_player_rating_away,
          l.tier AS league_tier,
          m.date AS match_kickoff
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        JOIN match_feature_vectors mfv ON mfv.match_id = sb.match_id
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE b.is_active = true
          AND sb.result IN ('won', 'lost')
          AND sb.clv_pinnacle IS NOT NULL
          -- META-MODEL-CLV-LABEL-SANITY-2026-08-26: 205 of 2,633 backfilled
          -- rows (7.8 pct) carry an impossible CLV — the extremes run to +397 pct,
          -- all on longshots at odds 6.5-13.0. A +397 pct CLV means the soft book
          -- offered 13.0 while de-vigged Pinnacle called it a 38 pct chance; that is
          -- a data error (a mislabelled market or an outlier price), not an edge.
          -- Left in, they dominate a gradient-boosted fit, because the model can
          -- separate them trivially and they are concentrated on one odds band.
          -- 50 pct is a generous bound: a genuine closing-line beat of more than half
          -- the price does not happen.
          AND abs(COALESCE(sb.clv_pinnacle_devig, sb.clv_pinnacle)) <= 0.50
          AND sb.match_id IS NOT NULL
          AND sb.created_at >= NOW() - INTERVAL '{days} days'
        ORDER BY sb.created_at ASC
    """, [])

    if not rows:
        console.print("[red]No bets-mode rows found.[/red]")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    console.print(f"  Loaded {len(df):,} fired bets with real Pinnacle CLV")

    # Label: is this bet in the top half of CLV for this cohort?
    median_clv = float(df["clv_pinnacle"].median())
    console.print(f"  CLV median: {median_clv*100:+.1f}%  "
                  f"  range: {df['clv_pinnacle'].min()*100:.1f}% – {df['clv_pinnacle'].max()*100:.1f}%")

    # Map selection to home/draw/away for feature lookup
    SEL_MAP = {"1x2_home": "home", "1x2_draw": "draw", "1x2_away": "away",
               "home": "home", "draw": "draw", "away": "away"}

    long_rows = []
    skipped_non_1x2 = 0
    for _, r in df.iterrows():
        raw_sel = str(r["selection"]).lower()
        sel = SEL_MAP.get(raw_sel)
        # B-ML3-BETS-MODE-1X2-FILTER (2026-06-21): the previous behaviour
        # silently coerced OU/BTTS/AH/DC selections to the home-1X2 slot
        # via a None-fallback assignment. The MFV feature columns the model uses
        # (pinnacle_line_move_home_at_t6h, sharp_consensus_home_at_t6h,
        # opening_implied_home, etc.) describe the 1X2 market — joining
        # them to an OU/BTTS/AH/DC CLV label trains the model on garbage:
        # features describe one market, label describes a different one.
        # Audit 2026-06-21 found 22.6% of the 60d --bets-mode training
        # set (121/535 rows) was this kind of pollution. Skip non-1X2
        # rows entirely; per-market meta-modelling needs its own bundles.
        if sel is None:
            skipped_non_1x2 += 1
            continue
        ens = r.get(f"ensemble_prob_{sel}")
        imp = r.get(f"opening_implied_{sel}")
        if ens is None or imp is None:
            continue
        ens = float(ens) if ens is not None else None
        imp = float(imp) if imp is not None else None
        if ens is None or imp is None:
            continue
        ttk = None
        if r["built_at"] is not None and r["match_kickoff"] is not None:
            ttk = (r["match_kickoff"] - r["built_at"]).total_seconds() / 3600.0
        long_rows.append({
            "match_id": r["match_id"],
            "match_date": r["match_date"],
            "selection": sel,
            "ensemble_prob": ens,
            "opening_implied": imp,
            "edge_proxy": ens - imp,
            "pinnacle_line_move": r.get(f"pinnacle_line_move_{sel}_at_t6h"),
            "sharp_consensus": r.get(f"sharp_consensus_{sel}_at_t6h"),
            "odds_volatility": r.get(f"odds_volatility_{sel}_at_t6h"),
            **{c: r[c] for c in MATCH_LEVEL_FEATURES if c in r.index},
            "time_to_kickoff_h": ttk,
            "league_tier": int(r["league_tier"]) if r["league_tier"] is not None else 4,
            # Target: is this bet above the median CLV for this cohort?
            "y_clv_beat": 1 if float(r["clv_pinnacle"]) > median_clv else 0,
        })

    long_df = pd.DataFrame(long_rows)
    console.print(f"  Bets-mode rows: {len(long_df):,}  "
                  f"label balance: {long_df['y_clv_beat'].mean():.3f} positive")
    if skipped_non_1x2:
        console.print(f"  Skipped {skipped_non_1x2:,} non-1X2 bets "
                      f"(meta-model is 1X2-only by feature schema)")
    return long_df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=f"v_{_date.today().strftime('%Y%m%d')}",
                    help="Version tag — produces data/models/meta/<version>/")
    ap.add_argument("--model", choices=("logistic", "xgboost"), default="logistic",
                    help="Meta-model architecture. S6-P2 (2026-05-25) added xgboost.")
    ap.add_argument("--dry-run", action="store_true", help="Train but don't save the bundle")
    ap.add_argument("--bets-mode", action="store_true",
                    help="Train on actual bot-fired bets with real Pinnacle CLV label "
                         "(B-ML3-BETS-MODE 2026-06-07). Fixes distribution mismatch: "
                         "old mode trained on all MFV rows, not the bets that actually fired.")
    ap.add_argument("--bets-days", type=int, default=60,
                    help="Look-back window in days for --bets-mode (default 60)")
    # META-FEATURE-PRUNE (2026-09-05) — all three default to the historical path.
    ap.add_argument("--feature-set", choices=tuple(FEATURE_SETS), default="full",
                    help="Which feature block to train on. 'full' = historical 44-col "
                         "behaviour (default). 'lean'/'core'/'micro' progressively drop "
                         "the near-zero-coefficient features.")
    ap.add_argument("--missing-mode", choices=tuple(MISSING_MODES), default="all",
                    help="How to handle missingness. 'all' = historical 11 indicator "
                         "flags (default). 'none' = median-impute with no indicator, "
                         "removing the data-coverage leak. 'structural' = keep only "
                         "the non-book-coverage indicators.")
    ap.add_argument("--cutoff", default=None,
                    help="Exclude rows with match_date >= this ISO date from training. "
                         "Required for any honest out-of-sample evaluation — see "
                         "ANALYSIS_GOTCHAS #35.")
    args = ap.parse_args()

    if args.bets_mode:
        long_df = _load_bets_mode_data(days=args.bets_days)
        min_rows = 50
    else:
        long_df = _load_training_data()
        min_rows = 1000

    if args.cutoff:
        before = len(long_df)
        cut = pd.to_datetime(args.cutoff).date()
        keep_mask = pd.to_datetime(long_df["match_date"]).dt.date < cut
        long_df = long_df[keep_mask].reset_index(drop=True)
        console.print(f"  [cyan]--cutoff {args.cutoff}: {before:,} -> {len(long_df):,} "
                      f"training rows (match_date < cutoff)[/cyan]")

    if len(long_df) < min_rows:
        console.print(f"[red]Only {len(long_df)} training rows — need ≥{min_rows}. Aborting.[/red]")
        sys.exit(1)

    X, y, feature_cols = _build_feature_matrix(
        long_df, feature_set=args.feature_set, missing_mode=args.missing_mode)
    console.print(f"\n[bold]Feature matrix: {X.shape[0]:,} rows × {X.shape[1]} features[/bold]")
    console.print(f"  Features: {feature_cols}")

    model, scaler, metrics = _train_and_evaluate(X, y, feature_cols, model_type=args.model)
    thresh = _pick_threshold(model, scaler, X, y)
    console.print(f"\n[bold]Chosen firing threshold: {thresh['threshold']:.3f}[/bold]")
    console.print(f"  At threshold: n_fired={thresh['metrics']['n_fired']:,}  "
                  f"precision={thresh['metrics']['precision']:.3f}  "
                  f"recall={thresh['metrics']['recall']:.3f}")

    if args.dry_run:
        console.print("\n[yellow]--dry-run: not saving bundle[/yellow]")
        return

    # Save bundle. For xgboost path scaler is None; still serialize None so
    # the loader contract stays uniform (None means no scaling needed).
    out_dir = MODELS_DIR / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / "b_ml3.pkl")
    joblib.dump(scaler, out_dir / "scaler.pkl")
    joblib.dump(feature_cols, out_dir / "feature_cols.pkl")
    # Record the model architecture so meta_b_ml3.score_bet can branch correctly.
    with open(out_dir / "model_type.txt", "w") as f:
        f.write(args.model)
    with open(out_dir / "threshold.json", "w") as f:
        json.dump({
            "chosen_threshold": thresh["threshold"],
            "threshold_metrics": thresh["metrics"],
            "feature_set": args.feature_set,
            "missing_mode": args.missing_mode,
            "training_cutoff": args.cutoff,
            "bets_mode": bool(args.bets_mode),
            **metrics,
        }, f, indent=2)
    with open(out_dir / "coefficients.json", "w") as f:
        json.dump(metrics["coefficients"], f, indent=2)
    console.print(f"\n[bold green]✓ Bundle saved to {out_dir}[/bold green]")
    console.print(f"  Next: wire into production via xgboost_ensemble.py or daily_pipeline_v2.py")


if __name__ == "__main__":
    main()
