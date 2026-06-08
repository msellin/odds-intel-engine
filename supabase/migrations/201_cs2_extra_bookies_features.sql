-- Additional CS2 columns for multi-book odds + new model features.
-- coolbet_odds  : scraped every ~30min by cs2_coolbet_scanner
-- pinnacle_odds : reserved for future Pinnacle public-feed scanner
-- is_lan        : derived from tournament name (LAN events behave differently)
-- days_since_roster_change : derived from bo3.gg /player_transfers (chemistry proxy)

ALTER TABLE cs2_upcoming_matches
    ADD COLUMN IF NOT EXISTS coolbet_odds1                FLOAT,
    ADD COLUMN IF NOT EXISTS coolbet_odds2                FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_odds1               FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_odds2               FLOAT,
    ADD COLUMN IF NOT EXISTS is_lan                       BOOLEAN,
    ADD COLUMN IF NOT EXISTS days_since_roster_change1    INTEGER,
    ADD COLUMN IF NOT EXISTS days_since_roster_change2    INTEGER;

-- Mirror columns on the prediction history table so retraining can include them
ALTER TABLE cs2_predictions
    ADD COLUMN IF NOT EXISTS coolbet_odds1                FLOAT,
    ADD COLUMN IF NOT EXISTS coolbet_odds2                FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_odds1               FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_odds2               FLOAT,
    ADD COLUMN IF NOT EXISTS is_lan                       BOOLEAN,
    ADD COLUMN IF NOT EXISTS days_since_roster_change1    INTEGER,
    ADD COLUMN IF NOT EXISTS days_since_roster_change2    INTEGER;

-- CS2 single-bot simulated_bets — same shape as soccer bot's tracking
CREATE TABLE IF NOT EXISTS cs2_simulated_bets (
    id               BIGSERIAL PRIMARY KEY,
    bot_name         TEXT        NOT NULL DEFAULT 'bot_cs2_value_v1',
    bo3gg_id         INTEGER     NOT NULL,
    placed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kickoff_time     TIMESTAMPTZ NOT NULL,
    team1            TEXT        NOT NULL,
    team2            TEXT        NOT NULL,
    market           TEXT        NOT NULL,                   -- match_winner | atleast1map
    pick             TEXT        NOT NULL,                   -- which team
    bookie           TEXT        NOT NULL,                   -- bo3gg | coolbet | pinnacle
    odds_at_pick     FLOAT       NOT NULL,
    fair_odds        FLOAT       NOT NULL,
    threshold_odds   FLOAT       NOT NULL,
    edge             FLOAT       NOT NULL,                   -- (odds-threshold)/threshold
    stake            FLOAT       NOT NULL DEFAULT 1.0,
    result           TEXT,                                   -- won | lost | void
    pnl              FLOAT,
    settled_at       TIMESTAMPTZ,
    -- One bet per (bot, match, market, bookie). New bookies on same match are
    -- separate bets — we'd never replay an already-priced opportunity.
    UNIQUE (bot_name, bo3gg_id, market, bookie)
);

CREATE INDEX IF NOT EXISTS cs2_simulated_bets_kickoff_idx ON cs2_simulated_bets (kickoff_time DESC);
CREATE INDEX IF NOT EXISTS cs2_simulated_bets_settle_idx  ON cs2_simulated_bets (result, placed_at DESC);

ALTER TABLE cs2_simulated_bets ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_simulated_bets FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
