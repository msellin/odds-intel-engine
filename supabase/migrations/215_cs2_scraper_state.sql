-- Centralized scraper progress + health tracking.
-- Each row = one scraper. Updated by the scraper itself at start + end of run.

CREATE TABLE IF NOT EXISTS cs2_scraper_state (
    scraper_name    TEXT        PRIMARY KEY,
    description     TEXT,
    status          TEXT        NOT NULL DEFAULT 'idle', -- idle | running | error
    last_run_at     TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error      TEXT,
    items_total     INTEGER     DEFAULT 0,
    items_done      INTEGER     DEFAULT 0,
    items_failed    INTEGER     DEFAULT 0,
    items_pending   INTEGER     DEFAULT 0,
    items_stale     INTEGER     DEFAULT 0,  -- needing refresh (>N days old)
    last_run_duration_s NUMERIC,
    notes           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cs2_scraper_state_updated_idx
    ON cs2_scraper_state (updated_at DESC);

-- RLS — public read so the admin UI can show it without service role.
ALTER TABLE cs2_scraper_state ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "public read" ON cs2_scraper_state FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Seed rows so the UI shows all scrapers even before first run.
INSERT INTO cs2_scraper_state (scraper_name, description) VALUES
    ('match_details_queue',     'Walks HLTV /results pages, queues match IDs'),
    ('match_details_process',   'Fetches + parses /matches/{id} pages'),
    ('team_map_stats',          'Per-team-per-map career win rates'),
    ('player_stats',            'Per-player career stats (rating, K/D, ADR, KAST)'),
    ('player_ratings',          'HLTV Rating 2.1 archive scrape'),
    ('map_meta',                'Per-map overall stats (Mirage, Inferno, etc.)'),
    ('hltv_rankings',           'HLTV top-30 weekly ranking snapshot'),
    ('clv_snapshot',            'Closing-odds snapshot for pending bets')
ON CONFLICT (scraper_name) DO NOTHING;
