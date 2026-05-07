-- Migration 060: api_budget_log table
-- Persists BudgetTracker state after each hourly sync_with_server() call.
-- write_ops_snapshot() reads latest row for af_calls_today / af_budget_remaining.

CREATE TABLE IF NOT EXISTS api_budget_log (
  id           SERIAL PRIMARY KEY,
  logged_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  log_date     DATE        NOT NULL DEFAULT CURRENT_DATE,
  calls_today  INT         NOT NULL,
  remaining    INT         NOT NULL,
  daily_limit  INT         NOT NULL DEFAULT 75000,
  source       TEXT        NOT NULL DEFAULT 'sync'  -- 'sync' | 'startup'
);

CREATE INDEX idx_api_budget_log_date ON api_budget_log (log_date, logged_at DESC);
