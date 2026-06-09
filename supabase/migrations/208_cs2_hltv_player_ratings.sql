-- Live HLTV per-player Rating 3.0, scraped from /player/{id}/{nickname}.
-- Used by scanner to compute current PQ (replacing the Oct 2025 CSV avg).

CREATE TABLE IF NOT EXISTS cs2_hltv_player_ratings (
    hltv_player_id   INTEGER     PRIMARY KEY,
    nickname         TEXT        NOT NULL,
    rating           FLOAT       NOT NULL,                -- Rating 3.0 if available, else 2.x
    fetched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cs2_hltv_player_ratings_nickname_idx
    ON cs2_hltv_player_ratings (LOWER(nickname));

ALTER TABLE cs2_hltv_player_ratings ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_hltv_player_ratings FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
