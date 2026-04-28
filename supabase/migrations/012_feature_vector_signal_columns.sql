-- Migration 012: New signal columns on match_feature_vectors
-- Adds S3b (standings), S3c (H2H), S3d (referee), S3e (overnight move),
-- S3f (rest days), and T2 (season stats) columns.

ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS league_position_home   numeric(5,4),
    ADD COLUMN IF NOT EXISTS league_position_away   numeric(5,4),
    ADD COLUMN IF NOT EXISTS points_to_relegation_home integer,
    ADD COLUMN IF NOT EXISTS points_to_relegation_away integer,
    ADD COLUMN IF NOT EXISTS points_to_title_home   integer,
    ADD COLUMN IF NOT EXISTS points_to_title_away   integer,
    ADD COLUMN IF NOT EXISTS h2h_win_pct            numeric(5,4),
    ADD COLUMN IF NOT EXISTS overnight_line_move    numeric(7,5),
    ADD COLUMN IF NOT EXISTS rest_days_home         integer,
    ADD COLUMN IF NOT EXISTS rest_days_away         integer,
    ADD COLUMN IF NOT EXISTS referee_home_win_pct   numeric(5,4),
    ADD COLUMN IF NOT EXISTS referee_over25_pct     numeric(5,4),
    ADD COLUMN IF NOT EXISTS goals_for_avg_home     numeric(5,2),
    ADD COLUMN IF NOT EXISTS goals_for_avg_away     numeric(5,2),
    ADD COLUMN IF NOT EXISTS goals_against_avg_home numeric(5,2),
    ADD COLUMN IF NOT EXISTS goals_against_avg_away numeric(5,2);
