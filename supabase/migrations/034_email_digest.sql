-- ============================================================================
-- ENG-4: Email digest infrastructure
-- Adds email_digest_enabled to user_notification_settings + log table
-- ============================================================================

-- Add digest opt-in column (default true — new users are subscribed)
ALTER TABLE user_notification_settings
  ADD COLUMN IF NOT EXISTS email_digest_enabled boolean NOT NULL DEFAULT true;

-- Log one row per user per send to prevent duplicates + enable analytics
CREATE TABLE IF NOT EXISTS email_digest_log (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  digest_date   date NOT NULL,
  tier          text NOT NULL,                -- free / pro / elite
  sent_at       timestamptz NOT NULL DEFAULT now(),
  resend_id     text,                         -- Resend message ID for tracking
  email_to      text NOT NULL,
  status        text NOT NULL DEFAULT 'sent', -- sent / failed / skipped
  error_msg     text,
  preview_count smallint DEFAULT 0,
  value_bet_count smallint DEFAULT 0,

  UNIQUE(user_id, digest_date)
);

CREATE INDEX IF NOT EXISTS idx_email_digest_log_date   ON email_digest_log(digest_date);
CREATE INDEX IF NOT EXISTS idx_email_digest_log_user   ON email_digest_log(user_id);

ALTER TABLE email_digest_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own digest log"
  ON email_digest_log FOR SELECT
  USING (auth.uid() = user_id);
