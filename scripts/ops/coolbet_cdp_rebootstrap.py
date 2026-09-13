#!/usr/bin/env python3
"""COOLBET-CDP-REBOOTSTRAP — self-heal a CDP-Chrome profile that Coolbet walls.

    python3 scripts/ops/coolbet_cdp_rebootstrap.py            # check + report
    python3 scripts/ops/coolbet_cdp_rebootstrap.py --apply    # heal if needed
    python3 scripts/ops/coolbet_cdp_rebootstrap.py --force    # heal regardless

THE FAULT THIS HEALS, and why it took so long to name
-----------------------------------------------------
Coolbet serves the CDP-Chrome profile a **504,929-byte real SPA that renders the
9 characters "STAY COOL"** and nothing else. Every route — /et/, /et/sport,
/en/login — identical. HTTP 200, no console errors, no failed requests, no 4xx,
and **zero Imperva markers in the raw HTML**.

For weeks `COOLBET_RUNBOOK.md` §2 said that body WAS the Imperva wall, so every
attempt reached for bot-detection remedies. Measured 2026-09-13, all against
this exact state, all ineffective:

    all 5 Imperva cookies cleared ........ unchanged
    localStorage reese84 + uuid cleared .. unchanged
    fresh goto / hard reload ............. unchanged
    CDP-Chrome process restart ........... unchanged
    foreground tab + JS PoW time ......... unchanged

Meanwhile the operator's NORMAL Chrome, same machine, same IP, loaded Coolbet
perfectly and logged in first try. That is the whole diagnosis: **the block is
bound to the CDP profile ON DISK**, not to the IP, the account, the cookies or
the process.

THE FIX, which is also the architecture lesson
-----------------------------------------------
Do not try to log in through CDP-Chrome. Keep the OPERATOR'S NORMAL CHROME
logged in, and re-copy that profile. `launch_chrome_for_sync.sh` has always done
this copy on first run; it simply was never re-run when the copy went bad.

    1. quit CDP-Chrome
    2. move the walled profile aside (kept, never deleted — it is evidence)
    3. re-copy the normal profile (atomic + vanish-tolerant, see the launcher)
    4. relaunch CDP-Chrome
    5. sync the JWT into coolbet_session_state, or the DB still reads expired
       and the health-ping breaker stays open

Step 5 is easy to forget and makes the heal look like it failed: the browser
holds a valid token while every consumer reads the stale DB row.

WHAT IT CANNOT DO. If the normal Chrome profile is ALSO logged out, the copy
carries nothing and no amount of retrying helps. That is the one case a human
must handle, and it is a far better ask than before: "stay logged in to your own
Chrome", not "log in to the automation window that Coolbet walls".

RATE LIMITED. The copy moves several GB, so it runs at most once per
--min-gap-hours (default 6). A heal loop that re-copies every tick would be its
own outage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

CDP = "http://localhost:9222"
LAUNCHER = REPO / "local" / "launch_chrome_for_sync.sh"
PROFILE = Path.home() / "Library/Application Support/Google/Chrome-CDP-OddsIntel"
STAMP = REPO / "dev" / "active" / ".cdp_rebootstrap_last"
WALL_TEXT = "stay cool"


def _cdp_up() -> bool:
    try:
        urllib.request.urlopen(f"{CDP}/json/version", timeout=5)
        return True
    except Exception:  # noqa: BLE001
        return False


def diagnose() -> dict:
    """Is the CDP profile healthy? Read-only."""
    out = {"cdp_up": _cdp_up(), "walled": None, "has_jwt": None,
           "jwt_state": None, "jwt_ttl_s": None, "detail": ""}
    if not out["cdp_up"]:
        out["detail"] = "CDP-Chrome not reachable on :9222"
        return out
    try:
        from patchright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        out["detail"] = f"patchright unavailable: {e}"
        return out
    try:
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(CDP)
            ctx = b.contexts[0]
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.goto("https://www.coolbet.com/et/", wait_until="domcontentloaded",
                    timeout=45000)
            pg.wait_for_timeout(7000)
            body = (pg.inner_text("body") or "")[:200]
            out["walled"] = WALL_TEXT in body.lower() and len(body.strip()) < 40
            out["detail"] = f"body={body.strip()[:40]!r}"
    except Exception as e:  # noqa: BLE001
        out["detail"] = f"probe failed: {type(e).__name__}: {str(e)[:90]}"

    # JWT CHECK RUNS OUTSIDE THE PATCHRIGHT CONNECTION, and must.
    #
    # Two mistakes here, both of which would have made this self-heal DESTROY a
    # healthy system, and both caught only by running it against one:
    #   1. A naive `keys().includes('cbauth')` filter read False on a perfectly
    #      good session — Coolbet does not always name the key that way, which
    #      is why the repo's own scanner reports `key unknown`. Reuse the
    #      scanner; never re-implement the check.
    #   2. Calling that scanner from INSIDE an open patchright CDP connection
    #      returns `chrome_down`: two CDP clients on one browser collide (it
    #      also leaks an un-awaited coroutine). So the connection above is
    #      closed before this runs.
    # Either bug alone yields needs_heal=True forever, i.e. a multi-GB re-copy
    # on a loop. A self-heal with a false-positive detector is worse than none.
    try:
        from workers.automation.coolbet_browser_sync import diagnose_cdp_jwt_state
        st = diagnose_cdp_jwt_state()
        out["has_jwt"] = st.get("state") == "valid"
        out["jwt_state"] = st.get("state")
        out["jwt_ttl_s"] = st.get("ttl_s")
    except Exception as e:  # noqa: BLE001
        out["jwt_state"] = f"check failed: {type(e).__name__}"
    return out


def _recently_healed(min_gap_h: float) -> float | None:
    try:
        age_h = (time.time() - STAMP.stat().st_mtime) / 3600.0
        return age_h if age_h < min_gap_h else None
    except FileNotFoundError:
        return None


def heal() -> dict:
    """Quit CDP-Chrome, park the walled profile, re-copy, relaunch, sync JWT."""
    steps = []
    subprocess.run(["pkill", "-f", "Chrome-CDP-OddsIntel"], capture_output=True)
    time.sleep(3)
    steps.append("quit CDP-Chrome")

    if PROFILE.exists():
        parked = PROFILE.with_name(
            PROFILE.name + ".walled-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        # MOVED, never deleted: if the heal makes things worse this is the way
        # back, and the walled profile is the only evidence of what went wrong.
        PROFILE.rename(parked)
        steps.append(f"parked walled profile -> {parked.name}")

    r = subprocess.run(["bash", str(LAUNCHER)], capture_output=True, text=True,
                       timeout=900)
    steps.append(f"relaunch rc={r.returncode}")
    if r.returncode != 0:
        return {"ok": False, "steps": steps,
                "error": (r.stderr or r.stdout or "")[-400:]}

    # Step 5 — without this the DB keeps the expired token and every consumer
    # (placer, health ping, router) still believes we are logged out.
    sync = subprocess.run(
        [sys.executable, "-m", "workers.automation.coolbet_browser_sync",
         "--refresh-jwt"], capture_output=True, text=True, cwd=str(REPO),
        timeout=600)
    ok = "jwt_obtained      = True" in sync.stdout
    steps.append(f"jwt sync {'OK' if ok else 'FAILED'}")
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.touch()
    return {"ok": ok, "steps": steps,
            "error": None if ok else (sync.stdout or sync.stderr)[-400:]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="heal if the profile is walled")
    ap.add_argument("--force", action="store_true", help="heal even if it looks fine")
    ap.add_argument("--min-gap-hours", type=float, default=6.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    d = diagnose()
    needs = bool(a.force or d.get("walled") or
                 (d["cdp_up"] and d.get("has_jwt") is False))
    result = {"diagnosis": d, "needs_heal": needs, "healed": None}

    if needs and (a.apply or a.force):
        recent = _recently_healed(a.min_gap_hours)
        if recent is not None and not a.force:
            result["healed"] = {"ok": False, "skipped": True,
                                "reason": f"healed {recent:.1f}h ago "
                                          f"(< {a.min_gap_hours}h) — re-copying "
                                          f"several GB every tick would be its "
                                          f"own outage"}
        else:
            result["healed"] = heal()

    if a.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"CDP up      : {d['cdp_up']}")
        print(f"walled      : {d['walled']}   ({d['detail']})")
        print(f"JWT         : {d.get('jwt_state')}"
              f"{'' if d.get('jwt_ttl_s') is None else f"  (ttl {d['jwt_ttl_s']}s)"}")
        print(f"needs heal  : {needs}")
        h = result["healed"]
        if h:
            for s in h.get("steps", []):
                print(f"  - {s}")
            if h.get("skipped"):
                print(f"  SKIPPED: {h['reason']}")
            elif h.get("ok"):
                print("  ✓ HEALED — JWT synced to coolbet_session_state")
            else:
                print(f"  ✗ HEAL FAILED: {h.get('error','')[:200]}")
                print("  If your NORMAL Chrome is also logged out, the copy "
                      "carries nothing — log in there, then re-run.")
        elif needs:
            print("  (re-run with --apply to heal)")
    if result["healed"] and not result["healed"].get("ok"):
        return 1
    return 0 if not needs or (result["healed"] or {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
