-- HRG-V2 2026-05-28
--
-- bot_high_roi_global retired (migration 142): targeted Scotland/Ireland/Korea/Singapore
-- from a 2005-2015 mega-backtest. Live data showed -46% to -78% Pinnacle CLV in
-- Scotland/Ireland — those markets are now sharp.
--
-- V2 rebuilt from live Pinnacle CLV scan + 3-year historical odds data:
--   Spain:     44 live bets, +5.7% avg CLV. Away underdogs specifically:
--              La Liga away +7.8% CLV, Segunda away +5.8% CLV (avg odds 4.61-4.67).
--              Historical Pinnacle naive away-backing: +1.8% ROI on 1145 bets (3yr).
--              Gap: bot_aggressive_v2 caps at 3.30 odds; proven_leagues_v2 is home-only.
--              Spain away underdogs at 4.60+ are completely uncovered by existing bots.
--   Australia: 21 live bets, +13% CLV (+15.1% home specifically). Beta — 1 month data.
--   Iceland:   12 live bets, +11.5% CLV. Beta — 1 month data.

INSERT INTO bots (
    name,
    strategy,
    description,
    strategy_description,
    starting_bankroll,
    current_bankroll,
    is_active,
    maturity_label
) VALUES (
    'bot_high_roi_global_v2',
    '1x2 home/away — Spain/Australia/Iceland, odds 1.50-5.50',
    'HRG-V2 2026-05-28: Globally soft market rebuild. Spain validated (44 live bets, +5.7% avg CLV; away underdogs at +9.4% CLV with structural historical backing). Australia/Iceland added as beta (high CLV on 1-month samples, 21 and 12 bets respectively). Targets the uncovered gap: away underdogs at 3.30-5.50 odds not reached by bot_aggressive_v2 (caps at 3.30) or bot_proven_leagues_v2 (home-only).',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
);
