-- BOT-NO-PIN-SHADOW-2026-08-18 Phase 1
--
-- New shadow-only bot that logs 1X2 picks on matches WITHOUT Pinnacle
-- coverage. Motivation: currently ~15-20% of odds-carrying fixtures per day
-- have no Pinnacle 1X2 (mostly cup ties, U-teams, lower-tier leagues in
-- less-covered regions) and get skipped entirely by production bots because
-- our filters (OU-PIN-REQUIRED + ODDS-OUTLIER-FILTER-2026-08-18) require
-- Pinnacle as the sharp anchor. This bot fires when:
--   • main-model 1X2 prob exists (source='ensemble')
--   • NO Pinnacle 1X2 quote exists for that match
--   • ≥3 accessible bookmakers quote the same selection (median = anchor)
--   • best-of-accessible edge vs model prob ≥ 8%
--
-- Writes to shadow_bets only — never places a real or simulated bet, never
-- touches bankroll. Data-collection experiment (Phase 1).
--
-- Phase 2 trigger (promote to paper beta writing simulated_bets):
--   ≥50 settled shadow bets AND ROI ≥ +3% over the observation window.
-- Phase 2 auto-kill: ROI ≤ −8% at n≥50.
--
-- Ships as maturity_label='experimental' (matches bot_acca_leg_shadow
-- pattern — shadow-only, never placed).

INSERT INTO bots (
    name,
    description,
    strategy,
    strategy_description,
    is_active,
    maturity_label,
    starting_bankroll,
    current_bankroll
) VALUES (
    'bot_no_pin_shadow_v1',
    'Shadow bot for matches without Pinnacle 1X2 — logs hypothetical bets when best accessible-book edge vs model prob ≥ 8% and ≥3 books quote the selection.',
    'no_pinnacle_1x2',
    'Fires on 1X2 markets where Pinnacle is absent but ≥3 accessible bookmakers quote the same selection. Uses main ensemble model probability. Best-of-accessible odds × model prob − 1 ≥ 0.08 required. Writes only to shadow_bets — never places or affects bankroll. Purpose: measure whether the model has edge on the ~15-20% of daily fixtures currently skipped by the Pinnacle-required gate.',
    TRUE,
    'experimental',
    -- Bankroll must be > 0 per chk_bots_bankroll_positive. Nominal 1.00 —
    -- never touched (shadow_bets uses fixed 10u nominal stake).
    1.00,
    1.00
)
ON CONFLICT (name) DO UPDATE
SET is_active = TRUE,
    maturity_label = 'experimental',
    description = EXCLUDED.description,
    strategy = EXCLUDED.strategy,
    strategy_description = EXCLUDED.strategy_description,
    retired_at = NULL,
    retired_reason = NULL,
    updated_at = NOW();
