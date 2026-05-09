-- Track when each team's coach data was last fetched, regardless of whether
-- any rows were stored. Mirrors team_transfer_cache (074): teams that AF
-- returns empty for would otherwise re-enter the queue every backfill run,
-- wasting AF calls and parking the dashboard progress bar below 100%.

CREATE TABLE IF NOT EXISTS team_coaches_cache (
    team_af_id   INTEGER     PRIMARY KEY,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed from team_coaches: any team that already has at least one coach row
-- has been successfully probed, so mark it as fetched.
INSERT INTO team_coaches_cache (team_af_id, fetched_at)
SELECT DISTINCT team_af_id, NOW()
FROM team_coaches
ON CONFLICT (team_af_id) DO NOTHING;

-- Replace the count helper to count probed teams (not just teams with data).
-- Empty AF responses are still "done" — there's nothing more to fetch.
CREATE OR REPLACE FUNCTION count_distinct_coached_teams()
RETURNS integer
LANGUAGE sql STABLE
AS $$
  SELECT COUNT(*)::integer FROM team_coaches_cache;
$$;
