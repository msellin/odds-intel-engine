-- ML-NEW-FEATURES (2026-05-25)
--
-- Adds four new columns to match_feature_vectors that pivot the signals
-- now landing in match_signals into per-row features ready for the next
-- B-ML3 retrain (v3+). Source signals are computed nightly by:
--   compute_team_avg_player_rating.py → team_avg_player_rating_{home,away}
--   compute_injury_severity.py         → injury_severity_score_{home,away}
--   compute_league_clv_efficiency.py   → league_clv_efficiency
-- (last one already loaded per-match at pipeline time as
-- match["_league_clv_efficiency"]; this column makes it persistent for
-- training pivots.)
--
-- Columns added:
--   team_avg_player_rating_home FLOAT  — rolling 10-match avg AF player rating, home team
--   team_avg_player_rating_away FLOAT  — same for away team
--   injury_severity_score_home FLOAT   — severity-weighted injury count for home team
--   injury_severity_score_away FLOAT   — same for away team
--   league_clv_efficiency FLOAT        — per-league 60d mean pseudo_clv beatability index
--
-- Backfill: empty for now; the next MFV rebuild (next Sunday 03:00 UTC via
-- weekly_retrain pipeline) will pivot the corresponding match_signals
-- rows into these columns. A standalone backfill script can be added later
-- if we want to avoid the weekly wait.

ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS team_avg_player_rating_home FLOAT,
    ADD COLUMN IF NOT EXISTS team_avg_player_rating_away FLOAT,
    ADD COLUMN IF NOT EXISTS injury_severity_score_home FLOAT,
    ADD COLUMN IF NOT EXISTS injury_severity_score_away FLOAT,
    ADD COLUMN IF NOT EXISTS league_clv_efficiency FLOAT;

COMMENT ON COLUMN match_feature_vectors.team_avg_player_rating_home IS
    'Rolling mean of last 10 home-team matches'' average AF player rating (players with >=60 mins). '
    'Source signal: match_signals.signal_name=''team_avg_player_rating_home''.';
COMMENT ON COLUMN match_feature_vectors.team_avg_player_rating_away IS
    'Rolling mean of last 10 away-team matches'' average AF player rating. '
    'Source signal: match_signals.signal_name=''team_avg_player_rating_away''.';
COMMENT ON COLUMN match_feature_vectors.injury_severity_score_home IS
    'Severity-weighted injury count: SEVERE×3 + MODERATE×1.5 + MINOR×0.5 + UNKNOWN×1. '
    'Replaces raw injury count; SEVERE class captures ACL/Achilles/Cruciate (season-ending). '
    'Source signal: match_signals.signal_name=''injury_severity_score_home''.';
COMMENT ON COLUMN match_feature_vectors.injury_severity_score_away IS
    'Same as injury_severity_score_home but for away side.';
COMMENT ON COLUMN match_feature_vectors.league_clv_efficiency IS
    '60d rolling mean pseudo_clv per league: +ve = beatable closing line, -ve = sharp. '
    'Source signal: match_signals.signal_name=''league_clv_efficiency''.';
