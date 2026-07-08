-- BOT-SUMMER-SPECIALIST (2026-07-08)
--
-- New paper bot filling the mid-week volume gap during Northern-Hemisphere
-- summer (June-August). bot_v10_all covers the weekend fringe well but its
-- v10 model has ~zero prediction coverage on the North-American / Nordic /
-- Baltic summer leagues (verified 2026-07-08 prereq check: MLS Next Pro /
-- USL Championship / USL League One / Finland / Iceland / Estonia all show
-- 0 v10 predictions in the last 14d). This bot uses the main model version
-- (v20260607 / v20260621 / v20260705) via daily_pipeline_v2's default and
-- whitelists 12 leagues with 100% odds+prediction coverage that play
-- midweek in summer.
--
-- Ships as maturity_label='beta' — paper-only for the 4-week observation
-- window. Coolbet placer's COOLBET_RECORD_ALLOWED_MATURITY=calibrated
-- filter naturally excludes beta bots from real-money placement.
--
-- Re-evaluate 2026-08-08: if ≥30 settled bets with ROI ≥ 0% and CLV ≥ +1%,
-- promote to 'active'. If ROI < -5% or CLV < 0%, retire.

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
    'bot_summer_specialist',
    'Summer-active league specialist — USA (MLS Next Pro / USL Championship / USL League One), Nordic (Finland / Iceland), Baltic (Estonia), China L2, Sweden Damallsvenskan + Allsvenskan.',
    'league_whitelist',
    'Targets midweek summer fixtures the v10-model bots miss (v10 has no prediction coverage on these leagues). Main model + tier-adjusted edge thresholds slightly looser than bot_v10_all (1-2pp) to compensate for higher lower-tier variance. Markets: 1x2 + o/u only.',
    TRUE,
    'beta',
    1000.00,
    1000.00
)
ON CONFLICT (name) DO UPDATE
SET is_active = TRUE,
    maturity_label = 'beta',
    description = EXCLUDED.description,
    strategy = EXCLUDED.strategy,
    strategy_description = EXCLUDED.strategy_description,
    retired_at = NULL,
    retired_reason = NULL,
    updated_at = NOW();
