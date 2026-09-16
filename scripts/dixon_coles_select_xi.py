#!/usr/bin/env python3
"""DIXON-COLES-SELECT-XI — choose the time-decay rate on a VALIDATION window.

Phase 3 of `DIXON-COLES-OU-BASELINE`. The one hyper-parameter the fit does not
learn for itself is xi, the exponential decay per day. Picking it on the
evaluation window would manufacture whatever result we wanted, so it is chosen
here, on a window that ENDS BEFORE the out-of-sample cutoff, and then frozen.

WHY THIS STOPPED BEING A FORMALITY. Phase 2 found the model sitting 0.097 goals
per match below actual, and diagnosed it: the fit window (prior 365d) averages
2.8601 goals while the scored window averages 3.0887 — a +0.2286 regime shift
that a history-fitted model cannot see coming. A faster decay weights recent
matches harder and should track it. That is a hypothesis with a direction, which
is exactly what a validation window is for.

SELECTION CRITERION: log-loss of P(over 2.5) against the realised outcome.
Log-loss rather than the calibration gap, because a model can be perfectly
calibrated on average and useless per match; log-loss punishes both miscalibration
and lack of discrimination. The calibration gap is reported alongside so a
degenerate winner is visible.

The chosen value is printed for pasting into the phase-4 run. It is NOT written
back automatically — a hyper-parameter that edits itself is a hyper-parameter
nobody has checked.

    python3 scripts/dixon_coles_select_xi.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_dc_fit", Path(__file__).resolve().parent / "dixon_coles_fit.py")
_dc = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_dc)

# The grid, as half-lives so the values mean something: 0 = no decay, then
# roughly 2 years down to 3 months.
XI_GRID = [0.0, 0.0005, 0.001, 0.0018, 0.003, 0.005, 0.008]

# OOS cutoff — the validation window must end strictly before this.
OOS_CUTOFF = dt.date(2026, 8, 20)


def logloss(ps: list[float], ys: list[int]) -> float:
    e = 1e-9
    return -sum(y * math.log(max(min(p, 1 - e), e))
                + (1 - y) * math.log(max(min(1 - p, 1 - e), e))
                for p, y in zip(ps, ys)) / len(ps)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--val-from", default="2026-07-08")
    ap.add_argument("--val-to", default="2026-08-19",
                    help="inclusive; MUST be before the OOS cutoff 2026-08-20")
    ap.add_argument("--history-days", type=int, default=1100)
    a = ap.parse_args()

    vf = dt.date.fromisoformat(a.val_from)
    vt = dt.date.fromisoformat(a.val_to)
    if vt >= OOS_CUTOFF:
        print(f"REFUSED: validation window ends {vt}, which is not before the "
              f"OOS cutoff {OOS_CUTOFF}. Selecting xi on the evaluation window "
              f"would manufacture the phase-4 result.")
        return 1

    print(f"\n=== xi selection on VALIDATION {vf} .. {vt} "
          f"(OOS cutoff {OOS_CUTOFF}, untouched) ===\n")
    print(f"{'xi':>8s} {'half-life':>10s} {'n':>7s} {'logloss':>9s} "
          f"{'pred':>7s} {'actual':>7s} {'cal gap':>8s}")

    # Load ONCE. The sweep was re-pulling ~170k rows per grid point.
    shared = _dc.load(a.history_days)
    print(f"  (loaded {len(shared):,} matches once, reused across the grid)\n")

    results = []
    for xi in XI_GRID:
        # to_date bounds the refit grid itself, so no week past `vt` is ever
        # fitted — the previous version fitted them and then discarded them.
        out, _skipped, _n = _dc.run(vf, xi, a.history_days, verify=False,
                                    to_date=vt, rows=shared)
        if len(out) < 500:
            print(f"{xi:8.4f} {'-':>10s} {len(out):7,}  too few scored")
            continue
        ps = [r["p_over25"] for r in out]
        ys = [1 if r["hg"] + r["ag"] > 2 else 0 for r in out]
        ll = logloss(ps, ys)
        pred, act = sum(ps) / len(ps), sum(ys) / len(ys)
        hl = "never" if xi == 0 else f"{math.log(2)/xi:.0f}d"
        print(f"{xi:8.4f} {hl:>10s} {len(out):7,} {ll:9.5f} "
              f"{pred:7.4f} {act:7.4f} {pred-act:+8.4f}")
        results.append((ll, xi, pred - act, len(out)))

    if not results:
        print("\nnothing selected")
        return 1
    results.sort()
    best_ll, best_xi, best_gap, best_n = results[0]
    print(f"\nSELECTED xi = {best_xi}  (log-loss {best_ll:.5f}, "
          f"calibration gap {best_gap:+.4f}, n={best_n:,})")
    if best_xi == XI_GRID[-1]:
        print("  ⚠️ the winner is at the EDGE of the grid — the optimum may lie "
              "beyond it; widen before trusting this.")
    if best_xi == 0.0:
        print("  note: no decay won, so the regime-tracking hypothesis from "
              "phase 2 is NOT supported on this window.")
    print("\nPaste into phase 4:  python3 scripts/dixon_coles_fit.py "
          f"--xi {best_xi} --from-date 2026-08-20")
    print("Not written back automatically — a hyper-parameter that edits itself "
          "is one nobody has checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
