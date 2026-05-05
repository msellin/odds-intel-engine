-- 046: Value bet alert log
-- Tracks afternoon (16:00) and evening (20:45) value bet alert emails for Pro/Elite users.
-- UNIQUE(user_id, alert_date, slot) prevents double-sending within a slot.

CREATE TABLE value_bet_alert_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    alert_date  date        NOT NULL,
    slot        text        NOT NULL,  -- 'afternoon' or 'evening'
    sent_at     timestamptz NOT NULL DEFAULT now(),
    resend_id   text,
    email_to    text        NOT NULL,
    tier        text        NOT NULL DEFAULT 'pro',
    status      text        NOT NULL DEFAULT 'sent',  -- 'sent', 'failed', 'skipped'
    bet_count   int         NOT NULL DEFAULT 0,
    error_msg   text,
    UNIQUE (user_id, alert_date, slot)
);

CREATE INDEX value_bet_alert_log_user_date ON value_bet_alert_log (user_id, alert_date);
