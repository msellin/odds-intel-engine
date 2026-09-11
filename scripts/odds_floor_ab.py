#!/usr/bin/env python3
"""ODDS-FLOOR-AB — 1x2 odds floor 2.80 vs 2.00 at a fixed edge floor, on every
dataset size we have.

Owner (2026-09-11): "theres a big diff on 2.0 vs 2.8, can you just run a sweep.
10%, 2.8 vs 2.0 odds floor on every size of data, up to 100k".

THE QUESTION, STATED PRECISELY
------------------------------
Both gates keep 1x2 HOME picks at edge >= the edge floor. They differ only in
what they admit:

    GATE A (live)     home, edge >= E, odds >= 2.80
    GATE B (proposed) home, edge >= E, odds >= 2.00
    MARGINAL BAND     home, edge >= E, 2.00 <= odds < 2.80   <- the whole difference

So "is B better than A" reduces to **"is the MARGINAL BAND profitable?"** —
because B is exactly A plus that band. Comparing A's ROI to B's ROI is the wrong
test: B is a volume-weighted blend of A and the band, so it is mathematically
pulled toward A and will always look "similar" no matter how good the band is.
This script therefore reports the band on its own, with a significance test.

WHY A SIGNIFICANCE TEST AND NOT JUST ROI
-----------------------------------------
Betting returns are extremely high-variance: a single 4.00 winner moves a
100-bet ROI by three points. The repo's own note is that ~9,300 settled bets are
needed for +/-2% on ROI. So a bare "+12.7%" on n=509 means nothing without a
standard error, and the honest output is often "positive but not distinguishable
from zero". Reported here as a t-statistic and a bootstrap 95% CI.

DATASETS — every size, smallest to largest
-------------------------------------------
Includes the fixture-level basis the owner asked for ("up to 100k"), but see
the warning it prints: that basis is BLIND TO THE ODDS AXIS by construction
(best-of-books systematically rescues low-odds picks we could never have taken
at that price — ANALYSIS_GOTCHAS §52/§55), and its edge is a de-vig/line-shop
edge, not our model's. It is shown for completeness and must not decide this
question. The pick-level datasets are the evidence.

Usage:
  python3 scripts/odds_floor_ab.py                 # edge >= 10%
  python3 scripts/odds_floor_ab.py --edge 13
  python3 scripts/odds_floor_ab.py --lo 2.0 --hi 2.8
"""
from __future__ import annotations

import argparse
import importlib.util
import pathlib
import random
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

_spec = importlib.util.spec_from_file_location(
    "_cube", pathlib.Path(__file__).parent / "floor_grid_sweep.py")
_cube = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cube)


def _stats(rows: list[dict]) -> dict:
    """ROI, its standard error, a t-stat vs zero, and a bootstrap 95% CI."""
    rets = [r["ret"] for r in rows]
    n = len(rets)
    if n < 2:
        return {"n": n, "roi": None, "se": None, "t": None, "lo": None, "hi": None,
                "clv": None}
    mean = st.mean(rets)
    se = st.stdev(rets) / (n ** 0.5)
    boot = []
    rnd = random.Random(20260911)          # fixed seed: the CI must be reproducible
    for _ in range(2000):
        s = [rets[rnd.randrange(n)] for _ in range(n)]
        boot.append(st.mean(s))
    boot.sort()
    cl = [r["clv_pinnacle"] for r in rows if r.get("clv_pinnacle") is not None]
    return {"n": n, "roi": 100 * mean, "se": 100 * se,
            "t": (mean / se) if se else None,
            "lo": 100 * boot[int(0.025 * len(boot))],
            "hi": 100 * boot[int(0.975 * len(boot))],
            "clv": 100 * st.mean(cl) if len(cl) >= 25 else None}


