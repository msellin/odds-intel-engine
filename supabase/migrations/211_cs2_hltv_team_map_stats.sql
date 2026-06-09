-- HLTV per-team-per-map stats, scraped from /stats/teams/maps/{id}/{slug}
-- via authenticated Cloudflare cookies (HLTV_AUTH_COOKIES env var).

CREATE TABLE IF NOT EXISTS cs2_hltv_team_map_stats (
    id                          BIGSERIAL PRIMARY KEY,
    hltv_team_id                INTEGER     NOT NULL,
    team_name                   TEXT        NOT NULL,
    map_name                    TEXT        NOT NULL,
    wins                        INTEGER,
    draws                       INTEGER,
    losses                      INTEGER,
    win_pct                     FLOAT,
    total_rounds                INTEGER,
    round_win_pct_after_first_kill   FLOAT,
    round_win_pct_after_first_death  FLOAT,
    pick_pct                    FLOAT,
    ban_pct                     FLOAT,
    snapshot_date               DATE        NOT NULL DEFAULT CURRENT_DATE,
    fetched_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hltv_team_id, map_name, snapshot_date)
);

CREATE INDEX IF NOT EXISTS cs2_hltv_team_map_stats_team_idx
    ON cs2_hltv_team_map_stats (hltv_team_id, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS cs2_hltv_team_map_stats_map_idx
    ON cs2_hltv_team_map_stats (map_name, snapshot_date DESC);

ALTER TABLE cs2_hltv_team_map_stats ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_hltv_team_map_stats FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
