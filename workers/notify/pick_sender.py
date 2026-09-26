"""ONE AUDITED PICK SENDER — #162 W5.3 (2026-09-26).

WHAT. `send_pick()` is the only way a pick reaches a customer: the public channel
(@oddsintelpicks), the private VIP channel, or the Pro/Elite DMs. Every caller —
the model signaler (workers/automation/coolbet_signaler.py), the forward-test publisher
(workers/scheduler.py job + scripts/publish_picks_forward_test.py --send), the pipeline's VIP
1X2 branch (daily_pipeline_v2) and the O/U EARLY VIP send (workers/jobs/ou_sharp_outlier.py) —
hands it (channel, bot, pick ref, text) and it:

  1. checks the operator pause (`coolbet_session_state.publishing_paused`, /pausepicks) — for
     EVERY channel; the VIP senders used to ignore it;
  2. checks the bot's distribution (view `bot_distribution`, #155): `sent_public` for the
     public channel, `vip_channel` for the VIP channel and the DMs; for the PUBLIC channel also
     THE public-Telegram rule ([[#174]], `bot_status.public_channel_skip_reason`): ACTIVE
     always ([[#175]]: was BETA / CALIBRATED), TESTING only at EV >= 5% (callers pass `ev`);
  3. claims a `pick_sends` row (migration 457) BEFORE the send and finalises it after
     (message id / recipients, sent / failed + reason); skips are recorded with their reason;
  4. dedupes in the DB (unique on channel + pick ref), so a scheduler restart can never
     send the same pick to the same channel twice. It replaces the in-memory 600 s
     `_LAST_SENT` dedupe the VIP DMs used, and "which picks were sent" is now a query.

WHY ONE SENDER. Five send paths each re-implemented (or forgot) the pause and the
distribution rule — the VIP paths read neither — and none recorded the VIP send at all
(audits B-publishing R3, A-producers R10; RELIABILITY_LEDGER "second code path").

FAILURE POLICY (a bug here silences the channel, so it is deliberate and asymmetric):
  * pause / distribution UNREADABLE -> do NOT send, record why (fail CLOSED: a pick we cannot
    prove may go out does not go out). Note this is stricter than `is_publishing_paused()`,
    which falls open for its other callers' logging.
  * the AUDIT write fails (claim or finalise) -> SEND ANYWAY, log at ERROR (fail OPEN: losing
    the audit row must not mute customers). An in-process set then stands in for the DB
    dedupe until the DB is back.
  * a 'sending' row (crash between the Telegram POST and the finalise) is treated as sent and
    never re-claimed — a duplicate message is the worse failure. 'failed' / 'skipped' rows are
    re-claimed by a later pass.

Operator-only messages (alerts, summaries, the owner's per-pick prompt, the day-one header)
are NOT picks and do not come through here.
"""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

log = logging.getLogger(__name__)

CHANNEL_PUBLIC = "public"
CHANNEL_VIP = "vip_channel"
CHANNEL_VIP_DM = "vip_dm"
# Which bot_distribution column permits each channel — THE distribution rule for sends.
CHANNEL_DISTRIBUTION = {
    CHANNEL_PUBLIC: "sent_public",
    CHANNEL_VIP: "vip_channel",
    CHANNEL_VIP_DM: "vip_channel",
}
PICK_TABLES = ("simulated_bets", "picks_forward_test")

# Stand-in dedupe for when the audit table is unreachable (fail open) — never the primary.
_FALLBACK_SENT: set[tuple[str, str, str]] = set()


class PickSend(NamedTuple):
    status: str                   # sent | failed | skipped
    message_id: Optional[int] = None
    recipients: Optional[int] = None
    reason: Optional[str] = None

    @property
    def sent(self) -> bool:
        return self.status == "sent"


