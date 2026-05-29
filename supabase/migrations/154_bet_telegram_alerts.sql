-- BET-TELEGRAM-ALERTS 2026-05-29
--
-- Side table mapping each per-bet Telegram alert message back to its
-- simulated_bet_id. Lets the auto-record step + the manual-placement drain
-- edit each alert in-place with the recording outcome (✓ €X.XX placed /
-- ✗ no_event / ✗ no_market), so the admin scrolling the chat sees per-bet
-- status without cross-referencing.
--
-- One row per send. The pipeline only writes once per (match, market,
-- selection) because _tele_bets dedups across bots, but we don't enforce
-- uniqueness here — an inplay re-alert is fine to append a second row.

CREATE TABLE IF NOT EXISTS bet_telegram_alerts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    simulated_bet_id  UUID NOT NULL REFERENCES simulated_bets(id) ON DELETE CASCADE,
    chat_id           BIGINT NOT NULL,
    message_id        BIGINT NOT NULL,
    original_text     TEXT NOT NULL DEFAULT '',
    sent_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Lookup is always "find the latest message for this bet"
CREATE INDEX IF NOT EXISTS idx_bet_telegram_alerts_simbet
    ON bet_telegram_alerts (simulated_bet_id, sent_at DESC);

COMMENT ON TABLE bet_telegram_alerts IS
    'Per-bet Telegram alert message_ids — used to edit each alert in place with the recording outcome.';
