-- BOT-TIMING: Add timing_cohort column to simulated_bets
-- Tracks which time-window cohort placed each bet (morning / midday / pre_ko).
-- Used to compare CLV + ROI per cohort and find optimal bet timing.

ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS timing_cohort TEXT CHECK (timing_cohort IN ('morning', 'midday', 'pre_ko'));

-- Index for cohort-based performance queries
CREATE INDEX IF NOT EXISTS idx_simulated_bets_timing_cohort
    ON simulated_bets(timing_cohort);

-- Backfill existing bets as 'morning' (they were all placed in the morning run)
UPDATE simulated_bets SET timing_cohort = 'morning' WHERE timing_cohort IS NULL;
