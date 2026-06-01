-- PERF-HERO-COHORT-SPLIT 2026-06-01
--
-- Performance page hero currently averages pre-match and in-play ROI into one
-- "System ROI" tile. Last-30d data: in-play +14.5% ROI on n=861, pre-match
-- -1.2% on n=1,974. The 15.7pp gap is real and stable across 7/14/30d windows.
-- Averaging them hides the in-play story from new visitors.
--
-- This migration adds cohort-split rollup columns to dashboard_cache so the
-- frontend can render separate hero tiles without a hot-path query.
--
-- avg_clv is intentionally NOT included for in-play — simulated_bets.clv
-- semantics differ for live bets (live closing line vs pre-match closing
-- line), causing weird aggregate values. Surface ROI + hit rate only on the
-- in-play tile until inplay-CLV is properly defined.

ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS prematch_settled_bets   INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS prematch_won_bets       INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS prematch_total_staked   FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS prematch_total_pnl      FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS prematch_roi_pct        FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS prematch_avg_clv        FLOAT;

ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS inplay_settled_bets     INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS inplay_won_bets         INTEGER;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS inplay_total_staked     FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS inplay_total_pnl        FLOAT;
ALTER TABLE dashboard_cache ADD COLUMN IF NOT EXISTS inplay_roi_pct          FLOAT;
