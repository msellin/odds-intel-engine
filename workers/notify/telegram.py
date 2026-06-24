"""Send-only Telegram notifications.

User flow: create a bot via @BotFather → get TOKEN; send /start to bot
from your account → curl getUpdates to read CHAT_ID; store both in .env.

Env vars (both required for any sending — daemon silently skips if either
missing, so absence is non-blocking for dev):
    TELEGRAM_BOT_TOKEN     bot token from @BotFather
    TELEGRAM_CHAT_ID       your chat id (numeric)
    TELEGRAM_PREFIX        optional message prefix (default "[OI]"; useful
                           if you reuse the same bot across multiple projects)

Single public function: send_telegram(msg). Failures log a warning and
return False — never raise. Designed to be sprinkled at alert sites
without operators worrying about it crashing the daemon.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_LAST_SENT: dict[str, float] = {}  # dedup window: key → unix ts of last send

_TIER_SETS = {
    "pro":   ("pro", "elite"),
    "elite": ("elite",),
}


def get_elite_30d_clv() -> float | None:
    """GROWTH-CLV-FIRST-MESSAGING (2026-06-05) — pulls the 30-day rolling CLV
    of the elite cohort from dashboard_cache for the alert footer.

    Returns float (e.g. 9.8 for "+9.8%") or None if dashboard_cache is
    empty / stale / unreachable. Best-effort: any error returns None so
    the caller falls back to a static footer.
    """
    try:
        import json
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT elite_value_bets_30d FROM dashboard_cache "
            "ORDER BY computed_at DESC LIMIT 1"
        )
        if not rows:
            return None
        blob = rows[0].get("elite_value_bets_30d")
        if blob is None:
            return None
        # Column is jsonb — psycopg2 may return dict already, or str
        if isinstance(blob, str):
            blob = json.loads(blob)
        pct = blob.get("clv_pct")
        if pct is None:
            return None
        return float(pct)
    except Exception as e:
        log.debug("get_elite_30d_clv failed (non-fatal): %s", e)
        return None


def clv_footer_line(clv_pct: float | None = None) -> str:
    """GROWTH-CLV-FIRST-MESSAGING (2026-06-05) — one-line CLV-first footer
    for user-facing alerts. The CLV moat doesn't help if every alert only
    shows hit-rate and never reinforces the metric we want users to
    judge us by.

    If `clv_pct` is None and dashboard_cache has a fresh number, we'll
    fetch it inline. If both fail, we render a static link-only fallback
    so the footer is never empty (worst case is a quieter line, never a
    broken alert)."""
    if clv_pct is None:
        clv_pct = get_elite_30d_clv()
    if clv_pct is None:
        return "📊 CLV-tracked · oddsintel.app/performance"
    sign = "+" if clv_pct > 0 else ""
    return f"📊 {sign}{clv_pct:.1f}% CLV (30d) · oddsintel.app/performance"


def send_telegram(
    msg: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_window_s: int = 600,
    silent: bool = False,
    reply_markup: Optional[dict] = None,
) -> Optional[int]:
    """Send `msg` to the configured Telegram chat.

    dedup_key + dedup_window_s: if set, skip the send when an identical
        key was sent within the last window_s seconds. Prevents Imperva-403
        alerts from firing every minute of the outage; one alert per 10
        min instead. Use a stable key like "imperva-403" or "place-success".
        Leave unset for one-off messages that should always send.

    silent: maps to Telegram's `disable_notification` — message lands in
        the chat but doesn't ping. Useful for routine summary messages.

    reply_markup (MANUAL-PLACE 2026-05-29): optional Telegram reply_markup
        dict — typically `{"inline_keyboard": [[{"text":..., "callback_data":...}]]}`.
        Forwarded verbatim to sendMessage.

    Returns the Telegram message_id on 200, None otherwise (or when env
    missing / dedup-skipped). Callers that need to edit the message later
    (e.g. MANUAL-PLACE) store this id alongside the chat id.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None  # no creds — silently skip (don't spam logs)

    if dedup_key is not None:
        last = _LAST_SENT.get(dedup_key, 0)
        if time.time() - last < dedup_window_s:
            return None
        _LAST_SENT[dedup_key] = time.time()

    prefix = os.getenv("TELEGRAM_PREFIX", "[OI]")
    body = f"{prefix} {msg}" if prefix else msg

    payload = {
        "chat_id": chat_id,
        "text": body[:4000],   # Telegram caps at 4096; leave headroom
        "disable_notification": silent,
        "parse_mode": "HTML",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(
            _API_URL.format(token=token),
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            try:
                return int(resp.json().get("result", {}).get("message_id") or 0) or None
            except Exception:
                return None
        log.warning("Telegram sendMessage %d: %s", resp.status_code, resp.text[:200])
        return None
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return None


def send_telegram_public(
    msg: str,
    *,
    silent: bool = False,
    reply_markup: Optional[dict] = None,
) -> Optional[int]:
    """Post `msg` to the PUBLIC Telegram channel (separate from the operator
    chat used by send_telegram).

    Env required:
        TELEGRAM_BOT_TOKEN         same bot token as send_telegram
        TELEGRAM_PUBLIC_CHANNEL    public channel target — accepts either
                                   '@oddsintelpicks' (username) or a numeric
                                   chat_id starting with -100…

    The bot MUST be an admin of the channel with 'Post Messages' permission,
    or Telegram returns 403 "bot is not a member of the channel chat".

    Differences from send_telegram:
      - Different chat target (the channel, not the operator)
      - NO prefix — public messages should look clean, not '[OI] ...'
      - No dedup window — every pick should post once and only once at
        signal time; callers should not retry, and the pipeline gates
        per-bet writes via simulated_bet.signal_message_id
      - Returns the channel-side message_id on success, None on failure
        (and never raises)
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel = os.getenv("TELEGRAM_PUBLIC_CHANNEL")
    if not token or not channel:
        return None  # not configured — silently skip

    payload: dict = {
        "chat_id": channel,
        "text": msg[:4000],
        "disable_notification": silent,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(
            _API_URL.format(token=token),
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            try:
                return int(resp.json().get("result", {}).get("message_id") or 0) or None
            except Exception:
                return None
        log.warning(
            "Telegram public sendMessage %d: %s", resp.status_code, resp.text[:200],
        )
        return None
    except Exception as e:
        log.warning("Telegram public send failed: %s", e)
        return None


def record_bet_alert(
    simulated_bet_id: str,
    message_id: int,
    original_text: str,
    chat_id: int | str | None = None,
) -> None:
    """Persist a (simulated_bet_id → message_id) mapping so the auto-record
    step and the manual-place drain can later edit this alert in place with
    the recording outcome. Stores the original text so the edit can keep the
    bet context visible above the status line. Best-effort: failures log a
    warning and return.
    """
    if not message_id:
        return
    if chat_id is None:
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not chat_id:
            return
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            """
            INSERT INTO bet_telegram_alerts (simulated_bet_id, chat_id, message_id, original_text)
            VALUES (%s, %s, %s, %s)
            """,
            (simulated_bet_id, int(chat_id), int(message_id), original_text or ""),
        )
    except Exception as e:
        log.warning("record_bet_alert(%s) failed: %s", simulated_bet_id, e)


def edit_bet_alert_outcome(simulated_bet_id: str, status_line: str) -> bool:
    """Look up the latest Telegram message for this bet and append a status
    line (replaces the inline button). No-op if no mapping exists. Returns
    True on successful edit.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """
            SELECT chat_id, message_id, original_text
            FROM bet_telegram_alerts
            WHERE simulated_bet_id = %s
            ORDER BY sent_at DESC
            LIMIT 1
            """,
            (simulated_bet_id,),
        )
    except Exception as e:
        log.warning("edit_bet_alert_outcome lookup failed: %s", e)
        return False
    if not rows:
        return False
    chat_id = rows[0]["chat_id"]
    message_id = rows[0]["message_id"]
    original = rows[0].get("original_text") or ""
    # Keep original above the status so admin still sees what the bet was
    new_text = (original + f"\n\n<b>{status_line}</b>") if original else f"<b>{status_line}</b>"
    return edit_telegram_message(
        chat_id, int(message_id),
        new_text,
        remove_buttons=True,
    )


def place_button_markup(simulated_bet_id: str, *, label: str = "📝 Record at Coolbet") -> dict:
    """Build the inline_keyboard markup that triggers MANUAL-PLACE for a bet.

    The webhook at /api/telegram/webhook parses callback_data prefix "place:"
    and queues the placement; only the admin TELEGRAM_CHAT_ID is honored.
    """
    return {
        "inline_keyboard": [[{
            "text": label,
            "callback_data": f"place:{simulated_bet_id}",
        }]]
    }


def edit_telegram_message(
    chat_id: int | str,
    message_id: int,
    text: str,
    *,
    remove_buttons: bool = True,
) -> bool:
    """Edit an earlier sendMessage. Used by MANUAL-PLACE to swap the inline
    button for a "✓ Recorded" / "✗ no_event" status line once placement runs.

    remove_buttons=True replaces reply_markup with an empty inline_keyboard so
    the button can't be tapped again.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return False
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4000],
        "parse_mode": "HTML",
    }
    if remove_buttons:
        payload["reply_markup"] = {"inline_keyboard": []}
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/editMessageText",
            json=payload,
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        log.warning("Telegram editMessageText %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.warning("Telegram editMessageText failed: %s", e)
        return False


def send_telegram_to_users(
    msg: str,
    *,
    tier_minimum: str = "pro",
    dedup_key: Optional[str] = None,
    dedup_window_s: int = 600,
) -> int:
    """Send `msg` to every Pro/Elite user who has connected their Telegram account.

    tier_minimum: "pro" (sends to pro+elite) or "elite" (sends to elite only).
    dedup_key: same semantics as send_telegram — prevents double-sends on pipeline retries.
    Returns the number of users successfully notified.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return 0

    if dedup_key is not None:
        last = _LAST_SENT.get(dedup_key, 0)
        if time.time() - last < dedup_window_s:
            return 0
        _LAST_SENT[dedup_key] = time.time()

    tiers = _TIER_SETS.get(tier_minimum, ("pro", "elite"))

    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT telegram_chat_id FROM profiles WHERE telegram_chat_id IS NOT NULL AND tier::text = ANY(%s)",
            (list(tiers),),
        )
    except Exception as e:
        log.warning("send_telegram_to_users: DB query failed: %s", e)
        return 0

    # ADMIN-NO-DOUBLE-NOTIFY (2026-05-29): admin already gets the per-bet
    # admin alert (with the manual-place inline button); the user broadcast
    # would be a near-identical duplicate. Skip the admin chat here.
    admin_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    sent = 0
    url = _API_URL.format(token=token)
    for row in rows:
        chat_id = row.get("telegram_chat_id")
        if not chat_id:
            continue
        if admin_chat_id and str(chat_id) == str(admin_chat_id):
            continue  # skip duplicate to admin chat
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": msg[:4000],
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                sent += 1
            else:
                log.warning("send_telegram_to_users chat_id=%s: %d %s", chat_id, resp.status_code, resp.text[:100])
        except Exception as e:
            log.warning("send_telegram_to_users chat_id=%s failed: %s", chat_id, e)

    return sent
