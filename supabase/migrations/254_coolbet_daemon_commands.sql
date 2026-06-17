-- COOLBET-DAEMON-COMMANDS (2026-06-17)
--
-- Queue table for operator-initiated daemon commands triggered via
-- Telegram inline buttons. The flow:
--   1. Daemon-fail-burst alert / daily summary Telegram includes inline
--      buttons (🔄 Heal, ⏸ Pause, ▶ Resume).
--   2. Operator taps a button → Telegram callback → odds-intel-web webhook.
--   3. Webhook INSERTs a row here (heal) OR directly updates
--      coolbet_session_state (pause/resume — those are direct DB writes
--      and don't need daemon action).
--   4. Mac daemon polls this table every ~30s (between placement ticks)
--      and runs the requested command (currently just heal). Marks row
--      executed_at + result_*.
--
-- One pending command at a time per command_type — INSERT path checks for
-- existing pending row first. Avoids queue pileup when user double-taps.
--
-- Commands are immutable once executed (executed_at != NULL); operator
-- can re-tap to enqueue a new one.

CREATE TABLE IF NOT EXISTS coolbet_daemon_commands (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    command_type    TEXT NOT NULL,       -- 'heal' (more in future: 'refresh_jwt', 'restart')
    requested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requested_by    TEXT NOT NULL,       -- 'telegram_user_<id>' / 'cli'
    -- Execution + result. NULL while pending.
    executed_at     TIMESTAMPTZ,
    result_status   TEXT,                -- 'recovered' / 'stalled' / 'error'
    result_message  TEXT,
    result_actions  JSONB                -- actions trail from auto_self_heal
);

-- Index over pending rows so the daemon's poll is O(log n) instead of
-- a full table scan as the table grows.
CREATE INDEX IF NOT EXISTS idx_coolbet_daemon_commands_pending
    ON coolbet_daemon_commands (requested_at DESC)
    WHERE executed_at IS NULL;

COMMENT ON TABLE coolbet_daemon_commands IS
    'Queue for Telegram-button-initiated daemon commands. Daemon polls
     pending rows every ~30s, executes, marks executed_at. Pause/resume
     bypass this and write coolbet_session_state directly.';
