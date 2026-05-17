"""
COMBO-RESEARCH-PHASE-B (2026-05-17) — joint probabilities for SGM legs.

The same Dixon-Coles-corrected P(h, a) matrix that `_poisson_probs` in
daily_pipeline_v2.py builds also gives us every joint probability we need
for Same-Game Multi (SGM) pricing. Books often price SGM legs as the
*product of marginals* (ignoring correlation), which lets us spot mispricings
on legs that are genuinely correlated.

Example mispricing the model can catch:
    Marginal P(BTTS Yes)         = 0.55
    Marginal P(Over 2.5)         = 0.50
    Product (book's SGM model)   = 0.275  → fair odds 3.64
    True joint P(BTTS & Over 2.5)= 0.48   → fair odds 2.08

That's a ~76% miss in the book's favour — if Coolbet prices a 3.64 SGM here,
the actual fair value is 2.08 and we're getting massive edge.

This module exposes:
  build_joint_matrix(exp_h, exp_a, rho, league_draw_pct) → 2D numpy array
  prob_event(matrix, event)                              → marginal P(A)
  prob_joint(matrix, event_a, event_b)                   → joint P(A & B)
  enumerate_correlated_sgm_legs()                        → useful pairs to test

Event grammar (strings):
  "home", "draw", "away"                — 1X2 outcomes
  "over_1.5", "over_2.5", "over_3.5"    — total goals lines
  "under_1.5", "under_2.5", "under_3.5"
  "btts_yes", "btts_no"
  "home_scores", "away_scores"          — single-team-to-score
"""

from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.stats import poisson


# Max goals each team in the grid. 10 covers >99.99% probability mass even
# for very high-scoring matches (e.g., exp goals = 4.0 per side); the tail
# beyond that is irrelevant to any SGM we'd price.
_MAX_GOALS = 10
_GRID = np.arange(_MAX_GOALS + 1)


def _dc_tau(h: int, a: int, exp_h: float, exp_a: float, rho: float) -> float:
    """Dixon-Coles correction factor τ for the four low-scoring outcomes.
    Mirrors workers/jobs/daily_pipeline_v2.py:_dc_tau exactly."""
    if h == 0 and a == 0:
        return 1.0 - exp_h * exp_a * rho
    if h == 1 and a == 0:
        return 1.0 + exp_a * rho
    if h == 0 and a == 1:
        return 1.0 + exp_h * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def build_joint_matrix(
    exp_h: float,
    exp_a: float,
    rho: float = -0.13,
    league_draw_pct: float | None = None,
) -> np.ndarray:
    """Build the DC-corrected joint scoreline distribution P(h, a).

    Returns an 11×11 numpy array M where M[h, a] = P(home scores h, away scores a).
    Sums to 1.0 (after renormalisation post-DC and post-draw-inflate).

    Mirrors daily_pipeline_v2._poisson_probs's internals but returns the matrix
    instead of just the marginals. Keep in sync with that function — any change
    to DC application or draw inflation here should also happen there (or
    vice versa) so the model behaviour stays consistent across single-bet and
    SGM pricing.
    """
    # Independent Poisson outer product over the grid
    p_h = poisson.pmf(_GRID, exp_h)
    p_a = poisson.pmf(_GRID, exp_a)
    matrix = np.outer(p_h, p_a)

    # Dixon-Coles τ patch on the four low-scoring cells
    matrix[0, 0] *= 1.0 - exp_h * exp_a * rho
    matrix[1, 0] *= 1.0 + exp_a * rho
    matrix[0, 1] *= 1.0 + exp_h * rho
    matrix[1, 1] *= 1.0 - rho

    # Renormalise so the grid sums to 1.0 (DC adjustment + finite grid truncation)
    total = matrix.sum()
    if total > 0:
        matrix = matrix / total

    # Optional: apply the same DRAW-INFLATE adjustment as _poisson_probs so
    # marginal P(draw) here matches what the single-bet model uses. We need the
    # draws spread across the diagonal proportionally, not just patched at (1,1).
    if league_draw_pct is not None:
        raw_inflate = 1.0 + max(0.0, (league_draw_pct - 0.268) / 0.268 * 0.08)
        draw_inflate = max(1.03, min(1.15, raw_inflate))
    else:
        draw_inflate = 1.08  # validated global default

    if draw_inflate > 1.0:
        diag_mask = np.eye(_MAX_GOALS + 1, dtype=bool)
        diag_mass_before = matrix[diag_mask].sum()
        diag_mass_after = diag_mass_before * draw_inflate
        if diag_mass_after >= 1.0:
            # Shouldn't happen with the clamp, but guard against pathological inputs
            return matrix
        scale_off_diag = (1.0 - diag_mass_after) / (1.0 - diag_mass_before)
        # Scale off-diagonal cells down, inflate diagonal cells
        matrix = matrix * scale_off_diag
        matrix[diag_mask] = matrix[diag_mask] / scale_off_diag * draw_inflate

    return matrix


