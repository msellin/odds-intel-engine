-- 457 — #162 W5.3 (2026-09-26): pick_sends — ONE row per (channel, pick) that any customer-facing
-- pick sender tried to put in front of a reader.
--
-- WHY. Nothing recorded which picks were actually SENT (audits B-publishing R3, A-producers R10):
-- the VIP senders deduped on an in-memory 600 s dict (workers/notify/telegram.py `_LAST_SENT`,
-- wiped on every scheduler restart — RELIABILITY_LEDGER #13) and threw the message id away, and the
-- signaler's public post kept no message id at all. "Which picks went to the VIP channel last week"
-- was unanswerable, and a restart could double-send.
--
-- HOW. Every pick send goes through workers/notify/pick_sender.py `send_pick()`, which (1) checks
-- `publishing_paused`, (2) checks the bot's distribution (bot_distribution.sent_public for the public
-- channel, .vip_channel for the VIP channel / Pro+Elite DMs), (3) CLAIMS this row before the send and
-- finalises it after, and (4) dedupes on the unique index below — so the DB, not a process, decides
-- "already sent". A 'skipped' / 'failed' row may be re-claimed by a later pass; a 'sent' or
-- 'sending' row never is (a crash between the Telegram POST and the finalise leaves 'sending' —
-- treated as sent, because a duplicate message is the worse failure).
--
-- PRIVATE: operator audit data. No anon / authenticated access (the default ACL would grant
-- authenticated arwd, so it is revoked explicitly); the engine writes as the owner role.

SET lock_timeout = '3s';

CREATE TABLE IF NOT EXISTS pick_sends (
    id            bigserial PRIMARY KEY,
    channel       text NOT NULL CHECK (channel IN ('public', 'vip_channel', 'vip_dm')),
    bot_name      text NOT NULL,
    pick_table    text NOT NULL CHECK (pick_table IN ('simulated_bets', 'picks_forward_test')),
    pick_id       uuid NOT NULL,
    match_id      uuid,
    market        text,
    selection     text,
    status        text NOT NULL CHECK (status IN ('sending', 'sent', 'failed', 'skipped')),
    reason        text,             -- why skipped / failed (NULL when sent)
    message_id    bigint,           -- Telegram message id (public / vip_channel)
    recipients    integer,          -- users reached (vip_dm)
    attempts      integer NOT NULL DEFAULT 1,
    first_attempt_at timestamptz NOT NULL DEFAULT now(),
    attempted_at  timestamptz NOT NULL DEFAULT now(),
    sent_at       timestamptz
);

-- THE dedupe: one row per channel per pick.
CREATE UNIQUE INDEX IF NOT EXISTS pick_sends_channel_pick_uq
    ON pick_sends (channel, pick_table, pick_id);
CREATE INDEX IF NOT EXISTS pick_sends_sent_at_idx ON pick_sends (sent_at DESC) WHERE status = 'sent';
CREATE INDEX IF NOT EXISTS pick_sends_match_idx ON pick_sends (match_id, market, selection);

COMMENT ON TABLE pick_sends IS
  '#162 W5.3: every customer-facing pick send (public channel, VIP channel, Pro/Elite DMs), written by workers/notify/pick_sender.send_pick. Unique on (channel, pick_table, pick_id) = the DB dedupe. Private (service_role only).';

ALTER TABLE pick_sends ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON pick_sends FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE pick_sends_id_seq FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON pick_sends TO service_role;
GRANT USAGE, SELECT ON SEQUENCE pick_sends_id_seq TO service_role;
