-- CS2 ELO scanner output table
-- Written by scripts/esports/cs2_elo_scanner.py --record
-- Structured identically to lol_upcoming_matches for frontend reuse

CREATE TABLE IF NOT EXISTS cs2_upcoming_matches (
    id               SERIAL PRIMARY KEY,
    bo3gg_id         INTEGER,                  -- bo3.gg match ID
    league           TEXT        NOT NULL,
    kickoff_time     TIMESTAMPTZ NOT NULL,
    state            TEXT        NOT NULL DEFAULT 'unstarted',
    best_of          INTEGER     NOT NULL DEFAULT 3,
    team1            TEXT        NOT NULL,
    team2            TEXT        NOT NULL,
    elo1             FLOAT,
    elo2             FLOAT,
    win_prob1        FLOAT,
    win_prob2        FLOAT,
    fair_odds1       FLOAT,
    fair_odds2       FLOAT,
    threshold_odds1  FLOAT,
    threshold_odds2  FLOAT,
    has_elo_history  BOOLEAN     NOT NULL DEFAULT TRUE,
    fair_odds_map1   FLOAT,      -- wins ≥1 map (BO3/5 only)
    fair_odds_map2   FLOAT,
    threshold_map1   FLOAT,
    threshold_map2   FLOAT,
    bookie_odds1     FLOAT,      -- bo3.gg bookmaker reference odds
    bookie_odds2     FLOAT,
    scanned_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS cs2_upcoming_matches_uniq
    ON cs2_upcoming_matches (team1, team2, kickoff_time);

ALTER TABLE cs2_upcoming_matches ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_upcoming_matches FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
