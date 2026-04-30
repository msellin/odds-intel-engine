-- Migration 021: backfill_progress table
--
-- Tracks historical data backfill progress per league/season.
-- Used by scripts/backfill_historical.py to resume from where it left off.

CREATE TABLE IF NOT EXISTS backfill_progress (
    league_api_id   integer NOT NULL,
    season          integer NOT NULL,
    phase           smallint NOT NULL DEFAULT 1,
    fixtures_total  integer DEFAULT 0,
    fixtures_done   integer DEFAULT 0,
    odds_done       integer DEFAULT 0,
    stats_done      integer DEFAULT 0,
    events_done     integer DEFAULT 0,
    status          text DEFAULT 'pending',  -- pending | in_progress | complete
    last_run_at     timestamptz,
    created_at      timestamptz DEFAULT now(),
    PRIMARY KEY (league_api_id, season)
);

-- Allow RLS read for monitoring (no public write)
ALTER TABLE backfill_progress ENABLE ROW LEVEL SECURITY;

CREATE POLICY "backfill_progress_read" ON backfill_progress
    FOR SELECT USING (true);
