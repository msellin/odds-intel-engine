-- 303_pseudo_clv_sign.sql
-- META-MFV-TARGET-INVERTED-2026-09-06
--
-- `matches.pseudo_clv_{home,draw,away}` was computed as
--
--     (1/opening) / (1/closing) - 1   ==   closing/opening - 1
--
-- which is POSITIVE when the price DRIFTS OUT. Real closing-line value is
-- positive when you BEAT the close, i.e. when the price SHORTENS after you bet.
-- The stored value was therefore the exact reciprocal of the intended one, and
-- the default meta-model label `P(pseudo_clv_home > 0)` was the precise
-- opposite of the quantity the model exists to predict.
--
-- Measured against real `clv_pinnacle_devig` on matched settled bets:
--
--                        since 2026-05-06   since 2026-08-01
--   stored value              r=-0.029           r=-0.559
--   binary label              r=-0.275           r=-0.365
--   sign-corrected            r=+0.235           r=+0.567
--   corrected label           r=+0.282           r=+0.380
--
-- settlement.py is fixed, and it recomputes by date unconditionally, so rows in
-- its rolling window self-heal. This migration corrects the HISTORY it will
-- never revisit.
--
-- The transform is exact: new = 1/(1 + old) - 1. The original ±0.5 plausibility
-- guard was applied in the OLD space, which is asymmetric once inverted
-- (|old| <= 0.5 maps to [-0.333, +1.0]), so the guard is re-applied in the new
-- space and anything outside becomes NULL rather than a value we would not have
-- stored had it been computed correctly the first time.
--
-- Nothing is currently gated on this: META_B_ML3_ENABLED=false on the VPS since
-- 2026-09-05. Every meta bundle trained before this date was trained on the
-- inverted label and should be considered void rather than merely stale.

UPDATE matches
   SET pseudo_clv_home = CASE
           WHEN pseudo_clv_home IS NULL OR pseudo_clv_home <= -1 THEN NULL
           WHEN ABS(1.0 / (1.0 + pseudo_clv_home) - 1.0) > 0.5    THEN NULL
           ELSE ROUND((1.0 / (1.0 + pseudo_clv_home) - 1.0)::numeric, 5)
       END,
       pseudo_clv_draw = CASE
           WHEN pseudo_clv_draw IS NULL OR pseudo_clv_draw <= -1 THEN NULL
           WHEN ABS(1.0 / (1.0 + pseudo_clv_draw) - 1.0) > 0.5   THEN NULL
           ELSE ROUND((1.0 / (1.0 + pseudo_clv_draw) - 1.0)::numeric, 5)
       END,
       pseudo_clv_away = CASE
           WHEN pseudo_clv_away IS NULL OR pseudo_clv_away <= -1 THEN NULL
           WHEN ABS(1.0 / (1.0 + pseudo_clv_away) - 1.0) > 0.5   THEN NULL
           ELSE ROUND((1.0 / (1.0 + pseudo_clv_away) - 1.0)::numeric, 5)
       END
 WHERE pseudo_clv_home IS NOT NULL
    OR pseudo_clv_draw IS NOT NULL
    OR pseudo_clv_away IS NOT NULL;
