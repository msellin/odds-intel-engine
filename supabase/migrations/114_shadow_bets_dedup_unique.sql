-- SHADOW-DEDUP (2026-05-20): Railway rolling deploys briefly run two scheduler
-- instances simultaneously. Both fire the same shadow run window and both call
-- bulk_store_shadow_bets, producing duplicate rows with different shadow_run_ids
-- but identical (shadow_cohort, bot_id, match_id, market, selection).
--
-- The existing uq_shadow_bet_per_run constraint is scoped to shadow_run_id so
-- it doesn't catch cross-run duplicates.  Fix:
--   1. Remove existing duplicates (keep earliest pick_time).
--   2. Drop the run-scoped unique index.
--   3. Add a cohort-scoped unique index — the real business key.

-- 1. Remove duplicates, keeping earliest pick_time per cohort slot.
DELETE FROM shadow_bets
WHERE id IN (
    SELECT id
    FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY shadow_cohort, bot_id, match_id, market, selection
                   ORDER BY pick_time, id
               ) AS rn
        FROM shadow_bets
    ) ranked
    WHERE rn > 1
);

-- 2. Drop the run-scoped unique constraint (superseded below).
--    Must use ALTER TABLE DROP CONSTRAINT because it was created as a table
--    constraint, not a bare index — DROP INDEX would fail with 2BP01.
ALTER TABLE shadow_bets DROP CONSTRAINT IF EXISTS uq_shadow_bet_per_run;

-- 3. Cohort-scoped unique constraint: one bet per bot per match per market per window.
CREATE UNIQUE INDEX uq_shadow_bet_per_cohort
    ON shadow_bets (shadow_cohort, bot_id, match_id, market, selection);
