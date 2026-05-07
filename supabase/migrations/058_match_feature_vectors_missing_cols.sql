-- Fix match_feature_vectors: add 16 columns that _build_feature_row_batched writes
-- but the table didn't have. The upsert has been silently failing since these signals
-- were added after the initial April 27 deployment, leaving the table stuck at Apr 28.
ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS goals_for_avg_home       NUMERIC,
    ADD COLUMN IF NOT EXISTS goals_for_avg_away       NUMERIC,
    ADD COLUMN IF NOT EXISTS goals_against_avg_home   NUMERIC,
    ADD COLUMN IF NOT EXISTS goals_against_avg_away   NUMERIC,
    ADD COLUMN IF NOT EXISTS h2h_win_pct              NUMERIC,
    ADD COLUMN IF NOT EXISTS league_position_home     NUMERIC,
    ADD COLUMN IF NOT EXISTS league_position_away     NUMERIC,
    ADD COLUMN IF NOT EXISTS overnight_line_move      NUMERIC,
    ADD COLUMN IF NOT EXISTS points_to_relegation_home INTEGER,
    ADD COLUMN IF NOT EXISTS points_to_relegation_away INTEGER,
    ADD COLUMN IF NOT EXISTS points_to_title_home     INTEGER,
    ADD COLUMN IF NOT EXISTS points_to_title_away     INTEGER,
    ADD COLUMN IF NOT EXISTS referee_home_win_pct     NUMERIC,
    ADD COLUMN IF NOT EXISTS referee_over25_pct       NUMERIC,
    ADD COLUMN IF NOT EXISTS rest_days_home           INTEGER,
    ADD COLUMN IF NOT EXISTS rest_days_away           INTEGER;
