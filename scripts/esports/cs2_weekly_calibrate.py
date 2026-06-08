#!/usr/bin/env python3
"""
Weekly CS2 calibration cron.

Refits Platt scaling on the last 90 days of (cs2_predictions ⨝ cs2_results).
If new log-loss beats the currently-saved Platt coefficients, promote.

Output: data/esports/cs2/platt_coefficients.json (read by the scanner at
startup; coefficients are applied to win_prob before computing fair_odds).

Usage:
    python3 scripts/esports/cs2_weekly_calibrate.py           # report only
    python3 scripts/esports/cs2_weekly_calibrate.py --promote # write if improved
"""
import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.esports.cs2_calibrate import _load_pairs, _log_loss, _accuracy, _ece, _fit_platt
from workers.api_clients.db import execute_query


PLATT_FILE = Path("data/esports/cs2/platt_coefficients.json")
WINDOW_DAYS = 90
MIN_SAMPLES = 200          # need at least this many settled bets to refit
PROMOTE_LOGLOSS_MARGIN = 0.001  # require ≥0.001 absolute log-loss improvement


def _load_recent_pairs(model: str, days: int = WINDOW_DAYS) -> list[tuple[float, int]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = execute_query("""
        SELECT p.win_prob1, r.winner
        FROM cs2_predictions p
        JOIN cs2_results r ON p.bo3gg_id = r.bo3gg_id
        WHERE p.model_version = %s
          AND p.scan_time >= %s
          AND p.win_prob1 IS NOT NULL
    """, (model, cutoff))

    out = []
    for row in rows:
        prob = float(row["win_prob1"])
        if row["winner"] == "team1":
            out.append((prob, 1))
        elif row["winner"] == "team2":
            out.append((prob, 0))
    return out


def _apply_platt(pairs: list[tuple[float, int]], a: float, b: float) -> list[tuple[float, int]]:
    """Apply a Platt transform to the predicted probabilities."""
    import math
    eps = 1e-6
    out = []
    for p, y in pairs:
        p_c = min(max(p, eps), 1 - eps)
        logit = math.log(p_c / (1 - p_c))
        cal = 1.0 / (1.0 + math.exp(-(a * logit + b)))
        out.append((cal, y))
    return out


def _load_existing() -> dict:
    if not PLATT_FILE.exists():
        return {}
    try:
        return json.loads(PLATT_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def calibrate(model: str, promote: bool = False) -> None:
    print(f"\n=== CS2 WEEKLY CALIBRATION  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  model_version: {model}")
    print(f"  window: last {WINDOW_DAYS} days\n")

    pairs = _load_recent_pairs(model)
    print(f"  recent (predicted, outcome) pairs: {len(pairs):,}")

    if len(pairs) < MIN_SAMPLES:
        print(f"  ✗ insufficient samples (<{MIN_SAMPLES}) — skipping refit")
        return

    # Baseline (no Platt)
    raw_loss = _log_loss(pairs)
    raw_acc = _accuracy(pairs)
    raw_ece = _ece(pairs)
    print(f"\n  raw    : acc={raw_acc*100:5.1f}%  log_loss={raw_loss:.4f}  ECE={raw_ece*100:.2f}%")

    # Existing Platt
    existing = _load_existing()
    existing_entry = existing.get(model)
    existing_loss = None
    if existing_entry:
        a, b = float(existing_entry["a"]), float(existing_entry["b"])
        cal = _apply_platt(pairs, a, b)
        existing_loss = _log_loss(cal)
        print(f"  current: a={a:.4f} b={b:.4f}  log_loss={existing_loss:.4f}  (saved {existing_entry.get('updated_at','?')[:10]})")

    # New Platt fit
    a_new, b_new = _fit_platt(pairs)
    cal_new = _apply_platt(pairs, a_new, b_new)
    new_loss = _log_loss(cal_new)
    new_ece = _ece(cal_new)
    print(f"  new fit: a={a_new:.4f} b={b_new:.4f}  log_loss={new_loss:.4f}  ECE={new_ece*100:.2f}%")

    # Decision
    if existing_loss is None or new_loss + PROMOTE_LOGLOSS_MARGIN < existing_loss:
        improvement = (existing_loss - new_loss) if existing_loss else (raw_loss - new_loss)
        print(f"\n  ✓ PROMOTE  (log_loss improved by {improvement:.4f})")
        if promote:
            existing[model] = {
                "a": a_new, "b": b_new,
                "n": len(pairs),
                "log_loss": new_loss,
                "accuracy": _accuracy(cal_new),
                "ece": new_ece,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            PLATT_FILE.parent.mkdir(parents=True, exist_ok=True)
            PLATT_FILE.write_text(json.dumps(existing, indent=2))
            print(f"  → wrote {PLATT_FILE}")
        else:
            print(f"  (dry-run; add --promote to persist)")
    else:
        print(f"\n  ✗ keep current (new log_loss {new_loss:.4f} not better than {existing_loss:.4f})")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="elo+pq_v1", help="Model version to calibrate")
    p.add_argument("--promote", action="store_true", help="Persist improved coefficients to JSON")
    args = p.parse_args()
    calibrate(args.model, args.promote)


if __name__ == "__main__":
    main()
