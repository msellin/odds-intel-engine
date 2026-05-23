-- REAL-BETS-CLV-EDGE (2026-05-23)
-- Add CLV + edge-taken tracking to real_bets so the admin log can show how
-- well our actual placement prices performed vs the bot's pick prices and
-- vs the eventual closing line.
--
-- New columns:
--   edge_pct_taken numeric(8,5)
--     Edge implied by the price we ACTUALLY GOT (actual_odds), using the
--     bot's model_probability from simulated_bets. Stored as a decimal
--     fraction (0.05 = +5% edge). Set at insert time by store_real_bet().
--
--   clv numeric(8,5)
--     Closing-Line Value vs actual_odds: (actual_odds / closing_odds) - 1.
--     Stored as a decimal fraction (0.03 = +3% CLV). Set at settlement time
--     in _settle_real_bets_for_matches when match closes and
--     get_closing_odds() returns a value.
--
-- slippage_pct already exists (migration 027) but the placer has been writing
-- it as NULL — fixed in this batch by populating it in store_real_bet().
--
-- Both new columns are nullable: combos and bets without a paired
-- simulated_bet_id (manual admin entries) won't have model_probability /
-- closing_odds to compute against.

ALTER TABLE real_bets
  ADD COLUMN IF NOT EXISTS edge_pct_taken numeric(8,5),
  ADD COLUMN IF NOT EXISTS clv            numeric(8,5);

COMMENT ON COLUMN real_bets.edge_pct_taken IS
  'REAL-BETS-CLV-EDGE: edge implied by actual_odds × model_probability − 1. Decimal fraction (0.05 = +5%).';
COMMENT ON COLUMN real_bets.clv IS
  'REAL-BETS-CLV-EDGE: (actual_odds / closing_odds) − 1. Decimal fraction. Set at settlement when closing_odds available.';
