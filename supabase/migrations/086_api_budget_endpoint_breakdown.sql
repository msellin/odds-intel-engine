-- Migration 086: per-endpoint AF call attribution on api_budget_log
-- Origin: AF-FETCHES-AUDIT (PRIORITY_QUEUE.md). The 26K-call mystery cannot
-- be diagnosed without per-endpoint breakdown — sync_with_server() now drains
-- BudgetTracker._endpoint_counts into endpoint_breakdown (since-last-sync) and
-- snapshots cumulative day-to-date into endpoint_breakdown_today.
--
-- Both columns are JSONB maps of endpoint string → call count, e.g.:
--   {"fixtures": 412, "odds": 4567, "fixtures/statistics": 8123, ...}
--
-- Existing rows (pre-migration) get NULL — the breakdown report tolerates this.

ALTER TABLE api_budget_log
  ADD COLUMN IF NOT EXISTS endpoint_breakdown        JSONB,
  ADD COLUMN IF NOT EXISTS endpoint_breakdown_today  JSONB;

COMMENT ON COLUMN api_budget_log.endpoint_breakdown IS
  'Per-endpoint call counts since the previous hourly sync row (BudgetTracker._endpoint_counts).';
COMMENT ON COLUMN api_budget_log.endpoint_breakdown_today IS
  'Per-endpoint cumulative call counts since UTC midnight (BudgetTracker._endpoint_counts_today).';
