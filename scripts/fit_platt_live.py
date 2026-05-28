"""
Fit Platt calibration from settled simulated_bets for markets that lack
offline-model holdout data: double_chance and asian_handicap.

BTTS already has btts_yes Platt fitted from offline model holdout (2026-05-27).
This script supplements it by fitting from live settled bets — useful as a
stopgap until the June 8 full retrain re-fits everything properly.

Writes to model_calibration table using the same market keys that
apply_platt() in improvements.py expects: "{market}_{selection}".

Usage:
    python3 scripts/fit_platt_live.py             # DC + AH + BTTS check
    python3 scripts/fit_platt_live.py --dry-run   # print params, don't write
    python3 scripts/fit_platt_live.py --markets double_chance asian_handicap
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from sklearn.metrics import brier_score_loss
from sklearn.calibration import calibration_curve

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import get_conn

MIN_SAMPLES = 50  # below this, skip — not enough to fit reliably

# Maps (market in simulated_bets, selection in simulated_bets) → platt key
# Must match the platt_market = f"{os_market}_{os_selection}" in daily_pipeline_v2.py
MARKET_SELECTION_MAP = {
    # Double Chance
    ("double_chance", "1X"):  "double_chance_1x",
    ("double_chance", "X2"):  "double_chance_x2",
    ("double_chance", "12"):  "double_chance_12",
    # Asian Handicap — key selections
    ("asian_handicap", "Home -0.5"):  "asian_handicap_Home -0.5",
    ("asian_handicap", "Away -0.5"):  "asian_handicap_Away -0.5",
    ("asian_handicap", "Home -1"):    "asian_handicap_Home -1",
    ("asian_handicap", "Away -1"):    "asian_handicap_Away -1",
    ("asian_handicap", "Home +0.5"):  "asian_handicap_Home +0.5",
    ("asian_handicap", "Away +0.5"):  "asian_handicap_Away +0.5",
    # BTTS — supplement the offline-fitted btts_yes
    ("btts", "Yes"):  "btts_yes",
}


def _platt(p: np.ndarray, a: float, b: float) -> np.ndarray:
    z = np.clip(a * p + b, -30, 30)
    return 1.0 / (1.0 + np.exp(-z))


def _fit_platt(p: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """MLE fit of Platt sigmoid. Returns (a, b)."""
    def nll(params):
        q = np.clip(_platt(p, params[0], params[1]), 1e-12, 1 - 1e-12)
        return -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))
    res = minimize(nll, x0=[1.0, 0.0], method="Nelder-Mead")
    return float(res.x[0]), float(res.x[1])


def _ece(p_cal: np.ndarray, y: np.ndarray, n_bins: int = 5) -> float:
    """Expected Calibration Error."""
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (p_cal >= lo) & (p_cal < hi)
        if mask.sum() == 0:
            continue
        ece += mask.sum() / len(y) * abs(p_cal[mask].mean() - y[mask].mean())
    return float(ece)


def fetch_settled_bets(conn, markets: list[str]) -> dict[tuple, tuple[np.ndarray, np.ndarray]]:
    """Return {(market, selection): (model_probs, outcomes)} for settled bets."""
    placeholders = ",".join(["%s"] * len(markets))
    cur = conn.cursor()
    cur.execute(f"""
        SELECT market, selection, model_probability, result
        FROM simulated_bets
        WHERE market IN ({placeholders})
          AND result NOT IN ('pending', 'void')
          AND model_probability IS NOT NULL
        ORDER BY pick_time
    """, markets)
    rows = cur.fetchall()
    cur.close()

    from collections import defaultdict
    buckets: dict[tuple, list] = defaultdict(list)
    for market, sel, mp, result in rows:
        outcome = 1 if result == "won" else 0
        buckets[(market, sel)].append((float(mp), outcome))

    return {
        k: (np.array([x[0] for x in v]), np.array([x[1] for x in v]))
        for k, v in buckets.items()
    }


def write_platt(conn, platt_key: str, a: float, b: float,
                ece_before: float, ece_after: float, n: int) -> None:
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO model_calibration (market, platt_a, platt_b, ece_before, ece_after, sample_count, fitted_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
    """, (platt_key, a, b, ece_before, ece_after, n))
    conn.commit()
    cur.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and print params without writing to DB")
    ap.add_argument("--markets", nargs="+",
                    default=["double_chance", "asian_handicap", "btts"],
                    help="Markets to process")
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    args = ap.parse_args()

    with get_conn() as conn:
        data = fetch_settled_bets(conn, args.markets)

        print(f"\n{'Platt key':<35} {'n':>5} {'raw_ece':>9} {'cal_ece':>9} {'a':>8} {'b':>8}  action")
        print("-" * 90)

        for (market, sel), (p, y) in sorted(data.items()):
            platt_key = MARKET_SELECTION_MAP.get((market, sel))
            if platt_key is None:
                platt_key = f"{market}_{sel}"

            n = len(p)
            hit_rate = float(y.mean())
            model_mean = float(p.mean())

            if n < args.min_samples:
                print(f"{platt_key:<35} {n:>5}  — skipped (n < {args.min_samples}), hit={hit_rate:.3f} model={model_mean:.3f}")
                continue

            ece_before = _ece(p, y)
            a, b = _fit_platt(p, y)
            p_cal = _platt(p, a, b)
            ece_after = _ece(p_cal, y)
            brier_before = float(brier_score_loss(y, p))
            brier_after = float(brier_score_loss(y, p_cal))

            gap_before = model_mean - hit_rate
            gap_after = float(p_cal.mean()) - hit_rate

            action = "DRY-RUN" if args.dry_run else "WRITING"
            print(f"{platt_key:<35} {n:>5} {ece_before:>9.4f} {ece_after:>9.4f} {a:>8.3f} {b:>8.3f}  {action}")
            print(f"  model_mean={model_mean:.3f}  hit_rate={hit_rate:.3f}  gap_before={gap_before:+.3f}  gap_after={gap_after:+.3f}  brier: {brier_before:.4f} → {brier_after:.4f}")

            if not args.dry_run:
                write_platt(conn, platt_key, a, b, ece_before, ece_after, n)
                print(f"  ✓ Written to model_calibration")

    print()
    if args.dry_run:
        print("Dry run complete — no DB writes.")
    else:
        print("Done. Pipeline will pick up new Platt params on next run (cache resets per pipeline start).")


if __name__ == "__main__":
    main()
