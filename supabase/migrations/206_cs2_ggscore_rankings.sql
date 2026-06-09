-- GGScore CS2 team ranking — site is 403'd to scrapers so this is a
-- manual snapshot table (user pastes new data weekly via /admin/cs2 or
-- a load script). Treated identically to cs2_hltv_rankings for the
-- consensus-strength comparison.

CREATE TABLE IF NOT EXISTS cs2_ggscore_rankings (
    id              BIGSERIAL PRIMARY KEY,
    team_name       TEXT        NOT NULL,
    ggscore_rank    INTEGER     NOT NULL,
    ggscore_rating  INTEGER     NOT NULL,
    snapshot_date   DATE        NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (team_name, snapshot_date)
);

CREATE INDEX IF NOT EXISTS cs2_ggscore_rankings_team_idx ON cs2_ggscore_rankings (team_name, snapshot_date DESC);

ALTER TABLE cs2_ggscore_rankings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_ggscore_rankings FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS ggscore_rank1     INTEGER,
    ADD COLUMN IF NOT EXISTS ggscore_rank2     INTEGER,
    ADD COLUMN IF NOT EXISTS ggscore_rating1   INTEGER,
    ADD COLUMN IF NOT EXISTS ggscore_rating2   INTEGER;

ALTER TABLE cs2_predictions
    ADD COLUMN IF NOT EXISTS ggscore_rank1     INTEGER,
    ADD COLUMN IF NOT EXISTS ggscore_rank2     INTEGER,
    ADD COLUMN IF NOT EXISTS ggscore_rating1   INTEGER,
    ADD COLUMN IF NOT EXISTS ggscore_rating2   INTEGER;
