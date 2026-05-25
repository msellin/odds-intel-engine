"""GLOBAL-PLATT-OVERCONFIDENCE diagnostic — does isotonic Stage-2 beat Platt?

LONGSHOT-GEO-AUDIT (2026-05-25) found 30-50% calibrated_prob bins are
overconfident by 12-16pp globally. This script tests whether replacing
the 2-parameter Platt with isotonic regression closes the gap on a
held-out slice — without changing any production code.

Method:
  1. Load `v_20260525_signals` bundle
  2. Score on the most-recent 14-day MFV slice
  3. Apply existing Platt → ECE_platt
  4. Fit isotonic regression on first half, apply to second → ECE_isotonic
  5. Print side-by-side per-bin gap

If isotonic wins by ≥3pp ECE on the 30-50% range, the fix is viable.
Decision (post-Phase-3.5): switch Stage-2 calibrator OR add isotonic
as an env-gated fallback in workers/model/improvements.py.

Run: python3 scripts/calibrate_isotonic_test.py [--version v_20260525_signals]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from scipy.optimize import minimize
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()
MODELS_ROOT = Path(__file__).resolve().parent.parent / "data" / "models" / "soccer"


def _platt_sigmoid(p, a, b):
    z = np.clip(a * p + b, -30, 30)
    return 1.0 / (1.0 + np.exp(-z))


def _fit_platt(p, y):
    def nll(params):
        a, b = params
        q = _platt_sigmoid(p, a, b)
        q = np.clip(q, 1e-12, 1 - 1e-12)
        return -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))
    res = minimize(nll, x0=[1.0, 0.0], method="Nelder-Mead")
    return float(res.x[0]), float(res.x[1])


def _ece(p, y, n_bins: int = 20) -> tuple[float, list]:
    """Expected calibration error + per-bin (low, hi, n, avg_pred, actual)."""
    bins = []
    weights = []
    deltas = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        mask = (p >= lo) & (p < hi if i < n_bins - 1 else p <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        avg_pred = float(p[mask].mean())
        actual = float(y[mask].mean())
        bins.append((lo, hi, n, avg_pred, actual))
        weights.append(n)
        deltas.append(abs(actual - avg_pred))
    if not weights:
        return 0.0, []
    total = sum(weights)
    ece = sum(w * d for w, d in zip(weights, deltas)) / total
    return ece, bins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v_20260525_signals")
    args = ap.parse_args()

    bundle_dir = MODELS_ROOT / args.version
    console.print(f"[bold]GLOBAL-PLATT-OVERCONFIDENCE diagnostic — bundle {args.version}[/bold]")
    if not bundle_dir.exists():
        console.print(f"[red]Bundle not found at {bundle_dir}[/red]")
        sys.exit(1)

    model = joblib.load(bundle_dir / "result_1x2.pkl")
    feature_cols = joblib.load(bundle_dir / "feature_cols.pkl")

    # Load held-out slice
    console.print("Loading held-out MFV slice (last 14 days)...")
    rows = execute_query("""
        SELECT mfv.*, m.score_home, m.score_away,
               m.result AS truth
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        WHERE m.score_home IS NOT NULL
          AND mfv.match_date >= NOW() - INTERVAL '14 days'
        ORDER BY mfv.match_date ASC
    """)
    df = pd.DataFrame(rows)
    console.print(f"  Loaded {len(df):,} settled matches")
    if len(df) < 200:
        console.print(f"[red]Too few rows ({len(df)}) — abort[/red]")
        sys.exit(1)

    # Align features the way model expects
    X = df.reindex(columns=[c for c in feature_cols if c in df.columns]).copy()
    for c in feature_cols:
        if c not in X.columns:
            X[c] = np.nan
    X = X[feature_cols]
    # Add missing indicators (skipped — model handles NaN via XGBoost natively)
    # Fill numeric NaN with column median (close to what _impute_features does)
    for c in X.columns:
        if c.endswith("_missing"):
            X[c] = X[c].fillna(False).astype(int) if c in X.columns else 0
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    X = X.fillna(X.median(numeric_only=True))

    # Score home probability (multi-class: 0=home, 1=draw, 2=away)
    proba = model.predict_proba(X)[:, 0]  # home class
    y_home = (df["truth"] == "home").astype(int).values

    # Time-split: first 70% calibrate, last 30% evaluate
    n = len(df)
    split = int(n * 0.7)
    p_cal, y_cal = proba[:split], y_home[:split]
    p_te, y_te = proba[split:], y_home[split:]
    console.print(f"  Calibration set: n={split:,}  ·  Test set: n={n - split:,}")

    # Raw (no calibration) baseline
    ece_raw, _ = _ece(p_te, y_te)
    console.print(f"\n[bold]Raw model (no Stage-2): ECE = {ece_raw:.4f}[/bold]")

    # Platt
    a, b = _fit_platt(p_cal, y_cal)
    p_platt = _platt_sigmoid(p_te, a, b)
    ece_platt, bins_platt = _ece(p_platt, y_te)
    console.print(f"[bold]Platt (a={a:.3f}, b={b:.3f}):  ECE = {ece_platt:.4f}[/bold]")

    # Isotonic
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(p_cal, y_cal)
    p_iso = iso.predict(p_te)
    ece_iso, bins_iso = _ece(p_iso, y_te)
    console.print(f"[bold]Isotonic:                    ECE = {ece_iso:.4f}[/bold]")

    # Compare in the 30-50% bins specifically
    console.print("\n[bold]30-50% bin comparison (the LONGSHOT-GEO-AUDIT finding):[/bold]")
    t = Table()
    for c in ("p bin", "n", "Platt actual%", "Platt gap", "Isotonic actual%", "Isotonic gap"):
        t.add_column(c)
    bins_platt_d = {(round(b[0], 2), round(b[1], 2)): b for b in bins_platt}
    bins_iso_d = {(round(b[0], 2), round(b[1], 2)): b for b in bins_iso}
    for lo in (0.30, 0.35, 0.40, 0.45):
        key = (lo, round(lo + 0.05, 2))
        bp = bins_platt_d.get(key)
        bi = bins_iso_d.get(key)
        if not bp or not bi:
            continue
        t.add_row(
            f"{key[0]:.2f}-{key[1]:.2f}",
            str(bp[2]),
            f"{bp[4]*100:.1f}%",
            f"{(bp[4] - bp[3])*100:+.1f}pp",
            f"{bi[4]*100:.1f}%",
            f"{(bi[4] - bi[3])*100:+.1f}pp",
        )
    console.print(t)

    delta = ece_platt - ece_iso
    console.print(f"\n[bold]Overall ECE improvement (Platt → Isotonic): {delta:+.4f}[/bold]")
    if delta >= 0.005:
        console.print(f"[green]✓ Isotonic wins by ECE — switch Stage-2 calibrator at next retrain (post-Phase-3.5)[/green]")
    elif delta >= 0.002:
        console.print(f"[yellow]Marginal — keep Platt unless 30-50% bins improve specifically[/yellow]")
    else:
        console.print(f"[red]No material lift — Platt is fine; the gap must come from earlier in the pipeline[/red]")


if __name__ == "__main__":
    main()
