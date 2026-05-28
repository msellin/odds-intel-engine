-- MATURITY-LABELS 2026-05-29
--
-- Introduce 'testing' maturity label and clean up label assignments across bots.
--
-- Label semantics:
--   calibrated  — backtest-validated with confirmed league whitelists (specialist bots)
--   beta        — backtest-validated or edge-optimized, limited live history
--   testing     — no confirmed signals; firing to accumulate per-league data only.
--                 BTTS also paused due to model miscalibration (June 8 retrain target).
--   active      — broad bots, no specific signal (default, no chip on performance page)
--   experimental — hidden from performance page

-- Specialist bots → calibrated (confirmed backtest signals, league whitelists)
UPDATE bots SET maturity_label = 'calibrated'
WHERE name IN (
    'bot_draw_specialist',
    'bot_dnb_specialist',
    'bot_ou25_specialist',
    'bot_1x2_specialist',
    'bot_dc_specialist'
);

-- Data-collection / uncalibrated bots → testing
-- BTTS: model miscalibrated (62.1% predicted vs 46.5% actual hit rate), paused until June 8 retrain
-- AH: no backtest support; firing to accumulate live per-league evidence
-- OU35: active but no confirmed per-league signals (max 11 bets per league in backtest)
UPDATE bots SET maturity_label = 'testing'
WHERE name IN (
    'bot_btts_all',
    'bot_btts_conservative',
    'bot_ah_home_fav',
    'bot_ah_away_dog',
    'bot_ou35_attacking'
);

-- AH home fav: expanded to T3 for more per-league data accumulation
UPDATE bots
SET description = 'AH home — favourite covers T1-3 (expanded from T1-2 on 2026-05-29 to accumulate more per-league data). handicap_line_max=-0.5 so only true favourites (giving goals). Paused +0 line after -54% ROI on 8 bets.'
WHERE name = 'bot_ah_home_fav';
