-- DRIFT-FEATURE (2026-06-04) — open-to-close drift columns on
-- match_feature_vectors. The existing pinnacle_line_move_*_at_t6h columns
-- (migration 128) use a "≤ kickoff−6h" cutoff that excludes CSV closing
-- snapshots (timestamped at kickoff). These new columns capture the
-- full open→close drift signal that the CSV-FULL-EXTRACT backtest showed
-- produces an 8.76pp home win-rate spread top vs bottom quintile.
--
-- Storage: implied probability delta = (close_implied − open_implied) for
-- each 1X2 selection. Positive = market moved toward this selection.

ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS pinnacle_drift_home FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_drift_draw FLOAT,
    ADD COLUMN IF NOT EXISTS pinnacle_drift_away FLOAT;

COMMENT ON COLUMN match_feature_vectors.pinnacle_drift_home IS
    'Pinnacle 1X2 home: closing_implied − opening_implied. Positive = sharp money backed home. Source: odds_snapshots is_opening + is_closing rows. Backfilled 2026-06-04 from CSV-FULL-EXTRACT data.';
COMMENT ON COLUMN match_feature_vectors.pinnacle_drift_draw IS
    'Pinnacle 1X2 draw drift (close − open implied). See pinnacle_drift_home.';
COMMENT ON COLUMN match_feature_vectors.pinnacle_drift_away IS
    'Pinnacle 1X2 away drift (close − open implied). See pinnacle_drift_home.';
