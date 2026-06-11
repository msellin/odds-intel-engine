#!/usr/bin/env python3
"""
Coolbet session health-ping — runs every ~5 min from the scheduler.

Tests the full auth chain end-to-end:
  1. FlareSolverr reachability
  2. FS session ('coolbet_prod') alive
  3. JWT valid + Imperva cookies fresh
  4. A real authenticated GET (/s/casino/fo/maintenance) succeeds

On success: updates coolbet_session_state.last_heartbeat_at and sets
session_healthy=TRUE. On failure: records the error in last_error and
flips session_healthy=FALSE.

The coolbet_session_health_alert cron (scheduled separately) reads the
state and fires Telegram alerts when session_healthy stays FALSE for
> 30 min.

Usage:
    python3 scripts/coolbet/health_ping.py             # human report
    python3 scripts/coolbet/health_ping.py --json      # machine-readable

Exit codes:
    0 — healthy
    1 — unhealthy (auth chain broken)
    2 — config error (env missing, etc.)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv
load_dotenv()

from workers.automation.coolbet_state import mark_heartbeat


def ping() -> dict:
    """Returns a dict: { ok: bool, elapsed_s: float, error: str | None,
    detail: str }. Always writes to coolbet_session_state."""
    start = time.monotonic()
    try:
        # Lazy import — only loaded inside the try so a syntax/import error
        # surfaces as a structured failure, not a script crash.
        from workers.automation.coolbet_session import CoolbetSession
    except Exception as e:
        mark_heartbeat(False, note=f"import: {e}")
        return {"ok": False, "elapsed_s": 0.0, "error": f"import: {e}",
                "detail": "CoolbetSession failed to import — workers package broken"}

    try:
        session = CoolbetSession(require_auth=True)
    except Exception as e:
        elapsed = time.monotonic() - start
        mark_heartbeat(False, note=f"init: {e}")
        return {"ok": False, "elapsed_s": elapsed, "error": f"init: {e}",
                "detail": "CoolbetSession init failed — likely JWT expired or env misconfig"}

    try:
        ok = session.keep_alive()
    except Exception as e:
        elapsed = time.monotonic() - start
        mark_heartbeat(False, note=f"keep_alive: {e}")
        return {"ok": False, "elapsed_s": elapsed, "error": f"keep_alive: {e}",
                "detail": "keep_alive raised — FS down / cookies expired / JWT dead"}

    elapsed = time.monotonic() - start
    if ok:
        mark_heartbeat(True)
        return {"ok": True, "elapsed_s": elapsed, "error": None,
                "detail": f"maintenance probe succeeded in {elapsed:.2f}s"}
    mark_heartbeat(False, note="maintenance probe returned non-200")
    return {"ok": False, "elapsed_s": elapsed,
            "error": "maintenance probe non-200",
            "detail": "FS + auth probably OK but Coolbet returned non-200 on the probe endpoint"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of human report")
    args = p.parse_args()

    result = ping()
    result["timestamp"] = datetime.now(timezone.utc).isoformat()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        glyph = "✓" if result["ok"] else "✗"
        print(f"\n=== Coolbet health-ping  {result['timestamp']} ===")
        print(f"  {glyph} {result['detail']}")
        if result.get("error"):
            print(f"    error: {result['error']}")
        print(f"  elapsed: {result['elapsed_s']:.2f}s")
        print(f"  written to coolbet_session_state.last_heartbeat_at")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
