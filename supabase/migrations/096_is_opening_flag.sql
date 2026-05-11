-- MODEL-SIGNALS (2026-05-11)
-- Add is_opening flag to odds_snapshots so the true market opening line is
-- preserved through any future pruning (pruner already keeps is_closing rows;
-- this is the symmetric flag for the open).
-- Backfill marks the earliest snapshot per (match_id, bookmaker, market,
-- selection) as is_opening=true for all existing data.

ALTER TABLE odds_snapshots
    ADD COLUMN IF NOT EXISTS is_opening boolean NOT NULL DEFAULT false;

-- Backfill: mark the earliest row per combination.
WITH earliest AS (
    SELECT DISTINCT ON (match_id, bookmaker, market, selection) id
    FROM odds_snapshots
    ORDER BY match_id, bookmaker, market, selection, timestamp ASC
)
UPDATE odds_snapshots SET is_opening = true WHERE id IN (SELECT id FROM earliest);

-- Index so pruner and MFV build can filter efficiently.
CREATE INDEX IF NOT EXISTS idx_odds_snapshots_is_opening
    ON odds_snapshots (match_id, bookmaker, market, selection)
    WHERE is_opening = true;
