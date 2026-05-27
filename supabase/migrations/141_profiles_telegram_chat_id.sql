-- USER-TELE-NOTIFY: store the Telegram chat_id for users who connect their account.
-- Written by the webhook handler (service_role) when a user sends /start {uuid} to the bot.
-- Nulled on /stop or via the "Disconnect" button in profile settings.
ALTER TABLE profiles
  ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT DEFAULT NULL;
