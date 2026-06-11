#!/usr/bin/env python3
"""
FlareSolverr diagnostic — answers "is FS healthy and serving requests?"

Background
----------
FlareSolverr is the headless-Chrome proxy that lets us bypass Imperva's
bot detection. Every Coolbet API call routes through it (post 2026-06-11
COOLBET-FS-SESSION-STABLE). HLTV scrapers also fall back to it on plain-
GET 403s. When FS is unhealthy, the entire Coolbet + most HLTV pipelines
silently fail — symptoms surface ~hours later as "no model coverage"
or "no bets placed" on admin pages.

This diagnostic exercises the FS stack in increasing-cost order:

  1. /v1 reachability — can we even talk to it (no Chrome involved)
  2. sessions.list — does it answer commands at all
  3. sessions.create — can it spin up a Chrome (costliest but cheap)
  4. request.get https://example.com — does Chrome actually work
  5. request.get https://www.coolbet.com/ — does the real target return
  6. request.get https://www.hltv.org/matches — does HLTV return

Each step times itself, prints status, and short-circuits the rest on
fatal error (no point pinging Coolbet if Chrome won't even start).

Usage
-----
    python3 scripts/diagnose/flaresolverr.py            # human report
    python3 scripts/diagnose/flaresolverr.py --json     # machine-readable
    python3 scripts/diagnose/flaresolverr.py --target https://...  # override probe URL

Exit codes
----------
    0 — all healthy
    1 — FS reachable but failing requests (Chrome crash, OOM, etc.)
    2 — FS not reachable at all (down, wrong URL)
    3 — unexpected error
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


DEFAULT_FS_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")
DEFAULT_SESSION = "diagnose_probe"

# Test targets in order of cost — drop out at the first hard failure.
PROBES = [
    ("example.com",   "https://example.com"),
    ("coolbet.com",   "https://www.coolbet.com/"),
    ("hltv.org",      "https://www.hltv.org/matches"),
]


def _fs_call(fs_url: str, body: dict, *, timeout_s: int) -> tuple[float, dict | None, str | None]:
    """Returns (elapsed_s, parsed_json, error_str). One of (json, error)
    is set, never both. Timing is wall clock incl. urlopen connect."""
    start = time.monotonic()
    try:
        req = urllib.request.Request(
            f"{fs_url.rstrip('/')}/v1",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            elapsed = time.monotonic() - start
            return elapsed, json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - start
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return elapsed, None, f"HTTP {e.code}: {body_text or e.reason}"
    except urllib.error.URLError as e:
        elapsed = time.monotonic() - start
        return elapsed, None, f"connection: {e.reason}"
    except Exception as e:
        elapsed = time.monotonic() - start
        return elapsed, None, f"{type(e).__name__}: {e}"


def _print(label: str, status: str, detail: str = "", elapsed: float | None = None) -> None:
    """Human-readable line: STATUS LABEL  (timing)  detail"""
    glyph = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "·"}.get(status, "?")
    timing = f" ({elapsed:.2f}s)" if elapsed is not None else ""
    print(f"  {glyph} {label:30}{timing}  {detail}")


def step_reachability(fs_url: str, results: dict) -> bool:
    """Step 1 — can we even reach FS's /v1 endpoint? Uses sessions.list
    which is cheap (no Chrome involved)."""
    elapsed, data, err = _fs_call(fs_url, {"cmd": "sessions.list"}, timeout_s=15)
    results["reachability"] = {
        "elapsed_s": round(elapsed, 3),
        "ok": err is None,
        "error": err,
        "sessions_visible": (data or {}).get("sessions") or [],
    }
    if err:
        _print("Reachability (sessions.list)", "fail", err, elapsed)
        return False
    sess_names = (data or {}).get("sessions") or []
    _print(
        "Reachability (sessions.list)", "ok",
        f"{len(sess_names)} existing sessions: {sess_names[:5]}",
        elapsed,
    )
    return True


def step_session_create(fs_url: str, results: dict) -> bool:
    """Step 2 — can FS spin up a Chrome instance? sessions.create is the
    most expensive cheap call (launches Chrome ~5-10s if cold)."""
    elapsed, data, err = _fs_call(
        fs_url, {"cmd": "sessions.create", "session": DEFAULT_SESSION}, timeout_s=60,
    )
    results["session_create"] = {
        "elapsed_s": round(elapsed, 3),
        "ok": err is None or (data or {}).get("status") == "ok",
        "error": err,
        "raw_status": (data or {}).get("status"),
        "message": (data or {}).get("message"),
    }
    if err:
        # "Session already exists" is fine — counts as success.
        if "already exists" in (err or "").lower():
            _print("sessions.create", "ok", "session already exists (fine)", elapsed)
            return True
        _print("sessions.create", "fail", err, elapsed)
        return False
    msg = (data or {}).get("message") or ""
    if "already exists" in msg.lower():
        _print("sessions.create", "ok", "already exists (fine)", elapsed)
    else:
        _print("sessions.create", "ok", f"created session {DEFAULT_SESSION!r}", elapsed)
    return True


def step_navigate(fs_url: str, target_url: str, label: str, results: dict,
                  timeout_ms: int = 60_000) -> bool:
    """Step 3+ — actual browser navigation. Returns True if Chrome
    successfully fetched the page (any 2xx/3xx)."""
    elapsed, data, err = _fs_call(
        fs_url,
        {
            "cmd": "request.get",
            "url": target_url,
            "session": DEFAULT_SESSION,
            "maxTimeout": timeout_ms,
        },
        timeout_s=(timeout_ms // 1000) + 30,
    )
    if err:
        results[label] = {
            "elapsed_s": round(elapsed, 3), "ok": False, "error": err,
        }
        _print(label, "fail", err, elapsed)
        return False
    sol = (data or {}).get("solution") or {}
    status = int(sol.get("status") or 0)
    body_len = len(sol.get("response") or "")
    cookies_set = len(sol.get("cookies") or [])
    results[label] = {
        "elapsed_s": round(elapsed, 3),
        "ok": 200 <= status < 400,
        "http_status": status,
        "body_bytes": body_len,
        "cookies_returned": cookies_set,
        "url_final": sol.get("url"),
    }
    if 200 <= status < 400:
        _print(label, "ok", f"HTTP {status}, {body_len:,} bytes, {cookies_set} cookies", elapsed)
        return True
    elif status == 403:
        # Browser made it through but the target blocked us — that's a
        # SEPARATE problem from FS being broken. Mark warn (FS works).
        _print(label, "warn", f"HTTP 403 from target (FS itself is fine)", elapsed)
        return True
    else:
        _print(label, "fail", f"HTTP {status}, {body_len} bytes", elapsed)
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--fs-url", default=DEFAULT_FS_URL,
                   help=f"FlareSolverr base URL (default: {DEFAULT_FS_URL})")
    p.add_argument("--target", action="append", default=None,
                   help="Override probe target URLs (repeat). Format: 'label=URL'.")
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of human report")
    p.add_argument("--keep-session", action="store_true",
                   help="Don't destroy the probe session at the end (useful for chained tests)")
    p.add_argument("--cleanup", action="store_true",
                   help="Destroy STALE existing sessions before probing. Respects the "
                        "production whitelist (coolbet_prod, coolbet_dev, hltv_*) by "
                        "default — protects sessions that hold trust markers from "
                        "accidental destruction. Pass --cleanup-all to override.")
    p.add_argument("--cleanup-only", action="store_true",
                   help="Destroy stale sessions and exit immediately (no probing). "
                        "Same whitelist rule as --cleanup.")
    p.add_argument("--cleanup-all", action="store_true",
                   help="DANGEROUS — destroys EVERY session including coolbet_prod, "
                        "which holds the Coolbet 2FA trust marker. After running this "
                        "you'll need to re-enroll via flaresolverr_login_enroll.py "
                        "(SMS verification required). Use only when you're certain.")
    args = p.parse_args()

    ts = datetime.now(timezone.utc).isoformat()
    results: dict = {
        "timestamp": ts,
        "fs_url": args.fs_url,
        "session": DEFAULT_SESSION,
        "steps": {},
        "verdict": None,
    }

    if not args.json:
        print(f"\n=== FlareSolverr diagnostic  {ts} ===")
        print(f"  fs_url: {args.fs_url}")
        print(f"  probe session: {DEFAULT_SESSION}\n")

    # Step 1: reachability
    if not step_reachability(args.fs_url, results["steps"]):
        results["verdict"] = "fs_unreachable"
        return _finish(results, args.json, exit_code=2)

    # Optional cleanup pass — destroys stale sessions before probing.
    # Default behaviour PRESERVES the production whitelist (coolbet_prod,
    # coolbet_dev, hltv_*) because they hold trust markers / login state
    # that we don't want to recreate from scratch (re-enrollment requires
    # SMS 2FA — operator-in-the-loop). Use --cleanup-all to override.
    if args.cleanup or args.cleanup_only or args.cleanup_all:
        existing = results["steps"]["reachability"].get("sessions_visible") or []
        # Production-whitelist — mirrors scripts/coolbet/sweep_stale_sessions.py
        # so both tools enforce the same rule. Kept in sync manually for now;
        # if the list grows, factor into a shared constant.
        WHITELIST_EXACT = {"coolbet_prod", "coolbet_dev"}
        WHITELIST_PREFIXES = ("hltv_",)
        def _is_whitelisted(name: str) -> bool:
            return name in WHITELIST_EXACT or any(name.startswith(p) for p in WHITELIST_PREFIXES)

        if args.cleanup_all:
            to_destroy = list(existing)
            preserved: list = []
        else:
            to_destroy = [s for s in existing if not _is_whitelisted(s)]
            preserved = [s for s in existing if _is_whitelisted(s)]

        if to_destroy:
            if not args.json:
                _print(f"Cleanup: destroying {len(to_destroy)} stale sessions", "warn",
                       f"{to_destroy[:5]}{'...' if len(to_destroy) > 5 else ''}")
            for s in to_destroy:
                _fs_call(args.fs_url, {"cmd": "sessions.destroy", "session": s},
                          timeout_s=20)
            results["cleanup_destroyed"] = to_destroy
        elif not args.json:
            _print("Cleanup: no stale sessions to destroy", "ok", "(nothing to do)")
        if preserved and not args.json:
            _print(f"Cleanup: preserved {len(preserved)} whitelisted", "ok",
                   f"{preserved}")
            results["cleanup_preserved"] = preserved
        if args.cleanup_only:
            results["verdict"] = "cleanup_done"
            return _finish(results, args.json, exit_code=0)

    # Step 2: session create
    if not step_session_create(args.fs_url, results["steps"]):
        results["verdict"] = "session_create_failed"
        return _finish(results, args.json, exit_code=1)

    # Step 3: navigate to each probe target (cheap → expensive)
    probes = PROBES
    if args.target:
        probes = []
        for t in args.target:
            if "=" in t:
                lbl, url = t.split("=", 1)
                probes.append((lbl, url))
            else:
                probes.append((t, t))

    any_target_failed = False
    for label, url in probes:
        ok = step_navigate(args.fs_url, url, label, results["steps"])
        if not ok:
            any_target_failed = True

    # Cleanup
    if not args.keep_session:
        _fs_call(args.fs_url, {"cmd": "sessions.destroy", "session": DEFAULT_SESSION},
                  timeout_s=15)

    if any_target_failed:
        results["verdict"] = "target_navigation_failed"
        return _finish(results, args.json, exit_code=1)
    results["verdict"] = "healthy"
    return _finish(results, args.json, exit_code=0)


def _finish(results: dict, as_json: bool, *, exit_code: int) -> int:
    if as_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(f"\n  verdict: {results['verdict']!r}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
