-- COOLBET-JWT-DB-BACKED (2026-06-12): persist the live JWT to DB so
-- processes can bootstrap from a single canonical source instead of
-- env vars that drift between local and Railway.
--
-- Without this: Railway's COOLBET_MANUAL_JWT env var is set once via
-- the dashboard, then goes stale every 30 min. Local can self-heal
-- (Imperva trusts the IP); Railway can't (Imperva 403's /s/auth/login
-- from cloud IPs). Result: operator has to paste a fresh JWT to
-- Railway env every time something breaks.
--
-- With this: any process that successfully obtains a JWT (local SMS
-- enrollment, API login, or in-process renew-token) writes it to this
-- row. Any process starting up reads from this row first, falling back
-- to env var only if DB is empty. Railway can now bootstrap from the
-- JWT local just refreshed, then keep it alive via /s/auth/renew-token
-- (which IS accepted from Railway IP).
--
-- Security: the JWT is auth material. Drop the open-anon-SELECT policy
-- shipped in mig 242 — service_role retains full access (RLS doesn't
-- apply to it) so the existing Telegram /status command continues to
-- work via createAdmin (SUPABASE_SERVICE_ROLE_KEY).

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS jwt_current TEXT,
    ADD COLUMN IF NOT EXISTS jwt_current_set_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS jwt_login_session_id TEXT,
    ADD COLUMN IF NOT EXISTS jwt_set_by TEXT;

COMMENT ON COLUMN coolbet_session_state.jwt_current IS
    'Live Coolbet JWT bearer token. Updated on every successful login + '
    'renewal. Read by any process starting up so Railway can self-heal '
    'across restarts without env-var pushes. NOT exposed via anon RLS — '
    'sensitive auth material.';

COMMENT ON COLUMN coolbet_session_state.jwt_set_by IS
    'Process identifier for debugging — e.g. "local_enroll", "railway_renew", '
    '"railway_api_login". Helps trace which side last refreshed the JWT.';

-- Tighten RLS — jwt_current shouldn't be readable via anon key. The
-- existing /status webhook + admin pages use service_role which bypasses
-- RLS entirely, so this drop doesn't break observability.
DROP POLICY IF EXISTS coolbet_session_state_anon_read ON coolbet_session_state;

-- Keep observability accessible: a view that exposes everything EXCEPT
-- the JWT. Any future anon-key read path can use this safely.
CREATE OR REPLACE VIEW coolbet_session_state_public AS
    SELECT id, last_login_at, last_login_method, jwt_user_id, jwt_exp_at,
           last_heartbeat_at, last_heartbeat_ok, session_healthy,
           last_error, last_error_at, fs_session_name, fs_url,
           cookies_last_refresh_at, cookies_count_last,
           placement_paused, placement_paused_at, placement_paused_reason,
           device_id, jwt_current_set_at, jwt_set_by, updated_at
    FROM coolbet_session_state;

GRANT SELECT ON coolbet_session_state_public TO anon, authenticated;

COMMENT ON VIEW coolbet_session_state_public IS
    'Observability projection of coolbet_session_state without the JWT. '
    'Use this for any read path that goes through anon/authenticated keys. '
    'service_role reads the base table directly (which includes jwt_current).';
