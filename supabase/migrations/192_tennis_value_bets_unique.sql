-- Add unique constraint to prevent duplicate rows per fixture+bookmaker+selection scan.
-- ON CONFLICT DO UPDATE keeps latest book_odds and edge if re-scanned same day.
ALTER TABLE tennis_value_bets
    ADD COLUMN IF NOT EXISTS scan_date date NOT NULL DEFAULT CURRENT_DATE;

CREATE UNIQUE INDEX IF NOT EXISTS tennis_value_bets_unique
    ON tennis_value_bets (fixture_id, bookmaker, selection, scan_date);
