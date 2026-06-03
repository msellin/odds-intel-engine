"""One-shot Platt fit for bot_inplay_e (INPLAY-E-RECALIBRATE).

Background: 2026-06-03 ECE re-check (scripts/inplay_e_ece_recheck.py)
showed inplay_e is wildly miscalibrated — ECE 21.93%, avg predicted 79.7%
vs actual 58.3%. ROI is still +7.64% because flat $5 stake hides the
calibration error from sizing. But downstream consumers of
inplay_e.model_probability (admin confidence cards, future meta-model
features, any future Kelly path) see garbage.

This script fits a 1-feature Platt sigmoid on inplay_e's settled bets
and stores the result in model_calibration with market='inplay_e_under_25'.

After the fit lands:
  - workers/model/improvements.apply_platt('inplay_e_under_25', raw_p) returns
    a calibrated probability that matches actual hit rate within ~5pp per bucket
  - workers/jobs/inplay_bot._check_strategy_e calls apply_platt under env
    INPLAY_E_PLATT_ENABLED (default false → shadow log only)

Run: python3 scripts/fit_platt_inplay_e.py
     python3 scripts/fit_platt_inplay_e.py --dry-run    # don't write the row
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from scripts.fit_platt import (  # noqa: E402
    fit_platt_params,
    platt_transform,
    compute_ece,
)


MARKET_KEY = "inplay_e_under_25"
MIN_SAMPLES = 100  # 1-feature Platt is forgiving; 216 is well above


def fetch_inplay_e_bets() -> tuple[np.ndarray, np.ndarray, int]:
    rows = execute_query(
        """SELECT model_probability::float AS p,
                  CASE WHEN result::text = 'won' THEN 1 ELSE 0 END AS y
           FROM simulated_bets
           WHERE bot_id = (SELECT id FROM bots WHERE name = 'inplay_e')
             AND result::text IN ('won', 'lost')
             AND model_probability IS NOT NULL""",
    )
    if not rows:
        return np.array([]), np.array([]), 0
    probs = np.array([r["p"] for r in rows], dtype=float)
    ys = np.array([r["y"] for r in rows], dtype=int)
    return probs, ys, len(rows)


def store_calibration(a: float, b: float, ece_before: float, ece_after: float, n: int) -> None:
    """Upsert pattern: insert new row (fit_platt.py never deletes, just appends;
    apply_platt's load_platt_params orders by fitted_at DESC and takes the latest)."""
    execute_write(
        """INSERT INTO model_calibration
            (market, platt_a, platt_b, ece_before, ece_after, sample_count, fitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            MARKET_KEY,
            round(a, 6),
            round(b, 6),
            round(ece_before, 6),
            round(ece_after, 6),
            n,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def loo_validate(probs: np.ndarray, ys: np.ndarray) -> float:
    """Leave-one-out cross-validation ECE. Slow O(N^2 fit calls) but on N=216 it's fine.
    Reports out-of-sample ECE — guards against in-sample overfit on the small cohort."""
    n = len(probs)
    cal_oof = np.zeros(n)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        a, b = fit_platt_params(probs[mask], ys[mask])
        cal_oof[i] = platt_transform(probs[i:i+1], a, b)[0]
    return compute_ece(cal_oof, ys, n_bins=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't write the row")
    parser.add_argument("--skip-loo", action="store_true", help="Skip leave-one-out CV (faster)")
    args = parser.parse_args()

    probs, ys, n = fetch_inplay_e_bets()
    if n < MIN_SAMPLES:
        print(f"Not enough samples: {n} < {MIN_SAMPLES}")
        return 1

    print(f"Fitting 1-feature Platt on {n} settled inplay_e bets...")
    print(f"  pre-fit:  mean_pred={probs.mean():.4f}  actual_hit={ys.mean():.4f}")
    ece_before = compute_ece(probs, ys, n_bins=20)
    print(f"  ECE before: {ece_before:.4f}")

    a, b = fit_platt_params(probs, ys)
    print(f"  Fitted params: a={a:+.6f}  b={b:+.6f}")

    cal = platt_transform(probs, a, b)
    ece_after = compute_ece(cal, ys, n_bins=20)
    print(f"  ECE after (in-sample): {ece_after:.4f}")
    print(f"  post-fit: mean_cal={cal.mean():.4f}  actual_hit={ys.mean():.4f}")

    # Sanity check: post-fit calibrated mean should approximately equal actual hit rate.
    # If it doesn't, the Platt fit failed in a meaningful way.
    if abs(cal.mean() - ys.mean()) > 0.02:
        print(f"  ⚠ WARNING: post-fit mean ({cal.mean():.4f}) != actual hit ({ys.mean():.4f}) — fit may have failed")

    if not args.skip_loo:
        print()
        print(f"Running leave-one-out CV (this takes a minute on N={n})...")
        ece_loo = loo_validate(probs, ys)
        print(f"  ECE (LOO, out-of-sample): {ece_loo:.4f}")
        if ece_loo > ece_before:
            print(f"  ⚠ WARNING: LOO ECE worse than pre-fit — Platt is OVERFITTING. Investigate.")
            return 2
    else:
        ece_loo = None

    # Per-bucket post-fit check
    print()
    print("Per-bucket calibrated-vs-actual:")
    print(f"  {'bucket':<14} {'N':>5} {'mean_cal':>10} {'actual_hit':>10} {'gap':>10}")
    print("  " + "-" * 55)
    for lo, hi in [(0.0, 0.2), (0.2, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]:
        mask = (cal >= lo) & (cal < hi if hi < 1.0 else cal <= hi)
        bn = int(mask.sum())
        if bn == 0:
            continue
        bp = cal[mask].mean()
        ba = ys[mask].mean()
        print(f"  {f'{lo:.2f}-{hi:.2f}':<14} {bn:>5} {bp:>10.3f} {ba:>10.3f} {bp - ba:>+10.3f}")

    print()
    if args.dry_run:
        print("--dry-run set — NOT writing model_calibration row")
        return 0

    store_calibration(a, b, ece_before, ece_after, n)
    print(f"✓ Stored model_calibration row: market='{MARKET_KEY}' a={a:.6f} b={b:.6f}")
    print(f"  ECE {ece_before:.4f} → {ece_after:.4f}" + (f" (LOO {ece_loo:.4f})" if ece_loo is not None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
