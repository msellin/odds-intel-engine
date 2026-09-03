-- TEAM-SCORING-RATES-OWN-RESULTS-2026-09-03
--
-- Rolling team scoring rates are computed by looking up, for each fixture, every
-- prior match either team played in the trailing 365 days. `matches` has
-- separate indexes on home_team_id and away_team_id but neither carries `date`,
-- so each lookup degrades to a heap scan over the team's entire history and the
-- backfill across ~39k feature rows becomes unusable.
--
-- Partial on `score_home IS NOT NULL` because the lookup only ever considers
-- played matches; that keeps these indexes off the ~60k unplayed fixtures.

CREATE INDEX IF NOT EXISTS idx_matches_home_team_date
    ON matches (home_team_id, date)
    WHERE score_home IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_matches_away_team_date
    ON matches (away_team_id, date)
    WHERE score_home IS NOT NULL;
