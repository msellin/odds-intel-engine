#!/usr/bin/env python3
"""A/B: DO HALF-TIME FEATURES HELP AS ONE INPUT AMONG MANY? ([[#084]] step 4)

Trains TWO bundles at the same moment on the same data, differing by EXACTLY the
five migration-378 columns, then scores both through the residual harness.

WHY AN A/B RATHER THAN A BEFORE/AFTER
-------------------------------------
Backfills are running as this executes — lineups, referee, assists and the
shot-location columns are all changing fill rates. A feature's `_missing`
indicator changes meaning when its fill changes, so comparing a new model against
a bundle trained hours ago would measure the backfill, not the work. Two arms
trained in the same minute share every one of those confounds and cancel them.

WHY IT RUNS DESPITE THE PROBE FAILING
-------------------------------------
`probe_half_time_alpha.py` scored the half-time ratings STANDALONE and failed:
alpha 0.0100 on de-vigged Pinnacle, residual AUC 0.4107. That answers "can this
carry a model alone" (no). It cannot answer "does it help as one input among 52",
and assuming a standalone failure settles that nearly produced a wrong conclusion
in [[#077]].

THE CHECKS THIS SCRIPT MAKES BEFORE TRUSTING ANY NUMBER
--------------------------------------------------------
Every one of these has a corresponding way the experiment could silently be
meaningless, and RELIABILITY_LEDGER #23 is what happens when they are skipped:

  1. Arm A reproduces the SHIPPED feature set exactly (52 real features).
     Otherwise the A/B measures my flag choices rather than half-time features.
  2. Arm B is arm A plus EXACTLY the five columns — no more, no fewer.
  3. Both arms train on the same number of rows.
  4. The half-time columns carry real variance in the training window. If they
     were constant or empty, arm B IS arm A and a null result means nothing.
  5. Both bundles are scored by the SAME harness invocation, differing only in
     `--bundle`.

Usage:
    python3 scripts/ab_half_time_features.py --cutoff 2026-08-20
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import joblib  # noqa: E402

from workers.model.train import (  # noqa: E402
    FEATURE_COLS, PINNACLE_FEATURE_COLS, OU_MARKET_FEATURE_COLS,
    HALFTIME_FEATURE_COLS, train_all, load_training_data,
)

SHIPPED = "data/models/soccer/v20260914_clean_cut0820"
ROOT = Path("data/models/soccer")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default="2026-08-20")
    ap.add_argument("--tag", default="ab_ht")
    a = ap.parse_args()

    print("A/B — HALF-TIME FEATURES ([[#084]] step 4)")
    print(f"  cutoff {a.cutoff}; both arms trained now, same data, same minute\n")

    # ── CHECK 1: arm A must reproduce the shipped feature set ────────────────
    shipped = [c for c in joblib.load(f"{SHIPPED}/feature_cols.pkl")
               if not c.endswith("_missing")]
    arm_a_cols = FEATURE_COLS + PINNACLE_FEATURE_COLS + OU_MARKET_FEATURE_COLS
    assert set(arm_a_cols) == set(shipped), (
        f"arm A does not reproduce the shipped feature set "
        f"({len(arm_a_cols)} vs {len(shipped)}) — the A/B would measure the flag "
        f"choice, not half-time features. Symmetric difference: "
        f"{set(arm_a_cols) ^ set(shipped)}")
    print(f"  [check 1] arm A reproduces the shipped {len(shipped)} features ✓")

    # ── CHECK 2: arm B differs by exactly the five columns ───────────────────
    arm_b_cols = arm_a_cols + HALFTIME_FEATURE_COLS
    delta = set(arm_b_cols) - set(arm_a_cols)
    assert delta == set(HALFTIME_FEATURE_COLS) and len(arm_b_cols) == len(arm_a_cols) + 5, (
        f"arm B differs from arm A by {sorted(delta)} — it must differ by exactly "
        f"the five half-time columns and nothing else")
    print(f"  [check 2] arm B = arm A + exactly {sorted(delta)} ✓")

    # ── CHECK 3 + 4: same rows, and the new columns are not dead ─────────────
    fa, ta = load_training_data(include_pinnacle=True, include_ou_market=True,
                                cutoff_date=a.cutoff)
    fb, tb = load_training_data(include_pinnacle=True, include_ou_market=True,
                                include_halftime=True, cutoff_date=a.cutoff)
    assert len(fa) == len(fb), f"arms trained on different row counts: {len(fa)} vs {len(fb)}"
    print(f"  [check 3] both arms train on {len(fa):,} rows ✓")

    for c in HALFTIME_FEATURE_COLS:
        col = fb[c]
        nn, sd = col.notna().sum(), col.std()
        assert nn > 10000 and sd and sd > 1e-6, (
            f"{c} is dead in the training window (non-null {nn}, sd {sd}) — arm B "
            f"would be arm A and a null result would mean nothing")
    print("  [check 4] all five half-time columns carry real variance:")
    for c in HALFTIME_FEATURE_COLS:
        print(f"              {c:20s} n={fb[c].notna().sum():>7,}  "
              f"mean {fb[c].mean():+.3f}  sd {fb[c].std():.3f}")

    # ── train both arms ──────────────────────────────────────────────────────
    for arm, ht in (("A", False), ("B", True)):
        ver = f"{a.tag}_{arm}"
        print(f"\n  === training arm {arm} ({'with' if ht else 'without'} half-time) "
              f"-> {ROOT/ver} ===")
        train_all(version=ver, include_pinnacle=True, include_ou_market=True,
                  include_halftime=ht, cutoff_date=a.cutoff, output_root=ROOT)

    # ── CHECK 5: score both through the SAME harness ─────────────────────────
    print("\n" + "=" * 74)
    print("SCORING BOTH ARMS THROUGH residual_test_ou.py (same command, --bundle only)")
    print("=" * 74)
    for arm in ("A", "B"):
        print(f"\n########## ARM {arm} ##########")
        subprocess.run([sys.executable, "scripts/residual_test_ou.py",
                        "--line", "25", "--bundle", str(ROOT / f"{a.tag}_{arm}")],
                       check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
