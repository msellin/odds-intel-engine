#!/usr/bin/env python3
"""Coolbet automation status — one-shot health check.

Run:
    python3 scripts/coolbet/status.py

Reports:
  - launchd daemon process (running? pid? exit code?)
  - CDP-Chrome reachability + Coolbet tab presence + logged-in state
  - FlareSolverr reachability
  - coolbet_session_state DB row (JWT TTL, placement_paused, last activity)
  - Mac daemon log tail: last tick result, last error if any
  - Today's real_bets count + qualifying simulated_bets count

Designed to answer 'is it up and placing?' in under 2 seconds.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LOG_PATH = REPO_ROOT / "dev" / "active" / "coolbet-mac-daemon.log"
CDP_URL = os.getenv("COOLBET_CHROME_CDP_URL", "http://localhost:9222")
FS_URL = os.getenv("FLARESOLVERR_URL", "http://localhost:8191")

# ANSI colors — minimal palette
G = "\033[32m"  # green
Y = "\033[33m"  # yellow
R = "\033[31m"  # red
D = "\033[2m"   # dim
B = "\033[1m"   # bold
N = "\033[0m"   # reset


def _dot(ok: bool | None) -> str:
    if ok is True:
        return f"{G}●{N}"
    if ok is False:
        return f"{R}●{N}"
    return f"{Y}●{N}"


def _line(label: str, ok: bool | None, body: str) -> None:
    print(f"  {_dot(ok)} {B}{label:<18}{N} {body}")


def _check_launchd() -> tuple[bool, str]:
    try:
        out = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.oddsintel.coolbet-mac-daemon"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception as e:
        return False, f"launchctl failed: {e}"
    state = "?"; pid = "?"; last_exit = "?"; runs = "?"
    for ln in out.splitlines():
        s = ln.strip()
        if s.startswith("state =") and state == "?":
            state = s.split("=", 1)[1].strip()
        elif s.startswith("pid ="):
            pid = s.split("=", 1)[1].strip()
        elif s.startswith("last exit code ="):
            last_exit = s.split("=", 1)[1].strip()
        elif s.startswith("runs ="):
            runs = s.split("=", 1)[1].strip()
    ok = (state == "running" and pid != "?" and pid != "0")
    return ok, f"state={state} pid={pid} runs={runs} last_exit={last_exit}"


def _check_cdp() -> tuple[bool | None, str]:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/list", timeout=3) as r:
            tabs = json.loads(r.read())
    except Exception as e:
        return False, f"unreachable at {CDP_URL} ({e})"
    pages = [t for t in tabs if t.get("type") == "page"]
    coolbet = [t for t in pages if "coolbet.com" in (t.get("url") or "")]
    history_open = any("/panuste-ajalugu" in (t.get("url") or "") for t in coolbet)
    if not coolbet:
        return None, f"{len(pages)} pages, 0 coolbet tabs (open coolbet.com to enable JWT self-heal)"
    suffix = " — history tab open (bet-dedup ON)" if history_open else " — no history tab (bet-dedup OFF, JWT-only)"
    return True, f"{len(pages)} pages, {len(coolbet)} coolbet tab(s){suffix}"


def _check_fs() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{FS_URL}/", timeout=3) as r:
            r.read()
        return True, f"reachable at {FS_URL}"
    except Exception as e:
        return False, f"unreachable at {FS_URL} ({e})"


def _check_db_state() -> tuple[bool | None, str, dict]:
    try:
        # Late import — DB module pulls in env loading + pool creation.
        sys.path.insert(0, str(REPO_ROOT))
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT jwt_exp_at, placement_paused, placement_paused_reason,
                      session_healthy, last_login_method, last_login_at,
                      last_error, last_error_at
               FROM coolbet_session_state WHERE id = 1"""
        )
    except Exception as e:
        return False, f"DB read failed: {e}", {}
    if not rows:
        return False, "coolbet_session_state row missing", {}
    r = dict(rows[0])
    now = datetime.now(timezone.utc)
    exp = r.get("jwt_exp_at")
    ttl_s = None
    if isinstance(exp, datetime):
        ttl_s = (exp - now).total_seconds()
    jwt_status = (
        f"{G}fresh{N} ({ttl_s/60:.0f}m left)" if ttl_s and ttl_s > 60
        else f"{Y}stale{N} (self-heal will fire next tick)" if ttl_s and ttl_s > -3600
        else f"{R}expired{N} >1h ago"
    )
    paused = r.get("placement_paused")
    paused_status = f"{R}PAUSED{N}: {r.get('placement_paused_reason') or 'no reason'}" if paused else f"{G}placing enabled{N}"
    healthy = r.get("session_healthy")
    body = (
        f"jwt: {jwt_status}  |  {paused_status}  |  "
        f"session_healthy={'✓' if healthy else '✗'}  |  "
        f"last_login={r.get('last_login_method')} @ {r.get('last_login_at')}"
    )
    ok = bool(healthy) and not paused and (ttl_s is None or ttl_s > -3600)
    if r.get("last_error_at"):
        # Show last error inline so the operator sees the recent failure cause.
        err_short = (r.get("last_error") or "")[:140]
        body += f"\n     {D}last_error @ {r.get('last_error_at')}:{N} {err_short}"
    return ok, body, r


