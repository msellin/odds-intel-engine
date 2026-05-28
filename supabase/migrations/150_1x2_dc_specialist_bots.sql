-- 1X2-DC-SPECIALIST 2026-05-29
--
-- Expand bot_draw_specialist (+3 leagues), create bot_1x2_specialist and bot_dc_specialist.
-- All three backed by 2023-2026 clean backtest signals (30+ bets, >10% ROI).
--
-- bot_draw_specialist: +China Super League (+73.8%), USA USL League Two (+29.2%),
--   Azerbaijan Birinci Dasta (+13.3%). Total 15 leagues.
--
-- bot_1x2_specialist (multi-strategy):
--   "Away Value"  — Arg Liga Profesional (+26.4%), Eng League Two (+18.3%), France Ligue 1 (+12%)
--   "Home Value"  — Austria Bundesliga (+57.6%), Spain Segunda División (+15.1%)
--
-- bot_dc_specialist (multi-strategy):
--   "X2 Value"    — Brazil Serie B (+20.1%), China Super League (+13.7%)
--   "1X Israel"   — Israel Liga Leumit (+13.3%)

-- 1. Update bot_draw_specialist description (15 leagues now)
UPDATE bots
SET description = 'DRAW-LEAGUE-WHITELIST 2026-05-29: draw specialist — 15 leagues confirmed by 2023-2026 clean backtest. +3 leagues added 2026-05-29: China Super League (+73.8%), USA USL League Two (+29.2%), Azerbaijan Birinci Dasta (+13.3%).'
WHERE name = 'bot_draw_specialist';

-- 2. Create bot_1x2_specialist
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
    'bot_1x2_specialist',
    '1x2 — home/away value specialist with per-strategy league whitelists',
    '1X2-SPECIALIST 2026-05-29: home/away value specialist backed by 2023-2026 clean backtest. Profile "Away Value": Argentina Liga Profesional (+26.4%, 41 bets), England League Two (+18.3%, 168 bets), France Ligue 1 (+12%, 103 bets). Profile "Home Value": Austria Bundesliga (+57.6%, 56 bets), Spain Segunda División (+15.1%, 34 bets). Per-profile ROI queryable via strategy_profile column on simulated_bets.',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
);

-- 3. Create bot_dc_specialist
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
    'bot_dc_specialist',
    'Double Chance — X2 + 1X specialist with per-strategy league whitelists',
    'DC-SPECIALIST 2026-05-29: double-chance specialist backed by 2023-2026 clean backtest. Profile "X2 Value": Brazil Serie B (+20.1%, 40 bets), China Super League (+13.7%, 32 bets). Profile "1X Israel": Israel Liga Leumit (+13.3%, 30 bets). Per-profile ROI queryable via strategy_profile column on simulated_bets.',
    NULL,
    1000.00,
    1000.00,
    true,
    'beta'
);
