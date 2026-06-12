-- COOLBET-SIGNALER-A-BUTTONS (2026-06-12): track operator action on each
-- bet-signal. The Telegram signal includes inline buttons "✅ Placed" /
-- "⏭ Skip" — tapping either fires a callback that lands in the webhook
-- and updates one of these columns. The signaler reads them to render
-- a status footer on the message itself ("✓ Marked placed at 09:15 UTC")
-- so the chat history doubles as a placement log.
--
-- Why a column instead of real_bets: the operator places manually, so
-- there's no Coolbet ticket/odds/stake to write to real_bets — those
-- live in their Coolbet account, not our DB. user_placed_at is a thin
-- "yes I did it" marker; the future Mac daemon (option B) will write
-- the full real_bets row when it places auto. The two columns are
-- independent: a bet can be marked placed manually then later have a
-- real_bets row added if it shows up via settlement.

ALTER TABLE simulated_bets
    ADD COLUMN IF NOT EXISTS user_placed_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS user_skipped_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS signal_message_id BIGINT;

COMMENT ON COLUMN simulated_bets.user_placed_at IS
    'Set by the Telegram "✅ Placed" button callback. The operator manually '
    'placed this bet (or its sibling — same match/market/selection). Mutually '
    'exclusive with user_skipped_at in practice but not enforced.';

COMMENT ON COLUMN simulated_bets.user_skipped_at IS
    'Set by the Telegram "⏭ Skip" button callback. Operator decided not to '
    'place this signal (odds moved, fat-finger risk, lost interest). Keeps '
    'the signal from re-firing if signaled_at is later cleared for debug.';

COMMENT ON COLUMN simulated_bets.signal_message_id IS
    'Telegram message_id of the bet-signal. Stored so the callback handler '
    'can edit the original message to append the placement status footer.';
