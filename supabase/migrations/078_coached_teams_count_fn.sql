-- Helper: count distinct teams that have at least one entry in team_coaches.
-- Fixes ops dashboard showing >100% progress (row count > distinct team count).
CREATE OR REPLACE FUNCTION count_distinct_coached_teams()
RETURNS integer
LANGUAGE sql STABLE
AS $$
  SELECT COUNT(DISTINCT team_af_id)::integer FROM team_coaches;
$$;
