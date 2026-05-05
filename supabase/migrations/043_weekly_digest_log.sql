-- ============================================================================
-- ENG-10: Weekly performance email log table
-- Prevents duplicate sends (one per user per week).
-- ============================================================================

CREATE TABLE IF NOT EXISTS weekly_digest_log (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  week_start    date NOT NULL,   -- Monday of the week covered (ISO: Monday-based)
  tier          text NOT NULL,
  sent_at       timestamptz NOT NULL DEFAULT now(),
  resend_id     text,
  email_to      text NOT NULL,
  status        text NOT NULL DEFAULT 'sent',
  error_msg     text,

  UNIQUE(user_id, week_start)
);

CREATE INDEX IF NOT EXISTS idx_weekly_digest_log_week   ON weekly_digest_log(week_start);
CREATE INDEX IF NOT EXISTS idx_weekly_digest_log_user   ON weekly_digest_log(user_id);

ALTER TABLE weekly_digest_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own weekly digest log"
  ON weekly_digest_log FOR SELECT
  USING (auth.uid() = user_id);
