-- PERF-HONEST-HEADLINE (2026-05-17): make /performance show an honest picture.
--
-- Three changes in one migration:
--   1. bots.retired_reason — short prose for why a bot was retired (shown on
--      the public /performance "Retired Strategies" section).
--   2. Backfill retired_reason for the 5 already-retired bots
--      (4 from BOTS-RETIRE-1X2 + bot_aggressive retired here).
--   3. dashboard_cache gets active-only headline columns + retired_bot_breakdown
--      JSONB so the public page can render two headline rows (all-time vs
--      active strategies) without re-querying simulated_bets on every render.
--
-- Why retire bot_aggressive: -5.7% ROI / -€141 on 441 settled bets, was the
-- single biggest drag on portfolio headline ROI. Loss breakdown:
--   draws            -€154 / 61 bets
--   home odds 3.30+  -€95  / 110 bets
--   OU under 2.5     -€46  / 88 bets
-- bot_aggressive_v2 (shipped earlier today, AGGRESSIVE-V2) keeps 129/441
-- of the same bets with: no draws, no under 2.5, odds 1.50-3.30, edge ≥5%.
-- Replay of v1's bets through v2's filters = +11.6% ROI / +€90 (€231 swing).
--
-- Re-enable trigger for bot_aggressive: never. bot_aggressive_v2 is the path
-- forward; if v2 disappoints, the next iteration is v3, not v1.

-- 1. Column ----------------------------------------------------------------
ALTER TABLE bots ADD COLUMN IF NOT EXISTS retired_reason TEXT;

-- 2. Backfill retired_reason for BOTS-RETIRE-1X2 bots ---------------------
UPDATE bots SET retired_reason =
    'T2-4 1X2-only. Live +83% ROI on 11 bets was variance. Starved by May 17 retrain — shrinkage_alpha_t2_1x2 = 0.00 means the model has no real edge over Pinnacle for T1-T2 1X2. Re-enable if alpha recovers > 0.15 or ≥30 bets at ≥3% ROI in shadow_bets.'
WHERE name = 'bot_lower_1x2' AND retired_reason IS NULL;

UPDATE bots SET retired_reason =
    'Optimizer-found T2-4 home longshots. Live +73% ROI on 15 bets = variance. Starved by May 17 retrain (alpha_t2_1x2 = 0.00). Re-enable on alpha recovery or 30+ bets at ≥3% ROI in shadow_bets.'
WHERE name = 'bot_opt_home_lower' AND retired_reason IS NULL;

UPDATE bots SET retired_reason =
    'T2-4 draws only. Same loss profile as bot_aggressive''s draw bucket: -€154 / 61 bets. Draws are the worst 1X2 selection across the portfolio under the May 17 calibration.'
WHERE name = 'bot_draw_specialist' AND retired_reason IS NULL;

UPDATE bots SET retired_reason =
    'T1-4 1X2 at ≥10% edge. Never fired in production since launch — criteria too tight for the live odds distribution.'
WHERE name = 'bot_conservative' AND retired_reason IS NULL;

-- 3. Retire bot_aggressive ------------------------------------------------
UPDATE bots
SET is_active = false,
    retired_at = COALESCE(retired_at, now()),
    retired_reason =
        'Replaced by bot_aggressive_v2. -5.7% ROI / -€141 on 441 settled bets — single biggest drag on portfolio ROI. Loss buckets: draws (61 bets / -€154), home odds 3.30+ (110 bets / -€95), OU under 2.5 (88 bets / -€46). v2 keeps 129/441 of the bets with no draws, no under 2.5, odds 1.50-3.30, edge ≥5% — replay shows +11.6% ROI / +€90.'
WHERE name = 'bot_aggressive';

-- 4. dashboard_cache schema additions -------------------------------------
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_total_staked   FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_total_pnl      FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_roi_pct        FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_settled_bets   INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_won_bets       INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_lost_bets      INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_total_bets     INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS active_avg_clv        FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS retired_bot_breakdown JSONB;