def _pause_block() -> Optional[str]:
    """None = not paused. A reason string = do not send (paused, or unreadable)."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT publishing_paused, publishing_paused_reason FROM coolbet_session_state WHERE id = 1")
    except Exception as e:  # noqa: BLE001 — fail CLOSED
        log.warning("send_pick: publishing_paused unreadable — not sending: %s", e)
        return "pause_unreadable"
    if rows and rows[0].get("publishing_paused"):
        return "paused: " + (rows[0].get("publishing_paused_reason") or "no reason given")
    return None


def _distribution_block(channel: str, bot: str, ev=None) -> Optional[str]:
    """None = the bot's status allows this channel. A reason string = do not send.
    [[#174]] for the PUBLIC channel the bot's status must also pass THE public-Telegram rule
    (bot_status.public_channel_skip_reason): ACTIVE always, TESTING only at EV >= 5%.
    A TESTING pick sent without its `ev` is refused (fail closed)."""
    col = CHANNEL_DISTRIBUTION[channel]
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            f"SELECT {col} AS ok, label, status FROM bot_distribution WHERE bot_name = %s", (bot,))
    except Exception as e:  # noqa: BLE001 — fail CLOSED
        log.warning("send_pick: bot_distribution unreadable — not sending %s: %s", bot, e)
        return "distribution_unreadable"
    if not rows:
        return "bot_unknown"
    if not rows[0]["ok"]:
        return f"not_distributed: {rows[0].get('label')} does not send to {channel}"
    if channel == CHANNEL_PUBLIC:
        from workers.utils.bot_status import public_channel_skip_reason
        return public_channel_skip_reason(rows[0].get("status"), ev)
    return None


_UPSERT = """
    INSERT INTO pick_sends (channel, bot_name, pick_table, pick_id, match_id, market, selection,
                            status, reason)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (channel, pick_table, pick_id) DO UPDATE
       SET status = EXCLUDED.status, reason = EXCLUDED.reason, bot_name = EXCLUDED.bot_name,
           attempts = pick_sends.attempts + 1, attempted_at = now()
     WHERE pick_sends.status IN ('failed', 'skipped')
    RETURNING id
"""


def _record(status: str, reason: Optional[str], key: tuple, meta: tuple) -> Optional[list]:
    """Upsert a 'sending' claim or a 'skipped' row. Returns the RETURNING rows ([] = a 'sent' /
    'sending' row already holds this key), or None when the audit table is unreachable."""
    channel, table, pick_id = key
    bot, match_id, market, selection = meta
    try:
        from workers.api_clients.db import execute_write_returning
        return execute_write_returning(
            _UPSERT, (channel, bot, table, pick_id, match_id, market, selection, status, reason))
    except Exception as e:  # noqa: BLE001 — fail OPEN on the audit only
        log.error("PICK-SENDS AUDIT WRITE FAILED (%s %s %s/%s): %s", status, channel, table, pick_id, e)
        return None


def _finalise(row_id, status: str, reason, message_id, recipients) -> None:
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            """UPDATE pick_sends SET status = %s, reason = %s, message_id = %s, recipients = %s,
                      sent_at = CASE WHEN %s = 'sent' THEN now() END, attempted_at = now()
                WHERE id = %s""",
            (status, reason, message_id, recipients, status, row_id))
    except Exception as e:  # noqa: BLE001 — the message is already out (or not); only the audit is lost
        log.error("PICK-SENDS AUDIT FINALISE FAILED (row %s -> %s): %s", row_id, status, e)


# The env each transport needs; an unset target is a SKIP (re-claimable once configured), not a
# failure — the VIP channel does not exist until TELEGRAM_VIP_CHAT_ID is set (#148).
_CHANNEL_ENV = {
    CHANNEL_PUBLIC: ("TELEGRAM_BOT_TOKEN", "TELEGRAM_PUBLIC_CHANNEL"),
    CHANNEL_VIP: ("TELEGRAM_BOT_TOKEN", "TELEGRAM_VIP_CHAT_ID"),
    CHANNEL_VIP_DM: ("TELEGRAM_BOT_TOKEN",),
}


def _unconfigured(channel: str) -> Optional[str]:
    import os
    missing = [v for v in _CHANNEL_ENV[channel] if not (os.getenv(v) or "").strip()]
    return ("channel_not_configured: " + ", ".join(missing)) if missing else None


def _no_audience(channel: str) -> Optional[str]:
    """[[#162]] review 2026-09-26: a VIP DM with NOBODY to send to is not a failed send — it was
    recorded 'failed' ('no recipients reached') on every VIP pick, 58 in the first hours, so any
    failure metric read as broken delivery. Same audience rule as telegram.send_telegram_to_users
    (Pro/Elite with a linked chat). Unreadable → None: the send is attempted and its own outcome
    recorded (this check can only reclassify an empty audience, never block a real one)."""
    if channel != CHANNEL_VIP_DM:
        return None
    try:
        from workers.api_clients.db import execute_query
        from workers.notify.telegram import _TIER_SETS
        r = execute_query("SELECT count(*) AS n FROM profiles WHERE telegram_chat_id IS NOT NULL "
                          "AND tier::text = ANY(%s)", (list(_TIER_SETS.get("pro", ("pro", "elite"))),))
        return "no_audience: no Pro/Elite user has linked Telegram" if r and int(r[0]["n"]) == 0 else None
    except Exception:  # noqa: BLE001
        return None


def _deliver(channel: str, text: str, silent: bool, reply_markup: Optional[dict]):
    """(message_id, recipients) from the channel's transport. Never raises."""
    from workers.notify import telegram as tg
    try:
        if channel == CHANNEL_PUBLIC:
            return tg.send_telegram_public(text, silent=silent, reply_markup=reply_markup), None
        if channel == CHANNEL_VIP:
            return tg.send_telegram_vip(text, silent=silent), None
        return None, tg.send_telegram_to_users(text, tier_minimum="pro")
    except Exception as e:  # noqa: BLE001
        log.warning("send_pick: %s transport raised: %s", channel, e)
        return None, None


def send_pick(channel: str, bot: str, pick_table: str, pick_id, text: str, *,
              match_id=None, market: Optional[str] = None, selection: Optional[str] = None,
              skip_reason: Optional[str] = None, silent: bool = False,
              reply_markup: Optional[dict] = None, ev=None) -> PickSend:
    """Send one pick to one customer channel — pause, distribution, audit row, DB dedupe.
    Never raises (except ValueError on an unknown channel/table — a programming error).
    `skip_reason`: the CALLER already decided not to send (e.g. #164 held back, or the pause it
    read at the start of its pass) — nothing is sent, the skip is recorded with that reason, so
    pick_sends also says why a recorded pick never went out. It can only ever PREVENT a send.
    `ev`: the pick's expected return (bot probability x published odds - 1). Required for a
    TESTING bot's pick on the PUBLIC channel ([[#174]] — sent only at EV >= 5%)."""
    if channel not in CHANNEL_DISTRIBUTION or pick_table not in PICK_TABLES:
        raise ValueError(f"send_pick: unknown channel/table {channel!r}/{pick_table!r}")
    key = (channel, pick_table, str(pick_id))
    meta = (bot, str(match_id) if match_id else None, market, selection)

    block = (skip_reason or _pause_block() or _distribution_block(channel, bot, ev) or _unconfigured(channel)
             or _no_audience(channel))
    if block:
        _record("skipped", block, key, meta)
        return PickSend("skipped", reason=block)

    claim = _record("sending", None, key, meta)
    if claim == [] or (claim is None and key in _FALLBACK_SENT):
        return PickSend("skipped", reason="duplicate: already sent to this channel")

    message_id, recipients = _deliver(channel, text, silent, reply_markup)
    ok = (message_id is not None) if channel != CHANNEL_VIP_DM else bool(recipients)
    status = "sent" if ok else "failed"
    reason = None if ok else ("no recipients reached" if channel == CHANNEL_VIP_DM
                              else "transport returned no message id")
    if ok:
        _FALLBACK_SENT.add(key)
    if claim:
        _finalise(claim[0]["id"], status, reason, message_id, recipients)
    return PickSend(status, message_id=message_id, recipients=recipients, reason=reason)


def send_vip_pick(bot: str, pick_id, text: str, *, match_id=None, market=None,
                  selection=None) -> dict[str, PickSend]:
    """A VIP pick goes to the Pro/Elite DMs AND the private VIP channel (#148) — two channels,
    two audit rows, one call so the VIP senders cannot diverge."""
    kw = dict(match_id=match_id, market=market, selection=selection)
    return {CHANNEL_VIP_DM: send_pick(CHANNEL_VIP_DM, bot, "simulated_bets", pick_id, text, **kw),
            CHANNEL_VIP: send_pick(CHANNEL_VIP, bot, "simulated_bets", pick_id, text, **kw)}
