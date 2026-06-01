-- RETIRE-BOT-AGGRESSIVE 2026-06-01
--
-- Third stale-flag fix in 24h (after bot_dc_specialist + bot_lower_1x2).
-- bot_aggressive's retired_reason has been populated since 2026-05-17 (PERF-
-- HONEST-HEADLINE migration 104) — "Replaced by bot_aggressive_v2. -5.7%
-- ROI / -€141 on 441 settled bets — single biggest drag on portfolio ROI."
--
-- The bot self-stopped firing on 2026-05-24 (last simulated_bet 2026-05-24
-- 19:38 UTC, last shadow_bet 2026-05-24 19:38 UTC). Likely cause: SLICE-LIVE-
-- VALIDATE on 2026-05-25 tightened bot_aggressive's odds range (1.25,5.00) →
-- (1.25,2.50) and excluded Draw selection, which combined with the active
-- cohort gating now produces 0 eligible matches per day.
--
-- The is_active flag was never flipped. Retiring it now:
--   • removes 705 stale settled bets from /performance "active" headline math
--   • removes 1X2 (-2.20% ROI) and O/U (-1.46% ROI) drag from the active cohort
--   • preserves training data — shadow_bets pathway is unaffected by retirement
--     (see daily_pipeline_v2.py SHADOW-RETIRED-OK 2026-05-20)
--
-- Coverage impact: bot_aggressive's 1X2 + O/U firings are backfilled by
-- bot_v10_all (calibrated, +1.87% O/U ROI), bot_aggressive_v2 (active, but
-- weak O/U), bot_high_alignment (1X2, marginal). O/U coverage thins slightly.
-- Specialist coverage gap (bot_ou_specialist not firing into simulated_bets)
-- is filed separately for diagnosis.

UPDATE bots
SET
    is_active     = false,
    retired_at    = NOW()
WHERE name = 'bot_aggressive'
  AND is_active = true;
