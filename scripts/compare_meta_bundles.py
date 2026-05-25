"""Compare B-ML3 meta-model bundles side-by-side (2026-05-25).

Loads every meta bundle in data/models/meta/, scores the same held-out
(match × selection) rows, and prints a comparison table: AUC, Brier,
log-loss, distribution stats (mean/median/std), and threshold-precision
sweep at 0.45 / 0.50 / 0.55 / 0.60.

Use this in the morning to make a data-driven decision on which bundle
to swap in via META_B_ML3_VERSION env on Railway.

Usage:
    python3 scripts/compare_meta_bundles.py
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import joblib
import json
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, brier_score_loss, log_loss
from rich.console import Console
from rich.table import Table

# Reuse the data loader from training so the comparison is on identical input
from scripts.train_b_ml3 import _load_training_data, _build_feature_matrix

console = Console()
MODELS_DIR = Path(__file__).resolve().parent.parent / "data" / "models" / "meta"


def _score_with_bundle(bundle_dir: Path, X: pd.DataFrame, feature_cols_train: list) -> np.ndarray | None:
    """Load bundle, score X, return probability array. None if bundle can't be loaded
    or feature schema differs."""
    try:
        model = joblib.load(bundle_dir / "b_ml3.pkl")
        bundle_features = joblib.load(bundle_dir / "feature_cols.pkl")
        mt_path = bundle_dir / "model_type.txt"
        model_type = mt_path.read_text().strip() if mt_path.exists() else "logistic"
        scaler = joblib.load(bundle_dir / "scaler.pkl") if model_type == "logistic" else None
        # Bundle may have a different feature set than current training — align.
        # If a column is missing from X, fill with 0; if extra, ignore.
        X_aligned = pd.DataFrame(0, index=X.index, columns=bundle_features, dtype=float)
        for c in bundle_features:
            if c in X.columns:
                X_aligned[c] = X[c].values
        X_eval = X_aligned.values if scaler is None else scaler.transform(X_aligned)
        proba = model.predict_proba(X_eval)[:, 1]
        return proba
    except Exception as e:
        console.print(f"  [yellow]Failed to score {bundle_dir.name}: {e}[/yellow]")
        return None


def main():
    console.print("\n[bold]Loading shared training data + feature matrix[/bold]")
    long_df = _load_training_data()
    X, y, feature_cols = _build_feature_matrix(long_df)
    console.print(f"  Comparing on {X.shape[0]:,} (match × selection) rows × {X.shape[1]} features\n")

    # Find every meta bundle
    bundles = sorted([d for d in MODELS_DIR.iterdir() if d.is_dir() and (d / "b_ml3.pkl").exists()])
    if not bundles:
        console.print("[red]No meta bundles found[/red]")
        return

    results = []
    for bd in bundles:
        proba = _score_with_bundle(bd, X, feature_cols)
        if proba is None:
            continue
        # Read threshold + model type from disk
        thresh_path = bd / "threshold.json"
        chosen_threshold = 0.5
        cv_auc = None
        if thresh_path.exists():
            try:
                j = json.loads(thresh_path.read_text())
                chosen_threshold = float(j.get("chosen_threshold", 0.5))
                cv_auc = j.get("cv_auc_mean")
            except Exception:
                pass
        mt_path = bd / "model_type.txt"
        model_type = mt_path.read_text().strip() if mt_path.exists() else "logistic"

        try:
            auc = roc_auc_score(y, proba)
            brier = brier_score_loss(y, proba)
            ll = log_loss(y, proba)
        except Exception:
            auc = brier = ll = float("nan")

        # Threshold sweep
        sweep = {}
        for t in (0.45, 0.50, 0.55, 0.60, 0.625, 0.65):
            mask = proba >= t
            n_fired = int(mask.sum())
            if n_fired:
                prec = float((y[mask] == 1).mean())
                rec = float(((proba >= t) & (y == 1)).sum() / max(int((y == 1).sum()), 1))
            else:
                prec = 0.0
                rec = 0.0
            sweep[t] = (n_fired, prec, rec)

        results.append({
            "name": bd.name,
            "type": model_type,
            "chosen_threshold": chosen_threshold,
            "cv_auc_mean": cv_auc,
            "is_auc": auc,
            "is_brier": brier,
            "is_log_loss": ll,
            "mean_score": float(proba.mean()),
            "median_score": float(np.median(proba)),
            "std_score": float(proba.std()),
            "sweep": sweep,
        })

    # Rank by in-sample AUC (higher = better)
    results.sort(key=lambda r: r["is_auc"], reverse=True)

    # Primary comparison table
    t = Table(title="Meta-bundle in-sample comparison (on same training cohort)")
    for col in ("bundle", "type", "CV AUC", "IS AUC", "IS Brier", "IS LL", "mean", "median", "std"):
        t.add_column(col)
    for r in results:
        t.add_row(
            r["name"], r["type"],
            f"{r['cv_auc_mean']:.4f}" if r["cv_auc_mean"] is not None else "—",
            f"{r['is_auc']:.4f}",
            f"{r['is_brier']:.4f}",
            f"{r['is_log_loss']:.4f}",
            f"{r['mean_score']:.3f}",
            f"{r['median_score']:.3f}",
            f"{r['std_score']:.3f}",
        )
    console.print(t)

    # Per-threshold precision/recall
    for r in results:
        t = Table(title=f"{r['name']} — threshold sweep (in-sample)")
        for col in ("thr", "n_fired", "fire_rate", "precision", "recall"):
            t.add_column(col)
        for thr, (n, prec, rec) in sorted(r["sweep"].items()):
            t.add_row(f"{thr:.3f}", str(n), f"{n / len(y) * 100:.1f}%",
                      f"{prec:.3f}", f"{rec:.3f}")
        console.print(t)

    # Recommendation
    best = results[0]
    console.print(f"\n[bold green]Best by in-sample AUC: {best['name']} (AUC {best['is_auc']:.4f}, CV {best['cv_auc_mean']})[/bold green]")
    console.print(
        f"To swap on Railway: set META_B_ML3_VERSION={best['name']}\n"
        f"  Threshold from training: {best['chosen_threshold']:.3f}\n"
        f"  At that threshold: "
        f"fires {best['sweep'].get(round(best['chosen_threshold'], 3), (0, 0, 0))[0]} of {len(y)} "
        f"(precision {best['sweep'].get(round(best['chosen_threshold'], 3), (0, 0, 0))[1]:.3f})"
    )


if __name__ == "__main__":
    main()
