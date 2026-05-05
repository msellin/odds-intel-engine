-- ENG-8: Watchlist signal alerts
-- Adds watchlist_alerts_enabled to user_notification_settings + alert log table
-- ============================================================================

-- Add watchlist alert opt-in column (default true)
ALTER TABLE user_notification_settings
  ADD COLUMN IF NOT EXISTS watchlist_alerts_enabled boolean NOT NULL DEFAULT true;

-- Log one row per alert type per user per match to prevent duplicate alerts
CREATE TABLE IF NOT EXISTS watchlist_alert_log (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
  match_id      uuid NOT NULL,
  alert_type    text NOT NULL,  -- 'kickoff_reminder' | 'odds_move'
  sent_at       timestamptz NOT NULL DEFAULT now(),
  resend_id     text,
  email_to      text,
  status        text NOT NULL DEFAULT 'sent',
  error_msg     text,
  UNIQUE (user_id, match_id, alert_type)
);

ALTER TABLE watchlist_alert_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can read own alert log"
  ON watchlist_alert_log FOR SELECT
  USING (auth.uid() = user_id);
