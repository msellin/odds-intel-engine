-- BOTS-RETIRE-1X2 (2026-05-17): retire four bots starved by the May 17 model retrain.
--
-- Today's weekly Platt + shrinkage retrain learned shrinkage_alpha_t1_1x2 = 0.05
-- and shrinkage_alpha_t2_1x2 = 0.00 — meaning for T1-T2 1X2 the calibrated
-- probability is now essentially the market-implied prob, leaving ~zero edge.
-- These four bots are 1X2-only on T2-4 (or T1-only) and would clear ~zero bets
-- under the new calibration. Their pre-retrain ROIs (+47% to +83%) came from
-- 8-19 settled bets each — variance, not signal.
--
-- Retirement keeps historical bets in simulated_bets (for /performance and
-- /admin/bots history) and sets retired_at so the betting pipeline skips them
-- (daily_pipeline_v2.py:1526 reads `is_active AND retired_at IS NULL`).
--
-- Reasons per bot:
--   bot_lower_1x2       — T2-4 1X2, +83% ROI on 11 bets (variance)
--   bot_opt_home_lower  — T2-4 home longshots, +73% ROI on 15 bets (variance)
--   bot_draw_specialist — T2-4 draws only, kept the worst draw bucket from v1
--   bot_conservative    — T1-4 1X2 at ≥10% edge, never fired since launch
--
-- Re-enable if either: a future retrain restores shrinkage_alpha_t2_1x2 > 0.15,
-- OR the bot demonstrates ≥30 settled bets at ≥3% real ROI in shadow_bets.

UPDATE bots
SET is_active = false,
    retired_at = now()
WHERE name IN (
    'bot_lower_1x2',
    'bot_opt_home_lower',
    'bot_draw_specialist',
    'bot_conservative'
) AND retired_at IS NULL;
