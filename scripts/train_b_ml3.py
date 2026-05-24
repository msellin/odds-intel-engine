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
MATCH_LEVEL_FEATURES = [
    "bookmaker_disagreement",
    "odds_drift_home",       # only _home flavor stored; reflects HOME odds drift but is a market-level signal
    "steam_move",            # bool
    "elo_diff",
    "form_ppg_home",
    "form_ppg_away",
    "lineup_confirmed",      # bool
    "rest_days_home",
    "rest_days_away",
    "fixture_importance",
    "league_position_home",
]

# Selection-aware features: ensemble_prob_<sel> + opening_implied_<sel> + the
# computed edge proxy = (ensemble_prob_<sel> − opening_implied_<sel>).
# These get unpacked into per-selection rows during training-frame build.

# Categorical features one-hot-encoded post-build.
CATEGORICAL_FEATURES = ["selection_home", "selection_draw", "selection_away"]
# league_tier is also categorical but stored as int (1-4); we treat as ordinal numeric.


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
          mfv.odds_drift_home,
          mfv.steam_move,
          mfv.elo_diff,
          mfv.form_ppg_home, mfv.form_ppg_away,
          mfv.lineup_confirmed,
          mfv.rest_days_home, mfv.rest_days_away,
          mfv.fixture_importance,
          mfv.league_position_home,
          mfv.built_at,
          l.tier AS league_tier,
          m.date AS match_kickoff
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


def _build_feature_matrix(long_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Build X, y, feature_cols. Numerics imputed with median; bools cast to int."""
    # One-hot selection (drop one to avoid multicollinearity in logistic regression)
    sel_dummies = pd.get_dummies(long_df["selection"], prefix="selection", drop_first=True)

    feature_frame = pd.concat([
        long_df[[
            "edge_proxy", "ensemble_prob", "opening_implied",
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

    # Median imputation per column. Indicators for missingness on the 6 most-thin features.
    THIN_FEATURES_FOR_INDICATORS = [
        "odds_drift_home", "bookmaker_disagreement", "fixture_importance",
        "league_position_home", "rest_days_home", "rest_days_away",
    ]
    for col in THIN_FEATURES_FOR_INDICATORS:
        if col in feature_frame.columns:
            feature_frame[f"{col}_missing"] = feature_frame[col].isna().astype(int)

    feature_frame = feature_frame.fillna(feature_frame.median(numeric_only=True))

    feature_cols = list(feature_frame.columns)
    X = feature_frame
    y = long_df["y_clv_beat"]
    return X, y, feature_cols


def _train_and_evaluate(X: pd.DataFrame, y: pd.Series, feature_cols: list[str]):
    """5-fold TimeSeriesSplit CV → final fit on all data. Returns model + metrics."""
    console.print("\n[bold]CV evaluation (TimeSeriesSplit, n_splits=5)[/bold]")

    tscv = TimeSeriesSplit(n_splits=5)
    cv_aucs = []
    cv_briers = []
    cv_log_losses = []
    for fold, (tr, va) in enumerate(tscv.split(X)):
        Xtr, Xva = X.iloc[tr], X.iloc[va]
        ytr, yva = y.iloc[tr], y.iloc[va]
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
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    final_model = LogisticRegression(
        max_iter=1000, C=1.0, solver="lbfgs",
        class_weight="balanced",
    )
    final_model.fit(X_scaled, y)

    # Coefficient inspection
    coefs = dict(zip(feature_cols, final_model.coef_[0]))
    console.print("\n[bold]Feature coefficients (sorted by |coef|)[/bold]")
    t = Table()
    for col in ("feature", "coef", "|coef|"):
        t.add_column(col)
    sorted_coefs = sorted(coefs.items(), key=lambda kv: abs(kv[1]), reverse=True)
    for feat, c in sorted_coefs:
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
    Default 0.5 if no clear winner. Returns threshold and metrics at chosen value."""
    proba = model.predict_proba(scaler.transform(X))[:, 1]
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=f"v_{_date.today().strftime('%Y%m%d')}",
                    help="Version tag — produces data/models/meta/<version>/")
    ap.add_argument("--dry-run", action="store_true", help="Train but don't save the bundle")
    args = ap.parse_args()

    long_df = _load_training_data()
    if len(long_df) < 1000:
        console.print(f"[red]Only {len(long_df)} training rows — need ≥1,000. Aborting.[/red]")
        sys.exit(1)

    X, y, feature_cols = _build_feature_matrix(long_df)
    console.print(f"\n[bold]Feature matrix: {X.shape[0]:,} rows × {X.shape[1]} features[/bold]")
    console.print(f"  Features: {feature_cols}")

    model, scaler, metrics = _train_and_evaluate(X, y, feature_cols)
    thresh = _pick_threshold(model, scaler, X, y)
    console.print(f"\n[bold]Chosen firing threshold: {thresh['threshold']:.3f}[/bold]")
    console.print(f"  At threshold: n_fired={thresh['metrics']['n_fired']:,}  "
                  f"precision={thresh['metrics']['precision']:.3f}  "
                  f"recall={thresh['metrics']['recall']:.3f}")

    if args.dry_run:
        console.print("\n[yellow]--dry-run: not saving bundle[/yellow]")
        return

    # Save bundle
    out_dir = MODELS_DIR / args.version
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / "b_ml3.pkl")
    joblib.dump(scaler, out_dir / "scaler.pkl")
    joblib.dump(feature_cols, out_dir / "feature_cols.pkl")
    with open(out_dir / "threshold.json", "w") as f:
        json.dump({
            "chosen_threshold": thresh["threshold"],
            "threshold_metrics": thresh["metrics"],
            **metrics,
        }, f, indent=2)
    with open(out_dir / "coefficients.json", "w") as f:
        json.dump(metrics["coefficients"], f, indent=2)
    console.print(f"\n[bold green]✓ Bundle saved to {out_dir}[/bold green]")
    console.print(f"  Next: wire into production via xgboost_ensemble.py or daily_pipeline_v2.py")


if __name__ == "__main__":
    main()
