-- UI-METRIC-SOT (2026-06-06)
--
-- Single-source-of-truth for the /performance cumulative-P&L charts.
-- Today the hero "Last 31d" sparkline reads cache.daily_pnl_curve_30d
-- (cohort: active + non-experimental + non-retired) and the extras
-- "Last 90d" cumulative chart runs its own ad-hoc query in
-- `_getPublicPerformanceExtrasUncached` over a DIFFERENT cohort
-- (non-experimental, INCLUDES retired). Endpoints diverge.
--
-- This column carries the 90-day curve from the same cohort settlement
-- already uses for the 30-day curve. Frontend then slices the last 30d
-- for the sparkline and reads the full series for the extras chart.
-- The ad-hoc 90d query in engine-data.ts goes away.
--
-- JSON shape: [{ "d": "2026-03-08", "cum": 12.34 }, ...]
-- Bucketed by DATE(pick_time) over `is_active AND retired_at IS NULL
-- AND maturity_label != 'experimental' AND result IN ('won','lost')`
-- AND pick_time >= now() - interval '90 days'.

ALTER TABLE dashboard_cache
    ADD COLUMN IF NOT EXISTS daily_pnl_curve_90d JSONB;
