-- Migration 063: Add per-game live tracker counts and inplay bot split to ops_snapshots
-- Replaces raw row counts with distinct-game counts so the dashboard reads as
-- "45 games tracked" not "12,500 snapshot rows".

ALTER TABLE ops_snapshots
  ADD COLUMN IF NOT EXISTS live_games_tracked      integer,
  ADD COLUMN IF NOT EXISTS live_games_with_xg      integer,
  ADD COLUMN IF NOT EXISTS live_games_with_odds    integer,
  ADD COLUMN IF NOT EXISTS inplay_active_bots      integer;
