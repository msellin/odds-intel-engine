-- RETIRE-LOWER-1X2 2026-06-01
--
-- bot_lower_1x2's existing retired_reason was already populated when the bot
-- was diagnosed as starved by the May 17 retrain ("shrinkage_alpha_t2_1x2=0.00
-- means the model has no real edge over Pinnacle for T1-T2 1X2. Re-enable if
-- alpha recovers > 0.15 or ≥30 bets at ≥3% ROI in shadow_bets"). The is_active
-- flag was never flipped, so the bot kept firing — 44 settled bets since
-- 2026-05-24 at -7.58% ROI with avg CLV +6.16%, last fire 2026-05-31.
--
-- The retired_reason is still accurate, so we keep it. We only flip is_active
-- and stamp retired_at so the pipeline + performance page stop counting it as
-- live. The text below preserves the original diagnosis and adds today's data.

UPDATE bots
SET
    is_active     = false,
    retired_at    = NOW(),
    retired_reason = 'RETIRE-LOWER-1X2 2026-06-01: stale-flag fix. T2-4 1X2-only. Original 2026-05-17 retirement: shrinkage_alpha_t2_1x2 = 0.00 means the model has no real edge over Pinnacle for T1-T2 1X2; live +83% on 11 bets was variance. Flag was never flipped — bot kept firing 44 bets at -7.58% ROI (+6.16% CLV) since 2026-05-24. Re-enable if alpha recovers > 0.15 OR ≥30 bets at ≥3% ROI in shadow_bets.'
WHERE name = 'bot_lower_1x2'
  AND is_active = true;
