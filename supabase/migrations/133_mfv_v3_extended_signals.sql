-- MFV-V3-PIVOT-EXTEND (2026-05-25)
--
-- Extends match_feature_vectors with the 4 new signals shipped today, so
-- the next retrain consumes them as features (alongside the 5 columns
-- already added by migration 132):
--
-- Columns added:
--   league_draw_rate_ytd FLOAT   — per-league season-to-date draw rate
--                                    Backtest: +11.6pp Q4 vs Q1 actual-draw lift
--                                    Source signal: scripts/compute_league_draw_rate.py
--
--   season_progress FLOAT        — per-match position in (league, season) window [0..1]
--                                    Backtest: late vs early +7.7pp Over 2.5,
--                                              +6.0pp BTTS, +6.7pp home win
--                                    Source signal: scripts/compute_league_season_phase.py
--
--   line_velocity FLOAT          — Pinnacle home implied-prob slope T-12h..T-2h
--                                    Backtest: -6.6pp CLV-beat Q4 |v| (REVERSE signal)
--                                    Source signal: scripts/compute_line_velocity.py
--
--   xg_overperf_home FLOAT       — rolling 10-match (goals − xG) home team
--                                    Source signal: scripts/compute_xg_overperformance.py
--   xg_overperf_away FLOAT       — same for away team
--
-- Backfill via scripts/backfill_mfv_v3_signals.py (extended in same commit).

ALTER TABLE match_feature_vectors
    ADD COLUMN IF NOT EXISTS league_draw_rate_ytd FLOAT,
    ADD COLUMN IF NOT EXISTS season_progress FLOAT,
    ADD COLUMN IF NOT EXISTS line_velocity FLOAT,
    ADD COLUMN IF NOT EXISTS xg_overperf_home FLOAT,
    ADD COLUMN IF NOT EXISTS xg_overperf_away FLOAT;

COMMENT ON COLUMN match_feature_vectors.league_draw_rate_ytd IS
    'Per-league season-to-date draw rate (draws/settled). Backtest +11.6pp Q4 vs Q1. '
    'Source signal: match_signals.signal_name=''league_draw_rate_ytd''.';
COMMENT ON COLUMN match_feature_vectors.season_progress IS
    'Per-match season position in (league, season) date window, normalized [0..1]. '
    'Backtest: late vs early +7.7pp Over 2.5, +6.0pp BTTS. '
    'Source signal: match_signals.signal_name=''season_progress''.';
COMMENT ON COLUMN match_feature_vectors.line_velocity IS
    'Pinnacle home implied-prob slope over T-12h..T-2h snapshots. REVERSE signal: '
    'high |velocity| → -6.6pp CLV-beat (we''re on wrong side at close). '
    'Source signal: match_signals.signal_name=''line_velocity''.';
COMMENT ON COLUMN match_feature_vectors.xg_overperf_home IS
    'Rolling 10-match mean of (goals_scored − xG) for the home team. '
    'Positive = team over-performing xG (expect regression down). '
    'Source signal: match_signals.signal_name=''xg_overperf_home''.';
COMMENT ON COLUMN match_feature_vectors.xg_overperf_away IS
    'Same as xg_overperf_home but for away team.';
