#!/usr/bin/env python3
"""COOLBET-LOGIN-STATE — read-only: what is the CDP-Chrome login tab ACTUALLY showing?

    python3 scripts/diagnose/coolbet_login_state.py

Reads nothing but the DOM. Places nothing, clicks nothing, changes no cookie.

WHY IT EXISTS. "The Coolbet login doesn't work" has recurred many times and each
round has been diagnosed from a SYMPTOM one layer away — `session_healthy=false`,
a non-200 heartbeat, "logged_out" from localStorage — none of which say what the
page is showing. Those three states have completely different fixes:

    Imperva wall        -> STAY COOL / Pardon Our Interruption   -> identity/footprint
    login form present  -> we are simply not logged in           -> log in
    logged in already   -> the JWT never reached localStorage    -> a code bug

Guessing between them is what makes this recur. This prints which one it is.
"""
from __future__ import annotations

import sys

CDP = "http://localhost:9222"
# MEASURED 2026-09-13 — "stay cool" is deliberately NOT in this list, and that
# correction is the whole point of this file. docs/COOLBET_RUNBOOK.md §2 has
# said for weeks that a "~9 chars (STAY COOL)" body IS the Imperva wall. It is
# not, and believing it sent every previous attempt down the bot-detection path
# for a fault that has nothing to do with bot detection:
#
#     CDP-Chrome on STAY COOL : HTTP 200, 504,929 bytes of real Coolbet SPA,
#                               ZERO Imperva markers, zero console errors,
#                               zero failed requests, zero HTTP >= 400
#     same Mac, plain request : 6,058-byte Imperva JS challenge with
#                               _Incapsula_Resource + "Pardon Our Interruption"
#
# Those are different responses. The interstitial has Imperva's fingerprints all
# over it; STAY COOL has none. Markers here are ONLY the ones Imperva actually
# emits, checked against raw HTML rather than rendered text — a 9-character
# body tells you the SPA rendered nothing, not who stopped it.
WALL_MARKERS = ("pardon our interruption", "_incapsula_resource",
                "incident id", "request unsuccessful")


def main() -> int:
    try:
        from patchright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        print(f"patchright unavailable: {e}", file=sys.stderr)
        return 2

    with sync_playwright() as p:
        try:
            b = p.chromium.connect_over_cdp(CDP)
        except Exception as e:  # noqa: BLE001
            print(f"cannot reach CDP-Chrome at {CDP}: {e}", file=sys.stderr)
            return 2
        pages = [pg for c in b.contexts for pg in c.pages
                 if "coolbet" in (pg.url or "").lower()]
        if not pages:
            print("no coolbet tab open in CDP-Chrome")
            return 1
        pg = pages[0]
        print(f"url     : {pg.url}")
        try:
            print(f"title   : {pg.title()!r}")
        except Exception as e:  # noqa: BLE001
            print(f"title   : <unreadable: {e}>")

        # Check the RAW HTML for wall markers, not the rendered text: the
        # rendered body was what made STAY COOL look like a block.
        try:
            raw_html = pg.content() or ""
        except Exception:  # noqa: BLE001
            raw_html = ""
        print(f"raw HTML: {len(raw_html)} bytes")
        try:
            body = (pg.inner_text("body", timeout=5000) or "")
        except Exception as e:  # noqa: BLE001
            body = ""
            print(f"body    : <unreadable: {e}>")
        print(f"body len: {len(body)}  (rendered text — a tiny body means the "
              f"SPA rendered nothing, NOT that something blocked it)")
        hit = [m for m in WALL_MARKERS if m in raw_html.lower()]

        # localStorage is what the rest of the stack reads, so show whether the
        # token the placer needs is actually there.
        try:
            has_jwt = pg.evaluate(
                "() => { try { return Object.keys(localStorage)"
                ".filter(k => k.toLowerCase().includes('cbauth')); } "
                "catch(e) { return ['<blocked: '+e+'>']; } }")
        except Exception as e:  # noqa: BLE001
            has_jwt = f"<eval failed: {e}>"
        print(f"cbauth  : {has_jwt}")

        # Is a real login form mounted? (the app shell can load without it)
        sel = {}
        for name, css in (("user", "input[name='username'], input[type='email']"),
                          ("pass", "input[type='password']"),
                          ("submit", "button[type='submit']")):
            try:
                sel[name] = pg.locator(css).count()
            except Exception:  # noqa: BLE001
                sel[name] = -1
        print(f"form    : {sel}")

        print("\nVERDICT")
        if hit:
            print(f"  IMPERVA WALL — markers {hit}")
            print("  The page is a challenge, not the site. Fix the IDENTITY and the")
            print("  FOOTPRINT (runbook §2/§7 + coolbet_reset_imperva_identity.py).")
            print("  Logging in is impossible until this clears.")
            return 1
        if isinstance(has_jwt, list) and has_jwt and not str(has_jwt[0]).startswith("<"):
            print("  LOGGED IN — cbauth present. If the stack still says logged_out,")
            print("  the bug is in how we READ it, not in the login.")
            return 0
        if sel.get("pass", 0) > 0:
            print("  LOGIN FORM PRESENT and no wall — the site is reachable and we are")
            print("  simply signed out. Sign in in this tab; nothing else is blocking.")
            return 1
        print("  APP SHELL LOADED, NO FORM, NO IMPERVA MARKERS.")
        print("  The site served us the real application and it chose to render")
        print("  nothing. This is NOT bot detection — do not reach for cookie resets,")
        print("  FS restarts or the footprint lever; all of those have been tried")
        print("  against this state and none of them touch it.")
        print()
        print("  RULED OUT BY MEASUREMENT (2026-09-13), do not repeat these:")
        print("    - Imperva interstitial   : raw HTML has no Imperva markers at all")
        print("    - flagged cookie identity: cleared all 5 Imperva cookies, unchanged")
        print("    - flagged localStorage   : cleared reese84 (732 chars) + uuid, unchanged")
        print("    - stale SPA route        : fresh goto to /et/login, unchanged")
        print("    - login-page-specific    : /et/sport, /et/ and /en/login are identical")
        print("    - a JS boot failure      : no console errors, no failed requests, no 4xx")
        print()
        print("  THE DECIDING TEST, which needs a human: open coolbet.com in a NORMAL")
        print("  Chrome window (NOT this --remote-debugging-port one).")
        print("    loads fine   -> the block follows THIS automation profile, and the")
        print("                    architecture must stop logging in through CDP-Chrome")
        print("    also walled  -> it is the IP or the ACCOUNT, and no browser-side")
        print("                    remedy will ever fix it")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
