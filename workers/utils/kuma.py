"""
Uptime Kuma push monitors — send a heartbeat when a scheduled job succeeds.

Kuma push monitors work like healthchecks.io: each monitor exposes a URL
that our job hits after success. If Kuma doesn't see a ping within the
grace window, it fires an alert (channels configured in Kuma UI).

Config (env):
  KUMA_URL_BASE   — e.g. https://status.oddsintel.app/api/push
                    Unset → all pings are silent no-ops (dev-safe).
  KUMA_TOKENS     — JSON map {job_id: kuma_token}, e.g.
                    {"morning_pipeline":"abc123","settlement":"xyz789"}
                    Missing token for a job = silent no-op for that job.

Usage:
    from workers.utils.kuma import push, monitor

    # Direct call at the end of a successful run:
    push("morning_pipeline", ping_ms=45231)

    # Or as a decorator on the job wrapper:
    @monitor("morning_pipeline")
    def job_morning():
        ...

The decorator fires status=up on clean return (with runtime as ping), and
status=down on any raised exception (then re-raises so APScheduler's own
error handling still runs). Failures in the ping itself never crash the
calling job.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from functools import wraps
from typing import Callable, Optional

from rich.console import Console

console = Console()

_URL_BASE = os.getenv("KUMA_URL_BASE", "").rstrip("/")


def _load_tokens() -> dict[str, str]:
    raw = os.getenv("KUMA_TOKENS", "").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
        if not isinstance(loaded, dict):
            console.print("[yellow]KUMA_TOKENS must be a JSON object — pings disabled[/yellow]")
            return {}
        return {str(k): str(v) for k, v in loaded.items()}
    except json.JSONDecodeError as e:
        console.print(f"[yellow]KUMA_TOKENS parse error: {e} — pings disabled[/yellow]")
        return {}


_TOKENS: dict[str, str] = _load_tokens()


def push(job_id: str, *, status: str = "up", msg: str = "OK",
         ping_ms: Optional[int] = None, timeout: float = 3.0) -> None:
    """Fire an Uptime Kuma push. Silent no-op if unconfigured or on error.

    Never raises — a monitor URL that's unreachable must not take down
    the actual scheduled job.
    """
    if not _URL_BASE:
        return
    token = _TOKENS.get(job_id)
    if not token:
        return

    params: dict[str, str] = {"status": status, "msg": msg}
    if ping_ms is not None:
        params["ping"] = str(int(ping_ms))
    url = f"{_URL_BASE}/{token}?{urllib.parse.urlencode(params)}"

    try:
        urllib.request.urlopen(url, timeout=timeout).read()
    except Exception as exc:
        console.print(f"[dim]kuma push {job_id} failed: {exc}[/dim]")


def monitor(job_id: str) -> Callable:
    """Decorator: ping Kuma on success (with runtime), 'down' on exception.

    Re-raises the exception so APScheduler still sees the job as failed
    and normal error handling (Telegram alerts, pipeline_runs row) runs.
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.time()
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:
                push(job_id, status="down",
                     msg=f"{type(exc).__name__}: {str(exc)[:120]}")
                raise
            push(job_id, status="up", msg="OK",
                 ping_ms=int((time.time() - t0) * 1000))
            return result
        return wrapper
    return decorator
