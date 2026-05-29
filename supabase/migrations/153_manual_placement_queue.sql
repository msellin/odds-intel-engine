-- MANUAL-PLACE 2026-05-29
--
-- Manual-place queue: admin taps a Telegram inline-keyboard button on a
-- value-bet alert; webhook (Vercel) inserts a row here; engine scheduler
-- (Railway) drains the queue every ~10s, runs the placer for that bet
-- in --record mode, and edits the original Telegram message with the
-- outcome.

CREATE TABLE IF NOT EXISTS manual_placement_queue (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    simulated_bet_id      UUID NOT NULL REFERENCES simulated_bets(id) ON DELETE CASCADE,
    requested_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requested_by_chat_id  BIGINT NOT NULL,
    telegram_message_id   BIGINT,
    telegram_chat_id      BIGINT,
    status                TEXT NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    result                TEXT,
    result_detail         TEXT,
    processed_at          TIMESTAMPTZ
);

-- Drain query is `WHERE status = 'pending' ORDER BY requested_at LIMIT n`
CREATE INDEX IF NOT EXISTS idx_manual_placement_queue_pending
    ON manual_placement_queue (requested_at)
    WHERE status = 'pending';

-- Idempotency probe: "is there an in-flight request for this bet right now?"
CREATE INDEX IF NOT EXISTS idx_manual_placement_queue_simbet
    ON manual_placement_queue (simulated_bet_id, status);

COMMENT ON TABLE manual_placement_queue IS
    'Admin-driven on-demand Coolbet --record placements. Vercel webhook inserts, Railway scheduler drains.';
