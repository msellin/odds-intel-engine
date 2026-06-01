-- PERF-HERO-EQUITY-SPARKLINE 2026-06-01
--
-- /performance hero currently shows ROI numbers but no trajectory. Last-30d
-- cumulative P&L on active bots is +€815 with a clean upward curve after the
-- May 7 dip — a tiny sparkline tells that story instantly. Data check passed
-- with cumulative going from -€127 (May 7) → +€815 (June 1).
--
-- Stored as JSON: array of {d, cum} (date string + running cumulative P&L
-- in EUR) ordered by date ascending. 30 entries. settlement.py builds it
-- alongside the cohort split rollup in write_dashboard_cache.
--
-- Schema choice: JSONB array of objects rather than two parallel arrays so a
-- future tooltip-on-hover renderer can show "on May 17 → +€277" without a
-- second cache field.

ALTER TABLE dashboard_cache
    ADD COLUMN IF NOT EXISTS daily_pnl_curve_30d JSONB;
