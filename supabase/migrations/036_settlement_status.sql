-- 036_settlement_status.sql
-- Adds settlement_status to matches so settlement can be tracked per-match
-- and a lightweight 15-min sweep can catch anything the live poller missed.
--
-- Values:
--   'none'  — match not finished yet (default for new rows)
--   'ready' — live poller marked this match FT/AET/PEN; bets not yet settled
--   'done'  — bets + user picks settled

ALTER TABLE matches
ADD COLUMN IF NOT EXISTS settlement_status TEXT NOT NULL DEFAULT 'none';

-- Backfill: all already-finished matches are treated as settled so the new
-- sweep job doesn't re-process them on its first run.
UPDATE matches
SET settlement_status = 'done'
WHERE status = 'finished';

-- Index so the 15-min sweep query is fast (most rows will be 'done' or 'none')
CREATE INDEX IF NOT EXISTS idx_matches_settlement_status
    ON matches (settlement_status)
    WHERE settlement_status != 'done';
