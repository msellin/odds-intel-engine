-- LoL ELO scanner output table
-- Written by scripts/esports/lol_elo_scanner.py --record
-- Read by /admin/lol frontend page

CREATE TABLE IF NOT EXISTS lol_upcoming_matches (
    id               SERIAL PRIMARY KEY,
    league           TEXT        NOT NULL,
    kickoff_time     TIMESTAMPTZ NOT NULL,
    state            TEXT        NOT NULL DEFAULT 'unstarted',  -- unstarted | inProgress
    best_of          INTEGER     NOT NULL DEFAULT 3,
    team1            TEXT        NOT NULL,
    team2            TEXT        NOT NULL,
    elo1             FLOAT,
    elo2             FLOAT,
    win_prob1        FLOAT,      -- 0-1
    win_prob2        FLOAT,
    fair_odds1       FLOAT,
    fair_odds2       FLOAT,
    threshold_odds1  FLOAT,      -- fair × (1 - edge)
    threshold_odds2  FLOAT,
    has_elo_history  BOOLEAN     NOT NULL DEFAULT TRUE,
    scanned_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS lol_upcoming_matches_uniq
    ON lol_upcoming_matches (team1, team2, kickoff_time);

-- Public read so the anon key frontend can read it
ALTER TABLE lol_upcoming_matches ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public read" ON lol_upcoming_matches
    FOR SELECT USING (true);
