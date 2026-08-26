-- CS2-REMOVAL-2026-08-26
--
-- Removes the CS2 esports vertical entirely, at the operator's instruction.
--
-- Context: CS2 had already been half-removed. CS2-REMOVE-SCHEDULER-2026-08-05
-- ripped its jobs out of workers/scheduler.py, so nothing had scheduled a CS2
-- pipeline run in three weeks — but 23,910 lines of Python, 163 files, 74 smoke
-- tests, 11 bot rows and 33 tables (311 MB) all remained, and the smoke tests
-- still asserted the deleted scheduler jobs existed. That was ~60 permanent
-- test failures masking real ones.
--
-- BACKUP TAKEN BEFORE THIS RAN. pg_dump of all 33 tables, verified to contain
-- 33 CREATE TABLE statements:
--     dev/active/cs2-removal-2026-08-26/cs2_backup.sql.gz   (41 MB, gitignored)
--     root@204.168.199.8:/tmp/cs2_backup.sql.gz
-- The 41 MB dump is deliberately NOT committed — both repos are public
-- ([[project_repos_public]]). It is also inside the nightly VPS backup
-- (14-day local / 90-day remote retention), so this is recoverable three ways.
--
-- Restore, if CS2 is ever revived:
--     gunzip -c cs2_backup.sql.gz | psql "$DATABASE_URL"
-- and revert the code deletion from git history.

-- 11 CS2 bot rows. Deleted before the tables so any FK from cs2_bets to bots
-- resolves cleanly regardless of drop order.
DELETE FROM bots WHERE name LIKE '%cs2%';

DROP TABLE IF EXISTS cs2_bets CASCADE;
DROP TABLE IF EXISTS cs2_computed_team_map_stats CASCADE;
DROP TABLE IF EXISTS cs2_egamersworld_rankings CASCADE;
DROP TABLE IF EXISTS cs2_ggscore_rankings CASCADE;
DROP TABLE IF EXISTS cs2_hltv_map_meta CASCADE;
DROP TABLE IF EXISTS cs2_hltv_match_maps CASCADE;
DROP TABLE IF EXISTS cs2_hltv_match_queue CASCADE;
DROP TABLE IF EXISTS cs2_hltv_match_veto CASCADE;
DROP TABLE IF EXISTS cs2_hltv_matches CASCADE;
DROP TABLE IF EXISTS cs2_hltv_news CASCADE;
DROP TABLE IF EXISTS cs2_hltv_player_match_stats CASCADE;
DROP TABLE IF EXISTS cs2_hltv_player_ratings CASCADE;
DROP TABLE IF EXISTS cs2_hltv_player_stats CASCADE;
DROP TABLE IF EXISTS cs2_hltv_rankings CASCADE;
DROP TABLE IF EXISTS cs2_hltv_team_ftu CASCADE;
DROP TABLE IF EXISTS cs2_hltv_team_map_stats CASCADE;
DROP TABLE IF EXISTS cs2_hltv_team_pistols CASCADE;
DROP TABLE IF EXISTS cs2_hltv_team_rosters CASCADE;
DROP TABLE IF EXISTS cs2_hltv_team_stats CASCADE;
DROP TABLE IF EXISTS cs2_hltv_top_players CASCADE;
DROP TABLE IF EXISTS cs2_leetify_player_match_stats CASCADE;
DROP TABLE IF EXISTS cs2_match_id_bridge CASCADE;
DROP TABLE IF EXISTS cs2_model_backtest_history CASCADE;
DROP TABLE IF EXISTS cs2_model_coefficients CASCADE;
DROP TABLE IF EXISTS cs2_pandascore_matches CASCADE;
DROP TABLE IF EXISTS cs2_player_id_bridge CASCADE;
DROP TABLE IF EXISTS cs2_predictions CASCADE;
DROP TABLE IF EXISTS cs2_real_bets CASCADE;
DROP TABLE IF EXISTS cs2_results CASCADE;
DROP TABLE IF EXISTS cs2_scraper_state CASCADE;
DROP TABLE IF EXISTS cs2_simulated_bets CASCADE;
DROP TABLE IF EXISTS cs2_team_pistol_stats CASCADE;
DROP TABLE IF EXISTS cs2_upcoming_matches CASCADE;
