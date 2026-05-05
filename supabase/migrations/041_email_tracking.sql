-- ============================================================================
-- INFRA-8: Email open/click tracking
-- Adds last_email_opened_at + last_email_clicked_at to profiles
-- for churn detection (14-day silence = at-risk user).
-- Populated by Resend webhook events via /api/resend-webhook.
-- ============================================================================

ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS last_email_opened_at  timestamptz,
  ADD COLUMN IF NOT EXISTS last_email_clicked_at timestamptz;

COMMENT ON COLUMN profiles.last_email_opened_at  IS 'Last time this user opened any Resend email. Updated via /api/resend-webhook.';
COMMENT ON COLUMN profiles.last_email_clicked_at IS 'Last time this user clicked a link in any Resend email. Updated via /api/resend-webhook.';
