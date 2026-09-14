-- ALPHA-IS-AN-UNREAD-INSTRUMENT (2026-09-14)
--
-- `scripts/fit_blend_weights.py::optimize_shrinkage_alpha` finds the alpha that
-- minimises log-loss of `alpha * model_prob + (1 - alpha) * market_implied`.
-- That makes alpha a direct, continuously-refitted, large-sample answer to the
-- only question that matters for a model-anchored bet: HOW MUCH DOES OUR MODEL
-- ADD OVER THE MARKET?
--
-- It has been answering, and nobody was reading:
--
--   shrinkage_alpha_t1_1x2       0.0154 (May) -> 0.0085 (Sep)   n=59,799
--   shrinkage_alpha_t2_1x2       0.0000                          n= 9,027
--   shrinkage_alpha_t4_1x2       0.0000                          n= 4,042
--   shrinkage_alpha_t1_goalline  0.9262 (May) -> 0.2278 (Sep)   n=92,221
--
-- Two 1x2 tiers are EXACTLY ZERO: the fitted optimum is to ignore our model
-- entirely. The goal-line weight has slid monotonically across 93 refits.
-- Meanwhile `edge = cal_prob - 1/odds` is sold as "model edge" — but at
-- alpha=0.0085 the 1x2 cal_prob is 99.15 pct a transform of the de-vigged Pinnacle
-- line, so the "disagreement" we bet on is mostly the Platt sigmoid's own
-- distortion, not a model opinion. That is the same conclusion the independent
-- AUC work reached (1x2 disagreement AUC 0.344, i.e. anti-predictive) arrived at
-- from the opposite direction.
--
-- The fitter ALREADY computes the clean comparison every run:
--     ll_model  = loss(alpha=1.0)   -- our model alone
--     ll_market = loss(alpha=0.0)   -- the market alone
-- and then prints them to stdout and discards them. `ece_before` holds the
-- previous alpha and `ece_after` the blended optimum, so neither baseline
-- survives the run. Months of the cleanest skill measurement in the system
-- scrolled past in a console.
--
-- These two columns make it a tracked time series. Nullable, no backfill: the
-- history is genuinely unrecoverable and inventing it would be worse than
-- leaving the gap visible.

ALTER TABLE model_calibration
  ADD COLUMN IF NOT EXISTS ll_model  DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS ll_market DOUBLE PRECISION;

COMMENT ON COLUMN model_calibration.ll_model IS
  'Log-loss of the model alone (alpha=1) on the fit sample. Only set on '
  'shrinkage_alpha_* rows. Compare with ll_market: ll_model > ll_market means '
  'our model is WORSE than the market on this tier/family.';

COMMENT ON COLUMN model_calibration.ll_market IS
  'Log-loss of the market-implied probability alone (alpha=0) on the fit '
  'sample. The baseline a model-anchored bet has to beat to be worth anything.';
