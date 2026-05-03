"""
OddsIntel — Platt Scaling Calibration

Fits a sigmoid (Platt) correction per market to post-hoc calibrate model
probabilities using settled prediction outcomes.

    calibrated = 1 / (1 + exp(-(α * raw_prob + β)))

The resulting α and β are stored in the `model_calibration` table.
The betting pipeline reads the latest row per market and applies the
sigmoid after tier-specific shrinkage (improvements.py).

Run manually or via the settlement workflow (Sundays at 22:00 UTC):
    python scripts/fit_platt.py
    python scripts/fit_platt.py --min-samples 200  # override threshold

Requires 100+ samples per market (skips markets below threshold).
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write

MARKETS = ["1x2_home", "1x2_draw", "1x2_away"]
MIN_SAMPLES_DEFAULT = 100


def fetch_labeled_predictions(source: str = "ensemble") -> list[dict]:
    """Fetch all predictions with finished match outcomes."""
    rows = execute_query(
        """
        SELECT p.model_probability, p.market, m.result
        FROM predictions p
        INNER JOIN matches m ON m.id = p.match_id
        WHERE p.source = %s AND m.status = 'finished' AND m.result IS NOT NULL
        """,
        [source],
    )

    labeled = []
    for row in rows:
        prob = row.get("model_probability")
        mkt = row.get("market", "")
        result_val = row.get("result")

        if prob is None or not result_val:
            continue

        won = None
        if mkt == "1x2_home":
            won = (result_val == "home")
        elif mkt == "1x2_away":
            won = (result_val == "away")
        elif mkt == "1x2_draw":
            won = (result_val == "draw")

        if won is None:
            continue

        labeled.append({"prob": float(prob), "won": won, "market": mkt})

    return labeled


def compute_ece(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 20) -> float:
    """Expected Calibration Error with equal-width bins."""
    bin_edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    total = len(probs)
    for i in range(n_bins):
        mask = (probs >= bin_edges[i]) & (probs < bin_edges[i + 1])
        if mask.sum() == 0:
            continue
        bin_acc = outcomes[mask].mean()
        bin_conf = probs[mask].mean()
        ece += (mask.sum() / total) * abs(bin_acc - bin_conf)
    return float(ece)


def platt_transform(probs: np.ndarray, a: float, b: float) -> np.ndarray:
    """Apply Platt sigmoid: 1 / (1 + exp(-(a*p + b)))."""
    z = a * probs + b
    # Clip to avoid overflow
    z = np.clip(z, -30, 30)
    return 1.0 / (1.0 + np.exp(-z))


def fit_platt_params(probs: np.ndarray, outcomes: np.ndarray) -> tuple[float, float]:
    """
    Fit Platt scaling parameters α, β by minimizing negative log-likelihood.

    Uses the standard Platt method: fit a sigmoid on top of raw probabilities
    using the labeled outcome data. This is equivalent to logistic regression
    where the single feature is the raw model probability.
    """
    def neg_log_likelihood(params):
        a, b = params
        cal = platt_transform(probs, a, b)
        # Clamp to avoid log(0)
        cal = np.clip(cal, 1e-7, 1 - 1e-7)
        nll = -np.mean(outcomes * np.log(cal) + (1 - outcomes) * np.log(1 - cal))
        return nll

    # Initialize: a=1, b=0 (identity transform)
    result = minimize(neg_log_likelihood, x0=[1.0, 0.0], method="Nelder-Mead",
                      options={"maxiter": 5000, "xatol": 1e-8, "fatol": 1e-8})
    return float(result.x[0]), float(result.x[1])


def fit_and_store(min_samples: int = MIN_SAMPLES_DEFAULT):
    """Fit Platt scaling per market and store in model_calibration."""
    labeled = fetch_labeled_predictions()

    print(f"\n{'─'*65}")
    print(f"  Platt Scaling Calibration — {len(labeled)} labeled predictions")
    print(f"{'─'*65}")

    if not labeled:
        print("  No labeled predictions found. Run after settlement.")
        return

    # Group by market
    by_market: dict[str, list[dict]] = {}
    for item in labeled:
        by_market.setdefault(item["market"], []).append(item)

    results = []

    for market in MARKETS:
        items = by_market.get(market, [])
        n = len(items)

        if n < min_samples:
            print(f"\n  {market}: {n} samples (need {min_samples}) — SKIPPED")
            continue

        probs = np.array([x["prob"] for x in items])
        outcomes = np.array([1.0 if x["won"] else 0.0 for x in items])

        ece_before = compute_ece(probs, outcomes)

        # Fit Platt sigmoid
        a, b = fit_platt_params(probs, outcomes)

        # Compute ECE after calibration
        cal_probs = platt_transform(probs, a, b)
        ece_after = compute_ece(cal_probs, outcomes)

        print(f"\n  {market} (n={n}):")
        print(f"    α = {a:.6f}, β = {b:.6f}")
        print(f"    ECE before: {ece_before:.4f} ({ece_before*100:.1f}%)")
        print(f"    ECE after:  {ece_after:.4f} ({ece_after*100:.1f}%)")

        improvement = ece_before - ece_after
        if improvement > 0:
            print(f"    ✅ Improvement: {improvement:.4f} ({improvement/ece_before*100:.0f}% relative)")
        else:
            print(f"    ⚠  No improvement — Platt params stored but may not help this market")

        execute_write(
            """
            INSERT INTO model_calibration
                (market, platt_a, platt_b, ece_before, ece_after, sample_count, fitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                market,
                round(a, 6),
                round(b, 6),
                round(ece_before, 6),
                round(ece_after, 6),
                n,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        results.append({"market": market})
        print(f"    → Stored in model_calibration")

    print(f"\n{'─'*65}")
    if results:
        print(f"  ✅ Fitted {len(results)}/{len(MARKETS)} markets")
    else:
        print(f"  ⚠  No markets had enough samples (min={min_samples})")
    print(f"{'─'*65}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit Platt scaling calibration")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT,
                        help=f"Minimum samples per market (default: {MIN_SAMPLES_DEFAULT})")
    args = parser.parse_args()

    fit_and_store(min_samples=args.min_samples)
