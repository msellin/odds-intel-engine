-- CS2-HLTV-TEAM-PISTOLS — per-team-per-map pistol-round splits from HLTV.
--
-- Existing cs2_hltv_team_stats has aggregate ct_pistol_pct / t_pistol_pct
-- BLENDED across all maps. v8's pistol_diff feature uses that aggregate. This
-- table adds per-map granularity (Mirage CT pistol, Inferno T pistol, etc.)
-- so v18 can compute pistol diffs specific to the upcoming match's main map
-- and capture broader per-map pistol skill differences that the aggregate
-- washes out.
--
-- Source: HLTV /stats/teams/pistols?startDate=&endDate=&maps=de_X[&side=...]
-- (bulk per-map page with team links — one fetch per (map, side) yields
--  ~100 teams). Scraper iterates 9 maps × 3 sides (overall + CT + T) = 27
-- requests to cover top-100 teams.
--
-- Re-runnable: ON CONFLICT DO UPDATE keyed on
-- (hltv_team_id, map_name, period_start, period_end).

CREATE TABLE IF NOT EXISTS cs2_hltv_team_pistols (
    hltv_team_id    BIGINT      NOT NULL,
    team_name       TEXT        NOT NULL,
    map_name        TEXT        NOT NULL,
    period_start    DATE        NOT NULL,
    period_end      DATE        NOT NULL,

    -- Overall pistol round counts on this map in this period.
    ct_pistol_won   INTEGER,
    ct_pistol_total INTEGER,
    t_pistol_won    INTEGER,
    t_pistol_total  INTEGER,

    -- Convenience percentages (also computed from won/total when available).
    ct_pistol_pct   NUMERIC(6,3),
    t_pistol_pct    NUMERIC(6,3),

    -- Maps played sample size (from /stats/teams/pistols table column "Maps").
    maps_played     INTEGER,

    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (hltv_team_id, map_name, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS cs2_hltv_team_pistols_team_idx
    ON cs2_hltv_team_pistols (hltv_team_id);
CREATE INDEX IF NOT EXISTS cs2_hltv_team_pistols_team_name_idx
    ON cs2_hltv_team_pistols (team_name, map_name);
CREATE INDEX IF NOT EXISTS cs2_hltv_team_pistols_period_idx
    ON cs2_hltv_team_pistols (period_end DESC);

ALTER TABLE cs2_hltv_team_pistols ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_hltv_team_pistols FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
