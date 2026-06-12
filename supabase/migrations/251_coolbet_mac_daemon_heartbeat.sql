-- COOLBET-MAC-DAEMON-HEARTBEAT (2026-06-12)
--
-- Lets the Telegram /status command tell the operator whether the
-- Mac-side placement daemon is actually running. Previously the only
-- visible signals were `session_healthy` (set by heartbeat probes) and
-- `placement_paused` (kill switch) — neither answered "is the daemon
-- process alive?" unambiguously. A laptop that's asleep / Docker
-- stopped / launchd job unloaded would silently produce no real_bets
-- and the operator wouldn't know until they checked manually.
--
-- The daemon writes mac_daemon_last_tick_at on every _tick() completion
-- and a compact JSON summary in mac_daemon_last_tick_result so the
-- Telegram bot can show: "last tick 4m ago — qualified=3 placed=1
-- errors=0". Stale heartbeat (>~35 min) flags as RED in /status.

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS mac_daemon_last_tick_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS mac_daemon_last_tick_result JSONB;

COMMENT ON COLUMN coolbet_session_state.mac_daemon_last_tick_at IS
    'Timestamp of the last Mac daemon tick completion. NULL until first tick.
     >35min stale = daemon process likely dead.';

COMMENT ON COLUMN coolbet_session_state.mac_daemon_last_tick_result IS
    'JSON summary of the last tick: {qualified, placed, skipped, errors,
     synced_from_coolbet, elapsed_s}. Read by the Telegram /status
     command to surface daemon liveness + recent activity.';
