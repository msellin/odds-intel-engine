-- INPLAY-ODDS-ARCHIVE — DB-ANCHOR-GROWTH step 1 (2026-09-11)
--
-- WHY: the 7-day retention policy is structurally fatal to in-play odds and
-- cannot be otherwise. `is_closing` is stamped only when
-- abs(minutes_to_kickoff) <= 15, and `prune_old_simple`'s anchorless-survivor
-- fallback only considers rows with `timestamp <= m.date`. A post-kickoff row
-- therefore can never be an anchor NOR a fallback survivor, so 100% of in-play
-- price history is condemned once its match passes the retention window.
--
-- We hold 155,048 is_live rows spanning 2026-05-07 → 2026-08-21 (the day
-- in-play was gated off), and 152,327 of them are already inside the pruner's
-- target set. They survive today only because the pruner has been running ~21x
-- too slow to reach them. The same commit that makes the pruner keep up would
-- therefore destroy the only in-play price history we own — so this archive is
-- taken FIRST and deliberately as its own migration.
--
-- The pruner is also being taught to downsample is_live rows to 1/minute
-- instead of deleting them (ODDS-INPLAY-RETENTION). That protects data from
-- here on, but it is lossy for what already exists: api-football-live polled at
-- 45s, so a 1/minute rule drops roughly a quarter of the existing rows. This
-- table keeps the raw history at full resolution, ~65 MB, inside the nightly
-- pg backup. Cheap insurance against an irreversible loss.
--
-- Idempotent: safe to re-run, and re-runnable later to capture anything new.

CREATE TABLE IF NOT EXISTS odds_snapshots_inplay_archive (
    LIKE odds_snapshots INCLUDING DEFAULTS
);

ALTER TABLE odds_snapshots_inplay_archive
    ADD COLUMN IF NOT EXISTS archived_at timestamptz NOT NULL DEFAULT now();

-- Unique index rather than a PRIMARY KEY so the migration stays re-runnable.
CREATE UNIQUE INDEX IF NOT EXISTS odds_snapshots_inplay_archive_id_uidx
    ON odds_snapshots_inplay_archive (id);

CREATE INDEX IF NOT EXISTS odds_snapshots_inplay_archive_match_ts_idx
    ON odds_snapshots_inplay_archive (match_id, timestamp DESC);

INSERT INTO odds_snapshots_inplay_archive (
    id, match_id, bookmaker, market, selection, odds, timestamp,
    is_closing, minutes_to_kickoff, is_live, handicap_line, is_opening
)
SELECT o.id, o.match_id, o.bookmaker, o.market, o.selection, o.odds, o.timestamp,
       o.is_closing, o.minutes_to_kickoff, o.is_live, o.handicap_line, o.is_opening
  FROM odds_snapshots o
 WHERE o.is_live
   AND NOT EXISTS (
         SELECT 1 FROM odds_snapshots_inplay_archive a WHERE a.id = o.id
       );
