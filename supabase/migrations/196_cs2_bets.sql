-- CS2 bet tracking — records bets placed by superadmin via /admin/cs2
CREATE TABLE IF NOT EXISTS cs2_bets (
    id               SERIAL PRIMARY KEY,
    match_id         INTEGER REFERENCES cs2_upcoming_matches(id),
    team_name        TEXT        NOT NULL,
    market           TEXT        NOT NULL DEFAULT 'match_winner',
    odds             FLOAT       NOT NULL,
    stake            FLOAT       NOT NULL DEFAULT 1.0,
    fair_odds        FLOAT,
    threshold_odds   FLOAT,
    result           TEXT,
    pnl              FLOAT,
    logged_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE cs2_bets ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service only" ON cs2_bets USING (false);
