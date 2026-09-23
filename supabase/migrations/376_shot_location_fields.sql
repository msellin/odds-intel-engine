-- 376_shot_location_fields.sql
--
-- SHOT-LOCATION-FIELDS-DISCARDED ([[#078]]), 2026-09-23.
--
-- WHAT THIS ADDS AND WHY IT IS NOT COSMETIC
-- =========================================
-- Every `/fixtures/statistics` response we already pay for, already request and
-- already parse carries `Shots insidebox` and `Shots outsidebox`. We stored
-- NEITHER -- a grep for `insidebox` across the whole repository returned zero
-- hits before this change. `goals_prevented` (post-shot xG on the keeper's
-- side) was likewise served and discarded.
--
-- WHY IT MATTERS. A shot from inside the box converts several times more often
-- than one from outside, so "12 shots" is a much weaker statement than "5
-- inside, 7 outside". This is the crudest useful proxy for shot QUALITY and it
-- is the ONLY one obtainable from this endpoint: real xG needs per-shot
-- COORDINATES, which `/fixtures/statistics` does not carry at any plan level,
-- and `/fixtures/events` does not either (goals, cards and substitutions only --
-- not every shot).
--
-- ⚠️ SO NOTHING BUILT ON THESE COLUMNS MAY BE CALLED xG. It is a binned
-- shot-quality index -- location x outcome instead of an exact coordinate. The
-- distinction is the difference between a number that survives scrutiny and one
-- that does not, which is the bar the 👥 PICKS direction is judged on.
--
-- THE URGENCY IS NOT THE MODEL -- IT IS THE HISTORY
-- =================================================
-- API-Football has been WITHDRAWING `expected_goals` from leagues that used to
-- have it. Measured on our own rows: daily xG went from 109/156 (2026-08-30) to
-- 0-1/day across 2026-09-04..08, with a partial recovery around 09-19, while
-- stats-row volume held steady. Probed live 2026-09-23: an MLS fixture still
-- returns `expected_goals`; Argentine Liga Profesional fixture 1493144 returns
-- NO such field, on a league that carried 109 xG matches in the preceding 90
-- days. That is a supplier coverage change, not a parse bug.
--
-- The inside/outside split is present on exactly those fixtures. 30,446 of our
-- 56,463 stats rows have no xG at all; nearly all of them can carry this
-- instead. Every day these columns do not exist is a day of shot-location data
-- we cannot recover for fixtures the supplier later drops.
--
-- A SECOND, LARGER FINDING MADE WHILE BUILDING THE BACKFILL
-- ==========================================================
-- `get_fixture_statistics` has always sent `half=true`. That parameter does NOT
-- degrade to full-match-only when AF lacks half splits -- it returns
-- `results: 0`, so the caller receives an EMPTY LIST and the fixture looks like
-- it has no statistics at all. Sampling one fixture per year from our own
-- ledger, plain call vs half=true:
--
--     2018  2 vs 0      2022  2 vs 0
--     2019  2 vs 0      2023  2 vs 0
--     2020  2 vs 0      2024  2 vs 2
--     2021  2 vs 2      2025  2 vs 2
--                       2026  2 vs 2
--
-- Per-fixture rather than a clean cutoff (2021 works, 2022 does not). Recent
-- fixtures are unaffected, so the LIVE pipeline was never losing rows; what was
-- lost is REACH INTO HISTORY, silently, for every caller. The wrapper now falls
-- back to a plain call when the half call comes back empty, which costs one
-- extra request only where we previously got nothing -- and immediately
-- recovered a 2018 fixture returning 11/3 and 4/2 inside/outside shots.
--
-- All columns are NULLable with no default and no backfill here: a migration
-- that rewrites 56k rows is a separate, re-runnable script
-- (`scripts/backfill_shot_location.py`), because it costs API quota and must be
-- resumable.

ALTER TABLE match_stats
    ADD COLUMN IF NOT EXISTS shots_insidebox_home     integer,
    ADD COLUMN IF NOT EXISTS shots_insidebox_away     integer,
    ADD COLUMN IF NOT EXISTS shots_outsidebox_home    integer,
    ADD COLUMN IF NOT EXISTS shots_outsidebox_away    integer,
    ADD COLUMN IF NOT EXISTS goals_prevented_home     numeric,
    ADD COLUMN IF NOT EXISTS goals_prevented_away     numeric,
    ADD COLUMN IF NOT EXISTS shots_insidebox_home_ht  integer,
    ADD COLUMN IF NOT EXISTS shots_insidebox_away_ht  integer,
    ADD COLUMN IF NOT EXISTS shots_outsidebox_home_ht integer,
    ADD COLUMN IF NOT EXISTS shots_outsidebox_away_ht integer;

COMMENT ON COLUMN match_stats.shots_insidebox_home IS
  'Shots taken from inside the penalty area (API-Football "Shots insidebox"). '
  'Added by migration 376 ([[#078]]) after being requested and discarded since '
  'the feed was built. With shots_outsidebox_* this is the only shot-QUALITY '
  'signal available from /fixtures/statistics -- real xG needs per-shot '
  'coordinates, which no endpoint on this plan provides. Anything derived from '
  'these columns is a shot-quality index and must NOT be presented as xG.';

COMMENT ON COLUMN match_stats.goals_prevented_home IS
  'Post-shot expected goals on the KEEPER''s side -- quality of shots faced '
  'minus goals actually conceded. Served by API-Football alongside '
  'expected_goals and discarded until migration 376 ([[#078]]).';
