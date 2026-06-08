#!/usr/bin/env python3
"""
CS2 model calibration analysis.

Joins cs2_predictions to cs2_results and reports:
  - sample size by model_version
  - hit rate (accuracy) overall and by predicted-prob bin
  - log loss
  - Expected Calibration Error (ECE)
  - Platt scaling coefficients (a, b) for use in production

Usage:
    python3 scripts/esports/cs2_calibrate.py                            # all model versions
    python3 scripts/esports/cs2_calibrate.py --model elo_v1_backfill    # one version
    python3 scripts/esports/cs2_calibrate.py --save-platt               # write platt.json
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query


PLATT_OUT = Path("data/esports/cs2/platt_coefficients.json")


def _load_pairs(model_version: str | None) -> list[tuple[float, int]]:
    """Return list of (predicted_prob_team1_wins, actual_team1_won) pairs."""
    where = ""
    params: tuple = ()
    if model_version:
        where = "AND p.model_version = %s"
        params = (model_version,)

    rows = execute_query(f"""
        SELECT p.win_prob1, r.winner
        FROM cs2_predictions p
        JOIN cs2_results r ON p.bo3gg_id = r.bo3gg_id
        WHERE p.win_prob1 IS NOT NULL {where}
    """, params)

    pairs: list[tuple[float, int]] = []
    for row in rows:
        prob = float(row["win_prob1"])
        if row["winner"] == "team1":
            pairs.append((prob, 1))
        elif row["winner"] == "team2":
            pairs.append((prob, 0))
    return pairs


def _log_loss(pairs: list[tuple[float, int]], eps: float = 1e-9) -> float:
    if not pairs:
        return 0.0
    total = 0.0
    for p, y in pairs:
        p = min(max(p, eps), 1 - eps)
        total += y * math.log(p) + (1 - y) * math.log(1 - p)
    return -total / len(pairs)


def _accuracy(pairs: list[tuple[float, int]]) -> float:
    if not pairs:
        return 0.0
    return sum(1 for p, y in pairs if (p >= 0.5) == (y == 1)) / len(pairs)


def _by_bin(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    """Hit rate by predicted-prob decile (calibration table)."""
    width = 1.0 / bins
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in pairs:
        b = min(int(p / width), bins - 1)
        buckets[b].append((p, y))
    out = []
    for i, bucket in enumerate(buckets):
        lo = i * width
        hi = (i + 1) * width
        if not bucket:
            out.append({"range": f"{lo:.2f}-{hi:.2f}", "n": 0, "predicted_avg": 0.0, "actual_avg": 0.0})
            continue
        n = len(bucket)
        avg_p = sum(p for p, _ in bucket) / n
        avg_y = sum(y for _, y in bucket) / n
        out.append({"range": f"{lo:.2f}-{hi:.2f}", "n": n, "predicted_avg": avg_p, "actual_avg": avg_y})
    return out


def _ece(pairs: list[tuple[float, int]], bins: int = 10) -> float:
    """Expected Calibration Error: weighted abs(predicted_avg - actual_avg) per bin."""
    if not pairs:
        return 0.0
    total = 0.0
    n_total = len(pairs)
    for b in _by_bin(pairs, bins):
        if b["n"] == 0:
            continue
        total += (b["n"] / n_total) * abs(b["predicted_avg"] - b["actual_avg"])
    return total


def _fit_platt(pairs: list[tuple[float, int]], lr: float = 0.05, iters: int = 800) -> tuple[float, float]:
    """Fit Platt-scaling parameters a, b such that p_cal = sigmoid(a * logit(p) + b).

    Uses simple gradient descent on cross-entropy. Returns (a, b).
    """
    if len(pairs) < 30:
        return 1.0, 0.0
    eps = 1e-6
    a, b = 1.0, 0.0
    n = len(pairs)
    for _ in range(iters):
        ga, gb = 0.0, 0.0
        for p, y in pairs:
            p_c = min(max(p, eps), 1 - eps)
            logit = math.log(p_c / (1 - p_c))
            z = a * logit + b
            sig = 1.0 / (1.0 + math.exp(-z))
            err = sig - y
            ga += err * logit
            gb += err
        a -= lr * ga / n
        b -= lr * gb / n
    return a, b


def _print_report(model: str, pairs: list[tuple[float, int]], save_platt: bool) -> None:
    print(f"\n── model: {model or 'ALL'} ──")
    print(f"  sample size : {len(pairs):,}")
    if not pairs:
        print("  (no data)")
        return

    print(f"  accuracy    : {_accuracy(pairs)*100:.1f}%")
    print(f"  log loss    : {_log_loss(pairs):.4f}")
    print(f"  ECE (10-bin): {_ece(pairs)*100:.2f}%")

    print("\n  calibration table (predicted vs actual hit rate):")
    print(f"    {'bucket':10}  {'n':>6}  {'pred avg':>8}  {'actual':>8}  {'diff':>7}")
    for b in _by_bin(pairs):
        if b["n"] == 0:
            continue
        diff = b["predicted_avg"] - b["actual_avg"]
        print(f"    {b['range']:10}  {b['n']:>6}  {b['predicted_avg']:>8.3f}  {b['actual_avg']:>8.3f}  {diff:>+7.3f}")

    a, b_ = _fit_platt(pairs)
    print(f"\n  Platt fit: a={a:.4f}, b={b_:.4f}")
    print("  (calibrated prob = sigmoid(a * logit(raw_prob) + b))")

    if save_platt and model:
        PLATT_OUT.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if PLATT_OUT.exists():
            existing = json.loads(PLATT_OUT.read_text())
        existing[model] = {"a": a, "b": b_, "n": len(pairs), "accuracy": _accuracy(pairs), "log_loss": _log_loss(pairs)}
        PLATT_OUT.write_text(json.dumps(existing, indent=2))
        print(f"  → wrote Platt coefficients to {PLATT_OUT}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", help="Filter to one model_version (e.g. elo_v1_backfill, elo+pq_v1)")
    p.add_argument("--save-platt", action="store_true", help="Persist Platt coefficients to data/esports/cs2/platt_coefficients.json")
    args = p.parse_args()

    if args.model:
        pairs = _load_pairs(args.model)
        _print_report(args.model, pairs, args.save_platt)
        return

    # List versions
    versions = execute_query(
        "SELECT model_version, COUNT(*) AS n FROM cs2_predictions GROUP BY 1 ORDER BY n DESC",
        (),
    )
    if not versions:
        print("[!] cs2_predictions is empty — run the scanner or backfill first")
        sys.exit(1)

    print(f"\n=== CS2 CALIBRATION ===")
    print(f"  model versions found: {len(versions)}")
    for v in versions:
        print(f"    {v['model_version']:30}  n={v['n']:,}")

    for v in versions:
        pairs = _load_pairs(v["model_version"])
        _print_report(v["model_version"], pairs, args.save_platt)


if __name__ == "__main__":
    main()
