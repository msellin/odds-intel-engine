-- CS2 match results — populated by cs2_settlement.py from bo3.gg finished feed.
-- Joins to cs2_predictions(bo3gg_id) for calibration and to cs2_bets(match_id) via cs2_upcoming_matches.

CREATE TABLE IF NOT EXISTS cs2_results (
    bo3gg_id        INTEGER     PRIMARY KEY,
    team1           TEXT        NOT NULL,
    team2           TEXT        NOT NULL,
    kickoff_time    TIMESTAMPTZ,
    best_of         INTEGER,
    winner          TEXT,                                -- 'team1' | 'team2' | NULL (forfeit/draw)
    score1          INTEGER,                             -- maps won by team1
    score2          INTEGER,
    finished_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_status      TEXT                                 -- bo3.gg status (finished/defwin/...)
);

CREATE INDEX IF NOT EXISTS cs2_results_kickoff_idx ON cs2_results (kickoff_time DESC);
CREATE INDEX IF NOT EXISTS cs2_results_teams_idx ON cs2_results (team1, team2);

ALTER TABLE cs2_results ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "public read" ON cs2_results FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
