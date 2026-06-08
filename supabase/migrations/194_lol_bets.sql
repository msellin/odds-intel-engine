-- LoL bet tracking — records bets placed by superadmin via /admin/lol
-- Written by /api/lol-bets route; read by GET /api/lol-bets

CREATE TABLE IF NOT EXISTS lol_bets (
    id               SERIAL PRIMARY KEY,
    match_id         INTEGER REFERENCES lol_upcoming_matches(id),
    team_name        TEXT        NOT NULL,
    market           TEXT        NOT NULL DEFAULT 'match_winner',  -- match_winner | atleast1map
    odds             FLOAT       NOT NULL,
    stake            FLOAT       NOT NULL DEFAULT 1.0,
    fair_odds        FLOAT,
    threshold_odds   FLOAT,
    result           TEXT,       -- NULL = pending, 'win', 'loss', 'void'
    pnl              FLOAT,
    logged_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE lol_bets ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "service only" ON lol_bets USING (false);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
