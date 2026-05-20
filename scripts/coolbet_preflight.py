"""Coolbet preflight checks — run before starting the daemon (or anytime
you're not sure if the session still works).

Exits 0 if every CRITICAL check passes (daemon would start cleanly).
Exits 1 if any critical check fails (do not start the daemon; fix first).

Checks:
  1. Imperva cookies — at least one present (loud warning if none)
  2. Login — JWT refresh succeeds (proves cookies + credentials work)
  3. Heartbeat — first authenticated GET returns 200
  4. JWT TTL — confirms we got a valid 30-min-ish token back
  5. Bot universe — DB has > 5 active bots (placement loop will have work)
  6. Optional: balance probe — if we can find the endpoint

Usage:
    python3 scripts/coolbet_preflight.py            # run all checks
    python3 scripts/coolbet_preflight.py --quiet    # only print failures
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env BEFORE any os.getenv() calls. coolbet_session.py also calls
# load_dotenv() at module-import time, but checks 1+2 fire before that
# import — without this they'd fail even when env is set correctly.
from dotenv import load_dotenv as _load_dotenv  # noqa: E402
_load_dotenv()

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def _section(title: str) -> None:
    print(f"\n── {title} ──")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def check_cookies() -> bool:
    _section("1. Imperva cookies")
    have_any = bool(
        os.getenv("COOLBET_COOKIE_REESE84")
        or os.getenv("COOLBET_COOKIE_VISID_INCAP")
        or os.getenv("COOLBET_COOKIE_NLBI")
        or os.getenv("COOLBET_COOKIE_NLBI2")
        or os.getenv("COOLBET_COOKIE_INCAP_SES")
        or os.getenv("COOLBET_IMPERVA_COOKIES")
    )
    if not have_any:
        _fail("No Imperva cookies in env — set COOLBET_COOKIE_* (or the legacy "
              "COOLBET_IMPERVA_COOKIES) in .env. Login will 403.")
        return False
    _ok("at least one Imperva cookie present")
    return True


def check_credentials() -> bool:
    _section("2. Credentials")
    user = os.getenv("COOLBET_USER") or os.getenv("COOLBET_EMAIL")
    pwd  = os.getenv("COOLBET_PASS") or os.getenv("COOLBET_PASSWORD")
    if not user or not pwd:
        _fail("COOLBET_USER / COOLBET_PASS missing from .env")
        return False
    _ok(f"COOLBET_USER={user[:3]}…")
    return True


def check_session_works() -> tuple[bool, object | None]:
    """Try to create a session + heartbeat. Critical."""
    _section("3. Session + heartbeat (login + 1 authenticated call)")
    from workers.automation.coolbet_session import CoolbetSession
    try:
        s = CoolbetSession()
    except Exception as e:
        _fail(f"Session init failed: {e}")
        return False, None
    try:
        ok = s.keep_alive()
    except Exception as e:
        msg = str(e)
        if "403" in msg or "Imperva" in msg:
            _fail("Login 403 — Imperva cookies expired. Re-capture from your "
                  "browser (DevTools → Application → Cookies for www.coolbet.com) "
                  "and update .env.")
        else:
            _fail(f"Heartbeat raised: {e}")
        return False, s
    if not ok:
        _fail("Heartbeat returned non-200 (no exception). Check Coolbet status.")
        return False, s
    ttl = s.jwt_seconds_remaining
    if ttl <= 0:
        _fail("Heartbeat OK but no JWT TTL — token didn't decode.")
        return False, s
    _ok(f"login + heartbeat succeeded — JWT TTL ≈ {int(ttl)}s ({ttl/60:.1f} min)")
    return True, s


def check_bot_universe() -> bool:
    _section("4. Active-bot universe")
    try:
        from workers.api_clients.supabase_client import execute_query
    except Exception as e:
        _warn(f"DB unreachable ({e}) — skipping bot-count check")
        return True
    try:
        rows = execute_query(
            "SELECT COUNT(*) AS n FROM bots "
            "WHERE is_active = true AND retired_at IS NULL",
            (),
        )
    except Exception as e:
        _warn(f"bots query failed ({e}) — skipping bot-count check")
        return True
    n = int(rows[0]["n"] if rows else 0)
    if n < 5:
        _fail(f"only {n} active bots — placement loop will have ~no work. "
              "Check `bots` table for accidental retirements.")
        return False
    _ok(f"{n} active bots in DB")
    return True


def check_balance(session) -> bool:
    """Best-effort balance probe. NOT critical — most Coolbet balance
    endpoints need extra knowledge we don't have."""
    _section("5. Balance probe (optional)")
    if session is None:
        _warn("skipped — no live session")
        return True
    # Try a few plausible endpoints. Failure is informational, not critical.
    for url in (
        "https://www.coolbet.com/s/user/balance",
        "https://www.coolbet.com/s/users/balance",
        "https://www.coolbet.com/s/account/balance",
    ):
        try:
            r = session.get(url)
            if r.status_code == 200:
                _ok(f"balance endpoint found: {url}  → {r.text[:200]}")
                return True
        except Exception:
            continue
    _warn("no balance endpoint responded — placement still works, just no "
          "pre-flight balance number. Add the right URL when you find it in "
          "DevTools.")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quiet", action="store_true",
                    help="Only print failures + the final verdict")
    args = ap.parse_args()

    print("Coolbet preflight " + ("(quiet) " if args.quiet else "") + "─" * 50)

    critical = []
    critical.append(check_cookies())
    critical.append(check_credentials())
    session_ok, session = check_session_works()
    critical.append(session_ok)
    critical.append(check_bot_universe())
    check_balance(session)  # never critical

    print()
    if all(critical):
        print("✓ ALL CRITICAL CHECKS PASSED — daemon can start.")
        return 0
    print("✗ CRITICAL CHECK FAILED — do NOT start the daemon. Fix the failures above first.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
