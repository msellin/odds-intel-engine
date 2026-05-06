-- Clean up Kambi-sourced orphan data: matches without api_football_id
-- and leagues/teams only referenced by those orphan matches.
-- These were created by the Kambi scraper (removed in commit 8faf744)
-- when team/league name matching failed against API-Football canonical data.

BEGIN;

-- 1. Delete related data for orphan matches (no api_football_id)
DELETE FROM simulated_bets WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM odds_snapshots WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM predictions WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_signals WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_page_views WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_stats WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_events WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM live_match_snapshots WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_injuries WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_player_stats WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_feature_vectors WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_notes WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_previews WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_votes WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM user_match_favorites WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM saved_matches WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);
DELETE FROM match_weather WHERE match_id IN (SELECT id FROM matches WHERE api_football_id IS NULL);

-- 2. Delete the orphan matches themselves
DELETE FROM matches WHERE api_football_id IS NULL;

-- 3. Delete teams with zero remaining matches (before leagues — FK constraint)
DELETE FROM teams WHERE id NOT IN (
    SELECT home_team_id FROM matches
    UNION
    SELECT away_team_id FROM matches
);

-- 4. Delete orphan leagues not referenced by any match or team
DELETE FROM leagues
WHERE id NOT IN (SELECT DISTINCT league_id FROM matches)
  AND id NOT IN (SELECT DISTINCT league_id FROM teams WHERE league_id IS NOT NULL);

COMMIT;
