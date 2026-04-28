-- Migration 007: API-Football integration
-- Adds api_football_id to matches for linking with API-Football data source.
-- This enables reliable settlement, live tracking, and post-match stats fetching.

-- Add API-Football fixture ID to matches
ALTER TABLE matches ADD COLUMN IF NOT EXISTS api_football_id integer;
CREATE INDEX IF NOT EXISTS idx_matches_api_football_id ON matches (api_football_id) WHERE api_football_id IS NOT NULL;

-- Add venue and referee columns (populated from API-Football fixture data)
ALTER TABLE matches ADD COLUMN IF NOT EXISTS venue_name text;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS referee text;

-- RLS: allow public read on new columns (inherits existing match RLS policies)
-- No new policies needed — existing SELECT policy on matches covers all columns.
