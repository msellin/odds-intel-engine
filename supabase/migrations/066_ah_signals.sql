-- Migration 066: handicap_line column on odds_snapshots
-- Supports AH-SIGNALS: Asian Handicap line storage and drift computation.
-- Nullable — only populated for asian_handicap market rows.

ALTER TABLE odds_snapshots ADD COLUMN IF NOT EXISTS handicap_line NUMERIC;

CREATE INDEX IF NOT EXISTS idx_odds_snapshots_ah
    ON odds_snapshots (match_id, bookmaker, timestamp DESC)
    WHERE market = 'asian_handicap';
