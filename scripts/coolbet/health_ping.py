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


def _skip_reason() -> str | None:
    """Why this tick must NOT put a request on the wire, or None to go ahead.

    Reads only the DB, never the network. Fails OPEN (returns None) on any
    error: a broken breaker must not silently disable the health check.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT jwt_current IS NULL AS no_jwt,
                      COALESCE(EXTRACT(EPOCH FROM (jwt_exp_at - NOW())), -1) AS jwt_ttl_s
                 FROM coolbet_session_state WHERE id = 1"""
        )
    except Exception:  # noqa: BLE001
        return None
    if not rows:
        return None
    r = rows[0]
    if r.get("no_jwt"):
        return "no JWT stored"
    ttl = float(r.get("jwt_ttl_s") or -1)
    if ttl <= 0:
        return f"JWT expired {abs(int(ttl // 60))}m ago"
    return None


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

    # HEALTH-PING-CIRCUIT-BREAKER (2026-09-13). This probe is AUTHENTICATED and
    # runs every 5 minutes. When the session is logged out it cannot possibly
    # succeed — and it does not merely waste a request, it actively works
    # against recovery: measured 2026-09-12, **143 failed authenticated probes
    # in 12 hours** from one residential IP, into an endpoint already answering
    # the Imperva wall. The runbook's own diagnosis of that wall (§2/§7) is
    # "usually triggered by our own request volume from one IP", and a retry
    # loop into a challenge is exactly that volume.
    #
    # So: when the state row says the session is logged out, report the SAME
    # unhealthy verdict from the DB without touching the network. The operator
    # still sees `session_healthy=False`, the alerter still fires, and the
    # footprint that sustains the flag stops. The breaker opens again the
    # moment a JWT with real life in it appears — recovery is never blocked by
    # this, because recovery happens through CDP-Chrome, not through here.
    #
    # NOT a backoff timer: a timer would still probe eventually and would need
    # tuning. The condition "we hold no usable credential" is exact.
    skip = _skip_reason()
    if skip:
        elapsed = time.monotonic() - start
        mark_heartbeat(False, note=f"probe skipped: {skip}")
        return {"ok": False, "elapsed_s": elapsed, "error": f"probe skipped: {skip}",
                "detail": ("Not probing Coolbet: we hold no usable credential, so the "
                           "request could only fail — and a retry loop into the Imperva "
                           "wall is what sustains it (runbook 2/7). Log in via "
                           "CDP-Chrome; the probe resumes by itself."),
                "skipped": True}

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

    # HEALTH-PING-SKIP-IS-NOT-A-FAILURE (2026-09-14). A deliberate skip used to
    # fall through to exit 1, identical to "Coolbet is down" — so every JWT lapse
    # was recorded as an outage in pipeline_runs and pushed status=down to Kuma.
    # Measured over 9h: 26 "failures", of which 14 were one real outage and the
    # other 12 were isolated skips at :55/:00 as the 30-minute JWT rolled over.
    # Alerting on a state the self-heal is designed to absorb is how a monitor
    # trains its operator to ignore it.
    #
    # 3 = skipped on purpose, not a verdict on Coolbet. The skip is still fully
    # visible: mark_heartbeat() records it, and scripts/ops/status.py reports JWT
    # age independently, so a JWT that is genuinely dead for hours still shows.
    if result.get("skipped"):
        return 3
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
