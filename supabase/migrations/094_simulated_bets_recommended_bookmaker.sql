-- ACCESSIBLE-BM: track which accessible bookmaker had the best odds at bet placement time.
-- Used by the daily picks script and value-bets frontend to tell users where to actually place.
-- Populated by daily_pipeline_v2.py; NULL for bets placed before this migration.
ALTER TABLE simulated_bets
  ADD COLUMN IF NOT EXISTS recommended_bookmaker TEXT;

COMMENT ON COLUMN simulated_bets.recommended_bookmaker IS
  'ACCESSIBLE-BM: which accessible bookmaker (Bet365/Unibet/etc) had the best odds when this bet was placed.';
