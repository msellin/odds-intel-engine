-- PERF-HERO-NEXT-MODEL 2026-06-01
--
-- Surface the most recent unpromoted model's offline-eval improvement vs
-- production on /performance. Selling story = "actively improving every week".
-- Live data 2026-06-01: v20260531 holdout eval shows 9/11 markets better than
-- v20260524_market (1X2 head −10% log_loss, AH −2.5 to −2.8%, BTTS −1.3%);
-- OU regresses +2.7% (TIER-C-EXPAND drag, will be pinned to old model on
-- promotion per OU-MODEL-PIN-RUNBOOK).
--
-- Payload built by write_dashboard_cache from model_versions.cv_metrics so
-- the callout updates automatically on each Sunday retrain. Cleared when no
-- candidate exists (post-promotion until next retrain).

ALTER TABLE dashboard_cache
    ADD COLUMN IF NOT EXISTS upcoming_model_summary JSONB;
