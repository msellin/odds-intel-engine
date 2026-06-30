-- CS2-MAP-STATS-EXPAND (2026-06-30): computed per-team-per-map win% from
-- cs2_hltv_match_maps history. Covers 2000+ teams vs 248 from the scraped
-- authenticated table. Used as fallback in load_map_winrate_map() when
-- cs2_hltv_team_map_stats doesn't have data for a team.

CREATE TABLE IF NOT EXISTS cs2_computed_team_map_stats (
    id              BIGSERIAL PRIMARY KEY,
    team_name       TEXT    NOT NULL,
    map_name        TEXT    NOT NULL,
    win_pct         FLOAT   NOT NULL,
    maps_played     INTEGER NOT NULL,
    computed_date   DATE    NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (team_name, map_name, computed_date)
);

CREATE INDEX IF NOT EXISTS cs2_computed_team_map_stats_team_idx
    ON cs2_computed_team_map_stats (lower(team_name), computed_date DESC);

ALTER TABLE cs2_computed_team_map_stats ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_computed_team_map_stats FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
