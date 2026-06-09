-- Captures each team's CURRENT roster + days_in_team per player.
-- The team page (/team/{id}/{slug}) shows the current 5-player roster with
-- "Days in team" stats. days_in_team lets us detect when a roster is fresh
-- vs stable — fresh rosters invalidate prior team-level stats.

CREATE TABLE IF NOT EXISTS cs2_hltv_team_rosters (
    id              BIGSERIAL PRIMARY KEY,
    hltv_team_id    INTEGER NOT NULL,
    team_name       TEXT    NOT NULL,
    hltv_player_id  INTEGER NOT NULL,
    nickname        TEXT    NOT NULL,
    days_in_team    INTEGER,                            -- snapshot value at fetch time
    role            TEXT,                               -- IGL/AWPer/Coach if HLTV labels it
    maps_played     INTEGER,
    rating_2_0      NUMERIC,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    snapshot_date   DATE    NOT NULL DEFAULT CURRENT_DATE,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hltv_team_id, hltv_player_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS cs2_hltv_team_rosters_team_idx
    ON cs2_hltv_team_rosters (hltv_team_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS cs2_hltv_team_rosters_player_idx
    ON cs2_hltv_team_rosters (hltv_player_id);

ALTER TABLE cs2_hltv_team_rosters ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "public read" ON cs2_hltv_team_rosters FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
