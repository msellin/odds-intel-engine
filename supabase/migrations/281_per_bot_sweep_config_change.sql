-- PER-BOT-SWEEP-2026-08-24 — retire 2 bots + record every config change.
--
-- Driven by dev/active/per-bot-sweep-2026-08-24-context.md. The operator
-- placed EUR 1,270 of real money on these bots 2026-08-22..24 and lost.
-- A point-in-time replay (scripts/per_bot_backtest_sweep.py) found that the
-- ORIGINAL selection method was the problem: picking configs on positive
-- backtest ROI scored -9.2% out of sample, worse than picking nothing.
--
-- This migration does three things:
--   1. Creates bot_config_history so every config is recoverable.
--   2. Snapshots the PRE-change config of all 8 bots (effective 2026-08-19,
--      superseded 2026-08-24) so we can always roll back.
--   3. Retires bot_pin_1x2_draw_tier4_v1 + bot_no_pin_home_v1 and snapshots
--      the POST-change config of the 6 survivors.

-- ---------------------------------------------------------------------------
-- 1. Config history table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bot_config_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_name        TEXT NOT NULL,
    config          JSONB NOT NULL,
    effective_from  DATE NOT NULL,
    superseded_at   DATE,
    change_ref      TEXT NOT NULL,
    rationale       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bot_config_history_bot
    ON bot_config_history (bot_name, effective_from DESC);

-- Only one live config row per bot at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_config_history_current
    ON bot_config_history (bot_name) WHERE superseded_at IS NULL;

ALTER TABLE bot_config_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bot_config_history_anon_read ON bot_config_history;
CREATE POLICY bot_config_history_anon_read ON bot_config_history FOR SELECT USING (true);

-- ---------------------------------------------------------------------------
-- 2. PRE-change snapshots (as deployed 2026-08-19..21, retired 2026-08-24)
-- ---------------------------------------------------------------------------
INSERT INTO bot_config_history (bot_name, config, effective_from, superseded_at, change_ref, rationale)
VALUES
 ('bot_sweep_1x2_home_v1',
  '{"engine":"model","market":"1x2","selection":"home","tiers":[2,3],"edge_min":0.10,"edge_basis":"raw_model","odds_min":2.00,"odds_max":5.00,"min_prob":0.25,"require_pinnacle":true,"stake":10.0}',
  '2026-08-19','2026-08-24','CONFIG-SWEEP-2026-08-19','Sweep winner #1: 501 bets, +9.34% ROI, +10.16% CLV in original backtest.'),
 ('bot_sweep_1x2_draw_v1',
  '{"engine":"model","market":"1x2","selection":"draw","tiers":[2,3],"edge_min":0.05,"edge_basis":"raw_model","odds_min":1.30,"odds_max":3.50,"min_prob":0.25,"require_pinnacle":true,"stake":10.0}',
  '2026-08-19','2026-08-24','CONFIG-SWEEP-2026-08-19','Sweep winner #2: 714 bets, +7.33% ROI.'),
 ('bot_sweep_btts_yes_v1',
  '{"engine":"model","market":"btts","selection":"yes","tiers":[2,3],"edge_min":0.05,"edge_basis":"raw_model","odds_min":2.00,"odds_max":2.50,"min_prob":0.25,"require_pinnacle":false,"stake":10.0}',
  '2026-08-19','2026-08-24','CONFIG-SWEEP-2026-08-19','Sweep winner #3: 318 bets, +5.44% ROI.'),
 ('bot_no_pin_home_v1',
  '{"engine":"model","market":"1x2","selection":"home","pinnacle":"must_be_absent","tiers":"all_except_0","edge_min":0.08,"edge_basis":"raw_model","odds_min":1.30,"odds_max":6.00,"min_prob":0.25,"min_books":3,"outlier_mult":1.35,"model_vs_market_cap_pp":20,"stake":10.0}',
  '2026-08-21','2026-08-24','BOT-NO-PIN-HOME-2026-08-21','Home-only refinement after bot_no_pin_shadow_v1 audit showed home n=42 ROI +32.7%.'),
 ('bot_sweep_ou25_v1',
  '{"engine":"lineshop","market":"over_under_25","selection":"both_sides","tiers":"none","edge_min":0.08,"edge_basis":"raw_vig_inclusive","odds_min":1.30,"odds_max":5.00,"max_vig":1.10,"outlier_mult":1.30,"stake":10.0}',
  '2026-08-21','2026-08-24','BOT-PIN-OU-SHADOW-2026-08-21','Ad-hoc simulation claimed +13-25% ROI per tier. Script never committed — unreproducible.'),
 ('bot_sweep_ou35_v1',
  '{"engine":"lineshop","market":"over_under_35","selection":"both_sides","tiers":"none","edge_min":0.08,"edge_basis":"raw_vig_inclusive","odds_min":1.30,"odds_max":5.00,"max_vig":1.10,"outlier_mult":1.30,"stake":10.0}',
  '2026-08-21','2026-08-24','BOT-PIN-OU-SHADOW-2026-08-21','Ad-hoc simulation claimed +15-40% ROI per tier. Script never committed — unreproducible.'),
 ('bot_pin_1x2_home_v1',
  '{"engine":"lineshop","market":"1x2","selection":"home","tiers":[1,2],"edge_min":0.12,"edge_basis":"raw_vig_inclusive","odds_min":1.30,"odds_max":6.00,"outlier_mult":1.35,"stake":10.0}',
  '2026-08-21','2026-08-24','BOT-PIN-1X2-SHADOW-2026-08-21','Ad-hoc simulation: tier 1 n=1208 +12.1%, tier 2 n=138 +31.3%.'),
 ('bot_pin_1x2_draw_tier4_v1',
  '{"engine":"lineshop","market":"1x2","selection":"draw","tiers":[4],"edge_min":0.05,"edge_basis":"raw_vig_inclusive","odds_min":1.30,"odds_max":6.00,"outlier_mult":1.35,"stake":10.0}',
  '2026-08-21','2026-08-24','BOT-PIN-1X2-SHADOW-2026-08-21','Ad-hoc simulation: tier 4 draws 5%+ n=348 ROI +6-18%. CONFIG-SWEEP two days earlier had concluded tier 4 never wins.')
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3a. Retire the two losers
-- ---------------------------------------------------------------------------
UPDATE bots
   SET is_active = FALSE,
       retired_at = COALESCE(retired_at, now()),
       retired_reason = 'PER-BOT-SWEEP-2026-08-24: 5% edge gate sat below the 12.2% Pinnacle '
                        'overround on tier-4 draws — 85% of live picks had negative de-vigged '
                        'edge. Live -40.8% (n=27); operator went 0W/11L for -110 EUR. Backtest '
                        '+7.8% was the single positive cell of 8 tier sets on a strategy that is '
                        '-3.6% overall, and is -10.0% once de-vigged.'
 WHERE name = 'bot_pin_1x2_draw_tier4_v1';

