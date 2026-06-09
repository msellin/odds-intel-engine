-- HLTV team rankings — fetched daily, stored as a time series so we can
-- evaluate the signal on accumulated data.

CREATE TABLE IF NOT EXISTS cs2_hltv_rankings (
    id              BIGSERIAL PRIMARY KEY,
    team_name       TEXT        NOT NULL,
    hltv_rank       INTEGER     NOT NULL,
    hltv_points     INTEGER     NOT NULL,
    players         TEXT[],                              -- current 5-man lineup per HLTV
    snapshot_date   DATE        NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (team_name, snapshot_date)
);

CREATE INDEX IF NOT EXISTS cs2_hltv_rankings_team_idx ON cs2_hltv_rankings (team_name, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS cs2_hltv_rankings_date_idx ON cs2_hltv_rankings (snapshot_date DESC);

ALTER TABLE cs2_hltv_rankings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_hltv_rankings FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Also expose hltv_rank / hltv_points on cs2_upcoming_matches so the admin
-- page can show them next to the model's threshold, and so a future model
-- variant can include them as features.
ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS hltv_rank1      INTEGER,
    ADD COLUMN IF NOT EXISTS hltv_rank2      INTEGER,
    ADD COLUMN IF NOT EXISTS hltv_points1    INTEGER,
    ADD COLUMN IF NOT EXISTS hltv_points2    INTEGER;

ALTER TABLE cs2_predictions
    ADD COLUMN IF NOT EXISTS hltv_rank1      INTEGER,
    ADD COLUMN IF NOT EXISTS hltv_rank2      INTEGER,
    ADD COLUMN IF NOT EXISTS hltv_points1    INTEGER,
    ADD COLUMN IF NOT EXISTS hltv_points2    INTEGER;
