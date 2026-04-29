-- =============================================================================
-- OddsIntel — Remove Sofascore dependency
-- Migration: 015_remove_sofascore.sql
--
-- 1. Delete matches that came from Sofascore fallback (no api_football_id)
-- 2. Drop sofascore_event_id column from matches table
-- =============================================================================

-- Delete Sofascore-only fixtures (have no AF ID, no enrichment data)
DELETE FROM matches WHERE api_football_id IS NULL;

-- Drop the sofascore_event_id column — no longer used
ALTER TABLE matches DROP COLUMN IF EXISTS sofascore_event_id;
