-- SHADOW-VIEW-COLUMN-DRIFT-2026-09-03
--
-- `shadow_bets_unique` is the canonical read path for shadow aggregates
-- (ANALYSIS_GOTCHAS #5: "always the view, never the base table"). A Postgres
-- view fixes its column list at creation time, so every ALTER TABLE that adds
-- a column to shadow_bets leaves the view a column short -- silently, because
-- selecting a missing column fails only at the point somebody tries.
--
-- It has now happened twice without being noticed:
--   pair_gap_hours     added by migration 289
--   odds_at_pick_live  added by migration 291
--
-- The second one bit immediately: an analysis of how the STALE-BEST-ODDS fix
-- would affect pick volume had to fall back to an inline DISTINCT ON against
-- the base table, which is exactly the thing gotcha #5 tells you not to do.
--
-- Recreated with the full column list. The accompanying smoke test compares
-- the view's columns against the base table's, so the next ALTER TABLE that
-- forgets this fails in CI rather than months later in someone's query.

DROP VIEW IF EXISTS shadow_bets_unique;

CREATE VIEW shadow_bets_unique AS
SELECT DISTINCT ON (sb.bot_id, sb.match_id, sb.market, sb.selection)
       sb.*
  FROM shadow_bets sb
 ORDER BY sb.bot_id, sb.match_id, sb.market, sb.selection, sb.pick_time ASC;

COMMENT ON VIEW shadow_bets_unique IS
  'One row per (bot, match, market, selection) — earliest pick_time. The '
  'canonical read path for shadow aggregates (ANALYSIS_GOTCHAS #5); querying '
  'shadow_bets directly multiplies every pick by its cohort re-recordings. '
  'NOTE: a view freezes its column list at creation, so any ALTER TABLE on '
  'shadow_bets must recreate this view — smoke SHADOW-VIEW-COLUMN-DRIFT '
  'enforces that.';

GRANT SELECT ON shadow_bets_unique TO anon, authenticated, service_role;

NOTIFY pgrst, 'reload schema';
