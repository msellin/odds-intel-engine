-- WC-F4 (2026-06-04): per-user log for the daily WC preview email.
--
-- Separate from email_digest_log (UNIQUE user_id, digest_date) so a user can
-- receive BOTH the regular OddsIntel daily digest AND the WC tournament
-- preview on the same calendar day without one blocking the other. Both
-- emails carry independent opt-in semantics (the digest has its own gate in
-- user_notification_settings.email_digest_enabled — we reuse that flag for
-- WC opt-in too: anyone with daily emails enabled is in for the WC variant
-- during the tournament window).
--
-- Idempotency: UNIQUE (user_id, email_date) — one WC email per user per day.
-- The wc_daily_email job consults this table before each send and inserts
-- a row immediately after, mirroring the email_digest_log pattern.

CREATE TABLE IF NOT EXISTS wc_email_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       uuid        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    email_date    date        NOT NULL,
    sent_at       timestamptz NOT NULL DEFAULT now(),
    resend_id     text,
    email_to      text        NOT NULL,
    tier          text        NOT NULL DEFAULT 'free',
    status        text        NOT NULL DEFAULT 'sent',  -- sent / failed / skipped
    fixture_count smallint    NOT NULL DEFAULT 0,
    settled_count smallint    NOT NULL DEFAULT 0,
    error_msg     text,
    UNIQUE (user_id, email_date)
);

CREATE INDEX IF NOT EXISTS idx_wc_email_log_date ON wc_email_log (email_date);
CREATE INDEX IF NOT EXISTS idx_wc_email_log_user ON wc_email_log (user_id);

ALTER TABLE wc_email_log ENABLE ROW LEVEL SECURITY;

-- Users can read their own send history (matches the email_digest_log policy).
CREATE POLICY "Users can read own wc email log"
  ON wc_email_log FOR SELECT
  USING (auth.uid() = user_id);

COMMENT ON TABLE wc_email_log IS
    'WC-F4 (2026-06-04): per-user/per-day dedupe + audit for the WC2026 daily '
    'preview email. UNIQUE(user_id, email_date) guarantees one send per user '
    'per calendar day even if the 07:30 UTC cron fires twice on a misfire.';
