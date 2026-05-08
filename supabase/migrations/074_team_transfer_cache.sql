-- Track when each team's transfer data was last fetched, regardless of whether
-- any transfer rows were stored. Prevents re-fetching teams with no transfer activity
-- every enrichment run (teams with no transfers never got a row in team_transfers,
-- so the old cache check always returned uncached for them).
CREATE TABLE IF NOT EXISTS team_transfer_cache (
    team_api_id  INTEGER     PRIMARY KEY,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