# ── Event masks ──────────────────────────────────────────────────────────────

def _event_mask(event: str) -> np.ndarray:
    """Return a boolean mask over the 11×11 grid for which (h, a) cells count
    as the event. Raises ValueError on unknown events so typos surface fast."""
    event = event.strip().lower()
    h, a = np.meshgrid(_GRID, _GRID, indexing="ij")

    if event == "home":
        return h > a
    if event == "draw":
        return h == a
    if event == "away":
        return a > h
    if event == "btts_yes":
        return (h >= 1) & (a >= 1)
    if event == "btts_no":
        return (h == 0) | (a == 0)
    if event == "home_scores":
        return h >= 1
    if event == "home_not_scores":
        return h == 0
    if event == "away_scores":
        return a >= 1
    if event == "away_not_scores":
        return a == 0
    if event.startswith("over_"):
        line = float(event.split("_", 1)[1])
        return (h + a) > line
    if event.startswith("under_"):
        line = float(event.split("_", 1)[1])
        return (h + a) < line + 1  # under 2.5 = total ≤ 2 = total < 3
    raise ValueError(f"Unknown event: {event!r}")


def prob_event(matrix: np.ndarray, event: str) -> float:
    """Marginal probability of a single event."""
    return float(matrix[_event_mask(event)].sum())


def prob_joint(matrix: np.ndarray, event_a: str, event_b: str) -> float:
    """Joint P(A and B). Order-independent."""
    return float(matrix[_event_mask(event_a) & _event_mask(event_b)].sum())


def prob_conditional(matrix: np.ndarray, event_a: str, given: str) -> float:
    """P(A | B). Useful for sanity-checking correlation strength."""
    p_b = prob_event(matrix, given)
    if p_b <= 0:
        return 0.0
    return prob_joint(matrix, event_a, given) / p_b


def correlation_ratio(matrix: np.ndarray, event_a: str, event_b: str) -> float:
    """How much does joint P(A & B) differ from the product of marginals?

    Ratio > 1 → positively correlated (book that prices as product underprices)
    Ratio = 1 → independent
    Ratio < 1 → negatively correlated

    This is the *exact* multiplier we'd apply to the book's product-of-legs SGM
    odds to get the fair odds: fair_odds = book_odds / ratio.
    """
    p_a = prob_event(matrix, event_a)
    p_b = prob_event(matrix, event_b)
    if p_a <= 0 or p_b <= 0:
        return 1.0
    joint = prob_joint(matrix, event_a, event_b)
    return joint / (p_a * p_b)


# ── Useful pairs to test against Coolbet's SGM offering ──────────────────────

# Pairs known from football data to have meaningful correlation. Ordered by
# typical strength of correlation (descending). Phase A audit should test
# the top of this list first.
HIGH_CORRELATION_PAIRS: list[tuple[str, str, str]] = [
    ("home", "home_scores",      "Heavy home favourites usually score (often multiple)"),
    ("away", "away_scores",      "Heavy away favourites usually score"),
    ("btts_yes", "over_2.5",     "BTTS overlaps strongly with O2.5 — both need ≥1 each plus enough total"),
    ("home", "over_2.5",         "Home wins tend to be high-scoring (favourites press)"),
    ("home", "under_2.5",        "Negative — home wins of 1-0 / 2-0 keep totals low (varies by league)"),
    ("draw", "under_2.5",        "Draws skew low-scoring (0-0, 1-1, 2-2)"),
    ("draw", "btts_no",          "Negative — 0-0 is a BTTS-no draw, 1-1+ is a BTTS-yes draw"),
    ("over_2.5", "btts_yes",     "Same as #3, listed both ways for completeness"),
]


def sample_sgm_edges(exp_h: float, exp_a: float, **kwargs) -> dict[str, dict]:
    """Convenience: compute marginal / joint / correlation_ratio for every
    pair in HIGH_CORRELATION_PAIRS. Useful when eyeballing a single match
    or for the Phase C SGM bot to pick which pairs to score.
    """
    matrix = build_joint_matrix(exp_h, exp_a, **kwargs)
    out = {}
    for a, b, desc in HIGH_CORRELATION_PAIRS:
        p_a = prob_event(matrix, a)
        p_b = prob_event(matrix, b)
        joint = prob_joint(matrix, a, b)
        product = p_a * p_b
        ratio = joint / product if product > 0 else 1.0
        out[f"{a}+{b}"] = {
            "marginal_a": round(p_a, 4),
            "marginal_b": round(p_b, 4),
            "joint": round(joint, 4),
            "product": round(product, 4),
            "correlation_ratio": round(ratio, 4),
            "edge_if_book_uses_product": round((ratio - 1) * 100, 2),  # percentage points
            "description": desc,
        }
    return out
