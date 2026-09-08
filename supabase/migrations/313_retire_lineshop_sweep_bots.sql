-- SHADOW-BOT-CONSOLIDATION-2026-09-08 (Step 1, owner-approved) — retire the
-- active LINE-SHOP sweep/pin experiments. BOT-2D-AUDIT (held-out OOS, per market)
-- shows all three LOSE out-of-sample:
--   bot_pin_1x2_home_v1   1x2  train +14% -> TEST -32%  (was "one to watch" on
--                              in-sample CLV; the OOS audit overturns that)
--   bot_sweep_ou25_v1     o/u  -> TEST -17%
--   bot_sweep_ou35_v1     o/u  +24% in-sample -> TEST -1% (matrix-mined)
-- Root cause (daily_pipeline_v2 comment ~L4797): line-shop takes the MAX across
-- soft books, which selects the most-mispriced/worst-calibrated book — a
-- selection artifact, not a real edge (57/58 were -EV at Coolbet). Model-edge
-- (bot_v10_all) is +28%/+34% OOS and is the path forward. Shadow-only, so no
-- real-money or /performance impact. Reversible: retired_at=NULL, is_active=true.
UPDATE bots
   SET is_active = FALSE,
       retired_at = COALESCE(retired_at, NOW()),
       retired_reason = 'SHADOW-BOT-CONSOLIDATION 2026-09-08: line-shop signal loses out-of-sample (BOT-2D-AUDIT held-out); model-edge is the path (owner-approved)',
       maturity_label = 'retired',
       updated_at = NOW()
 WHERE name IN ('bot_pin_1x2_home_v1', 'bot_sweep_ou25_v1', 'bot_sweep_ou35_v1')
   AND retired_at IS NULL;
