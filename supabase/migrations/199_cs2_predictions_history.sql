-- CS2 prediction history — append-only, one row per (match, scan)
-- Calibration + retraining input. Joins to cs2_results on bo3gg_id.

CREATE TABLE IF NOT EXISTS cs2_predictions (
    id              BIGSERIAL PRIMARY KEY,
    bo3gg_id        INTEGER     NOT NULL,
    scan_time       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kickoff_time    TIMESTAMPTZ NOT NULL,
    league          TEXT,
    best_of         INTEGER,
    team1           TEXT        NOT NULL,
    team2           TEXT        NOT NULL,
    elo1            FLOAT,
    elo2            FLOAT,
    pq1             FLOAT,
    pq2             FLOAT,
    win_prob1       FLOAT,
    win_prob2       FLOAT,
    fair_odds1      FLOAT,
    fair_odds2      FLOAT,
    bookie_odds1    FLOAT,
    bookie_odds2    FLOAT,
    roster_change1  BOOLEAN,
    roster_change2  BOOLEAN,
    model_version   TEXT        NOT NULL DEFAULT 'elo+pq_v1',
    UNIQUE (bo3gg_id, scan_time)
);

CREATE INDEX IF NOT EXISTS cs2_predictions_bo3gg_idx ON cs2_predictions (bo3gg_id);
CREATE INDEX IF NOT EXISTS cs2_predictions_kickoff_idx ON cs2_predictions (kickoff_time DESC);
CREATE INDEX IF NOT EXISTS cs2_predictions_model_idx ON cs2_predictions (model_version, scan_time DESC);

ALTER TABLE cs2_predictions ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_predictions FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
