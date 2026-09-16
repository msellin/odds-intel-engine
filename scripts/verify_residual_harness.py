#!/usr/bin/env python3
"""VERIFY-RESIDUAL-HARNESS — can the instrument recover an edge that IS there?

WHY. Every alpha this project has ever quoted comes out of one harness:
`fit_platt` + `fit_alpha` + the Shin de-vig + the first-half/second-half split.
On its output we have closed model-anchored 1x2, closed model-anchored O/U, and
rejected a feature addition. It has never been tested against a KNOWN answer.
It was inherited as pre-registered, and pre-registered is not the same as
correct.

The failure mode that matters is a FALSE NEGATIVE: a harness that reports
alpha = 0 when there is real edge would have produced exactly the results we
have, and nothing else in the pipeline would contradict it. So the central test
here is not "does it reject noise" (easy) but "does it FIND a planted edge".

METHOD. Build synthetic markets where the right answer is known by construction:

  * model = truth, market = degraded           -> alpha must go HIGH, PRIMARY pass
  * model = noise, market = CALIBRATED         -> alpha must be 0
  * model and market equally noisy views       -> alpha must land near 0.5
  * model = noise, market = OVER-CONFIDENT     -> alpha > 0, and that is CORRECT

WHAT THE LAST CASE TAUGHT US, and it changes how every alpha in this project
should be read. Platt maps a noise model to a near-constant, so blending it in is
pure SHRINKAGE toward the base rate — which genuinely improves an over-confident
market. Therefore:

    alpha > 0 does NOT prove the model carries information.
    alpha = 0 DOES say the market is calibrated and nothing, not even shrinkage,
              improves on it.

Our own results are all alpha = 0 against Pinnacle, so they land on the strong
side of that asymmetry: Pinnacle's de-vigged prices are well calibrated and our
models add nothing beyond them. It also retro-explains the control bundle's
alpha = 0.0150, which is more likely shrinkage than skill.

Plus mechanical properties that a silent refactor could break: `fit_alpha`
really returns the argmin of the log-loss it claims to minimise (checked against
an independent fine grid), Platt is monotone and fitted on the first half only,
Shin reduces to the proportional de-vig at zero overround and takes more margin
off the longshot, and the evaluation slice never overlaps the fitting slice.

Imports the REAL functions from `residual_test_ou.py` — a reimplementation here
would verify this file rather than the harness.

No DB, no network.

    python3 scripts/verify_residual_harness.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

# Load the harness module BY PATH rather than making `scripts/` a package.
# Adding a scripts/__init__.py to satisfy one import would change how every
# other script in this directory resolves, for no benefit. Importing the real
# module matters — a reimplementation here would verify this file, not the
# harness.
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location(
    "_residual_test_ou", Path(__file__).resolve().parent / "residual_test_ou.py")
_rt = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rt)
auc, fit_alpha, fit_platt = _rt.auc, _rt.fit_alpha, _rt.fit_platt
ll, shin2, sig = _rt.ll, _rt.shin2, _rt.sig

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def make_market(n: int, seed: int, model_noise: float, market_noise: float):
    """Two noisy views of one truth, plus outcomes drawn from that truth.

    `model_noise`/`market_noise` are sd on the LOGIT. Independent by
    construction, so neither view can be recovered from the other.
    """
    rng = np.random.default_rng(seed)
    z = rng.normal(0.0, 1.0, n)                      # true logit
    p_true = 1 / (1 + np.exp(-z))
    y = (rng.random(n) < p_true).astype(int)
    p_model = 1 / (1 + np.exp(-(z + rng.normal(0, model_noise, n))))
    p_market = 1 / (1 + np.exp(-(z + rng.normal(0, market_noise, n))))
    return list(p_model), list(p_market), list(y)


def harness_alpha(p_model, p_market, ys):
    """Run the harness exactly as the residual tests do: Platt on the FIRST half,
    alpha fitted on the first half, everything evaluated on the second."""
    cut = len(ys) // 2
    a, b = fit_platt(list(zip(p_model[:cut], [float(v) for v in ys[:cut]])))
    pm = [sig(a * p + b) for p in p_model]
    alpha = fit_alpha(pm[:cut], p_market[:cut], ys[:cut])
    te = slice(cut, None)
    l_mkt = ll(p_market[te], ys[te])
    l_bl = ll([alpha * m + (1 - alpha) * k for m, k in zip(pm[te], p_market[te])], ys[te])
    return alpha, l_mkt, l_bl


def main() -> int:
    N = 20000

    print("\n=== 1. THE CENTRAL TEST — does it FIND an edge that is really there? ===")
    # Model sees the truth; the market is a degraded view. Any honest harness
    # must put most of its weight on the model.
    pm, pk, ys = make_market(N, seed=1, model_noise=0.0, market_noise=0.9)
    alpha, l_mkt, l_bl = harness_alpha(pm, pk, ys)
    check("planted edge -> alpha goes HIGH", alpha > 0.5, f"alpha = {alpha:.4f}")
    check("planted edge -> blend beats market", l_bl < l_mkt,
          f"blend {l_bl:.4f} vs market {l_mkt:.4f}")
    check("planted edge -> PRIMARY would PASS", (l_bl < l_mkt) and (alpha > 0.02),
          "this is the false-negative guard: if this fails, every alpha=0 we have "
          "reported is worthless")

    # A SMALL but real edge — the realistic case, and the one most likely to be
    # missed. Model and market are both noisy, model slightly less so.
    pm, pk, ys = make_market(N, seed=2, model_noise=0.55, market_noise=0.70)
    alpha, l_mkt, l_bl = harness_alpha(pm, pk, ys)
    check("SMALL edge -> alpha clears the 0.02 bar", alpha > 0.02, f"alpha = {alpha:.4f}")
    check("SMALL edge -> blend beats market", l_bl < l_mkt,
          f"blend {l_bl:.4f} vs market {l_mkt:.4f}")

    print("\n=== 2. does it reject what is NOT there? ===")
    # Model is pure noise; the market is PERFECTLY CALIBRATED. Nothing to add
    # and nothing to shrink, so the only honest answer is zero.
    rng = np.random.default_rng(3)
    z = rng.normal(0.0, 1.0, N)
    p_true = 1 / (1 + np.exp(-z))
    ys = list((rng.random(N) < p_true).astype(int))
    pm_noise = list(rng.random(N))
    alpha, l_mkt, l_bl = harness_alpha(pm_noise, list(p_true), ys)
    check("pure noise vs a CALIBRATED market -> alpha = 0", alpha < 0.02,
          f"alpha = {alpha:.4f}")
    check("pure noise vs a CALIBRATED market -> blend does not beat it",
          l_bl >= l_mkt - 1e-9, f"blend {l_bl:.4f} vs market {l_mkt:.4f}")

    print("\n=== 2b. WHAT A NON-ZERO ALPHA ACTUALLY MEANS ===")
    # Repeat the same pure-noise model against an OVER-CONFIDENT market. Platt
    # maps noise to a near-constant (sd ~0.002), so blending it in is pure
    # SHRINKAGE toward the base rate — and shrinkage genuinely improves an
    # over-confident market. The harness is right to reward it, but it means:
    #
    #   alpha > 0 does NOT prove the model carries information.
    #   alpha = 0 DOES say the market is calibrated and nothing, not even
    #             shrinkage, improves on it.
    #
    # That is the stronger reading of our own alpha = 0.0000 results, and it is
    # why this section exists rather than being "fixed" away.
    pk_over = list(1 / (1 + np.exp(-(z + rng.normal(0, 0.4, N)))))
    a_over, lm_over, lb_over = harness_alpha(pm_noise, pk_over, ys)
    check("pure noise vs an OVER-CONFIDENT market -> alpha > 0 (shrinkage, not skill)",
          a_over > 0.02, f"alpha = {a_over:.4f}, blend {lb_over:.4f} < market {lm_over:.4f}")
    cutq = N // 2
    aq, bq = fit_platt(list(zip(pm_noise[:cutq], [float(v) for v in ys[:cutq]])))
    pmq = [sig(aq * x + bq) for x in pm_noise]
    check("...and the 'model' it is weighting is a near-constant",
          float(np.std(pmq)) < 0.02,
          f"sd of the calibrated noise = {float(np.std(pmq)):.4f}")

    print("\n=== 3. is the weight it picks the RIGHT weight? ===")
    # Equally informative independent views -> the optimum must be near half.
    pm, pk, ys = make_market(N, seed=4, model_noise=0.6, market_noise=0.6)
    alpha, _, _ = harness_alpha(pm, pk, ys)
    check("equally-noisy views -> alpha lands near 0.5", 0.30 < alpha < 0.70,
          f"alpha = {alpha:.4f}")

    # And fit_alpha must actually return the argmin of its own objective,
    # checked against an independently computed fine grid.
    cut = len(ys) // 2
    a, b = fit_platt(list(zip(pm[:cut], [float(v) for v in ys[:cut]])))
    pmc = [sig(a * p + b) for p in pm]
    got = fit_alpha(pmc[:cut], pk[:cut], ys[:cut])
    grid = [i / 1000 for i in range(1001)]
    best = min(grid, key=lambda g: ll([g * m + (1 - g) * k
                                       for m, k in zip(pmc[:cut], pk[:cut])], ys[:cut]))
    check("fit_alpha returns the argmin of its own objective", abs(got - best) <= 0.01,
          f"fit_alpha {got:.4f} vs independent fine grid {best:.4f}")

    print("\n=== 4. mechanics a silent refactor could break ===")
    # Platt must be monotone — it is a calibration, not a re-ranking. If it ever
    # inverted, AUC would be preserved and log-loss would look merely poor.
    a_, b_ = fit_platt([(0.1, 0.0), (0.3, 0.0), (0.5, 1.0), (0.9, 1.0)] * 200)
    xs = [0.05 * i for i in range(21)]
    ps = [sig(a_ * x + b_) for x in xs]
    check("Platt is monotone increasing", all(y2 >= y1 for y1, y2 in zip(ps, ps[1:])),
          f"a = {a_:.3f}")
    # It must be fitted on the FIRST half only; fitting on everything leaks the
    # evaluation half into the calibration.
    src = Path(__file__).resolve().parent.joinpath("residual_test_ou.py").read_text()
    check("Platt is fitted on the first half only", "raw[:cut]" in src)
    check("alpha is fitted on the first half only", "fit_alpha(pm[:cut]" in src)
    check("evaluation uses the held-out half", "slice(cut, None)" in src)

    # Shin: at zero overround it must agree with the proportional de-vig, and it
    # must take proportionally MORE margin off the longshot than off the favourite.
    fair = shin2(2.0, 2.0)
    check("Shin at zero overround = proportional", abs(fair - 0.5) < 1e-6, f"{fair:.6f}")
    # Fixture must be a REAL market: 1.40/3.60 sums to 0.9921, an arbitrage, and
    # Shin correctly returns the proportional answer when there is no vig to
    # remove. Using it as the fixture made this test fail against correct code.
    o_fav, o_dog = 1.40, 3.20
    prop_fav = (1 / o_fav) / (1 / o_fav + 1 / o_dog)
    shin_fav = shin2(o_fav, o_dog)
    check("Shin shifts probability toward the favourite vs proportional",
          shin_fav > prop_fav,
          f"proportional {prop_fav:.4f} -> Shin {shin_fav:.4f}")
    check("Shin output is a probability", 0.0 < shin_fav < 1.0)

    # AUC sanity: a perfect ranker is 1.0, a reversed one 0.0, a constant 0.5.
    ys_s = [0, 0, 1, 1]
    check("AUC perfect = 1.0", abs(auc([0.1, 0.2, 0.8, 0.9], ys_s) - 1.0) < 1e-9)
    check("AUC reversed = 0.0", abs(auc([0.9, 0.8, 0.2, 0.1], ys_s) - 0.0) < 1e-9)
    check("AUC constant = 0.5", abs(auc([0.5] * 4, ys_s) - 0.5) < 1e-9)

    print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
