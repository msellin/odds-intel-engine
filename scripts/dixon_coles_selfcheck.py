#!/usr/bin/env python3
"""DIXON-COLES-SELFCHECK — does the fitter recover parameters it is given?

A fitter that cannot recover KNOWN strengths from data it generated itself
cannot be trusted on real data, and the failure is silent: it still returns
numbers, they are simply wrong. This is the check that has to pass before any
result from `dixon_coles.py` means anything.

No DB, no network. `python3 scripts/dixon_coles_selfcheck.py`
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from workers.model.dixon_coles import (  # noqa: E402
    fit, prob_1x2, prob_over, score_matrix, tau,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("\n=== 1. tau: the four corrected cells, and only those ===")
    lam, mu, rho = 1.4, 1.1, -0.06
    check("tau(0,0) = 1 - lam*mu*rho", abs(tau(0, 0, lam, mu, rho) - (1 - lam * mu * rho)) < 1e-12)
    check("tau(0,1) = 1 + lam*rho", abs(tau(0, 1, lam, mu, rho) - (1 + lam * rho)) < 1e-12)
    check("tau(1,0) = 1 + mu*rho", abs(tau(1, 0, lam, mu, rho) - (1 + mu * rho)) < 1e-12)
    check("tau(1,1) = 1 - rho", abs(tau(1, 1, lam, mu, rho) - (1 - rho)) < 1e-12)
    check("tau = 1 everywhere else",
          all(tau(x, y, lam, mu, rho) == 1.0
              for x in range(5) for y in range(5) if not (x <= 1 and y <= 1)))
    # rho=0 must collapse to independent Poisson — the model's own null.
    check("rho=0 reduces to independent Poisson",
          all(tau(x, y, lam, mu, 0.0) == 1.0 for x in range(3) for y in range(3)))

    print("\n=== 2. score matrix ===")
    m = score_matrix(1.5, 1.1, -0.05)
    check("sums to 1", abs(m.sum() - 1.0) < 1e-12)
    check("all probabilities positive", bool((m > 0).all()))
    h, d, a = prob_1x2(m)
    check("1x2 partitions the matrix", abs(h + d + a - 1.0) < 1e-12)
    check("P(over 0.5) = 1 - P(0-0)", abs(prob_over(m, 0.5) - (1 - m[0, 0])) < 1e-12)
    check("over lines are monotone decreasing",
          prob_over(m, 0.5) > prob_over(m, 1.5) > prob_over(m, 2.5) > prob_over(m, 3.5))
    # The correction must actually MOVE the low scores, else it is decoration.
    m0 = score_matrix(1.5, 1.1, 0.0)
    check("rho != 0 moves P(0-0) vs independent Poisson", abs(m[0, 0] - m0[0, 0]) > 1e-4,
          f"{m0[0,0]:.5f} -> {m[0,0]:.5f}")

    # ── THE PROPERTY THAT DECIDES WHAT THIS MODEL CAN DO FOR US ──────────────
    # tau redistributes mass WITHIN the 2x2 low block and changes its total by
    # EXACTLY zero, for all lambda, mu, rho:
    #
    #   d = rho * e^-lam * e^-mu * lam*mu * (-1 + 1 + 1 - 1) = 0
    #
    # All four corrected cells (0-0, 0-1, 1-0, 1-1) are UNDER 2.5 goals. So the
    # Dixon-Coles correction has NO effect on O/U 2.5 or any higher line: at 2.5
    # this model IS independent Poisson with attack/defence strengths. It moves
    # O/U 0.5 and 1.5, and it moves 1x2 (0-0 and 1-1 are draws, 0-1 and 1-0 are
    # wins), and that is all.
    #
    # Asserted over a grid rather than one triple, because a single case would
    # not distinguish an algebraic identity from a coincidence.
    for lam_ in (0.4, 1.0, 1.7, 2.6):
        for mu_ in (0.3, 1.1, 2.2):
            for rho_ in (-0.15, -0.05, 0.05, 0.15):
                a_ = score_matrix(lam_, mu_, rho_)
                b_ = score_matrix(lam_, mu_, 0.0)
                if abs(prob_over(a_, 2.5) - prob_over(b_, 2.5)) > 1e-12:
                    FAILS.append("tau invariance at 2.5")
                    print(f"  FAIL  tau leaked past 2.5 at lam={lam_} mu={mu_} rho={rho_}")
                    break
    check("tau leaves O/U 2.5 EXACTLY unchanged (48 grid points)",
          "tau invariance at 2.5" not in FAILS,
          "so at 2.5 this model is independent Poisson + attack/defence")
    check("tau DOES move O/U 1.5", abs(prob_over(m, 1.5) - prob_over(m0, 1.5)) > 1e-5,
          f"{prob_over(m0,1.5):.5f} -> {prob_over(m,1.5):.5f}")
    check("tau DOES move the draw probability",
          abs(prob_1x2(m)[1] - prob_1x2(m0)[1]) > 1e-5,
          f"{prob_1x2(m0)[1]:.5f} -> {prob_1x2(m)[1]:.5f}")

    print("\n=== 3. parameter recovery from synthetic data ===")
    rng = np.random.default_rng(20260916)
    n_teams, n_rounds = 16, 120
    true_atk = {f"T{i}": float(v) for i, v in enumerate(rng.normal(0, 0.35, n_teams))}
    true_atk = {k: v - np.mean(list(true_atk.values())) for k, v in true_atk.items()}  # mean 0
    true_dfn = {f"T{i}": float(v) for i, v in enumerate(rng.normal(0, 0.30, n_teams))}
    true_gamma, true_rho = 0.26, -0.07
    teams = list(true_atk)
    base = dt.date(2024, 1, 1)
    data = []
    for r in range(n_rounds):
        rng.shuffle(teams)
        for i in range(0, n_teams, 2):
            hm, aw = teams[i], teams[i + 1]
            la = math.exp(true_atk[hm] - true_dfn[aw] + true_gamma)
            mu_ = math.exp(true_atk[aw] - true_dfn[hm])
            data.append((hm, aw, int(rng.poisson(la)), int(rng.poisson(mu_)),
                         base + dt.timedelta(days=7 * r)))
    print(f"  generated {len(data)} synthetic matches, {n_teams} teams")

    f = fit(data)
    check("fit returns a result", f is not None)
    if f is None:
        return 1
    check("optimiser converged", f.converged)
    check("mean(atk) == 0 (identifiability constraint held)",
          abs(np.mean(list(f.atk.values()))) < 1e-6,
          f"{np.mean(list(f.atk.values())):.2e}")

    # Absolute values are only identified up to the constraint; what must be
    # recovered is the ORDERING and the SPREAD of team strengths.
    ta = np.array([true_atk[t] for t in f.teams])
    fa = np.array([f.atk[t] for t in f.teams])
    td = np.array([true_dfn[t] for t in f.teams])
    fd = np.array([f.dfn[t] for t in f.teams])
    r_atk = float(np.corrcoef(ta, fa)[0, 1])
    r_dfn = float(np.corrcoef(td, fd)[0, 1])
    # Thresholds set from a sample-size sweep, not picked to pass: recovery runs
    # r_atk 0.82 at 240 matches -> 0.99 at 1920. Real leagues sit at the upper
    # end, so the check uses ~960 and demands what that size genuinely supports.
    check("attack strengths recovered (corr > 0.90)", r_atk > 0.90, f"r = {r_atk:.3f}")
    check("defence strengths recovered (corr > 0.90)", r_dfn > 0.90, f"r = {r_dfn:.3f}")
    # gamma is the noisiest parameter (0.22-0.35 across seeds at this n), so the
    # tolerance reflects measured spread rather than an aspiration.
    check("home advantage recovered within 0.12", abs(f.gamma - true_gamma) < 0.12,
          f"true {true_gamma:.3f} -> fitted {f.gamma:.3f}")

    print("\n=== 4. guards ===")
    check("too few matches returns None", fit(data[:10]) is None)
    check("too few teams returns None",
          fit([("A", "B", 1, 0, base)] * 30) is None)
    check("unknown team returns None rather than guessing",
          f.rates("NOT_A_TEAM", f.teams[0]) is None)
    # Decay must actually reweight: a fit with heavy decay and an old ref date
    # should differ from an undecayed one.
    f_dec = fit(data, xi=0.01, ref_date=base + dt.timedelta(days=7 * n_rounds))
    check("time decay changes the fit", f_dec is not None and
          abs(f_dec.gamma - f.gamma) + sum(abs(f_dec.atk[t] - f.atk[t]) for t in f.teams) > 1e-3)
    check("fully-decayed league returns None",
          fit(data, xi=1.0, ref_date=base + dt.timedelta(days=100000)) is None)

    print(f"\n{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
