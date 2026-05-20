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


def send_telegram(
    msg: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_window_s: int = 600,
    silent: bool = False,
) -> bool:
    """Send `msg` to the configured Telegram chat.

    dedup_key + dedup_window_s: if set, skip the send when an identical
        key was sent within the last window_s seconds. Prevents Imperva-403
        alerts from firing every minute of the outage; one alert per 10
        min instead. Use a stable key like "imperva-403" or "place-success".
        Leave unset for one-off messages that should always send.

    silent: maps to Telegram's `disable_notification` — message lands in
        the chat but doesn't ping. Useful for routine summary messages.

    Returns True on 200, False otherwise (or when env missing).
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False  # no creds — silently skip (don't spam logs)

    if dedup_key is not None:
        last = _LAST_SENT.get(dedup_key, 0)
        if time.time() - last < dedup_window_s:
            return False
        _LAST_SENT[dedup_key] = time.time()

    prefix = os.getenv("TELEGRAM_PREFIX", "[OI]")
    body = f"{prefix} {msg}" if prefix else msg

    try:
        resp = requests.post(
            _API_URL.format(token=token),
            json={
                "chat_id": chat_id,
                "text": body[:4000],   # Telegram caps at 4096; leave headroom
                "disable_notification": silent,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        log.warning("Telegram sendMessage %d: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return False
