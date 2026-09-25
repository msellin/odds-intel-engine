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


# ── ALTERNATIVE METHODS ([[#106]], 2026-09-24) ─────────────────────────────────
# Added for the per-market bake-off (docs/DEVIG_BAKEOFF_2026_09_24.md). They do NOT
# change what `devig()` returns — that stays Shin until the bake-off decides
# otherwise and the owner switches a rule version. Each returns probabilities in
# input order summing to 1, or None when the method is undefined for the market
# (additive can push a longshot below zero at high margins).

def _valid(odds) -> list[float] | None:
    if not odds or any(o is None or o <= 1.0 for o in odds):
        return None
    return [1.0 / o for o in odds]


def _solve(f, lo: float, hi: float) -> float:
    """Bisection for a root of a monotone f on [lo, hi] (f(lo), f(hi) opposite signs)."""
    flo = f(lo)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2.0


def additive_devig(odds: list[float]) -> list[float] | None:
    """Subtract an equal share of the margin from every outcome: p_i = pi_i − (PI−1)/n."""
    implied = _valid(odds)
    if implied is None:
        return None
    cut = (sum(implied) - 1.0) / len(implied)
    probs = [p - cut for p in implied]
    return probs if all(p > 0 for p in probs) else None


def power_devig(odds: list[float]) -> list[float] | None:
    """p_i = pi_i ** k with k chosen so the probabilities sum to 1. With a margin
    k > 1, which shrinks small (longshot) probabilities proportionally more."""
    implied = _valid(odds)
    if implied is None:
        return None
    if abs(sum(implied) - 1.0) < 1e-12:
        return implied
    k = _solve(lambda k: sum(p ** k for p in implied) - 1.0, 0.2, 20.0)
    probs = [p ** k for p in implied]
    s = sum(probs)
    return [p / s for p in probs]


def odds_ratio_devig(odds: list[float]) -> list[float] | None:
    """Cheung's odds-ratio method: the fair odds-ratio p/(1−p) is the implied
    odds-ratio divided by a constant c, i.e. p_i = pi_i / (c + pi_i − c·pi_i)."""
    implied = _valid(odds)
    if implied is None:
        return None
    if abs(sum(implied) - 1.0) < 1e-12:
        return implied
    f = lambda c: sum(p / (c + p - c * p) for p in implied) - 1.0  # noqa: E731
    c = _solve(f, 1e-6, 1e6)
    probs = [p / (c + p - c * p) for p in implied]
    s = sum(probs)
    return [p / s for p in probs]


# WPO ("margin weights proportional to the odds", Buchdahl) was in the pre-registered
# list and is deliberately ABSENT: fair odds_i = n·o_i / (n − M·o_i) gives
# p_i = 1/o_i − M/n, which is exactly `additive_devig`. Verified numerically
# 2026-09-24 on 3-way and 2-way markets; keeping both would double-count one method.


METHODS = {
    "shin": shin_devig,
    "proportional": proportional_devig,
    "additive": additive_devig,
    "power": power_devig,
    "odds_ratio": odds_ratio_devig,
}


def devig_by(method: str, odds: list[float]) -> list[float] | None:
    """De-vig with a named method from METHODS (the bake-off's entry point)."""
    return METHODS[method](odds)


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


# ── ONE fair-price rule per market shape (#162 W3.1, 2026-09-25) ────────────────
# The #162 audit (dev/active/bot-refactor-audit/A-producers.md §2) found fair probability computed FOUR
# ways across the producers, so CLV judged a pick with a different fair price from the one that chose it.
# This table is the written rule; `fair_prob` is the one entry point. It CHANGES NOTHING by itself —
# callers move to it one at a time, and a live bot's move is a twin + owner OK (#162 W3.3).
#   * 3-way (1x2) → Shin: ANALYSIS_GOTCHAS #78 — "use Shin for anything published"; proportional loses to
#     Shin on log-loss in every market (1x2 n = 26-35k) and manufactures edge on longshots.
#   * 2-way (O/U, BTTS, …) → power: what the live O/U paths already use (combined_ou, ou_sharp_outlier);
#     #78 lists power among the methods "not measurably worse than Shin". Never proportional.
# Known proportional users (to move, each via W3.3): corners_paper_bot / team_total_paper_bot
# `_devig_two_way`, the pinnacle_implied_* signal writer (supabase_client), market_consensus_1x2 (#154).
# smoke FAIR-PRICE-ONE-RULE pins every copy to its reference method so none can drift silently.
FAIR_METHOD_BY_SHAPE = {3: "shin", 2: "power"}


def fair_method(n_outcomes: int) -> str:
    """The method for a complete market of `n_outcomes` mutually exclusive outcomes."""
    return FAIR_METHOD_BY_SHAPE.get(n_outcomes, "shin")


def fair_prob(odds: list[float]) -> list[float] | None:
    """THE fair probabilities of a complete market (fixed order in, same order out, sums to 1),
    by FAIR_METHOD_BY_SHAPE. None when the method is undefined for these prices."""
    if _valid(odds) is None:          # every price present and > 1.0
        return None
    return devig_by(fair_method(len(odds)), list(odds))
