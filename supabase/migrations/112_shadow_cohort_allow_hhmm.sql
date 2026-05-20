-- SHADOW-COHORT-CONSTRAINT (2026-05-20): the scheduler's 30-min shadow run
-- (commit 3f81c5c) writes HHMM-style cohort labels like '0705' / '1435' so
-- each snapshot is independently analysable. Migration 101's original CHECK
-- restricted shadow_cohort to ('morning', 'midday', 'pre_ko') — every
-- scheduled shadow run since then has failed silently with
-- psycopg2.errors.CheckViolation (caught by bulk_store_shadow_bets's
-- try/except, logged but not surfaced). Result: audit_silent_bots.py
-- section 2 has been near-empty for weeks, hiding bot diagnostic data.
--
-- Drop the original CHECK and re-add a looser one that accepts both the
-- HHMM scheduler labels AND the original three named cohorts (the latter
-- still used by manual / one-shot invocations like funnel_diagnostic.py).

ALTER TABLE shadow_bets
    DROP CONSTRAINT IF EXISTS shadow_bets_shadow_cohort_check;

ALTER TABLE shadow_bets
    ADD CONSTRAINT shadow_bets_shadow_cohort_check
    CHECK (
      shadow_cohort IN ('morning', 'midday', 'pre_ko')
      OR shadow_cohort ~ '^[0-9]{4}$'
    );

COMMENT ON COLUMN shadow_bets.shadow_cohort IS
    'Which timing window this shadow was generated AT — NOT the bot''s assigned cohort. '
    'Scheduler writes HHMM (eg ''0705'', ''1435''); manual runs use the named '
    'cohorts (morning/midday/pre_ko). Updated by migration 112 (was triple-restricted).';
