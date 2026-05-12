"""
OddsIntel — Platt Scaling Calibration

Fits calibration corrections per market to post-hoc correct model probabilities
using settled prediction / bet outcomes.

1X2 markets (1x2_home / 1x2_draw / 1x2_away):
    Standard 1-feature Platt sigmoid fitted on predictions table:
        calibrated = 1 / (1 + exp(-(α * raw_prob + β)))
    α and β stored in model_calibration.platt_a / platt_b (platt_c = NULL).

O/U markets (over_under_25_over / over_under_25_under) — CAL-PLATT-UPGRADE:
    2-feature logistic fitted on simulated_bets (has both prob and odds):
        calibrated = 1 / (1 + exp(-(w0*shrunk_prob + w1*log(odds) + intercept)))
    Stored as platt_a=w0, platt_b=intercept, platt_c=w1.
    Applied in improvements.apply_platt() when platt_c is non-null.
    Threshold: ≥ 300 settled bets per selection. O/U met this threshold 2026-05-12.

Run manually or via the settlement workflow (Sundays at 22:00 UTC):
    python scripts/fit_platt.py
    python scripts/fit_platt.py --model-version v14        # only v14 predictions
    python scripts/fit_platt.py --min-samples 200          # override threshold

Always pass --model-version matching the current production model.
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write

MARKETS = ["1x2_home", "1x2_draw", "1x2_away"]
OU_MARKETS = ["over_under_25_over", "over_under_25_under"]
MIN_SAMPLES_DEFAULT = 100
MIN_SAMPLES_OU = 300  # higher threshold for 2-feature logistic to avoid overfitting


def fetch_labeled_predictions(source: str = "ensemble", model_version: str | None = None) -> list[dict]:
    """Fetch predictions with finished match outcomes, optionally filtered to one model version."""
    sql = """
        SELECT p.model_probability, p.market, m.result
        FROM predictions p
        INNER JOIN matches m ON m.id = p.match_id
        WHERE p.source = %s AND m.status = 'finished' AND m.result IS NOT NULL
    """
    params: list = [source]
    if model_version:
        sql += " AND p.model_version = %s"
        params.append(model_version)
    rows = execute_query(sql, params)

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


def fetch_settled_ou_bets(model_version: str | None = None) -> list[dict]:
    """
    Fetch settled O/U bets from simulated_bets for 2-feature logistic fitting.

    Uses calibrated_prob as the first feature (= shrunk_prob for bets placed before
    CAL-PLATT-UPGRADE deployed, since apply_platt returned unchanged for O/U).
    Includes odds_at_pick for the log(odds) feature.
    """
    sql = """
        SELECT calibrated_prob, odds_at_pick, result,
               CASE
                 WHEN selection ILIKE 'over%%' THEN 'over_under_25_over'
                 WHEN selection ILIKE 'under%%' THEN 'over_under_25_under'
               END AS market
        FROM simulated_bets
        WHERE market = 'O/U'
          AND result IN ('won', 'lost')
          AND calibrated_prob IS NOT NULL
          AND odds_at_pick IS NOT NULL
    """
    params: list = []
    if model_version:
        sql += " AND model_version = %s"
        params.append(model_version)
    rows = execute_query(sql, params)

    labeled = []
    for row in rows:
        mkt = row.get("market")
        if not mkt:
            continue
        labeled.append({
            "prob": float(row["calibrated_prob"]),
            "odds": float(row["odds_at_pick"]),
            "won": row["result"] == "won",
            "market": mkt,
        })
    return labeled


def fit_2feature_logistic_params(
    probs: np.ndarray, log_odds: np.ndarray, outcomes: np.ndarray
) -> tuple[float, float, float]:
    """
    Fit 2-feature logistic regression: X = [shrunk_prob, log(odds)] → outcome.

    Returns (w0, w1, intercept) where:
        calibrated = sigmoid(w0 * shrunk_prob + w1 * log(odds) + intercept)

    Stored as platt_a=w0, platt_c=w1, platt_b=intercept in model_calibration.
    """
    X = np.column_stack([probs, log_odds])
    clf = LogisticRegression(C=1.0, max_iter=1000, solver="lbfgs")
    clf.fit(X, outcomes)
    w0 = float(clf.coef_[0][0])
    w1 = float(clf.coef_[0][1])
    intercept = float(clf.intercept_[0])
    return w0, w1, intercept


def apply_2feature(
    probs: np.ndarray, log_odds: np.ndarray, w0: float, w1: float, intercept: float
) -> np.ndarray:
    """Apply 2-feature logistic: sigmoid(w0*prob + w1*log_odds + intercept)."""
    z = np.clip(w0 * probs + w1 * log_odds + intercept, -30, 30)
    return 1.0 / (1.0 + np.exp(-z))


def _store_calibration(
    market: str, a: float, b: float, ece_before: float, ece_after: float,
    n: int, c: float | None = None
) -> None:
    """Insert a calibration row into model_calibration. platt_c=None for 1-feature Platt."""
    if c is not None:
        execute_write(
            """
            INSERT INTO model_calibration
                (market, platt_a, platt_b, platt_c, ece_before, ece_after, sample_count, fitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                market,
                round(a, 6),
                round(b, 6),
                round(c, 6),
                round(ece_before, 6),
                round(ece_after, 6),
                n,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    else:
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


def fit_and_store(min_samples: int = MIN_SAMPLES_DEFAULT, model_version: str | None = None):
    """Fit calibration per market and store in model_calibration.

    1X2 markets: standard 1-feature Platt from predictions table.
    O/U markets: 2-feature logistic [shrunk_prob, log(odds)] from simulated_bets.
    """
    version_label = f" (model_version={model_version})" if model_version else " (all versions — mixing not recommended)"
    results = []

    # --- 1X2: standard Platt from predictions table ---
    labeled = fetch_labeled_predictions(model_version=model_version)

    print(f"\n{'─'*65}")
    print(f"  1X2 Platt Calibration — {len(labeled)} labeled predictions{version_label}")
    print(f"{'─'*65}")

    if not labeled:
        print("  No labeled predictions found. Run after settlement.")
    else:
        by_market: dict[str, list[dict]] = {}
        for item in labeled:
            by_market.setdefault(item["market"], []).append(item)

        for market in MARKETS:
            items = by_market.get(market, [])
            n = len(items)

            if n < min_samples:
                print(f"\n  {market}: {n} samples (need {min_samples}) — SKIPPED")
                continue

            probs = np.array([x["prob"] for x in items])
            outcomes = np.array([1.0 if x["won"] else 0.0 for x in items])

            ece_before = compute_ece(probs, outcomes)
            a, b = fit_platt_params(probs, outcomes)
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
                print("    ⚠  No improvement — Platt params stored but may not help this market")

            _store_calibration(market, a, b, ece_before, ece_after, n)
            results.append({"market": market})
            print("    → Stored in model_calibration")

    # --- O/U: 2-feature logistic from simulated_bets (CAL-PLATT-UPGRADE) ---
    ou_labeled = fetch_settled_ou_bets(model_version=model_version)

    print(f"\n{'─'*65}")
    print(f"  O/U 2-Feature Logistic Calibration — {len(ou_labeled)} settled bets{version_label}")
    print(f"{'─'*65}")

    if not ou_labeled:
        print("  No settled O/U bets found.")
    else:
        ou_by_market: dict[str, list[dict]] = {}
        for item in ou_labeled:
            ou_by_market.setdefault(item["market"], []).append(item)

        for market in OU_MARKETS:
            items = ou_by_market.get(market, [])
            n = len(items)

            if n < MIN_SAMPLES_OU:
                print(f"\n  {market}: {n} samples (need {MIN_SAMPLES_OU}) — SKIPPED")
                continue

            probs = np.array([x["prob"] for x in items])
            log_odds = np.log(np.array([x["odds"] for x in items]))
            outcomes = np.array([1.0 if x["won"] else 0.0 for x in items])

            ece_before = compute_ece(probs, outcomes)

            w0, w1, intercept = fit_2feature_logistic_params(probs, log_odds, outcomes)

            cal_probs = apply_2feature(probs, log_odds, w0, w1, intercept)
            ece_after = compute_ece(cal_probs, outcomes)

            print(f"\n  {market} (n={n}) — 2-feature logistic:")
            print(f"    w0 (prob)     = {w0:.6f}")
            print(f"    w1 (log_odds) = {w1:.6f}")
            print(f"    intercept     = {intercept:.6f}")
            print(f"    ECE before: {ece_before:.4f} ({ece_before*100:.1f}%)")
            print(f"    ECE after:  {ece_after:.4f} ({ece_after*100:.1f}%)")

            improvement = ece_before - ece_after
            if improvement > 0:
                print(f"    ✅ Improvement: {improvement:.4f} ({improvement/ece_before*100:.0f}% relative)")
            else:
                print("    ⚠  No ECE improvement — params stored anyway (odds-conditional correction may still help)")

            _store_calibration(market, w0, intercept, ece_before, ece_after, n, c=w1)
            results.append({"market": market})
            print("    → Stored in model_calibration (platt_a=w0, platt_b=intercept, platt_c=w1)")

    print(f"\n{'─'*65}")
    total_markets = len(MARKETS) + len(OU_MARKETS)
    if results:
        print(f"  ✅ Fitted {len(results)}/{total_markets} markets")
    else:
        print(f"  ⚠  No markets had enough samples")
    print(f"{'─'*65}\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit Platt scaling calibration")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT,
                        help=f"Minimum samples per market (default: {MIN_SAMPLES_DEFAULT})")
    parser.add_argument("--model-version", type=str, default=None,
                        help="Filter to a specific model version (e.g. v14). Strongly recommended.")
    args = parser.parse_args()

    fit_and_store(min_samples=args.min_samples, model_version=args.model_version)
