"""Parameterized Platt fitter for any in-play strategy.

INPLAY-CALIBRATION-COMPLETE (2026-06-15) — productizes the 12 near-duplicate
clone scripts that the original spec proposed (`fit_platt_inplay_a.py`,
`fit_platt_inplay_b.py`, ...). Mirrors `scripts/fit_platt_inplay_e.py`'s
math (reuses fit_platt_params / platt_transform / compute_ece / LOO) but
takes a `--strategy` argument and uses the canonical key from
`workers.jobs.inplay_bot.inplay_market_key()` so the read path
(`_build_inplay_bet_data` → `apply_platt`) and the write path (this script)
can't drift apart.

How to use:
  # Fit per-(market,selection) keys for one strategy:
  python3 scripts/fit_platt_inplay.py --strategy inplay_p_v2

  # Skip the leave-one-out cross-validation (faster, just in-sample ECE):
  python3 scripts/fit_platt_inplay.py --strategy inplay_o --skip-loo

  # Dry run — analyze + report, but don't write model_calibration rows:
  python3 scripts/fit_platt_inplay.py --strategy inplay_c --dry-run

  # Override the 100-sample minimum (e.g. for early validation runs):
  python3 scripts/fit_platt_inplay.py --strategy inplay_l --min-samples 50

Per-selection fits: each (market, selection) within a strategy gets its own
Platt row (e.g. `inplay_p_v2_1x2_home` and `inplay_p_v2_1x2_away` are fit
separately). A selection that doesn't meet --min-samples is skipped with a
clear message; the other selections still fit. This matches the original
spec's "market_key (e.g. `inplay_i_1x2_home`, `inplay_p_1x2_home`)" intent.

apply_platt's contract: returns the raw prob unchanged when no row exists
for the key. So a strategy that doesn't yet have enough data per selection
keeps writing raw model_probability → no behavior change until the fit
lands.

After the fit lands:
  - `simulated_bets.calibrated_prob` column populates for that strategy's
    new bets (centrally, via `_build_inplay_bet_data` — no per-strategy
    code change needed)
  - Operator can compare cal vs raw cohorts in the weekly bot review
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
from workers.jobs.inplay_bot import inplay_market_key  # noqa: E402
from scripts.fit_platt import (  # noqa: E402
    fit_platt_params,
    platt_transform,
    compute_ece,
)

DEFAULT_MIN_SAMPLES = 100  # matches the spec'd ≥100-settled-bet gate


def fetch_strategy_bets(bot_name: str):
    """Return list of {market, selection, p, y, odds} for settled bets."""
    rows = execute_query(
        """SELECT market,
                  selection,
                  model_probability::float AS p,
                  odds_at_pick::float       AS odds,
                  CASE WHEN result::text = 'won' THEN 1 ELSE 0 END AS y
           FROM simulated_bets
           WHERE bot_id = (SELECT id FROM bots WHERE name = %s)
             AND result::text IN ('won', 'lost')
             AND model_probability IS NOT NULL""",
        (bot_name,),
    )
    return rows or []


def group_by_selection(rows: list[dict], bot_name: str) -> dict[str, dict]:
    """Bucket rows by canonical market_key. Returns {key: {probs, ys, market, selection}}."""
    buckets: dict[str, dict] = {}
    for r in rows:
        key = inplay_market_key(bot_name, r["market"], r["selection"])
        b = buckets.setdefault(key, {
            "probs": [], "ys": [], "market": r["market"], "selection": r["selection"]
        })
        b["probs"].append(float(r["p"]))
        b["ys"].append(int(r["y"]))
    for b in buckets.values():
        b["probs"] = np.array(b["probs"], dtype=float)
        b["ys"]    = np.array(b["ys"],    dtype=int)
    return buckets


def store_calibration(market_key: str, a: float, b: float,
                      ece_before: float, ece_after: float, n: int) -> None:
    """Append-only: apply_platt's load_platt_params reads the latest by fitted_at."""
    execute_write(
        """INSERT INTO model_calibration
            (market, platt_a, platt_b, ece_before, ece_after, sample_count, fitted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            market_key,
            round(a, 6),
            round(b, 6),
            round(ece_before, 6),
            round(ece_after, 6),
            n,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def loo_validate(probs: np.ndarray, ys: np.ndarray) -> float:
    """Leave-one-out CV ECE. O(N^2) but with N≤500 it's seconds."""
    n = len(probs)
    cal_oof = np.zeros(n)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        a, b = fit_platt_params(probs[mask], ys[mask])
        cal_oof[i] = platt_transform(probs[i:i+1], a, b)[0]
    return compute_ece(cal_oof, ys, n_bins=10)


def fit_one(market_key: str, probs: np.ndarray, ys: np.ndarray,
            min_samples: int, dry_run: bool, skip_loo: bool) -> bool:
    """Fit a single market_key. Returns True if a row was written (or would be)."""
    n = len(probs)
    print(f"\n=== {market_key}  ·  n={n}")
    if n < min_samples:
        print(f"  skipped — need ≥{min_samples} settled bets")
        return False

    print(f"  pre-fit:  mean_pred={probs.mean():.4f}  actual_hit={ys.mean():.4f}")
    ece_before = compute_ece(probs, ys, n_bins=20)
    print(f"  ECE before: {ece_before:.4f}")

    a, b = fit_platt_params(probs, ys)
    print(f"  Fitted: a={a:+.6f}  b={b:+.6f}")
    cal = platt_transform(probs, a, b)
    ece_after = compute_ece(cal, ys, n_bins=20)
    print(f"  ECE after (in-sample): {ece_after:.4f}")
    print(f"  post-fit: mean_cal={cal.mean():.4f}  actual_hit={ys.mean():.4f}")

    if abs(cal.mean() - ys.mean()) > 0.02:
        print(f"  ⚠ WARN: post-fit mean ({cal.mean():.4f}) ≠ actual ({ys.mean():.4f}) — fit may have failed")

    if not skip_loo:
        ece_loo = loo_validate(probs, ys)
        print(f"  ECE (LOO, out-of-sample): {ece_loo:.4f}")
        if ece_loo > ece_before + 0.01:
            # +0.01 slack — LOO can be marginally worse on tiny n without
            # being a true overfit signal. >1pp worse is meaningful.
            print(f"  ⚠ SKIP write — LOO ECE {ece_loo:.4f} > pre-fit {ece_before:.4f} (overfit risk)")
            return False

    if dry_run:
        print("  --dry-run set — NOT writing model_calibration row")
        return False

    store_calibration(market_key, a, b, ece_before, ece_after, n)
    print(f"  ✓ Stored model_calibration: market='{market_key}'")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", required=True,
                        help="Bot name (e.g. inplay_p_v2, inplay_o, inplay_c)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Analyze + report but don't write rows")
    parser.add_argument("--skip-loo", action="store_true",
                        help="Skip leave-one-out CV (faster)")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES,
                        help=f"Minimum settled bets per selection (default {DEFAULT_MIN_SAMPLES})")
    args = parser.parse_args()

    rows = fetch_strategy_bets(args.strategy)
    if not rows:
        print(f"No settled bets for bot '{args.strategy}'. "
              f"Verify the name matches a row in `bots` and that there's been at least one won/lost bet.")
        return 1

    buckets = group_by_selection(rows, args.strategy)
    print(f"Strategy: {args.strategy}  ·  {len(rows)} settled bets  ·  {len(buckets)} (market, selection) buckets")
    print(f"Min samples per bucket: {args.min_samples}{'  (dry-run)' if args.dry_run else ''}")

    fitted = 0
    skipped = 0
    for market_key in sorted(buckets):
        b = buckets[market_key]
        if fit_one(market_key, b["probs"], b["ys"],
                   args.min_samples, args.dry_run, args.skip_loo):
            fitted += 1
        else:
            skipped += 1

    print(f"\nSummary: {fitted} fitted · {skipped} skipped · {len(buckets)} total buckets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
