-- PROVEN-LEAGUES-REFACTOR 2026-05-28
--
-- bot_high_roi_global and bot_proven_leagues both targeted Scotland/Austria/Ireland/Korea
-- based on a 2005-2015 mega-backtest. Live data (14/14 bets respectively) showed:
--   Scotland: -55% to -78% Pinnacle CLV — model miscalibrated, market is now sharp
--   Ireland:  -10% to -28% Pinnacle CLV — same issue
--   Austria:  +7% to +27% Pinnacle CLV  — edge exists, market still soft in lower divs
--
-- Replaced by bot_proven_leagues_v2 using live-validated league selection:
-- All leagues with ≥8 settled 1x2 bets were scanned. Kept only those with
-- Pinnacle CLV ≥ +5%: Italy (+9-18%), France (+5-11%), USA (+7-10%),
-- Austria (+7%), Ireland (+5%). 23 leagues evaluated, 8 made the cut.

UPDATE bots
SET
    is_active   = false,
    retired_at  = NOW(),
    retired_reason = 'PROVEN-LEAGUES-REFACTOR 2026-05-28: Historical 2005-2015 backtest edge no longer exists in Scotland (-78% Pinnacle CLV) or Ireland (-28% CLV) — modern bookmakers are sharp in those markets. Austria still showed +7-27% CLV and is preserved in bot_proven_leagues_v2. Replaced by data-driven v2 using live Pinnacle CLV scan across 23 leagues.'
WHERE name IN ('bot_high_roi_global', 'bot_proven_leagues');

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
    'bot_proven_leagues_v2',
    '1x2 home/away value — Italy/France/USA/Austria/Ireland, odds 2.80-5.00',
    'PROVEN-V2 2026-05-28: Successor to bot_proven_leagues. League list built from live Pinnacle CLV scan across 23 leagues (≥8 1x2 bets each). Kept: Italy +9-18% CLV (Serie B/C), France +5-11% CLV (L1/L2), USA +7-10% CLV (MLS/NP), Austria +7% CLV (2.Liga), Ireland +5% CLV. Targets home underdogs at 3-5 odds where the Poisson+XGBoost blend finds consistent edge vs closing line.',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
);
