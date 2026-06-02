-- PRO-TIER-V2 (2026-06-02)
--
-- /value-bets gets a rolling-30d hero card per tier — Pro shows the
-- calibrated-bot cohort stats, Elite shows the all-active cohort stats.
-- Cohort definitions:
--   Pro    = bots.maturity_label = 'calibrated' AND is_active = true
--   Elite  = bots.is_active = true (everything currently firing — calibrated,
--            active, experimental; inplay included)
--
-- We piggyback on the existing dashboard_cache table (one row per snapshot,
-- refreshed every 30 min by `job_dashboard_cache_refresh`). No new cron
-- needed. The two JSONB blobs each contain
-- `{n, won, roi_pct, clv_pct, win_rate_pct, computed_at}`. Nullable on
-- legacy rows (pre-this-migration).

ALTER TABLE dashboard_cache
    ADD COLUMN IF NOT EXISTS pro_value_bets_30d   JSONB,
    ADD COLUMN IF NOT EXISTS elite_value_bets_30d JSONB;
