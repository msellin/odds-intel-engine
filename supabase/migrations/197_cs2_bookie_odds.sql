-- Add bo3.gg bookmaker reference odds columns to cs2_upcoming_matches
-- (CREATE TABLE IF NOT EXISTS in 195 skipped these since the table already existed)
ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS bookie_odds1 FLOAT,
    ADD COLUMN IF NOT EXISTS bookie_odds2 FLOAT;