_TICK_RE = " — tick "


def _check_log() -> tuple[bool | None, str]:
    if not LOG_PATH.exists():
        return False, f"log missing at {LOG_PATH}"
    # Read last ~200 lines — enough to cover hours of ticks.
    try:
        with open(LOG_PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 40000))
            tail = f.read().decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        return False, f"log read failed: {e}"
    ticks = [ln for ln in tail if _TICK_RE in ln]
    if not ticks:
        return None, "no tick lines in recent log — daemon hasn't run yet?"
    last = ticks[-1].strip()
    # Pull out the qualified/placed/errors/elapsed numbers for a one-line summary.
    ok = "errors=0" in last and " — tick" in last
    # Trim timestamp + module prefix for readability.
    if " INFO __main__ — " in last:
        last_short = last.split(" INFO __main__ — ", 1)[1]
    else:
        last_short = last
    return ok, last_short


def _today_real_bets() -> str:
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT COUNT(*) AS c, COALESCE(SUM(stake), 0) AS total_stake
               FROM real_bets
               WHERE placed_at::date = (NOW() AT TIME ZONE 'UTC')::date"""
        )
        r = dict(rows[0]) if rows else {}
        return f"{r.get('c', 0)} bet(s), €{float(r.get('total_stake') or 0):.2f} total stake (today UTC)"
    except Exception as e:
        return f"DB read failed: {e}"


def _today_qualifying() -> str:
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT COUNT(*) AS c FROM simulated_bets sb
               JOIN matches m ON m.id = sb.match_id
               WHERE sb.combo_legs IS NULL
                 AND sb.user_placed_at IS NULL
                 AND sb.user_skipped_at IS NULL
                 AND m.date BETWEEN NOW() AND NOW() + INTERVAL '48 hours'
                 AND NOT EXISTS (
                     SELECT 1 FROM real_bets rb
                     WHERE rb.match_id = sb.match_id
                       AND rb.market = sb.market
                       AND rb.selection = sb.selection
                 )"""
        )
        return f"{dict(rows[0]).get('c', 0)} qualifying pick(s) waiting"
    except Exception as e:
        return f"DB read failed: {e}"


def main() -> int:
    print(f"\n{B}Coolbet automation status{N}  ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC)\n")

    ok_l, body_l = _check_launchd()
    _line("daemon", ok_l, body_l)

    ok_c, body_c = _check_cdp()
    _line("cdp-chrome", ok_c, body_c)

    ok_f, body_f = _check_fs()
    _line("flaresolverr", ok_f, body_f)

    ok_d, body_d, _ = _check_db_state()
    _line("db state", ok_d, body_d)

    ok_t, body_t = _check_log()
    _line("last tick", ok_t, body_t)

    print()
    print(f"  {D}today:{N} {_today_real_bets()}")
    print(f"  {D}queue:{N} {_today_qualifying()}")
    print()

    # Exit non-zero if ANY check failed, so this can be wired into a
    # cron / Slack / Telegram alert if you want a passive heartbeat.
    overall = all(
        x is True
        for x in (ok_l, ok_c, ok_f, ok_d, ok_t)
    )
    if overall:
        print(f"  {G}OK{N} — daemon is up, placing enabled, CDP healthy.\n")
        return 0
    else:
        print(f"  {Y}⚠ check the orange/red dots above.{N}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
