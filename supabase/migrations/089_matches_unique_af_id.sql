-- MATCH-DUPES-CLEANUP — Stage B
-- Partial unique index on api_football_id. Prevents future dupe inserts at the DB level
-- even if the application-level dedup misses (which is what created the 1,425 dupe groups
-- found 2026-05-10). Partial because legacy rows from the pre-AF era may have NULL afid.
-- Cleanup script (scripts/cleanup_match_dupes.py) ran first to ensure no duplicates exist.

CREATE UNIQUE INDEX IF NOT EXISTS matches_af_id_unique
  ON matches(api_football_id)
  WHERE api_football_id IS NOT NULL;
