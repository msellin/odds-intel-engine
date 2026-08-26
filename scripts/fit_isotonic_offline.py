"""CALIBRATION-ISOTONIC-IMPL — fit isotonic regression per market for a bundle.

Companion to scripts/fit_platt_offline.py. Loads a bundle, scores it on a
held-out MFV slice, fits an IsotonicRegression per market (1x2_home/draw/away,
over_25, btts_yes), saves one .pkl per market into the bundle directory:

  data/models/soccer/<version>/isotonic_<market>.pkl

These pickles are picked up by workers/model/improvements.py at inference
when env var STAGE2_CALIBRATOR=isotonic. Default behaviour stays unchanged
until that env is set (no production behaviour change today).

Run:
  python3 scripts/fit_isotonic_offline.py --version v_20260525_signals
  python3 scripts/fit_isotonic_offline.py --version v_20260525_signals --upload
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
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()
MODELS_ROOT = Path(__file__).resolve().parent.parent / "data" / "models" / "soccer"


def _load_settled(min_days_back: int = 60) -> pd.DataFrame:
    """Load settled matches with score + MFV row."""
    rows = execute_query("""
        SELECT mfv.*, m.score_home, m.score_away, m.result AS truth_1x2
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        WHERE m.score_home IS NOT NULL
          AND mfv.match_date >= NOW() - (%s || ' days')::interval
        ORDER BY mfv.match_date ASC
    """, (min_days_back,))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["over_25_truth"] = (df["score_home"] + df["score_away"]) > 2
    df["btts_truth"] = (df["score_home"] > 0) & (df["score_away"] > 0)
    return df


def _build_X(df: pd.DataFrame, feature_cols) -> pd.DataFrame:
    X = df.reindex(columns=[c for c in feature_cols if c in df.columns]).copy()
    for c in feature_cols:
        if c not in X.columns:
            X[c] = np.nan
    X = X[feature_cols]
    for c in X.columns:
        if c.endswith("_missing"):
            X[c] = X[c].fillna(False).astype(int)
        else:
            X[c] = pd.to_numeric(X[c], errors="coerce")
    X = X.fillna(X.median(numeric_only=True))
    return X


def _fit_market(p: np.ndarray, y: np.ndarray, name: str) -> tuple[IsotonicRegression, float, float]:
    """Fit isotonic on first 70%, evaluate ECE on last 30%."""
    n = len(p)
    split = int(n * 0.7)
    p_cal, y_cal = p[:split], y[:split]
    p_te, y_te = p[split:], y[split:]

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(p_cal, y_cal)

    # ECE before vs after
    def _ece(probs, ys, n_bins=20):
        if len(probs) == 0:
            return 0.0
        bins = np.linspace(0, 1, n_bins + 1)
        ws, ds = [], []
        for i in range(n_bins):
            lo, hi = bins[i], bins[i + 1]
            mask = (probs >= lo) & (probs < hi)
            if i == n_bins - 1:
                mask = (probs >= lo) & (probs <= hi)
            n_b = int(mask.sum())
            if n_b == 0:
                continue
            ws.append(n_b)
            ds.append(abs(float(ys[mask].mean()) - float(probs[mask].mean())))
        if not ws:
            return 0.0
        total = sum(ws)
        return sum(w * d for w, d in zip(ws, ds)) / total

    ece_raw = _ece(p_te, y_te)
    ece_iso = _ece(iso.predict(p_te), y_te)
    return iso, ece_raw, ece_iso


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    bundle = MODELS_ROOT / args.version
    if not bundle.exists():
        console.print(f"[red]Bundle not at {bundle}[/red]")
        sys.exit(1)

    feature_cols = joblib.load(bundle / "feature_cols.pkl")
    df = _load_settled(args.days)
    console.print(f"[bold]Fitting isotonic for {args.version} on {len(df):,} settled matches (last {args.days}d)[/bold]")
    if len(df) < 500:
        console.print(f"[red]Too few matches ({len(df)}) — abort[/red]")
        sys.exit(1)

    X = _build_X(df, feature_cols)

    results: list[tuple[str, float, float]] = []

    # 1X2 markets
    result_model = joblib.load(bundle / "result_1x2.pkl")
    proba = result_model.predict_proba(X)
    for class_name, cls_idx, truth_val in (("1x2_home", 0, "home"), ("1x2_draw", 1, "draw"), ("1x2_away", 2, "away")):
        p = proba[:, cls_idx]
        y = (df["truth_1x2"] == truth_val).astype(int).values
        iso, ece_before, ece_after = _fit_market(p, y, class_name)
        joblib.dump(iso, bundle / f"isotonic_{class_name}.pkl")
        results.append((class_name, ece_before, ece_after))

    # OU 2.5
    ou_model = joblib.load(bundle / "over_under.pkl")
    p_ou = ou_model.predict_proba(X)[:, 1]
    y_ou = df["over_25_truth"].astype(int).values
    iso_ou, e_b, e_a = _fit_market(p_ou, y_ou, "over_25")
    joblib.dump(iso_ou, bundle / "isotonic_over_25.pkl")
    results.append(("over_25", e_b, e_a))

    # BTTS
    btts_path = bundle / "btts.pkl"
    if btts_path.exists():
        btts_model = joblib.load(btts_path)
        p_btts = btts_model.predict_proba(X)[:, 1]
        y_btts = df["btts_truth"].astype(int).values
        iso_btts, e_b, e_a = _fit_market(p_btts, y_btts, "btts_yes")
        joblib.dump(iso_btts, bundle / "isotonic_btts_yes.pkl")
        results.append(("btts_yes", e_b, e_a))

    t = Table(title=f"Isotonic fit results — {args.version}")
    for c in ("market", "ECE before", "ECE after", "Δ"):
        t.add_column(c)
    for name, b, a in results:
        delta = b - a
        sym = "↓" if delta > 0 else "↑"
        t.add_row(name, f"{b:.4f}", f"{a:.4f}", f"{sym} {abs(delta):.4f}")
    console.print(t)
    console.print(f"\n[green]✓ Saved {len(results)} isotonic pickles to {bundle}[/green]")
    console.print(f"[dim]Activate via STAGE2_CALIBRATOR=isotonic on the VPS[/dim]")


if __name__ == "__main__":
    main()
