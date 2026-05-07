-- Migration 067: Store AF team IDs on matches
-- Needed by batch_write_morning_signals for MGR-CHANGE and H2H-SPLITS signals.
-- Previously computed transiently during enrichment, never persisted.

ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_team_api_id INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_team_api_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_matches_home_team_api_id ON matches (home_team_api_id) WHERE home_team_api_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_matches_away_team_api_id ON matches (away_team_api_id) WHERE away_team_api_id IS NOT NULL;
