-- TEAMS-LEAGUE-ID-BROKEN-2026-09-03
--
-- `teams.league_id` never equals the fixture's `matches.league_id`: zero
-- matches out of 27,605 over 90 days. That is not corruption -- it is what the
-- column has always meant, and the name is the problem.
--
-- `supabase_client.ensure_team()` creates a team with
--     ensure_league(f"{country} / Unknown", tier=0)
-- so every team is assigned a per-COUNTRY placeholder league. All 11,633 teams
-- point at one of 160 rows named 'Unknown'; exactly zero point at a named
-- league. The column is a byproduct of team creation, not a fact about where
-- the team plays.
--
-- It has no real consumers. `load_db_teams()` -- the one function that looks
-- like one -- correctly derives league membership from `matches`. So nothing
-- is broken today; the risk is entirely that someone reads the column name and
-- believes it. That already happened: the cross-tier hypothesis in
-- SWEEP-HOME-BOTS-CALIBRATION could not be tested as its ticket described,
-- because it assumed this column meant what it says.
--
-- Documented rather than dropped or repointed:
--   * Dropping it would change the insert path in ensure_team for no gain.
--   * Repointing it to "the team's league" is not well defined -- a team plays
--     in a domestic league, cups, and sometimes continental competition, so
--     there is no single correct value. That ambiguity is probably why it was
--     given a placeholder in the first place.
--   * The correct source for a fixture's league and tier is
--     matches.league_id -> leagues, which is populated and accurate.

COMMENT ON COLUMN teams.league_id IS
  'NOT the team''s league. A per-country placeholder league created by '
  'ensure_team() as "<country> / Unknown" (tier 0). All teams point at one of '
  '~160 such rows and none at a named league, so joining on it to get a '
  'league or tier returns placeholder data. Use matches.league_id -> leagues '
  'instead. TEAMS-LEAGUE-ID-BROKEN-2026-09-03.';
