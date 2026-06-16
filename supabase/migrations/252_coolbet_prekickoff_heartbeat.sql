-- COOLBET-PREKICKOFF-HEARTBEAT (2026-06-16, C2)
--
-- Closes the observability gap on the COOLBET-DAEMON-ALERTS catch-net.
-- The job runs every 5 min on Railway and is silent on healthy days
-- (daemon up, no calibrated picks at risk). Without a DB heartbeat, the
-- only proof Railway is actually firing the cron is Telegram messages —
-- and on a healthy day there ARE no Telegrams. Operator can't tell
-- "Railway healthy, catch-net silent because nothing to do" apart from
-- "Railway crashed and catch-net hasn't fired at all".
--
-- The job writes prekickoff_last_run_at on every invocation regardless
-- of outcome (healthy / candidates=0 / sent / dedup). prekickoff_last_run_result
-- carries a compact JSON summary so /admin pages + ad-hoc DB probes can
-- show "catch-net ran 2m ago, found 0 candidates, daemon healthy" without
-- tailing Railway logs.

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS prekickoff_last_run_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS prekickoff_last_run_result JSONB;

COMMENT ON COLUMN coolbet_session_state.prekickoff_last_run_at IS
    'Timestamp of the last COOLBET-DAEMON-ALERTS pre-kickoff catch-net run.
     NULL until first run. >10min stale = Railway cron likely down (job
     fires every 5min).';

COMMENT ON COLUMN coolbet_session_state.prekickoff_last_run_result IS
    'JSON summary of the last catch-net run: {healthy, candidates, sent,
     skipped_dedup}. Lets operators and admin pages tell "Railway healthy,
     nothing to alert" apart from "Railway not firing the job".';
