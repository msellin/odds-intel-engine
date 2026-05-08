-- Helper: count distinct AF team IDs seen across all matches (home + away).
-- Used by the ops dashboard backfill section to show coaches/transfers progress.
CREATE OR REPLACE FUNCTION count_distinct_team_af_ids()
RETURNS integer
LANGUAGE sql STABLE
AS $$
  SELECT COUNT(*)::integer FROM (
    SELECT home_team_api_id AS id FROM matches WHERE home_team_api_id IS NOT NULL
    UNION
    SELECT away_team_api_id           FROM matches WHERE away_team_api_id IS NOT NULL
  ) t;
$$;
