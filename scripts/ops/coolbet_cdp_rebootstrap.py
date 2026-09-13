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


def autologin() -> dict:
    """Cheapest tier: sign in through the repo's own CDP auto-login.

    Reads COOLBET_USER / COOLBET_PASS from .env and drives the real form in
    CDP-Chrome — the same routine the daemon has always had. Seconds, and it
    touches no profile. If Coolbet asks for SMS this cannot complete it and the
    next tick escalates, which is the correct division: automation handles the
    routine lapse, a human handles a genuine 2FA challenge.
    """
    steps = []
    r = subprocess.run(
        [sys.executable, "-m", "workers.automation.coolbet_browser_sync",
         "--cdp-auto-login"], capture_output=True, text=True, cwd=str(REPO),
        timeout=600)
    logged = "✓ logged in" in r.stdout
    steps.append(f"cdp auto-login {'OK' if logged else 'FAILED'}")
    if not logged:
        return {"ok": False, "tier": "autologin", "steps": steps,
                "error": (r.stdout or r.stderr)[-300:],
                "hint": "if Coolbet asked for SMS, complete it in CDP-Chrome"}
    # Same trap as every other tier: without this the browser is logged in and
    # every consumer still reads the stale DB row.
    sync = subprocess.run(
        [sys.executable, "-m", "workers.automation.coolbet_browser_sync",
         "--refresh-jwt"], capture_output=True, text=True, cwd=str(REPO),
        timeout=600)
    ok = "jwt_obtained      = True" in sync.stdout
    steps.append(f"jwt sync {'OK' if ok else 'FAILED'}")
    return {"ok": ok, "tier": "autologin", "steps": steps,
            "error": None if ok else (sync.stdout or sync.stderr)[-300:]}


def relaunch() -> dict:
    """Cheap tier: start CDP-Chrome again. Touches no profile.

    Separate from `heal()` on purpose — a crashed browser needs seconds, not a
    several-GB profile copy, and conflating the two either makes crashes
    expensive or makes walls unfixable.
    """
    steps = []
    subprocess.run(["pkill", "-f", "Chrome-CDP-OddsIntel"], capture_output=True)
    time.sleep(2)
    r = subprocess.run(["bash", str(LAUNCHER)], capture_output=True, text=True,
                       timeout=900)
    steps.append(f"relaunch rc={r.returncode}")
    if r.returncode != 0 or not _cdp_up():
        return {"ok": False, "tier": "relaunch", "steps": steps,
                "error": (r.stderr or r.stdout or "CDP still down")[-300:]}
    # Pull whatever session the profile still holds into the DB. Failure here
    # is NOT fatal: the browser is up again, which is what this tier promised,
    # and the next tick escalates to a rebootstrap if there is still no token.
    sync = subprocess.run(
        [sys.executable, "-m", "workers.automation.coolbet_browser_sync",
         "--refresh-jwt"], capture_output=True, text=True, cwd=str(REPO),
        timeout=600)
    ok = "jwt_obtained      = True" in sync.stdout
    if not ok:
        # The browser is back but signed out — try the cheap fix immediately
        # rather than waiting 30 minutes for the next tick. Escalation should
        # cost time only when it actually needs a human.
        steps.append("no JWT after relaunch — trying auto-login")
        al = autologin()
        steps.extend(al.get("steps", []))
        return {"ok": True, "tier": "relaunch+autologin", "steps": steps,
                "jwt": bool(al.get("ok"))}
    steps.append("jwt sync OK")
    return {"ok": True, "tier": "relaunch", "steps": steps, "jwt": ok}


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

    # TWO TIERS, and getting this wrong made the first version DECORATIVE.
    #
    # v1 asked `cdp_up AND has_jwt is False`. So when CDP-Chrome was DEAD —
    # exactly when reviving matters — `cdp_up` was False, the whole condition
    # collapsed to False, and the log filled with:
    #     CDP up: False / walled: None / needs heal: False
    # tick after tick, for hours, against a browser that was not running. A
    # reviver that stands down because its subject is down is worse than none:
    # it looks like supervision.
    #
    # But "Chrome crashed" must NOT trigger a multi-GB profile re-copy. The
    # cheap fix is a relaunch; the expensive one is only for a profile Coolbet
    # has actually walled. Hence:
    #     RELAUNCH  — CDP down. Seconds. No profile touched.
    #     REBOOTSTRAP — walled, or still no JWT after a relaunch. Several GB.
    tier = None
    if a.force:
        tier = "rebootstrap"
    elif d.get("walled"):
        tier = "rebootstrap"
    elif not d["cdp_up"]:
        tier = "relaunch"
    elif d.get("has_jwt") is False:
        # Browser is up and RENDERING (not walled) but holds no token — i.e. the
        # session simply expired. That is the common case by far, and it needs
        # neither a relaunch nor a multi-GB copy: `--cdp-auto-login` fills the
        # form from COOLBET_USER/PASS and takes seconds. Verified 2026-09-13:
        # "✓ logged in", no SMS prompt.
        #
        # An earlier draft sent this straight to `rebootstrap`, which would have
        # copied several GB every time a 30-minute token lapsed — turning the
        # most routine event into the most expensive one.
        tier = "autologin"
    result = {"diagnosis": d, "needs_heal": tier is not None, "tier": tier,
              "healed": None}

    if tier and (a.apply or a.force):
        if tier == "autologin":
            result["healed"] = autologin()
        elif tier == "relaunch":
            # Deliberately NOT rate-limited: relaunching a dead browser is
            # cheap and is the whole point of a 24/7 reviver.
            result["healed"] = relaunch()
        else:
            recent = _recently_healed(a.min_gap_hours)
            if recent is not None and not a.force:
                result["healed"] = {"ok": False, "skipped": True,
                                    "reason": f"re-bootstrapped {recent:.1f}h ago "
                                              f"(< {a.min_gap_hours}h) — copying "
                                              f"several GB every tick would be "
                                              f"its own outage"}
            else:
                result["healed"] = heal()

    if a.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"CDP up      : {d['cdp_up']}")
        print(f"walled      : {d['walled']}   ({d['detail']})")
        print(f"JWT         : {d.get('jwt_state')}"
              f"{'' if d.get('jwt_ttl_s') is None else f"  (ttl {d['jwt_ttl_s']}s)"}")
        print(f"needs heal  : {result['needs_heal']}"
              f"{'' if not tier else f'  (tier: {tier})'}")
        h = result["healed"]
        if h:
            for s in h.get("steps", []):
                print(f"  - {s}")
            if h.get("skipped"):
                print(f"  SKIPPED: {h['reason']}")
            elif h.get("ok") and h.get("jwt") is False:
                # Do not claim a synced JWT we did not get. The relaunch tier
                # succeeds at what it promised (browser up) while the session
                # may still be absent; saying "healed, JWT synced" there is a
                # status line that lies, which is the whole failure class this
                # session has been unpicking.
                print("  ✓ browser relaunched — but NO JWT yet (profile has no "
                      "session). Next tick escalates to a full re-bootstrap.")
            elif h.get("ok"):
                print("  ✓ HEALED — JWT synced to coolbet_session_state")
            else:
                print(f"  ✗ HEAL FAILED: {h.get('error','')[:200]}")
                print("  If your NORMAL Chrome is also logged out, the copy "
                      "carries nothing — log in there, then re-run.")
        elif tier:
            print(f"  (re-run with --apply to {tier})")
    if result["healed"] and not result["healed"].get("ok"):
        return 1
    return 0 if not tier or (result["healed"] or {}).get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
