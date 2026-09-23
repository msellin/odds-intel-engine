-- 377_shot_location_fields_part2.sql
--
-- SHOT-LOCATION-FIELDS, PART 2 ([[#078]]), 2026-09-23.
--
-- WHY THIS EXISTS AS A SEPARATE FILE — A MISTAKE WORTH NAMING
-- ===========================================================
-- Migration 376 shipped, `migrate.yml` applied it, and `_schema_migrations`
-- recorded it. I then EDITED 376 IN PLACE to add three more column pairs
-- (`free_kicks`, `shots_off_target`, `pass_pct`) after the owner rightly
-- objected to my skipping fields we could have.
--
-- An applied migration is IMMUTABLE. The runner keys on filename, so an already
-- recorded file is never re-run and every edit to it is silently ignored — the
-- columns simply never appeared, and the failure surfaced only when the backfill
-- ran and hit `column s.shots_off_target_home does not exist`. Nothing in CI
-- caught it, because CI saw a migration that had already succeeded.
--
-- Edits to applied migrations are invisible, not applied. New columns get a new
-- file, always.
--
-- The six columns below are exactly the ones 376's later edit claimed to add.
-- The ten it genuinely created (insidebox/outsidebox full + half-time, and
-- goals_prevented) are already live and are NOT repeated here.

ALTER TABLE match_stats
    -- Set-piece VOLUME. Present on 40/50 sampled team-rows (80%) and derivable
    -- from nothing else we hold.
    ADD COLUMN IF NOT EXISTS free_kicks_home       integer,
    ADD COLUMN IF NOT EXISTS free_kicks_away       integer,
    -- Stored after being wrongly skipped as "exactly derivable". The identity
    -- `Total Shots = on + off + blocked` holds on 74 of 78 team-rows -- and on
    -- 4 of 78 (5%) `Shots off Goal` is PRESENT while one of its three inputs is
    -- NULL, so there the field is the ONLY source and the derivation silently
    -- yields nothing. A derivation equals a stored field only when every input
    -- is guaranteed present, and API-Football omits fields per fixture.
    ADD COLUMN IF NOT EXISTS shots_off_target_home integer,
    ADD COLUMN IF NOT EXISTS shots_off_target_away integer,
    -- AF's "Passes %" -- the actual percentage, unlike `pass_accuracy_*`, which
    -- holds a COUNT despite its name (see the comment on that column).
    ADD COLUMN IF NOT EXISTS pass_pct_home         integer,
    ADD COLUMN IF NOT EXISTS pass_pct_away         integer;

COMMENT ON COLUMN match_stats.shots_off_target_home IS
  'API-Football "Shots off Goal". Usually equals Total - on - blocked, but on ~5% '
  'of team-rows one of those inputs is NULL and this is the only source. Stored '
  'rather than derived for exactly that reason (migration 377, [[#078]]).';

COMMENT ON COLUMN match_stats.pass_pct_home IS
  'API-Football "Passes %" -- a true percentage (0-100). Distinct from '
  'pass_accuracy_home, which despite its name holds a COUNT of accurate passes.';
