-- egamersworld CS2 team ranking — 4th oracle alongside HLTV / GGScore / ELO.
-- Manual snapshot pattern (egamersworld doesn't expose a clean API).
-- game-tournaments.com uses GGScore's feed (verified: identical numbers),
-- so we treat them as the same source.

CREATE TABLE IF NOT EXISTS cs2_egamersworld_rankings (
    id              BIGSERIAL PRIMARY KEY,
    team_name       TEXT        NOT NULL,
    egw_rank        INTEGER     NOT NULL,
    egw_rating      INTEGER     NOT NULL,
    snapshot_date   DATE        NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (team_name, snapshot_date)
);

CREATE INDEX IF NOT EXISTS cs2_egw_rankings_team_idx ON cs2_egamersworld_rankings (team_name, snapshot_date DESC);

ALTER TABLE cs2_egamersworld_rankings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_egamersworld_rankings FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS egw_rank1       INTEGER,
    ADD COLUMN IF NOT EXISTS egw_rank2       INTEGER,
    ADD COLUMN IF NOT EXISTS egw_rating1     INTEGER,
    ADD COLUMN IF NOT EXISTS egw_rating2     INTEGER;

ALTER TABLE cs2_predictions
    ADD COLUMN IF NOT EXISTS egw_rank1       INTEGER,
    ADD COLUMN IF NOT EXISTS egw_rank2       INTEGER,
    ADD COLUMN IF NOT EXISTS egw_rating1     INTEGER,
    ADD COLUMN IF NOT EXISTS egw_rating2     INTEGER;
