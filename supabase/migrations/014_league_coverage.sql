-- =============================================================================
-- OddsIntel — League Coverage + Pipeline Runs
-- Migration: 014_league_coverage.sql
--
-- 1. Add API-Football ID and coverage fields to leagues table
-- 2. Create pipeline_runs table for job orchestration
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. LEAGUE COVERAGE
-- ─────────────────────────────────────────────────────────────────────────────

-- API-Football league identifier (used by all AF endpoints)
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS api_football_id integer;

-- Coverage flags from AF /leagues endpoint (per current season)
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_odds boolean DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_predictions boolean DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_injuries boolean DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_lineups boolean DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_standings boolean DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_events boolean DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_statistics_fixtures boolean DEFAULT false;
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_statistics_players boolean DEFAULT false;

-- Full raw coverage blob for reference
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS af_coverage_raw jsonb;

-- Current AF season (e.g. 2025)
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS af_season_current integer;

-- When coverage was last refreshed
ALTER TABLE leagues ADD COLUMN IF NOT EXISTS coverage_fetched_at timestamptz;

-- Unique index on AF ID for upserts
CREATE UNIQUE INDEX IF NOT EXISTS idx_leagues_api_football_id
    ON leagues (api_football_id) WHERE api_football_id IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. PIPELINE RUNS (job orchestration)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name        text NOT NULL,
    run_date        date NOT NULL,
    status          text NOT NULL DEFAULT 'running',  -- running, completed, failed, skipped
    started_at      timestamptz NOT NULL DEFAULT now(),
    completed_at    timestamptz,
    fixtures_count  integer,
    records_count   integer,
    error_message   text,
    metadata        jsonb,
    UNIQUE (job_name, run_date, started_at)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_job_date
    ON pipeline_runs (job_name, run_date, status);

-- RLS: service role only (pipeline writes, no frontend reads needed)
ALTER TABLE pipeline_runs ENABLE ROW LEVEL SECURITY;
