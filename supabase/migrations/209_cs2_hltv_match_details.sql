-- HLTV match-detail tables. Slow scraper pulls each finished match page
-- and stores per-map results, veto sequence, and per-player match stats.

CREATE TABLE IF NOT EXISTS cs2_hltv_matches (
    hltv_match_id    INTEGER PRIMARY KEY,
    bo3gg_id         INTEGER,            -- joined later via team+date
    event_name       TEXT,
    stage            TEXT,
    match_date       TIMESTAMPTZ,
    team1_name       TEXT,
    team2_name       TEXT,
    score1           INTEGER,
    score2           INTEGER,
    winner_name      TEXT,
    best_of          INTEGER,
    raw_url          TEXT,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cs2_hltv_matches_date_idx ON cs2_hltv_matches (match_date DESC);
CREATE INDEX IF NOT EXISTS cs2_hltv_matches_bo3gg_idx ON cs2_hltv_matches (bo3gg_id);

CREATE TABLE IF NOT EXISTS cs2_hltv_match_maps (
    id               BIGSERIAL PRIMARY KEY,
    hltv_match_id    INTEGER NOT NULL REFERENCES cs2_hltv_matches(hltv_match_id) ON DELETE CASCADE,
    map_order        INTEGER NOT NULL,
    map_name         TEXT NOT NULL,
    team1_score      INTEGER,
    team2_score      INTEGER,
    winner_name      TEXT,
    UNIQUE (hltv_match_id, map_order)
);
CREATE INDEX IF NOT EXISTS cs2_hltv_match_maps_map_idx ON cs2_hltv_match_maps (map_name);

CREATE TABLE IF NOT EXISTS cs2_hltv_match_veto (
    id               BIGSERIAL PRIMARY KEY,
    hltv_match_id    INTEGER NOT NULL REFERENCES cs2_hltv_matches(hltv_match_id) ON DELETE CASCADE,
    step             INTEGER NOT NULL,
    team_name        TEXT NOT NULL,
    action           TEXT NOT NULL,      -- removed | picked | left_over
    map_name         TEXT NOT NULL,
    UNIQUE (hltv_match_id, step)
);

CREATE TABLE IF NOT EXISTS cs2_hltv_player_match_stats (
    id               BIGSERIAL PRIMARY KEY,
    hltv_match_id    INTEGER NOT NULL REFERENCES cs2_hltv_matches(hltv_match_id) ON DELETE CASCADE,
    hltv_player_id   INTEGER,
    nickname         TEXT NOT NULL,
    team_name        TEXT,
    kills            INTEGER,
    deaths           INTEGER,
    adr              FLOAT,
    kast             FLOAT,
    rating           FLOAT,
    UNIQUE (hltv_match_id, hltv_player_id)
);
CREATE INDEX IF NOT EXISTS cs2_hltv_player_match_stats_player_idx
    ON cs2_hltv_player_match_stats (hltv_player_id, hltv_match_id);

-- Tracks the scraper's progress so we can resume/throttle without re-fetching
CREATE TABLE IF NOT EXISTS cs2_hltv_match_queue (
    hltv_match_id    INTEGER PRIMARY KEY,
    slug             TEXT,
    discovered_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_at       TIMESTAMPTZ,
    error            TEXT
);

ALTER TABLE cs2_hltv_matches               ENABLE ROW LEVEL SECURITY;
ALTER TABLE cs2_hltv_match_maps            ENABLE ROW LEVEL SECURITY;
ALTER TABLE cs2_hltv_match_veto            ENABLE ROW LEVEL SECURITY;
ALTER TABLE cs2_hltv_player_match_stats    ENABLE ROW LEVEL SECURITY;
ALTER TABLE cs2_hltv_match_queue           ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_hltv_matches            FOR SELECT USING (true);
  CREATE POLICY "public read" ON cs2_hltv_match_maps         FOR SELECT USING (true);
  CREATE POLICY "public read" ON cs2_hltv_match_veto         FOR SELECT USING (true);
  CREATE POLICY "public read" ON cs2_hltv_player_match_stats FOR SELECT USING (true);
  CREATE POLICY "public read" ON cs2_hltv_match_queue        FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
