-- COOLBET-PLACER-PAUSE-FLAG (2026-06-11) — operator kill switch.
--
-- Why a DB flag, not an env var: env-var flip requires Railway service
-- restart, which can take 30-60s. The placer reads this flag at the start
-- of every run, so flipping it via Telegram /pause takes effect on the
-- next cron tick (≤30 min) without any restart. Faster operator response
-- when something looks off in flight.
--
-- The Telegram /pause and /resume operator commands toggle this column.
-- The placer's _run_coolbet_record() short-circuits when true.

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS placement_paused BOOLEAN DEFAULT FALSE;

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS placement_paused_at TIMESTAMPTZ;

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS placement_paused_reason TEXT;

COMMENT ON COLUMN coolbet_session_state.placement_paused IS
    'Operator kill switch via Telegram /pause command. When TRUE, the '
    'pipeline auto-placer skips its run entirely and writes nothing to '
    'real_bets. Resumes on /resume command or manual UPDATE. Faster than '
    'flipping COOLBET_AUTO_EXECUTE env var since it requires no Railway '
    'restart — next cron tick respects the flag immediately.';
