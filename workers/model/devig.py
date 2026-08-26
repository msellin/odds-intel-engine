"""LINESHOP-SHIN-DEVIG-2026-08-26 — removing bookmaker margin from quoted odds.

Every line-shopping bot compares a soft book's price against Pinnacle's implied
probability. Pinnacle's quote includes its margin, so the margin has to come out
before the comparison means anything. *How* it comes out matters more than it
looks.

Proportional (multiplicative) de-vig divides every implied probability by the
overround:

    p_i = (1 / o_i) / sum_j (1 / o_j)

This assumes the bookmaker spreads its margin evenly in proportional terms. For
a 2-way market (over/under, BTTS) that is close enough to true — the two sides
sit near 50/50 and the favourite-longshot distortion has nowhere to hide.

For a 3-way market it is wrong in a *known direction*. Bookmakers load margin
onto longshots, so a proportional removal takes too little margin off the
longshot and too much off the favourite. The de-vigged longshot probability
comes out too high, which manufactures apparent edge on exactly the selections
that lose — draws and away dogs.

Shin's method models the margin as arising from a proportion `z` of insider
money and solves for the probabilities a bookmaker would need to hold to break
even against it. It removes proportionally more margin from longshots, which is
what the empirical data shows bookmakers actually do.

    p_i = [sqrt(z^2 + 4(1 - z) * pi_i^2 / PI) - z] / (2 * (1 - z))

where pi_i = 1/o_i, PI = sum(pi), and z is chosen so sum(p_i) = 1.

Reference: Shin, H.S. (1993), "Measuring the Incidence of Insider Trading in a
Market for State-Contingent Claims".
"""
from __future__ import annotations

# Bisection bounds and tolerance for solving z. z is a proportion, so it is
# bounded below by 0 (no insiders => proportional de-vig) and above by 1.
_Z_TOL = 1e-10
_Z_MAX_ITER = 100


def proportional_devig(odds: list[float]) -> list[float] | None:
    """Divide out the overround proportionally. Returns None on bad input."""
    if not odds or any(o is None or o <= 1.0 for o in odds):
        return None
    implied = [1.0 / o for o in odds]
    total = sum(implied)
    if total <= 0:
        return None
    return [p / total for p in implied]


def _shin_sum(z: float, implied: list[float], total: float) -> float:
    """sum(p_i) under Shin at a given z. Decreasing in z, which is what lets
    bisection work."""
    if z >= 1.0:
        return 0.0
    acc = 0.0
    for pi in implied:
        acc += (((z * z + 4.0 * (1.0 - z) * pi * pi / total) ** 0.5) - z)
    return acc / (2.0 * (1.0 - z))


def shin_devig(odds: list[float]) -> list[float] | None:
    """Shin de-vig. Falls back to proportional when the market has no margin
    to remove (overround <= 1) or the solve degenerates."""
    if not odds or any(o is None or o <= 1.0 for o in odds):
        return None
    implied = [1.0 / o for o in odds]
    total = sum(implied)
    if total <= 1.0:
        # No overround (or a crossed market) — nothing for Shin to attribute to
        # insiders, and the solve has no root in [0, 1).
        return proportional_devig(odds)

    # sum(p) is 1 at the true z and decreasing in z, so bisect on
    # f(z) = sum(p_i at z) - 1.
    lo, hi = 0.0, 1.0 - 1e-9
    if _shin_sum(lo, implied, total) - 1.0 <= 0:
        # Already at or below 1 with no insider share — proportional is the
        # degenerate-correct answer.
        return proportional_devig(odds)

    for _ in range(_Z_MAX_ITER):
        mid = (lo + hi) / 2.0
        if _shin_sum(mid, implied, total) - 1.0 > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < _Z_TOL:
            break

    z = (lo + hi) / 2.0
    probs = []
    for pi in implied:
        probs.append(
            (((z * z + 4.0 * (1.0 - z) * pi * pi / total) ** 0.5) - z) / (2.0 * (1.0 - z))
        )
    s = sum(probs)
    if s <= 0:
        return proportional_devig(odds)
    # Renormalise away the last few ulps of bisection error.
    return [p / s for p in probs]


def devig(odds: list[float]) -> list[float] | None:
    """De-vig a complete market with Shin's method.

    `odds` must be the FULL set of mutually exclusive outcomes (home/draw/away,
    or over/under) in a fixed order; the returned probabilities match that order
    and sum to 1.
    """
    if not odds:
        return None
    return shin_devig(odds)


def devig_one(odds: list[float], index: int) -> float | None:
    """De-vigged probability of a single outcome within its market."""
    probs = devig(odds)
    if probs is None or not (0 <= index < len(probs)):
        return None
    return probs[index]
