-- Switch CS2 bot to soccer's pattern: one pick per (bot, match, market, side)
-- at the best-priced bookie, with closing-odds + CLV tracked at kickoff.
--
-- Step 1: dedupe existing rows so the new UNIQUE constraint can attach.
--   For each (bot_name, bo3gg_id, market, pick) keep only the highest-odds row.
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY bot_name, bo3gg_id, market, pick
               ORDER BY odds_at_pick DESC, id ASC
           ) AS rn
    FROM cs2_simulated_bets
)
DELETE FROM cs2_simulated_bets WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

-- Step 2: drop the old per-bookie UNIQUE and add the per-pick one.
ALTER TABLE cs2_simulated_bets
    DROP CONSTRAINT IF EXISTS cs2_simulated_bets_bot_name_bo3gg_id_market_bookie_key;

ALTER TABLE cs2_simulated_bets
    ADD CONSTRAINT cs2_simulated_bets_per_pick_key
    UNIQUE (bot_name, bo3gg_id, market, pick);

-- Step 3: CLV + market-consensus tracking.
ALTER TABLE cs2_simulated_bets
    ADD COLUMN IF NOT EXISTS closing_odds_at_kickoff   FLOAT,
    ADD COLUMN IF NOT EXISTS closing_odds_snapshot_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS clv                       FLOAT,
    ADD COLUMN IF NOT EXISTS consensus_implied_prob    FLOAT,
    ADD COLUMN IF NOT EXISTS n_books_at_pick           INTEGER;
