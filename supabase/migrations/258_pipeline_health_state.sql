-- RETRAIN-HEALTHCHECK (2026-06-21)
--
-- Generic table for DB-backed dedup of Railway-side pipeline healthchecks.
-- First consumer: weekly_retrain (silently failed 2026-06-07 + 2026-06-14
-- before today's 06-21 success — 14-day v20260607 lock-in cost us a week
-- of v20260621 model gains).
--
-- Designed to be reused by future healthchecks (live_poller, settlement,
-- inplay tracker, etc.) rather than spreading one column per pipeline
-- across various state tables. Singleton row per pipeline_name.
--
-- Why generic vs piggyback on coolbet_session_state: coolbet_session_state
-- holds Coolbet-specific JWT/session/heartbeat fields; bolting unrelated
-- pipeline-health columns on it would conflate domains. A 4-column table
-- with a primary key is cleaner and survives the inevitable next "another
-- silent failure class" task.

CREATE TABLE IF NOT EXISTS pipeline_health_state (
    pipeline_name      TEXT        PRIMARY KEY,
    last_alert_at      TIMESTAMPTZ,
    last_alert_reason  TEXT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE pipeline_health_state IS
    'DB-backed dedup for Railway-side pipeline healthcheck Telegram alerts.
     Survives Railway redeploys (the failure mode the in-process dedup has).
     One row per healthcheck name (e.g. weekly_retrain, live_poller).
     last_alert_at NULL = no active incident; non-NULL = alert was sent
     and the next alert is gated until either the dedup window elapses
     or a recovery message clears it.';
