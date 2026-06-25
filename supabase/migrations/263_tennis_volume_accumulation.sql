-- TENNIS-PAPER-BETS Phase 4 (2026-06-25) — volume accumulation.
--
-- Strategy pivot: stop requiring a Pinnacle reference to log a tennis value-bet
-- row. Coolbet sees hundreds of tennis matches daily (Challenger / ITF / Futures);
-- our scanner currently writes 0 rows for matches without a Pinnacle anchor in
-- tennis_fixtures_today. The Odds API free tier only covers ~3 tour-main
-- tournaments, so most of Coolbet's catalogue was being silently discarded.
--
-- New model: write a row for every Coolbet tennis match. Rows that have a
-- Pinnacle reference get full edge / kelly computation (current behaviour).
-- Rows without one get Coolbet odds stored, NULL Pinnacle fields, and a
-- `fair_source = 'coolbet_only'` tag for the future CSV backfill pass that
-- pulls Pinnacle closes + match results from tennis-data.co.uk weekly dumps.
--
-- Why this matters: more rows → better training data → better future models.
-- CLV / actionable-edge precision can be measured on the Pinnacle-anchored
-- subset; the Coolbet-only subset is observation-only until backfilled.

-- 1. Make Pinnacle-anchored fields nullable
ALTER TABLE tennis_value_bets
    ALTER COLUMN pin_fair_odds DROP NOT NULL;

ALTER TABLE tennis_value_bets
    ALTER COLUMN edge_pct DROP NOT NULL;

-- 2. Add fair_source — describes how `pin_fair_odds` was derived. Helps the
-- backfill job find rows that need a Pinnacle close, and helps analytics
-- separate actionable picks (Pinnacle-anchored) from training-only observations.
ALTER TABLE tennis_value_bets
    ADD COLUMN IF NOT EXISTS fair_source text;

-- 3. Backfill existing rows. Anything written prior to this migration came
-- through either the Odds API scanner (Pinnacle-anchored) or the Coolbet
-- scanner matched to a tennis_fixtures_today entry (still Pinnacle-anchored
-- via the join). Either way: `odds_api_pinnacle`.
UPDATE tennis_value_bets
   SET fair_source = 'odds_api_pinnacle'
 WHERE fair_source IS NULL
   AND pin_fair_odds IS NOT NULL;

-- Any other rows (shouldn't exist pre-migration) get tagged as 'unknown'
-- so the NOT NULL constraint below can fire without rejecting the migration.
UPDATE tennis_value_bets
   SET fair_source = 'unknown'
 WHERE fair_source IS NULL;

ALTER TABLE tennis_value_bets
    ALTER COLUMN fair_source SET NOT NULL;

ALTER TABLE tennis_value_bets
    ALTER COLUMN fair_source SET DEFAULT 'unknown';

CREATE INDEX IF NOT EXISTS tennis_value_bets_fair_source
    ON tennis_value_bets (fair_source);