def _folds(rows, n=3):
    xs = sorted(rows, key=lambda r: r["pick_time"])
    size = max(1, len(xs) // n)
    return [xs[i:i + size] for i in range(0, len(xs), size)][:n]


def _row(label, s, extra=""):
    if s["roi"] is None:
        print(f"  {label:26}{s['n']:>7}      (too few)")
        return
    clv = f"{s['clv']:>+9.1f}" if s["clv"] is not None else f"{'n/a':>9}"
    print(f"  {label:26}{s['n']:>7}{s['roi']:>+9.1f}{s['se']:>8.1f}"
          f"{s['t']:>7.2f}{clv}   [{s['lo']:+.1f}, {s['hi']:+.1f}]{extra}")


def run(edge: float, lo: float, hi: float) -> int:
    rows = _cube.load(include_idealized=True)
    rows = [r for r in rows if r["edge_scale"] == "fraction"]

    order = ["sim/calibrated", "sim/cohort", "sim/all-prematch",
             "shadow/all-prematch", "PICK-LEVEL POOLED", _cube.IDEALIZED_LABEL]
    by_ds = {}
    for ds in order:
        if ds == "PICK-LEVEL POOLED":
            sub = [r for r in rows
                   if r["dataset"] in ("sim/all-prematch", "shadow/all-prematch")
                   and r["edge_kind"] == "model"]
        elif ds == _cube.IDEALIZED_LABEL:
            sub = [r for r in rows if r["dataset"] == ds]
        else:
            sub = [r for r in rows if r["dataset"] == ds and r["edge_kind"] == "model"]
        by_ds[ds] = [r for r in sub
                     if r["family"] == "1x2" and r["selection"] == "home"
                     and r["ep"] >= edge]

    print(f"\n{'='*112}")
    print(f"1x2 HOME · edge >= {edge:.0%} · GATE A odds>={hi:.2f}  vs  "
          f"GATE B odds>={lo:.2f}   (B = A + the {lo:.2f}-{hi:.2f} band)")
    print(f"{'='*112}")
    print("B is a volume-weighted blend of A and the band, so it is pulled toward A "
          "by construction.\nThe real test is the MARGINAL BAND on its own — that is "
          "the entire difference between the gates.")

    for ds in order:
        sub = by_ds[ds]
        if not sub:
            continue
        A = [r for r in sub if r["odds"] >= hi]
        B = [r for r in sub if r["odds"] >= lo]
        M = [r for r in sub if lo <= r["odds"] < hi]
        note = ""
        if ds == _cube.IDEALIZED_LABEL:
            note = ("\n  ⚠️  BLIND TO THE ODDS AXIS by construction (best-of-books "
                    "rescues low-odds picks we\n      could not have taken) and its "
                    "edge is de-vig/line-shop, not our model's. Shown for\n      "
                    "completeness — it cannot decide this question.")
        print(f"\n── {ds} ──{note}")
        print(f"  {'':26}{'n':>7}{'ROI%':>9}{'SE':>8}{'t':>7}{'CLVpin':>9}   "
              f"bootstrap 95% CI")
        _row(f"GATE A  odds>={hi:.2f}", _stats(A))
        _row(f"GATE B  odds>={lo:.2f}", _stats(B))
        _row(f"MARGINAL {lo:.2f}-{hi:.2f}", _stats(M), "   <- the decision")
        if M:
            fr = [100 * st.mean([x["ret"] for x in f]) if f else None
                  for f in _folds(M)]
            cells = " ".join(f"{v:+.1f}" if v is not None else "  n/a" for v in fr)
            allpos = all(v is not None and v > 0 for v in fr) and len(fr) == 3
            print(f"  {'  marginal band by fold':26}{'':7}{cells}"
                  f"   {'ROBUST ✓' if allpos else 'not fold-robust'}")

    print(f"\n{'='*112}")
    print("HOW TO READ IT")
    print(f"{'='*112}")
    print("* The MARGINAL BAND line is the decision. Adding it to the gate adds "
          "exactly those bets.")
    print("* |t| < 2 means the band is NOT distinguishable from zero at this "
          "sample size, however\n  large the ROI looks. A bootstrap CI spanning 0 "
          "says the same thing.")
    print("* 'not fold-robust' means it lost in at least one time window. This "
          "repo's standing rule is\n  that a floor is adoptable only if positive in "
          "EVERY fold — a rule written after a 15% floor\n  was adopted and reverted "
          "in one day.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--edge", type=float, default=10.0, help="edge floor in PERCENT")
    ap.add_argument("--lo", type=float, default=2.00, help="proposed odds floor")
    ap.add_argument("--hi", type=float, default=2.80, help="current odds floor")
    a = ap.parse_args()
    return run(a.edge / 100.0, a.lo, a.hi)


if __name__ == "__main__":
    raise SystemExit(main())
