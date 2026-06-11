-- COOLBET-PLACER-DEVICEID-AUTO (2026-06-11): persist the bot's
-- Coolbet deviceId in coolbet_session_state so it stays stable across
-- process restarts.
--
-- Background: Coolbet's /s/bets/bets POST requires a non-empty
-- deviceId field. The browser frontend generates a UUID4 on first
-- visit and stores it in localStorage; subsequent visits read from
-- there. FlareSolverr-routed scrapes don't have access to that
-- localStorage (FS only exposes cookies), so we have to manage our
-- own deviceId.
--
-- Storing it in coolbet_session_state (singleton row) means:
--   - One stable ID per bot identity (Coolbet sees consistent traffic)
--   - No env-var fiddling required by the operator
--   - Visible to /admin pages + Telegram /status

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS device_id TEXT;

-- Backfill: generate a UUID4 if we don't have one yet. gen_random_uuid()
-- comes from pgcrypto; it's enabled on Supabase by default. Fall back to
-- a no-op if the function is missing (writer code will lazy-generate).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'gen_random_uuid') THEN
        UPDATE coolbet_session_state
        SET device_id = gen_random_uuid()::text
        WHERE id = 1 AND device_id IS NULL;
    END IF;
END $$;

COMMENT ON COLUMN coolbet_session_state.device_id IS
    'Stable client-side device UUID for Coolbet bet placement. Mirrors what '
    'a browser stores in localStorage on first visit. Auto-generated on '
    'first read by workers.automation.coolbet_state.get_or_create_device_id.';
