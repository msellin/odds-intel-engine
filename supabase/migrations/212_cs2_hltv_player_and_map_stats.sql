-- HLTV authenticated stats — per-player + per-map metadata.
-- Generic JSONB storage so we capture all stats-row entries without locking
-- the schema. Parser extracts labels + values into a flat key→value dict.

CREATE TABLE IF NOT EXISTS cs2_hltv_player_stats (
    hltv_player_id   INTEGER     PRIMARY KEY,
    nickname         TEXT        NOT NULL,
    stats            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cs2_hltv_player_stats_nick_idx
    ON cs2_hltv_player_stats (LOWER(nickname));

CREATE TABLE IF NOT EXISTS cs2_hltv_map_meta (
    hltv_map_id      INTEGER     PRIMARY KEY,
    map_name         TEXT        NOT NULL,
    stats            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cs2_hltv_player_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE cs2_hltv_map_meta     ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_hltv_player_stats FOR SELECT USING (true);
  CREATE POLICY "public read" ON cs2_hltv_map_meta     FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
