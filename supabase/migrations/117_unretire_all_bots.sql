-- BOTS-UNRETIRE-ALL (2026-05-22): un-retire all 8 main bots retired since launch.
--
-- Purpose: generate more live simulated_bets for analysis. All are paper trading
-- bots — no real money at risk.
--
-- Expected behaviour per group:
--   bot_lower_1x2, bot_opt_home_lower, bot_draw_specialist, bot_conservative,
--   bot_ou15_defensive — likely fire ~0 bets (edge-starved by May 7-8 calibration
--   shift and May 17 retrain). Will be visible as active but may produce nothing.
--
--   bot_dc_value, bot_dc_strong_fav, bot_dnb_away_value — will fire bets but
--   have proven negative EV in 1-year backtest (1000-2700 bets each). Good for
--   analysis volume; expect portfolio drag.
--
-- Inplay merge bots (inplay_a2, inplay_c_home, inplay_f) are intentionally
-- excluded — their logic was absorbed into surviving inplay bots; un-retiring
-- would cause duplicate bets.

UPDATE bots
SET is_active  = true,
    retired_at = NULL
WHERE name IN (
    'bot_lower_1x2',
    'bot_opt_home_lower',
    'bot_draw_specialist',
    'bot_conservative',
    'bot_dc_value',
    'bot_dc_strong_fav',
    'bot_dnb_away_value',
    'bot_ou15_defensive'
)
  AND retired_at IS NOT NULL;
