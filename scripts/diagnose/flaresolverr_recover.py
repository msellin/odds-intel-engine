#!/usr/bin/env python3
"""
FlareSolverr session-recovery tool — destroys stuck sessions so the next
scraper run creates fresh ones (forcing a fresh Cloudflare challenge).

When FS goes "half-down" (the service responds to /v1 but specific
sessions are stuck — Chrome tab crashed mid-challenge, browser hung,
etc.), the symptom is: SOME jobs succeed (those reusing healthy
sessions), but OTHER jobs fail every cron tick with timeouts or
"connection refused"-style errors from within the session. Restarting
the FS container is heavy and destroys all sessions including the
load-bearing `coolbet_prod` one. This tool is the surgical version:
destroy only the named sessions (or a prefix), forcing each scraper
to recreate them on next run.

The 2026-06-25 incident pattern: cs2_hltv_upcoming + cs2_coolbet_scanner
+ cs2_hltv_match_odds all started failing within an hour of each other
(11:17→12:12 UTC on 06-23) while coolbet_odds_snapshot kept working.
The fix below would have unstuck the failing sessions without touching
coolbet_prod (which was still serving coolbet_odds_snapshot fine).

Usage (against Railway FS — set FLARESOLVERR_URL first):
    # See what's there
    FLARESOLVERR_URL=https://your-fs.up.railway.app \\
        python3 scripts/diagnose/flaresolverr_recover.py --list

    # Destroy all hltv_* sessions (safest — leaves coolbet_prod alone)
    FLARESOLVERR_URL=... python3 scripts/diagnose/flaresolverr_recover.py \\
        --prefix hltv_ --apply

    # Nuke one specific stuck session
    FLARESOLVERR_URL=... python3 scripts/diagnose/flaresolverr_recover.py \\
        --session hltv_upcoming --apply

    # Reset everything including coolbet_prod (forces Imperva re-challenge)
    FLARESOLVERR_URL=... python3 scripts/diagnose/flaresolverr_recover.py \\
        --all --apply

Exit codes:
    0 — sessions destroyed (or dry-run completed)
    2 — FS unreachable
    3 — list/destroy command failed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone


FS_URL_DEFAULT = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")


def _fs_call(fs_url: str, body: dict, *, timeout_s: int = 30) -> dict:
    req = urllib.request.Request(
        f"{fs_url.rstrip('/')}/v1",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


def list_sessions(fs_url: str) -> list[str]:
    data = _fs_call(fs_url, {"cmd": "sessions.list"}, timeout_s=20)
    return data.get("sessions") or []


def destroy_session(fs_url: str, name: str) -> tuple[bool, str]:
    try:
        data = _fs_call(fs_url, {"cmd": "sessions.destroy", "session": name},
                        timeout_s=30)
        if data.get("status") == "ok":
            return True, "ok"
        return False, data.get("message", "unknown")
    except Exception as e:
        return False, str(e)


def pick_targets(sessions: list[str], *, names: list[str], prefixes: list[str],
                 all_: bool) -> list[str]:
    if all_:
        return list(sessions)
    out = set()
    for s in sessions:
        if s in names:
            out.add(s)
            continue
        for pre in prefixes:
            if s.startswith(pre):
                out.add(s)
                break
    return sorted(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--fs-url", default=FS_URL_DEFAULT,
                   help="FlareSolverr base URL (default: env FLARESOLVERR_URL)")
    p.add_argument("--list", action="store_true",
                   help="List current sessions and exit")
    p.add_argument("--session", action="append", default=[],
                   help="Specific session name to destroy (repeatable)")
    p.add_argument("--prefix", action="append", default=[],
                   help="Destroy all sessions whose name starts with this prefix (repeatable)")
    p.add_argument("--all", action="store_true",
                   help="Destroy EVERY session (including coolbet_prod — forces Imperva re-challenge)")
    p.add_argument("--apply", action="store_true",
                   help="Actually destroy (default: dry-run shows what would happen)")
    args = p.parse_args()

    ts = datetime.now(timezone.utc).isoformat()
    print(f"\n=== FS session recovery  {ts} ===")
    print(f"  fs_url: {args.fs_url}")

    try:
        sessions = list_sessions(args.fs_url)
    except Exception as e:
        print(f"  [fail] sessions.list error: {e}")
        return 2

    print(f"  {len(sessions)} sessions currently in FS:")
    for s in sessions:
        print(f"    - {s}")

    if args.list:
        return 0

    if not (args.session or args.prefix or args.all):
        print("\n  [!] no targets specified — pass --session NAME, --prefix PRE, or --all")
        print("      (re-run with --list to see what's there)")
        return 0

    targets = pick_targets(sessions, names=args.session, prefixes=args.prefix,
                           all_=args.all)
    if not targets:
        print("\n  no sessions matched the targets.")
        return 0

    mode = "APPLY" if args.apply else "dry-run"
    print(f"\n  mode: {mode}  targets: {len(targets)}")
    for s in targets:
        print(f"    → would destroy: {s}" if not args.apply else "    → destroying:   {0}".format(s))

    if not args.apply:
        print("\n  (re-run with --apply to actually destroy)")
        return 0

    ok_count = fail_count = 0
    for s in targets:
        ok, msg = destroy_session(args.fs_url, s)
        if ok:
            print(f"    ✓ destroyed {s}")
            ok_count += 1
        else:
            print(f"    ✗ failed   {s}: {msg}")
            fail_count += 1

    print(f"\n  done: {ok_count} destroyed, {fail_count} failed")
    if fail_count and not ok_count:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
