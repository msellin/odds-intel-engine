-- 391 — FEED AUTO-PAUSE: a circuit breaker for bot-protected book sweeps
-- ([[#107]] / [[#108]], 2026-09-23)
--
-- Owner: "can we add some mechanism that automatically pauses when needed".
--
-- #108 proved the failure mode: Coolbet's bot protection flagged our Estonian exit
-- IP, and every 30-minute retry (a fresh browser session + requests) kept the flag
-- fresh. A full FlareSolverr restart did NOT help — a brand-new session still hung
-- 67 s — so the only cures are time without traffic, or another IP. Hence:
--
--   2 failed runs in a row → pause automatically, auto_resume_at = now + backoff
--   at auto_resume_at      → resume and run ONE test sweep
--   test fails             → pause again, backoff doubles (1 h, 2 h, 4 h, 8 h, cap 12 h)
--   a successful run       → auto_pause_count resets to 0
--
-- Only pauses the ENGINE set (paused_by = 'auto') auto-resume; an operator's pause
-- from /admin/feeds is never overridden.

ALTER TABLE feed_controls ADD COLUMN IF NOT EXISTS auto_resume_at   timestamptz;
ALTER TABLE feed_controls ADD COLUMN IF NOT EXISTS auto_pause_count integer NOT NULL DEFAULT 0;
ALTER TABLE feed_status   ADD COLUMN IF NOT EXISTS auto_resume_at   timestamptz;

ALTER TABLE feed_actions DROP CONSTRAINT IF EXISTS feed_actions_action_check;
ALTER TABLE feed_actions ADD CONSTRAINT feed_actions_action_check
    CHECK (action IN ('pause', 'resume', 'run_now', 'auto_pause', 'auto_resume'));
