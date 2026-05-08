-- Migration 073: add odds_snapshots_total_rows to ops_snapshots
-- Tracks total row count across all dates so the ops dashboard can show DB bloat
-- and alert if pruning is not running.

ALTER TABLE ops_snapshots
    ADD COLUMN IF NOT EXISTS odds_snapshots_total_rows bigint DEFAULT 0;
