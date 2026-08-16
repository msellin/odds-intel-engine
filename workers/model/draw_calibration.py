"""
DRAW-CALIBRATION-2026-08-16 — shared post-hoc draw-probability shrink.

Extracted from an inline block in xgboost_ensemble.py so BOTH
inference paths use it:
  - workers/model/xgboost_ensemble.py  → live inference for bots/pipeline
  - scripts/weekly_eval_and_compare.py → rigorous_eval + weekly_eval

Without the shared helper, rigorous_eval bypasses the shrink and reports
uncalibrated numbers while the bots run on calibrated ones — the eval
would show "no change" and we'd never know if the calibration was
actually helping.

Motivation: 4 consecutive weekly retrains (v20260712 → v20260802 →
v20260809 → v20260816) show a persistent +3.2% to +5.7% log-loss
regression on 1x2_draw. Aug 3 SUMMARY_JSON showed the ensemble
predicts ~37% average draw rate vs ~20% observed — a 17pp
over-prediction that doesn't respond to more training data. Full plan
in dev/active/draw-regression-plan.md.

Semantics:
  DRAW_CAL_FACTOR = multiplicative shrink applied to draw_prob.
                    Home + away then renormalized proportionally so
                    the three probabilities sum to 1.
                    Default 1.0 = no change (safe rollout).
                    0.60 → 37% avg → ~22% avg, matching observed.
"""
from __future__ import annotations

import os


def apply_draw_calibration(home_prob: float, draw_prob: float, away_prob: float,
                             ) -> tuple[float, float, float]:
    """Apply DRAW_CAL_FACTOR shrink to draw_prob and renormalize.

    Returns the calibrated (home, draw, away) probability triple. If the
    env var is unset, invalid, or 1.0, returns inputs unchanged.

    Safety clamps:
      - Only applies if 0.0 < factor <= 1.5 (block obvious typos like -1
        or 10 from silently breaking inference)
      - If home + away sum to 0 (extreme edge case), returns unchanged
      - If shrunk draw is >= 1.0 (only if factor > 1), skip renormalize
    """
    try:
        factor = float(os.getenv("DRAW_CAL_FACTOR", "1.0"))
    except (TypeError, ValueError):
        return home_prob, draw_prob, away_prob
    if factor == 1.0 or not (0.0 < factor <= 1.5):
        return home_prob, draw_prob, away_prob

    new_draw = float(draw_prob) * factor
    others_total = float(home_prob) + float(away_prob)
    remaining = 1.0 - new_draw
    if others_total <= 0 or remaining <= 0:
        return home_prob, draw_prob, away_prob
    scale = remaining / others_total
    return (float(home_prob) * scale, new_draw, float(away_prob) * scale)
