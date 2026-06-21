-- COOLBET-DAEMON-HEALTHCHECK (2026-06-21)
--
-- Railway-side healthcheck for the Mac daemon. The in-process alert path
-- in coolbet_mac_daemon.py has three failure modes that left a 3-day
-- outage (2026-06-18 → 2026-06-21) silent:
--   1. alert_fired_this_burst suppresses repeat sends within one process
--      lifetime — first alert fires, the rest go nowhere.
--   2. In-process telegram dedup dies with the process.
--   3. Mac daemon is itself the alerter — Mac sleep / daemon crash kills
--      monitoring AND placement at the same time.
--
-- The new Railway job (workers/jobs/coolbet_daemon_healthcheck.py) reads
-- mac_daemon_last_tick_at + mac_daemon_last_tick_result from this state
-- row every 30 min and Telegrams when the daemon is silent (>90 min since
-- last tick) or sustainedly erroring (>2h without a clean tick, derived
-- from coolbet_heal_log). DB-backed dedup via the new column survives
-- Railway redeploys + bounds the alert rate to once per 4h.

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS last_health_alert_at TIMESTAMPTZ;

COMMENT ON COLUMN coolbet_session_state.last_health_alert_at IS
    'Most-recent Railway-side health-alert Telegram. Cleared back to NULL
     by the healthcheck job after a recovery message fires, so the next
     outage gets fresh alerting.';
