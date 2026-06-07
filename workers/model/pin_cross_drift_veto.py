"""PIN-CROSS-DRIFT veto — filter non-1X2 bets when the 1X2 line drifted
significantly pre-kickoff without our news pipeline explaining it.

Background
----------
Empirical study on 60 days of settled bets (scripts/pin_drift_veto_analysis.py)
showed that when Pinnacle's 1X2 implied probability drifts >=1-3% before
kickoff and our news_impact_score doesn't explain the move, OUR bets on the
SAME match in non-1X2 markets (BTTS, DC, O/U, AH) lose money systematically:

    Market          Threshold     60d N   60d PnL   Annualised save
    btts            >= 3%         28      -$89      ~$540
    double_chance   >= 1%         68      -$98      ~$600
    o/u             >= 3%         32      -$26      ~$160
    asian_handicap  >= 1%         112     -$119     ~$730
                                                    ~$2.0k/year total

The 1X2 itself is unaffected — that's where the drift feature lives, so the
direction-of-edge information cancels out the magnitude effect within 1X2.
The leak is cross-market: 1X2 drift is a *proxy* for "this match got news",
and our non-1X2 lines are stale.

Refresh-only
------------
The MFV column `pinnacle_line_move_*_at_t6h` is frozen at T-6h, so it's
NULL for matches placed by the 06:00 UTC morning pipeline (kickoffs 12-14h
later, no T-6h snapshot yet). Morning bets account for 14% of the leak.
Refresh-run bets (Phase 9, throughout the day) account for the other 86%.
Morning protection is a deferred follow-up; this module ships refresh-only.

Sample-size warnings
--------------------
- btts >= 3%: N=28 — sample is thin, but the -50% ROI is far from noise.
- o/u >= 3%: N=32, ROI -21% — same.
- DC/AH cohorts have N >= 66, more robust.
Re-evaluate thresholds quarterly as more bet data accumulates.
"""
from __future__ import annotations

from typing import Optional


# Per-market thresholds picked empirically from 60d settled-bets analysis.
# The veto fires when abs(max(pinnacle_line_move_home/draw/away_at_t6h)) >= threshold
# AND the news_impact_score does NOT explain the move.
#
# os_market values from the pipeline (daily_pipeline_v2.py candidate_specs):
#   O/U bets use "over_under_25", "over_under_15", "over_under_35" — NOT "o/u".
#   All three share the same threshold since the underlying 1X2 drift signal is
#   market-agnostic (it's a proxy for "match got unexplained news").
_PER_MARKET_THRESHOLDS: dict[str, float] = {
    "btts":            0.03,
    "double_chance":   0.01,
    "o/u":             0.03,   # kept for any legacy callers
    "over_under_25":   0.03,
    "over_under_15":   0.03,
    "over_under_35":   0.03,
    "asian_handicap":  0.01,
    # 1x2 — no veto (drift feature lives here; direction cancels magnitude leak)
    # draw_no_bet, combo — out of scope (insufficient sample)
}

# News-explained threshold: a non-trivial news_impact_score means our
# pipeline saw the same news Pinnacle did. Sparse today (~5% of matches),
# but the principle is correct — re-evaluate after RAG news ingestion lands.
_NEWS_EXPLAINED_ABS_THRESHOLD = 0.01


def check_pin_cross_drift_veto(
    market: str,
    pin_line_move_home_at_t6h: Optional[float],
    pin_line_move_draw_at_t6h: Optional[float],
    pin_line_move_away_at_t6h: Optional[float],
    news_impact_score: Optional[float],
) -> dict:
    """Return whether this bet should be vetoed by the cross-market drift filter.

    Returns:
        {
          "should_veto": bool,
          "reason":      str | None,
          "threshold":   float | None,
          "abs_drift":   float | None,
        }

    Fail-open semantics: if drift features are NULL (typical for morning bets
    placed >12h pre-KO), we DO NOT veto — there's no data to act on, and a
    false-veto costs us a real +EV bet. Better to under-filter than over-filter.
    """
    threshold = _PER_MARKET_THRESHOLDS.get(market)
    if threshold is None:
        # 1x2, draw_no_bet, combo — out of scope. No veto.
        return {"should_veto": False, "reason": None, "threshold": None, "abs_drift": None}

    # Compute the max-absolute 1X2 drift across the three selections.
    moves = [
        abs(v) for v in (pin_line_move_home_at_t6h, pin_line_move_draw_at_t6h, pin_line_move_away_at_t6h)
        if v is not None
    ]
    if not moves:
        # Drift feature unavailable (typically morning bets at T-12h+). Fail open.
        return {"should_veto": False, "reason": "no_drift_data", "threshold": threshold, "abs_drift": None}

    abs_drift = max(moves)

    if abs_drift < threshold:
        return {"should_veto": False, "reason": "below_threshold", "threshold": threshold, "abs_drift": abs_drift}

    # Drift exceeds threshold — check if news explained it.
    news_abs = abs(news_impact_score) if news_impact_score is not None else 0.0
    if news_abs >= _NEWS_EXPLAINED_ABS_THRESHOLD:
        return {"should_veto": False, "reason": "news_explained", "threshold": threshold, "abs_drift": abs_drift}

    # Drift exceeds threshold AND no news explains it → veto.
    return {
        "should_veto": True,
        "reason": "pin_cross_drift_unexplained",
        "threshold": threshold,
        "abs_drift": abs_drift,
    }


def get_thresholds() -> dict[str, float]:
    """Read-only access to per-market thresholds for diagnostics / tests."""
    return dict(_PER_MARKET_THRESHOLDS)
