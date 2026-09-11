#!/usr/bin/env python3
"""
FlareSolverr stale-session sweeper.

The bug we hit on 2026-06-11: every scraper that calls FS's sessions.create
without a matching sessions.destroy leaks a Chrome instance. the VPS's
container hit the slot limit and started hanging on new session creates.

This sweeper runs hourly. It lists current FS sessions and destroys any
that aren't in the active whitelist. The whitelist is the small set of
sessions the production scrapers are SUPPOSED to keep alive across runs;
everything else is a leak.

Usage:
    python3 scripts/coolbet/sweep_stale_sessions.py           # destroy stale
    python3 scripts/coolbet/sweep_stale_sessions.py --dry-run # list only

Whitelist sessions (canonical names used by the production scrapers):
    coolbet_prod   — CoolbetSession default (workers/automation/coolbet_session.py)
    hltv_*         — any session whose name starts with hltv_ (scrapers use
                     per-feature names: hltv_rosters, hltv_stats, etc.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone


FS_URL_DEFAULT = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")

# FS-SWEEP-WHITELIST-INCOMPLETE (2026-09-11). This listed `coolbet_prod` only,
# which was true of the VPS FlareSolverr this job actually runs against — and a
# landmine for the OPERATOR'S MAC FS, where Coolbet and Epicbet really route
# (COOLBET_NO_FS must stay unset; see docs/COOLBET_RUNBOOK.md). On that box the
# live session names are `coolbet_odds_reader` (set in
# local/launchd/com.oddsintel.coolbet-odds-snapshot.plist) and
# `epicbet_odds_reader` (epicbet_explorer._FS_SESSION_ID). Pointing this sweeper
# at the Mac — the obvious remedy the day someone notices the Mac FS is never
# swept — would have destroyed both mid-run, every hour, and looked like a
# scraper bug rather than a sweeper one.
#
# Whitelist the NAMES OF LIVE FEEDS, not the names one host happens to have.
WHITELIST_EXACT = {
    "coolbet_prod",          # Coolbet placement session (real money)
    "coolbet_odds_reader",   # Coolbet odds sweep, :03/:33 on the Mac
    "epicbet_odds_reader",   # Epicbet odds sweep, :02/:32
    # `coolbet_dev` holds the SMS-2FA enrolment (flaresolverr_login_enroll.py
    # defaults to it). Destroying it means an operator-in-the-loop re-enrol.
    # It was whitelisted in scripts/diagnose/flaresolverr.py and NOT here — the
    # exact divergence that file's own comment warned about.
    "coolbet_dev",
}
WHITELIST_PREFIXES = (
    "hltv_",
)


def _fs_call(fs_url: str, body: dict, *, timeout_s: int = 60) -> dict:
    req = urllib.request.Request(
        f"{fs_url.rstrip('/')}/v1",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


def is_whitelisted(name: str) -> bool:
    if name in WHITELIST_EXACT:
        return True
    return any(name.startswith(p) for p in WHITELIST_PREFIXES)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--fs-url", default=FS_URL_DEFAULT)
    p.add_argument("--dry-run", action="store_true",
                   help="List what would be destroyed without actually doing it")
    args = p.parse_args()

    ts = datetime.now(timezone.utc).isoformat()
    print(f"\n=== FS stale-session sweep  {ts} ===")
    print(f"  fs_url: {args.fs_url}")

    # Step 1: list existing sessions
    try:
        data = _fs_call(args.fs_url, {"cmd": "sessions.list"}, timeout_s=20)
    except Exception as e:
        print(f"  [fail] sessions.list error: {e}")
        return 2
    sessions = data.get("sessions") or []
    print(f"  {len(sessions)} sessions in FS: {sessions}")

    if not sessions:
        print("  nothing to do.")
        return 0

    # Step 2: partition by whitelist
    keep = [s for s in sessions if is_whitelisted(s)]
    stale = [s for s in sessions if not is_whitelisted(s)]
    print(f"  keep ({len(keep)}): {keep}")
    print(f"  stale ({len(stale)}): {stale}")

    if not stale:
        print("  ✓ no stale sessions.")
        return 0

    if args.dry_run:
        print("  [DRY-RUN] would destroy the above — re-run without --dry-run to act.")
        return 0

    # Step 3: destroy stale
    destroyed = []
    failed = []
    for s in stale:
        try:
            r = _fs_call(args.fs_url, {"cmd": "sessions.destroy", "session": s},
                          timeout_s=20)
            if (r.get("status") or "").lower() == "ok":
                destroyed.append(s)
            else:
                failed.append((s, r.get("message") or "no-message"))
        except Exception as e:
            failed.append((s, str(e)))

    print(f"\n  ✓ destroyed {len(destroyed)}: {destroyed}")
    if failed:
        print(f"  ✗ failed {len(failed)}: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
