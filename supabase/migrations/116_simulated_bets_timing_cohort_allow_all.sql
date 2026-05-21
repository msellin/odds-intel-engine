-- BOT-COHORTS-ALL set timing_cohort = 'all' on every bot, but only shadow_bets
-- had its check constraint updated (migration 112). simulated_bets still only
-- allowed ('morning','midday','pre_ko'), causing every store_bet() call to fail
-- silently since the BOT-COHORTS-ALL change. This migration mirrors 112 for
-- simulated_bets and also accepts HHMM-format labels for consistency.

ALTER TABLE simulated_bets
DROP CONSTRAINT IF EXISTS simulated_bets_timing_cohort_check;

ALTER TABLE simulated_bets
ADD CONSTRAINT simulated_bets_timing_cohort_check
CHECK (
    timing_cohort IS NULL
    OR timing_cohort IN ('morning', 'midday', 'pre_ko', 'all')
    OR timing_cohort ~ '^[0-9]{4}$'
);
