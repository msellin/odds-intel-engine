-- Migration 062: Add backfill_total_finished to ops_snapshots
-- Enables the backfill section to show progress as done/total (% complete).

ALTER TABLE ops_snapshots
  ADD COLUMN IF NOT EXISTS backfill_total_finished integer;
