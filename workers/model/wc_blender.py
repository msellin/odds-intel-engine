"""
OddsIntel — WC-A4 Bayesian Blender (own × market)

Combines the ELO+Poisson national-team predictor's 1X2 output with the
market-consensus 1X2 distribution scraped into `wc_market_consensus` (WC-A3).

Why blend?
  Our `national_team_v1` model is built from ~6.6k internationals and has
  systematic disagreements with the market on individual high-stakes
  fixtures (e.g. Brazil v Morocco opener: our model said Morocco 50% /
  Brazil 22%, the market had Brazil 55-69%). When five public sources all
  point one way and our model alone points the other, the prior says the
  market is closer to truth — but only when *enough* sources agree to
  outweigh single-source noise.

Math (mixture, not log-space):
    blended_p = (1 - λ) × own_p + λ × market_p     for each of {home, draw, away}

  Re-normalise after blending to absorb tiny floating-point drift —
  inputs sum to 1.0 ± ε so output should also.

  λ defaults to 0.6 (market-leaning), configurable via WC_BLEND_LAMBDA.
  `blend_with_confidence` scales λ by market-source count: with n=1 source
  the market is treated as ~10% reliable, n=3 → ~80%, n≥5 → 100% of λ.
  This is the actual Bayesian part — market confidence depends on how
  many independent feeds we have.

Failure mode handled:
  When `market is None` (A3 scraper hasn't run, fixture wasn't in the
  scraper's window, or the row genuinely doesn't exist yet), we return
  `own` unchanged with `blended=False`. Callers should propagate this so
  downstream writes can either skip or fall back to own-only.

Tested by `dev/active/wave2-a4-smoke.txt` description + manual repl.
"""
from __future__ import annotations

import os
from typing import Any


# ── λ from env, single read at import time ─────────────────────────────────
def _load_lambda() -> float:
    raw = os.getenv("WC_BLEND_LAMBDA", "0.6")
    try:
        lam = float(raw)
    except (TypeError, ValueError):
        return 0.6
    # clamp — anything outside [0,1] is a config mistake, not an intent
    return max(0.0, min(1.0, lam))


BLEND_LAMBDA: float = _load_lambda()

# Below this source count, scale λ down. Same constants used by FE later
# if it wants to mirror the confidence story.
_FULL_CONFIDENCE_N = 5   # n_sources ≥ this → λ at full strength
_MIN_CONFIDENCE_N = 1    # n_sources at this → λ at floor (~0.1× full λ)
_MIN_SCALE = 0.1         # never zero out the market completely once we have
                         # at least 1 source — that's the difference between
                         # "no signal" (use own) and "weak signal" (lean own).


def _normalise(p: dict[str, float]) -> dict[str, float]:
    """Re-normalise a {home, draw, away} triple to sum to 1.0."""
    s = float(p["home"]) + float(p["draw"]) + float(p["away"])
    if s <= 0:
        # Degenerate input — return a flat prior rather than NaN. Caller
        # should never hit this with a sane predictor, but defending here
        # is cheaper than a midnight pipeline failure.
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {
        "home": float(p["home"]) / s,
        "draw": float(p["draw"]) / s,
        "away": float(p["away"]) / s,
    }


def blend(
    own: dict[str, float],
    market: dict[str, float] | None,
    lam: float | None = None,
) -> dict[str, Any]:
    """
    Bayesian mixture blend of two 1X2 triples.

    Args:
        own:    {'home': p, 'draw': p, 'away': p}, summing to ~1.0.
        market: same shape, or None if no consensus row exists.
        lam:    blend weight on market. Defaults to BLEND_LAMBDA (env or 0.6).
                lam=0 → pure own model. lam=1 → pure market.

    Returns:
        {'home', 'draw', 'away', 'blended': bool, 'lambda_used': float}
        — sums to 1.0 ± 1e-9. `blended=False` when market is None;
        `lam_used=0` in that case so audits can tell unblended rows apart.
    """
    own_n = _normalise(own)
    if market is None:
        return {
            "home": own_n["home"],
            "draw": own_n["draw"],
            "away": own_n["away"],
            "blended": False,
            "lambda_used": 0.0,
        }

    lam_eff = BLEND_LAMBDA if lam is None else float(lam)
    lam_eff = max(0.0, min(1.0, lam_eff))

    mkt_n = _normalise(market)
    raw = {
        k: (1.0 - lam_eff) * own_n[k] + lam_eff * mkt_n[k]
        for k in ("home", "draw", "away")
    }
    out = _normalise(raw)
    out["blended"] = True
    out["lambda_used"] = lam_eff
    return out


def blend_with_confidence(
    own: dict[str, float],
    market: dict[str, float] | None,
    n_sources: int,
    base_lam: float | None = None,
) -> dict[str, Any]:
    """
    Same as blend(), but scales λ by market confidence (source count).

    The actual Bayesian flavour: λ_effective = base_lam × confidence(n_sources)
    where confidence linearly ramps from `_MIN_SCALE` at n=1 to 1.0 at
    n=_FULL_CONFIDENCE_N (default 5). Below 1 source we treat market as
    absent regardless of what the caller passed.

    Args:
        own:        {'home', 'draw', 'away'} triple.
        market:     same shape, or None.
        n_sources:  count of independent sources backing the market row
                    (read from wc_market_consensus.n_sources).
        base_lam:   base λ before scaling; defaults to BLEND_LAMBDA.

    Returns:
        dict with same shape as blend(), plus 'n_sources' and
        'lambda_used' reflects the SCALED λ (so writers can audit how
        confident the blender was at this fixture).
    """
    if market is None or n_sources < _MIN_CONFIDENCE_N:
        return blend(own, None)

    base = BLEND_LAMBDA if base_lam is None else float(base_lam)
    base = max(0.0, min(1.0, base))

    # Linear ramp from _MIN_SCALE @ n=1 to 1.0 @ n=_FULL_CONFIDENCE_N.
    if n_sources >= _FULL_CONFIDENCE_N:
        scale = 1.0
    else:
        span = _FULL_CONFIDENCE_N - _MIN_CONFIDENCE_N  # 4 by default
        scale = _MIN_SCALE + (1.0 - _MIN_SCALE) * (
            (n_sources - _MIN_CONFIDENCE_N) / span
        )
        scale = max(_MIN_SCALE, min(1.0, scale))

    lam_eff = base * scale
    out = blend(own, market, lam=lam_eff)
    out["n_sources"] = int(n_sources)
    return out


__all__ = ["BLEND_LAMBDA", "blend", "blend_with_confidence"]
