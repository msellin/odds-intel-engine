-- CS2-LEETIFY — Leetify API ingestion: player-ID bridge + per-player-per-match
-- stats covering 66 demo-derived fields. First independent signal source after
-- the HLTV-detail v10-v19 feature space turned out empty.
--
-- Source: https://api-public.cs-prod.leetify.com
--   Endpoints used:
--     GET /v3/profile?steam64_id=X            — profile + recent matches/teammates
--     GET /v3/profile/matches?steam64_id=X    — last 100 matches per Steam64
--     GET /v2/matches/{leetify_id}            — match details by UUID
--     GET /v2/matches/hltv/{full_filename}    — match details by HLTV filename
--
-- Cross-references HLTV match IDs natively (data_source='hltv',
-- data_source_match_id like "2394212-bc-game-vs-pain-m2-anubis.dem"), so the
-- bridge to v8's production universe is direct.

-- Player ID bridge: HLTV ↔ Steam64. Populated incrementally as we ingest.
CREATE TABLE IF NOT EXISTS cs2_player_id_bridge (
  hltv_player_id  BIGINT,
  steam64_id      TEXT NOT NULL,
  nickname        TEXT,
  confidence      NUMERIC DEFAULT 1.0,
  joined_by       TEXT,   -- 'leetify_profile' | 'manual' | 'name_match'
  created_at      TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (steam64_id)
);
CREATE INDEX IF NOT EXISTS cs2_player_id_bridge_hltv_idx
  ON cs2_player_id_bridge (hltv_player_id);
CREATE INDEX IF NOT EXISTS cs2_player_id_bridge_nickname_idx
  ON cs2_player_id_bridge (LOWER(nickname));

ALTER TABLE cs2_player_id_bridge ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_player_id_bridge FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- Per-player-per-match Leetify stats.
-- High-value columns are first-class for SQL aggregation; raw_stats keeps the
-- full 66-field payload so we can mine without re-scraping.
CREATE TABLE IF NOT EXISTS cs2_leetify_player_match_stats (
  leetify_match_id    UUID NOT NULL,
  hltv_match_id       BIGINT,                      -- parsed from data_source_match_id when data_source='hltv'
  data_source         TEXT NOT NULL,               -- 'hltv', 'faceit', 'matchmaking', etc
  data_source_match_id TEXT,
  map_name            TEXT,
  finished_at         TIMESTAMPTZ,
  steam64_id          TEXT NOT NULL,
  nickname            TEXT,
  team_number         INTEGER,                     -- usually 2 or 3
  -- 66 stat fields; capture the high-value subset, store rest as JSONB
  leetify_rating      NUMERIC,
  ct_leetify_rating   NUMERIC,
  t_leetify_rating    NUMERIC,
  preaim              NUMERIC,
  reaction_time       NUMERIC,
  accuracy            NUMERIC,
  accuracy_head       NUMERIC,
  spray_accuracy      NUMERIC,
  counter_strafing_good_shots_ratio NUMERIC,
  trade_kill_attempts_percentage    NUMERIC,
  trade_kills_success_percentage    NUMERIC,
  trade_kill_opportunities_per_round NUMERIC,
  traded_deaths_success_percentage  NUMERIC,
  multi1k INTEGER, multi2k INTEGER, multi3k INTEGER, multi4k INTEGER, multi5k INTEGER,
  flashbang_thrown      INTEGER,
  flashbang_hit_foe     INTEGER,
  flashbang_leading_to_kill INTEGER,
  he_thrown             INTEGER,
  molotov_thrown        INTEGER,
  smoke_thrown          INTEGER,
  utility_on_death_avg  NUMERIC,
  total_kills           INTEGER,
  total_deaths          INTEGER,
  total_assists         INTEGER,
  total_damage          INTEGER,
  rounds_count          INTEGER,
  rounds_won            INTEGER,
  rounds_survived       INTEGER,
  kd_ratio              NUMERIC,
  dpr                   NUMERIC,
  mvps                  INTEGER,
  raw_stats             JSONB,                     -- full 66-field payload for future use
  scraped_at            TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (leetify_match_id, steam64_id)
);
CREATE INDEX IF NOT EXISTS cs2_leetify_pms_hltv_idx
  ON cs2_leetify_player_match_stats (hltv_match_id);
CREATE INDEX IF NOT EXISTS cs2_leetify_pms_steam_idx
  ON cs2_leetify_player_match_stats (steam64_id);
CREATE INDEX IF NOT EXISTS cs2_leetify_pms_finished_idx
  ON cs2_leetify_player_match_stats (finished_at);
CREATE INDEX IF NOT EXISTS cs2_leetify_pms_source_idx
  ON cs2_leetify_player_match_stats (data_source);

ALTER TABLE cs2_leetify_player_match_stats ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_leetify_player_match_stats FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
