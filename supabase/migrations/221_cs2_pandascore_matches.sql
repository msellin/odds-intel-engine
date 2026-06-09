-- PandaScore match-history backfill table.
-- bo3.gg covers tier-1/2 + a slice of tier-3, but skips many qualifier and
-- amateur matches. The Oxuji incident (2026-06-09) showed our bot pricing
-- a team with "0 matches" in our DB even though they had 5+ recent finished
-- matches accessible via PandaScore.
--
-- Keeping this separate from cs2_results (which is bo3gg-keyed) so we don't
-- disrupt the existing settlement/scanner pipelines. Sneak peeks can UNION
-- both sources to widen recent-form / h2h / per-team-per-map calculations.

CREATE TABLE IF NOT EXISTS cs2_pandascore_matches (
    id              BIGSERIAL PRIMARY KEY,
    pandascore_id   INTEGER UNIQUE NOT NULL,
    team1_id        INTEGER,
    team1_name      TEXT NOT NULL,
    team2_id        INTEGER,
    team2_name      TEXT NOT NULL,
    score1          INTEGER,
    score2          INTEGER,
    winner          TEXT,                                  -- 'team1' | 'team2' | NULL
    winner_id       INTEGER,
    best_of         INTEGER,
    begin_at        TIMESTAMPTZ,                           -- match start
    end_at          TIMESTAMPTZ,                           -- match end
    status          TEXT,                                  -- finished / canceled / postponed
    tournament_name TEXT,
    serie_name      TEXT,
    league_id       INTEGER,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS cs2_pandascore_matches_team1_idx
    ON cs2_pandascore_matches (team1_name, begin_at DESC);
CREATE INDEX IF NOT EXISTS cs2_pandascore_matches_team2_idx
    ON cs2_pandascore_matches (team2_name, begin_at DESC);
CREATE INDEX IF NOT EXISTS cs2_pandascore_matches_begin_idx
    ON cs2_pandascore_matches (begin_at DESC) WHERE status = 'finished';

ALTER TABLE cs2_pandascore_matches ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY "public read" ON cs2_pandascore_matches FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