UPDATE bots
   SET is_active = FALSE,
       retired_at = COALESCE(retired_at, now()),
       retired_reason = 'PER-BOT-SWEEP-2026-08-24: negative at EVERY edge threshold tested '
                        '(-5.3% to -7.4% across 0.02-0.20) and in 2 of 3 backtest windows. '
                        'Live -10.6% (n=66). Model is 17.3pp overconfident here — no Pinnacle '
                        'means no sharp anchor on the most obscure fixtures on the board.'
 WHERE name = 'bot_no_pin_home_v1';

-- ---------------------------------------------------------------------------
-- 3b. POST-change snapshots for the 6 survivors + the 2 retirements
-- ---------------------------------------------------------------------------
INSERT INTO bot_config_history (bot_name, config, effective_from, change_ref, rationale)
VALUES
 ('bot_sweep_1x2_home_v1',
  '{"engine":"model","market":"1x2","selection":"home","tiers":[2,3],"edge_min":0.10,"edge_basis":"raw_model","odds_min":2.00,"odds_max":5.00,"min_prob":0.25,"require_pinnacle":true,"stake":10.0,"null_tier":"excluded"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24',
  'No threshold change — tier 3 is this bot''s BETTER half (+7.2% vs tier 2 -1.2%), so the general tier-3 exclusion does not apply. Only change: NULL-tier leagues no longer pass as tier 1.'),
 ('bot_sweep_1x2_draw_v1',
  '{"engine":"model","market":"1x2","selection":"draw","tiers":[2,3],"edge_min":0.05,"edge_basis":"raw_model","odds_min":1.30,"odds_max":3.50,"min_prob":0.25,"require_pinnacle":true,"stake":10.0,"null_tier":"excluded","watch":"kill at n=100 if W3 pattern persists"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24',
  'No threshold change, but flagged: most recent backtest window is -23% to -59% at EVERY edge threshold. That is a regime signal a re-gate cannot fix.'),
 ('bot_sweep_btts_yes_v1',
  '{"engine":"model","market":"btts","selection":"yes","tiers":[2,3],"edge_min":0.05,"edge_basis":"raw_model","odds_min":2.00,"odds_max":2.50,"min_prob":0.25,"require_pinnacle":false,"stake":10.0,"null_tier":"excluded"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24',
  'No change — lowest volume of the eight (~10 picks/day), too little data to conclude either way.'),
 ('bot_sweep_ou25_v1',
  '{"engine":"lineshop","market":"over_under_25","selection":"single_best_side","tiers":[1,2],"edge_min":0.03,"edge_basis":"devigged","odds_min":1.30,"odds_max":5.00,"max_vig":1.10,"outlier_mult":1.30,"stake":10.0,"null_tier":"excluded"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24',
  'De-vigged gate replaces the vig-inclusive 8%. Tier filter added (had NONE). Side lock added. Tier 3 was -16.3%, tier 1 +4.7%.'),
 ('bot_sweep_ou35_v1',
  '{"engine":"lineshop","market":"over_under_35","selection":"single_best_side","tiers":[1,2],"edge_min":0.03,"edge_basis":"devigged","odds_min":1.30,"odds_max":5.00,"max_vig":1.10,"outlier_mult":1.30,"stake":10.0,"null_tier":"excluded"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24',
  'Same as OU 2.5. Side lock fixes a real bug: the bot flipped over->under on the same total across cohorts in 2 matches, ending up holding both sides.'),
 ('bot_pin_1x2_home_v1',
  '{"engine":"lineshop","market":"1x2","selection":"home","tiers":[1,2],"edge_min":0.03,"edge_basis":"devigged","odds_min":1.30,"odds_max":6.00,"outlier_mult":1.35,"stake":10.0,"null_tier":"excluded"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24',
  'The one genuine winner: positive in ALL 3 backtest windows, positive across every tier variation, best CLV (+15.6%), 0% negative-true-edge picks live. Gate moves to the shared de-vigged basis; 0.03 de-vigged is close to the old 0.12 raw once the ~9% overround is removed.'),
 ('bot_no_pin_home_v1',
  '{"status":"retired","retired_on":"2026-08-24"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24','Retired — see bots.retired_reason.'),
 ('bot_pin_1x2_draw_tier4_v1',
  '{"status":"retired","retired_on":"2026-08-24"}',
  '2026-08-24','PER-BOT-SWEEP-2026-08-24','Retired — see bots.retired_reason.')
ON CONFLICT DO NOTHING;
