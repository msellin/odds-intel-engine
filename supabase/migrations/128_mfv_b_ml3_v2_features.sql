-- MFV-B-ML3-V2-FEATURES (2026-05-24)
--
-- Adds market-microstructure columns to match_feature_vectors needed for B-ML3 v2.
-- All new columns are computed using snapshots WHERE timestamp <= (match.date - 6h),
-- so historical settled-match rows can be backfilled without the closing-line leak
-- that contaminated v1's `odds_drift_home`.
--
-- Naming: `_at_t6h` suffix marks "as of T-6h before kickoff" semantics.
--
-- Columns added:
--   - odds_drift_home_at_t6h FLOAT    — closing-line-leak-free replacement for odds_drift_home
--   - steam_move_at_t6h BOOL          — |odds_drift_home_at_t6h| > 0.03
--   - pinnacle_line_move_home_at_t6h FLOAT  — Pinnacle 1X2 home implied drift from opening
--   - pinnacle_line_move_draw_at_t6h FLOAT  — Pinnacle 1X2 draw
--   - pinnacle_line_move_away_at_t6h FLOAT  — Pinnacle 1X2 away
--   - sharp_consensus_home_at_t6h FLOAT     — avg(implied) across sharp books at T-6h
--   - sharp_consensus_draw_at_t6h FLOAT
--   - sharp_consensus_away_at_t6h FLOAT
--   - odds_volatility_home_at_t6h FLOAT     — std(implied) across all accessible books at T-6h
--   - odds_volatility_draw_at_t6h FLOAT
--   - odds_volatility_away_at_t6h FLOAT
--   - pinnacle_ah_line_at_t6h FLOAT         — Pinnacle's AH main handicap line at T-6h
--   - pinnacle_ah_line_move FLOAT           — change in Pinnacle's AH main line opening→T-6h

ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS odds_drift_home_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS steam_move_at_t6h BOOLEAN,
    ADD COLUMN IF NOT EXISTS pinnacle_line_move_home_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_line_move_draw_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_line_move_away_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS sharp_consensus_home_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS sharp_consensus_draw_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS sharp_consensus_away_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS odds_volatility_home_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS odds_volatility_draw_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS odds_volatility_away_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_ah_line_at_t6h FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_ah_line_move FLOAT;

COMMENT ON COLUMN match_feature_vectors.odds_drift_home_at_t6h IS
    'Closing-line-leak-free home odds drift: implied(latest snapshot <= match.date-6h) - opening_implied. Replaces odds_drift_home for B-ML3 v2 training.';
COMMENT ON COLUMN match_feature_vectors.pinnacle_line_move_home_at_t6h IS
    'Pinnacle 1x2 home implied prob change from market open to T-6h. Higher signal than multi-book drift.';
COMMENT ON COLUMN match_feature_vectors.sharp_consensus_home_at_t6h IS
    'Average implied prob across sharp books (Pinnacle + Pinnacle-aligned) at T-6h. Robust to single-book noise.';
COMMENT ON COLUMN match_feature_vectors.odds_volatility_home_at_t6h IS
    'Std dev of implied prob across all accessible books at T-6h. Higher = market disagreement = potential edge.';
