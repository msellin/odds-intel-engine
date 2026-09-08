-- 1H-HT-GOALS (MARKET-EXPANSION): store half-by-half goals on matches.
-- Unlocks the 1H family (1x2_1h, over_under_1h_*) AND 2H / highest-scoring-half
-- analysis for ML sweeps. AF-native: /fixtures score.halftime + score.fulltime,
-- full history, zero extra API calls. 2H is stored EXPLICITLY (= FT - HT) rather
-- than derived on the fly, so matrix/sweep queries are trivial (owner request).
ALTER TABLE matches
  ADD COLUMN IF NOT EXISTS ht_score_home SMALLINT,
  ADD COLUMN IF NOT EXISTS ht_score_away SMALLINT,
  ADD COLUMN IF NOT EXISTS h2_score_home SMALLINT,
  ADD COLUMN IF NOT EXISTS h2_score_away SMALLINT;

COMMENT ON COLUMN matches.ht_score_home IS '1st-half goals, home (AF score.halftime.home). NULL = unknown/not backfilled.';
COMMENT ON COLUMN matches.ht_score_away IS '1st-half goals, away (AF score.halftime.away). NULL = unknown/not backfilled.';
COMMENT ON COLUMN matches.h2_score_home IS '2nd-half goals, home (= FT - HT). NULL = unknown/not backfilled.';
COMMENT ON COLUMN matches.h2_score_away IS '2nd-half goals, away (= FT - HT). NULL = unknown/not backfilled.';

-- partial index for the backfill sweep: finished matches still missing HT goals
CREATE INDEX IF NOT EXISTS idx_matches_missing_ht
  ON matches (date)
  WHERE status = 'finished' AND ht_score_home IS NULL;
