"""
OddsIntel — Calibration Validation Script

Plots predicted probability vs actual win rate in 5% bins.
A well-calibrated model should be close to the diagonal.

Usage:
    python scripts/check_calibration.py
    python scripts/check_calibration.py --market 1x2_home
    python scripts/check_calibration.py --min-date 2026-04-01
"""

import sys
import os
import json
import argparse
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from workers.api_clients.supabase_client import get_client


def check_calibration(market: str = None, min_date: str = None,
                      source: str = "ensemble") -> dict:
    client = get_client()

    # Fetch all predictions with known outcomes
    query = client.table("predictions").select(
        "model_probability, market, source, "
        "matches(status, result, score_home, score_away)"
    ).eq("source", source)

    if market:
        query = query.eq("market", market)

    result = query.execute()
    rows = result.data or []

    # Filter to finished matches only
    labeled = []
    for row in rows:
        m = row.get("matches") or {}
        if isinstance(m, list):
            m = m[0] if m else {}
        if m.get("status") != "finished":
            continue
        if m.get("result") is None:
            continue

        prob = row.get("model_probability")
        mkt = row.get("market", "")
        if prob is None:
            continue

        # Determine if prediction was correct
        result_val = m.get("result")  # 'home' | 'draw' | 'away'
        score_h = m.get("score_home")
        score_a = m.get("score_away")

        won = None
        if mkt == "1x2_home":
            won = (result_val == "home")
        elif mkt == "1x2_away":
            won = (result_val == "away")
        elif mkt == "1x2_draw":
            won = (result_val == "draw")
        elif mkt in ("over25", "over_under_25") and score_h is not None:
            won = ((score_h + score_a) > 2)
        elif mkt in ("under25",) and score_h is not None:
            won = ((score_h + score_a) < 3)

        if won is None:
            continue

        labeled.append({"prob": float(prob), "won": won, "market": mkt})

    if not labeled:
        print("No labeled predictions found. Run after settlement populates results.")
        return {}

    # Bin into 5% buckets
    bins = {}
    for step in range(0, 100, 5):
        lo = step / 100
        hi = (step + 5) / 100
        key = f"{step:02d}-{step+5:02d}%"
        items = [x for x in labeled if lo <= x["prob"] < hi]
        if items:
            actual_rate = sum(1 for x in items if x["won"]) / len(items)
            mid_prob = (lo + hi) / 2
            bins[key] = {
                "predicted_mid": round(mid_prob, 3),
                "actual_win_rate": round(actual_rate, 3),
                "count": len(items),
                "deviation": round(actual_rate - mid_prob, 3),
            }

    # Summary stats
    total = len(labeled)
    correct = sum(1 for x in labeled if x["won"])
    overall_hit_rate = correct / total if total else 0

    # Expected Calibration Error (ECE)
    ece = 0.0
    for b in bins.values():
        ece += (b["count"] / total) * abs(b["actual_win_rate"] - b["predicted_mid"])

    # Mean absolute deviation
    deviations = [abs(b["deviation"]) for b in bins.values()]
    mean_deviation = sum(deviations) / len(deviations) if deviations else 0

    result_dict = {
        "date": date.today().isoformat(),
        "source": source,
        "market_filter": market or "all",
        "total_predictions": total,
        "overall_hit_rate": round(overall_hit_rate, 4),
        "ece": round(ece, 4),
        "mean_abs_deviation": round(mean_deviation, 4),
        "bins": bins,
    }

    # Print table
    print(f"\n{'─'*65}")
    print(f"  Calibration Check — source={source}, market={market or 'all'}")
    print(f"  {total} labeled predictions | ECE={ece:.4f} | MAD={mean_deviation:.4f}")
    print(f"{'─'*65}")
    print(f"  {'Bin':>8}  {'Predicted':>10}  {'Actual':>8}  {'Count':>6}  {'Deviation':>10}")
    print(f"{'─'*65}")

    for key, b in sorted(bins.items()):
        dev = b["deviation"]
        flag = " ⚠" if abs(dev) > 0.05 else ""
        print(f"  {key:>8}  {b['predicted_mid']:>10.1%}  {b['actual_win_rate']:>8.1%}"
              f"  {b['count']:>6}  {dev:>+10.3f}{flag}")

    print(f"{'─'*65}")

    if ece < 0.02:
        print("  ✅ Well calibrated (ECE < 2%)")
    elif ece < 0.05:
        print("  ⚠️  Moderate miscalibration (ECE 2-5%) — monitor")
    else:
        print(f"  ❌ Poor calibration (ECE={ece:.1%}) — fix before trusting edge calculations")

    if result_dict["total_predictions"] < 200:
        print(f"\n  ⚠  Only {total} predictions — need 500+ for reliable calibration check")
    elif result_dict["total_predictions"] < 500:
        print(f"\n  ℹ  {total} predictions available (500+ recommended for Platt scaling)")
    else:
        print(f"\n  ✅ {total} predictions — sufficient for Platt scaling")

    print()

    # Save to logs
    log_dir = Path(__file__).parent.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"calibration_{date.today().isoformat()}.json"
    with open(log_file, "w") as f:
        json.dump(result_dict, f, indent=2)
    print(f"  Saved: {log_file}")

    return result_dict


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check prediction calibration")
    parser.add_argument("--market", default=None,
                        help="Filter to specific market (e.g. 1x2_home)")
    parser.add_argument("--min-date", default=None,
                        help="Only include predictions after this date (YYYY-MM-DD)")
    parser.add_argument("--source", default="ensemble",
                        help="Prediction source to check (ensemble/poisson/xgboost/af)")
    args = parser.parse_args()

    check_calibration(market=args.market, min_date=args.min_date, source=args.source)
