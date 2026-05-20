"""Inspect or refresh the Coolbet session JWT.

Usage:
    python3 scripts/coolbet_keepalive.py           # one-shot keep-alive + TTL report
    python3 scripts/coolbet_keepalive.py --watch   # loop forever, ping every 20 min

Tells you:
  • Current JWT exp (UTC) and seconds remaining
  • Whether the heartbeat call succeeded
  • Loop mode (--watch) — keeps the session warm so the browser-side
    "you've been logged out" prompt never fires.

In production the scheduler runs `job_coolbet_keepalive` every 20 min UTC
(workers/scheduler.py). This script is the manual / one-off equivalent.
"""
import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.automation.coolbet_session import CoolbetSession


def _report(session: CoolbetSession) -> None:
    ttl = session.jwt_seconds_remaining
    exp_utc = datetime.fromtimestamp(time.time() + ttl, tz=timezone.utc).isoformat() if ttl > 0 else "—"
    print(f"  JWT exp: {exp_utc}  (TTL = {int(ttl)}s ≈ {ttl/60:.1f} min)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true",
                    help="Loop forever, heartbeat every 20 min")
    ap.add_argument("--interval-sec", type=int, default=1200,
                    help="Heartbeat interval in --watch mode (default 1200 = 20 min)")
    args = ap.parse_args()

    session = CoolbetSession()
    # First call triggers initial login so we have a JWT to report on.
    print("Initial heartbeat…")
    ok = session.keep_alive()
    print(f"  status: {'✓' if ok else '✗'}")
    _report(session)

    if not args.watch:
        return

    print(f"\nWatch mode — heartbeat every {args.interval_sec}s. Ctrl-C to stop.\n")
    while True:
        time.sleep(args.interval_sec)
        ok = session.keep_alive()
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"[{ts}] heartbeat {'✓' if ok else '✗'}")
        _report(session)


if __name__ == "__main__":
    main()
