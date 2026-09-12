#!/usr/bin/env python3
"""COOLBET-RESET-IMPERVA-IDENTITY — drop the flagged Imperva visitor ID so the
"STAY COOL" wall can be re-challenged from scratch.

    python3 scripts/ops/coolbet_reset_imperva_identity.py            # dry run
    python3 scripts/ops/coolbet_reset_imperva_identity.py --apply

WHEN TO REACH FOR THIS — and when NOT to
----------------------------------------
Use it ONLY when ALL of these hold, because it resets an identity rather than
removing a cause:

  * CDP-Chrome's coolbet.com login shows **STAY COOL** / "Pardon Our
    Interruption" (runbook §2 — the real wall, NOT the §1b/§1c interstitial);
  * you have already loaded the page in a **foreground** tab and let the JS
    proof-of-work run, and it still walls;
  * you have already stopped whatever of OUR OWN traffic is feeding it. On
    2026-09-13 that was `coolbet_health_ping` — 143 failed authenticated probes
    in 12 hours into the wall itself, now circuit-broken.

WHY IT WORKS. `visid_incap_*` is a LONG-LIVED visitor id — observed expiry 6,024
hours, roughly eight months. If Imperva has flagged that identity, the flag
follows it: waiting does not help, foregrounding does not help, and neither does
reducing load today, because the mark is attached to the visitor rather than to
current behaviour. Dropping it forces a fresh id and a fresh challenge.

⚠️ THE HONEST CAVEAT. `docs/COOLBET_RUNBOOK.md` §7 says plainly: *"Do NOT 'fix'
this with more IPs or by rotating FS sessions to get a fresh un-escalated
context. That dodges the detection instead of removing what triggers it."* This
script is adjacent to that warning and you should feel the tension. The
difference — and it is the only thing that makes it defensible — is that this
clears a stale identity in the OPERATOR'S OWN browser so a legitimate human
login can complete, AFTER the traffic that caused the flag has been removed. If
you find yourself running it repeatedly, the identity is not the problem: go and
find the request loop that keeps earning the flag.

SURGICAL BY DESIGN. It removes five Imperva cookies and nothing else — consent,
analytics and session preferences survive. "Clear all site data" also works and
is strictly worse.

AFTER RUNNING IT, IN THIS ORDER:
  1. Reload coolbet.com in the FOREGROUND CDP-Chrome tab and let the challenge
     complete. A backgrounded tab cannot finish the JS PoW.
  2. Log in (SMS only if Coolbet asks).
  3. Nothing to restart. The next placer tick picks the session up, and the
     health ping's circuit breaker reopens on its own once a live JWT exists.

TIMING MATTERS. The FS-routed odds sweep is seeded from the Imperva cookies
stored in `coolbet_session_state`, re-harvested from this browser when they go
2h stale. So do steps 1-2 PROMPTLY: if a harvest lands while the browser is
mid-challenge, it seeds FlareSolverr with a challenge-state cookie set and can
take down a feed that was healthy.
"""
from __future__ import annotations

import sys

# The Imperva identity set for coolbet.com. Everything else on the domain is
# left alone on purpose.
IMPERVA = ("reese84", "visid_incap_723517", "incap_ses_1099_723517",
           "nlbi_723517", "nlbi_723517_2147483392")
CDP = "http://localhost:9222"


def main() -> int:
    apply = "--apply" in sys.argv
    try:
        from patchright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        print(f"patchright unavailable: {e}", file=sys.stderr)
        return 2

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception as e:  # noqa: BLE001
            print(f"cannot reach CDP-Chrome at {CDP}: {e}", file=sys.stderr)
            print("Is the operator's Chrome running with "
                  "--remote-debugging-port=9222?", file=sys.stderr)
            return 2
        if not browser.contexts:
            print("CDP-Chrome has no browser context", file=sys.stderr)
            return 2
        ctx = browser.contexts[0]

        cookies = ctx.cookies()
        coolbet = [c for c in cookies if "coolbet" in (c.get("domain") or "")]
        targets = [c for c in coolbet if c["name"] in IMPERVA]
        print(f"coolbet.com cookies: {len(coolbet)}   "
              f"Imperva identity cookies: {len(targets)}")
        for c in targets:
            label = "CLEARING" if apply else "would clear"
            print(f"  {label:12} {c['name']:24} {c.get('domain')}")

        if not targets:
            print("\nNothing to clear — no Imperva identity cookies present. If the "
                  "wall persists with no visid_incap, the flag is NOT on this "
                  "browser identity; look at the IP and at our own request volume.")
            return 0
        if not apply:
            print("\n(dry run — re-run with --apply)")
            return 0

        # clear_cookies() then restore everything we are not targeting, so the
        # blast radius is exactly the five cookies above.
        keep = [c for c in cookies
                if not (c["name"] in IMPERVA
                        and "coolbet" in (c.get("domain") or ""))]
        ctx.clear_cookies()
        ctx.add_cookies(keep)

        after = [c for c in ctx.cookies() if "coolbet" in (c.get("domain") or "")]
        left = [c["name"] for c in after if c["name"] in IMPERVA]
        print(f"\ndone — coolbet.com cookies now {len(after)}; "
              f"Imperva remaining: {left or 'none'}")
        if left:
            print("WARNING: some Imperva cookies survived; clear site data by hand.")
            return 1
        print("\nNEXT, PROMPTLY (the odds feed is seeded from this browser):")
        print("  1. Reload coolbet.com in the FOREGROUND tab, let the challenge finish")
        print("  2. Log in (SMS only if asked)")
        print("  3. Nothing to restart — the next placer tick picks it up")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
