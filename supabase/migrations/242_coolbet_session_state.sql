-- COOLBET-FS-SESSION-STABLE Step 1.3 (2026-06-11): observable session state.
--
-- Singleton table — one row, queried/upserted by id=1. Tracks the live state
-- of the Coolbet+FlareSolverr authenticated session so:
--   - /admin pages can show "session healthy / unhealthy + last_error"
--   - Telegram /status command has data to report
--   - heartbeat cron can detect stale state and alert
--   - on-call ops can answer "is the bot logged in?" without ssh
--
-- The session itself lives in FlareSolverr's browser memory (named session
-- "coolbet_prod"). This table is the observability layer ON TOP of that —
-- it records EVENTS (login, error, heartbeat) but doesn't store the JWT
-- or cookies themselves (those belong in env/FS only, never in the DB).

CREATE TABLE IF NOT EXISTS coolbet_session_state (
    id                  INTEGER PRIMARY KEY DEFAULT 1,

    -- Authentication lifecycle
    last_login_at       TIMESTAMPTZ,
    last_login_method   TEXT,          -- 'manual_jwt' | 'api_login' | 'jwt_renew'
    jwt_user_id         TEXT,           -- sub claim from current JWT (for /status)
    jwt_exp_at          TIMESTAMPTZ,    -- exp claim; populated on each adopt

    -- Health signals
    last_heartbeat_at   TIMESTAMPTZ,    -- updated by health_ping job every ~5min
    last_heartbeat_ok   BOOLEAN,         -- did the last heartbeat succeed
    session_healthy     BOOLEAN DEFAULT FALSE,  -- consolidated: green light?

    -- Error tracking — last_error_at + last_error give "what broke and when"
    -- without log archaeology. Cleared on next successful operation.
    last_error          TEXT,
    last_error_at       TIMESTAMPTZ,

    -- FS metadata
    fs_session_name     TEXT DEFAULT 'coolbet_prod',
    fs_url              TEXT,           -- last FLARESOLVERR_URL the bot used

    -- Cookie harvest state
    cookies_last_refresh_at  TIMESTAMPTZ,
    cookies_count_last       INTEGER,    -- how many cookies were in the last harvest

    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT singleton_row CHECK (id = 1)
);

-- Seed the row so upserts can always assume it exists.
INSERT INTO coolbet_session_state (id) VALUES (1)
ON CONFLICT (id) DO NOTHING;

-- Trigger auto-bumps updated_at on every UPDATE. Less error-prone than
-- relying on every writer to remember to set it.
CREATE OR REPLACE FUNCTION coolbet_session_state_touch() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS coolbet_session_state_touch_trg ON coolbet_session_state;
CREATE TRIGGER coolbet_session_state_touch_trg
    BEFORE UPDATE ON coolbet_session_state
    FOR EACH ROW EXECUTE FUNCTION coolbet_session_state_touch();

-- Public read so /admin pages + Telegram command handlers (which use anon
-- key for public reads) can SELECT without service_role. Writes still
-- require service_role.
ALTER TABLE coolbet_session_state ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS coolbet_session_state_anon_read ON coolbet_session_state;
CREATE POLICY coolbet_session_state_anon_read ON coolbet_session_state
    FOR SELECT USING (true);

COMMENT ON TABLE coolbet_session_state IS
    'Singleton observable state for the Coolbet+FlareSolverr session. '
    'Updated by workers/automation/coolbet_session.py on login/error/heartbeat. '
    'Queried by admin pages, Telegram /status, and the health alert cron.';
