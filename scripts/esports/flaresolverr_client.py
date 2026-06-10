"""
FlareSolverr client — defeats Cloudflare bot protection by routing requests
through a headless-Chrome container that solves CF challenges automatically.

Run FlareSolverr locally or on Railway:
    docker run -d --name=flaresolverr --restart unless-stopped \\
      -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest

Env override:
    FLARESOLVERR_URL=http://localhost:8191    (default)

Usage:
    from flaresolverr_client import fetch
    html = fetch("https://www.hltv.org/stats/teams/pistols?...")

The session= parameter on FlareSolverr's API lets us reuse the same
solved-CF browser across multiple URLs — much faster than solving fresh
each time. We use a single named session per scraper module.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Optional


FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")
DEFAULT_TIMEOUT_MS = 90000

# Global rate-limit guard. FlareSolverr itself uses a real browser, so HLTV
# sees us as a normal visitor — but we should still pace requests to avoid
# triggering rate-limiting on HLTV's side (or FlareSolverr's own internal
# queue). 6s default gives us ~10 req/min per session, plenty for cron
# scrapers, well below any reasonable rate limit.
#
# Override via FLARESOLVERR_MIN_INTERVAL_S env if needed.
_MIN_INTERVAL_S = float(os.getenv("FLARESOLVERR_MIN_INTERVAL_S", "6.0"))
_last_request_at = 0.0


def is_available() -> bool:
    """Quick GET / to check FlareSolverr is reachable.

    15s timeout — Railway internal DNS can be slow on cold container start.
    Previously 5s caused silent fallback to plain requests, hitting CF 403s.
    """
    try:
        with urllib.request.urlopen(f"{FLARESOLVERR_URL}/", timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"  [flaresolverr] is_available probe failed: {e}")
        return False


def _enforce_rate_limit() -> None:
    """Ensure at least _MIN_INTERVAL_S has elapsed since the previous request.

    Defensive politeness: even though FlareSolverr presents as a real browser,
    bursting hundreds of requests would still flag the IP. This caps us at
    ~10 req/min on the slowest path (6s default interval) — invisible to
    HLTV but more than fast enough for any cron-style backfill.
    """
    global _last_request_at
    now = time.monotonic()
    wait = _MIN_INTERVAL_S - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def fetch(
    url: str,
    *,
    session: str = "hltv_default",
    max_timeout_ms: int = DEFAULT_TIMEOUT_MS,
    retries: int = 2,
    polite_sleep: float = 0.0,  # use global _MIN_INTERVAL_S instead
) -> Optional[str]:
    """Fetch URL through FlareSolverr. Returns HTML string or None on failure.

    Sessions are persistent FlareSolverr browser contexts — first request
    on a new session takes ~5-15s (CF challenge); subsequent calls on the
    same session reuse the warm browser (~1-2s).

    Rate-limited globally by _MIN_INTERVAL_S (default 6s). All callers go
    through the same throttle so concurrent batches stay polite.
    """
    _enforce_rate_limit()

    body = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout_ms,
        "session": session,
    }

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                f"{FLARESOLVERR_URL}/v1",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=max_timeout_ms // 1000 + 30) as resp:
                data = json.loads(resp.read())
            if data.get("status") == "ok":
                if polite_sleep > 0:
                    time.sleep(polite_sleep)
                return (data.get("solution") or {}).get("response")
            last_err = data.get("message", "unknown")
            print(f"  [flaresolverr] attempt {attempt + 1} non-ok: {last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"  [flaresolverr] attempt {attempt + 1} error: {e}")
        if attempt < retries:
            # Backoff increases per attempt
            time.sleep(5 + 5 * attempt)

    print(f"  [flaresolverr] gave up after {retries + 1} attempts: {last_err}")
    return None


def fetch_full(
    url: str,
    *,
    session: str = "hltv_default",
    max_timeout_ms: int = DEFAULT_TIMEOUT_MS,
    retries: int = 2,
) -> Optional[dict]:
    """Like fetch(), but returns the full solution envelope so callers can
    capture cookies + user-agent for plain-requests handoff.

    Returns: {"html": str, "cookies": list[dict], "user_agent": str, "status": int}
    or None on failure.
    """
    _enforce_rate_limit()

    body = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout_ms,
        "session": session,
    }

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                f"{FLARESOLVERR_URL}/v1",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=max_timeout_ms // 1000 + 30) as resp:
                data = json.loads(resp.read())
            if data.get("status") == "ok":
                sol = data.get("solution") or {}
                return {
                    "html": sol.get("response"),
                    "cookies": sol.get("cookies") or [],
                    "user_agent": sol.get("userAgent") or "",
                    "status": sol.get("status") or 0,
                }
            last_err = data.get("message", "unknown")
            print(f"  [flaresolverr] fetch_full attempt {attempt + 1} non-ok: {last_err}")
        except Exception as e:
            last_err = str(e)
            print(f"  [flaresolverr] fetch_full attempt {attempt + 1} error: {e}")
        if attempt < retries:
            time.sleep(5 + 5 * attempt)

    print(f"  [flaresolverr] fetch_full gave up after {retries + 1}: {last_err}")
    return None


def destroy_session(session: str = "hltv_default") -> None:
    """Tear down a FlareSolverr session (releases the browser). Idempotent."""
    try:
        body = {"cmd": "sessions.destroy", "session": session}
        req = urllib.request.Request(
            f"{FLARESOLVERR_URL}/v1",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
