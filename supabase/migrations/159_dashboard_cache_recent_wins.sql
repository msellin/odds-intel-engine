-- PERF-HERO-RECENT-WINS 2026-06-01
--
-- /performance currently has no "concrete recent wins" story — visitors see
-- aggregate ROI numbers but nothing specific. Top 14-day wins by CLV beat:
--   8 unique matches qualify, CLV +30% to +55%, odds 1.78 to 4.25, 8 countries
-- The diversity is itself a story: model finds edge globally, not just EPL.
--
-- Stored as JSON array (top 8 deduped by match+market+selection) with the
-- fields the public reel needs. No P&L or stake (free-tier visible). Refreshed
-- by write_dashboard_cache alongside the other rollups.

ALTER TABLE dashboard_cache
    ADD COLUMN IF NOT EXISTS recent_top_wins JSONB;
