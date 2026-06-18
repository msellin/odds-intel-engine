-- COOLBET-AUTO-LOGIN-ON-HEAL (2026-06-18)
--
-- Rate-limit tracking for the `cdp_auto_login` step now integrated into
-- auto_self_heal's logged_out branch. Without rate limiting, a sustained
-- outage (Coolbet rotates device trust → SMS required → operator misses
-- the notification → daemon keeps retrying) could trigger one form-submit
-- per consecutive-error burst. Bounded by once-per-hour AND once-per-burst
-- (the daemon's in-process alert_fired_this_burst guard already gates the
-- per-burst case; this column gates the cross-burst case).
--
-- One column, no separate table — the singleton state row already exists
-- and adding a column is cheap.

ALTER TABLE coolbet_session_state
    ADD COLUMN IF NOT EXISTS last_auto_login_attempt_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_auto_login_outcome    TEXT;
                                                 -- 'success' / 'sms_timeout' /
                                                 -- 'error' / 'rate_limited'

COMMENT ON COLUMN coolbet_session_state.last_auto_login_attempt_at IS
    'Timestamp of the most recent auto_self_heal attempt at cdp_auto_login.
     Rate-limited to once per hour to bound SMS exposure in the unlikely
     case Coolbet rotates device trust and starts requiring SMS again.';
