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


def is_available() -> bool:
    """Quick GET / to check FlareSolverr is reachable."""
    try:
        with urllib.request.urlopen(f"{FLARESOLVERR_URL}/", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def fetch(
    url: str,
    *,
    session: str = "hltv_default",
    max_timeout_ms: int = DEFAULT_TIMEOUT_MS,
    retries: int = 2,
    polite_sleep: float = 1.0,
) -> Optional[str]:
    """Fetch URL through FlareSolverr. Returns HTML string or None on failure.

    Sessions are persistent FlareSolverr browser contexts — first request
    on a new session takes ~5-15s (CF challenge); subsequent calls on the
    same session reuse the warm browser (~1-2s).
    """
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
            time.sleep(3)

    print(f"  [flaresolverr] gave up after {retries + 1} attempts: {last_err}")
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
