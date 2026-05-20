"""Telegram bot — two-way commands for the Coolbet daemon.

Uses just `requests` (no python-telegram-bot dep) — long-polls /getUpdates
in a background thread, dispatches /command messages to registered handlers.

Daemon embeds it like:

    from workers.notify.telegram_bot import start_listener
    start_listener({
        "/status":  handle_status,
        "/pause":   handle_pause,
        ...
    })

Handlers receive (args: list[str]) and return a string reply (Telegram-
formatted HTML, will be sent back). Returning None = silent ack.

Security: only messages from TELEGRAM_CHAT_ID are accepted. Bot won't
respond to anyone else (you control the bot via @BotFather; anyone
who guesses your bot token can't hijack because they don't match
chat_id). Hard-coded — don't relax.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

import requests

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"

# Public: handlers dict shared across the module
_HANDLERS: dict[str, Callable[[list[str]], Optional[str]]] = {}
_LISTENER_THREAD: Optional[threading.Thread] = None
_STOP_LISTENER = threading.Event()


def _post(token: str, method: str, **payload):
    try:
        r = requests.post(_API.format(token=token, method=method), json=payload, timeout=35)
        if r.status_code != 200:
            log.warning("telegram %s %d: %s", method, r.status_code, r.text[:200])
            return None
        return r.json()
    except Exception as e:
        log.warning("telegram %s raised: %s", method, e)
        return None


def _send_reply(token: str, chat_id: str, msg: str) -> None:
    if not msg:
        return
    _post(token, "sendMessage",
          chat_id=chat_id, text=msg[:4000], parse_mode="HTML")


def _listener_loop(token: str, allowed_chat_id: str) -> None:
    """Long-poll getUpdates, dispatch /cmd messages to _HANDLERS."""
    last_id = 0
    # Drain existing updates on startup so we don't process week-old commands
    init = _post(token, "getUpdates", timeout=0, offset=-1)
    if init and init.get("result"):
        last_id = max((u["update_id"] for u in init["result"]), default=0)

    log.info("telegram bot listener up (chat_id whitelist: %s)", allowed_chat_id)

    while not _STOP_LISTENER.is_set():
        updates = _post(token, "getUpdates", timeout=30, offset=last_id + 1)
        if not updates or not updates.get("result"):
            continue
        for upd in updates["result"]:
            last_id = max(last_id, upd["update_id"])
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = msg.get("chat") or {}
            text = (msg.get("text") or "").strip()
            sender_chat = str(chat.get("id") or "")

            # Reject anyone not the configured operator
            if sender_chat != str(allowed_chat_id):
                log.warning("telegram: rejected message from chat %s", sender_chat)
                continue

            if not text.startswith("/"):
                continue

            parts = text.split()
            cmd  = parts[0].split("@")[0].lower()   # strip @botname suffix
            args = parts[1:]

            handler = _HANDLERS.get(cmd)
            if handler is None:
                _send_reply(token, sender_chat,
                            f"Unknown command <code>{cmd}</code>. Try /help.")
                continue
            try:
                reply = handler(args)
            except Exception as e:
                log.warning("handler %s raised: %s", cmd, e)
                reply = f"❌ <code>{cmd}</code> raised: <pre>{e}</pre>"
            if reply:
                _send_reply(token, sender_chat, reply)


def start_listener(handlers: dict[str, Callable[[list[str]], Optional[str]]]) -> bool:
    """Spawn the long-polling listener in a background thread. Returns True if
    started, False if config missing or already running.

    `handlers` maps "/cmd" → fn(args: list[str]) -> Optional[str]. Reply
    string is sent back to the user; None = silent ack.
    """
    global _LISTENER_THREAD, _HANDLERS
    if _LISTENER_THREAD is not None and _LISTENER_THREAD.is_alive():
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("telegram bot listener skipped — TELEGRAM_BOT_TOKEN/CHAT_ID not set")
        return False
    _HANDLERS.update(handlers)
    _STOP_LISTENER.clear()
    _LISTENER_THREAD = threading.Thread(
        target=_listener_loop, args=(token, chat_id), daemon=True, name="tg-bot",
    )
    _LISTENER_THREAD.start()
    return True


def stop_listener() -> None:
    _STOP_LISTENER.set()
