-- Per-team pistol round stats.
-- Mechanism (research): 70-80% pistol→match correlation in pro Bo1.
-- Pistol win → $3,250 anti-eco → bonus round → 3-0 start = ~$15k economy lead.
-- Estimated AUC lift: +0.010-0.015 (partly orthogonal to overall rating).
--
-- Source: HLTV /stats/teams/pistols (auth required)

CREATE TABLE IF NOT EXISTS cs2_team_pistol_stats (
    id                  BIGSERIAL PRIMARY KEY,
    hltv_team_id        INTEGER,
    team_name           TEXT NOT NULL,
    -- Overall pistol stats (CT + T combined)
    pistols_played      INTEGER,
    pistols_won         INTEGER,
    pistol_win_pct      NUMERIC,           -- 0-100
    -- CT-side pistol stats (from ?side=COUNTER_TERRORIST)
    ct_pistols_played   INTEGER,
    ct_pistols_won      INTEGER,
    ct_pistol_win_pct   NUMERIC,
    -- T-side pistol stats (from ?side=TERRORIST)
    t_pistols_played    INTEGER,
    t_pistols_won       INTEGER,
    t_pistol_win_pct    NUMERIC,
    rounds_played       INTEGER,
    snapshot_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hltv_team_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS cs2_team_pistol_stats_team_idx
    ON cs2_team_pistol_stats (team_name, snapshot_date DESC);

ALTER TABLE cs2_team_pistol_stats ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "public read" ON cs2_team_pistol_stats FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
