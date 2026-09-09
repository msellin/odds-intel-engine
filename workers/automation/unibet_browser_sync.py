"""UNIBET-UI-PLACER Phase 1 — CDP session + login + interstitial handling for
unibet.ee, mirroring the Coolbet stack (workers/automation/coolbet_browser_sync.py).

WHY THIS IS SEPARATE FROM THE ODDS FEED
`workers/automation/unibet_kambi.py` reads the PUBLIC Kambi offering API (no auth,
no bot-protection) for ODDS. This module is the OPPOSITE: the authenticated
placement site `unibet.ee`, which (per KAMBI-FEED-DIVERGENCE) is a BESPOKE SPA (not
the Kambi widget), sits behind **DataDome** bot-protection, and interstitials the
sportsbook with a "Panustamise limiit" (spending-limit) modal. So we must drive the
operator's REAL logged-in CDP-Chrome (:9222) — never a headless/fresh browser —
exactly as the Coolbet stack drives Imperva.

Phase-0 spike findings that shaped this (dev/active/unibet-ui-placer-plan.md):
  * DataDome present → real session only, rate-limited, behavioral-block risk.
  * Bespoke (not Kambi) → selectors are Unibet-specific, mapped against a live event.
  * Estonian locale → odds `2,50` (comma), balance `100,00 €`.
  * The spending-limit modal re-appears on navigation → dismiss it robustly, on a loop.

SAFETY: this module ONLY establishes/verifies a session and dismisses interstitials.
It never selects an outcome, sets a stake, or places a bet — that is the Phase-2
driver, gated by its own PLACEABLE set. Credentials are read from the environment
(UNIBET_EMAIL / UNIBET_PASSWORD); they are never logged or returned.
"""
from __future__ import annotations

import logging
import os
import re
import time

log = logging.getLogger(__name__)

CDP_URL = os.getenv("UNIBET_CHROME_CDP_URL", "http://localhost:9222")
LOGIN_URL = "https://www.unibet.ee/login"
SPORT_URL = "https://www.unibet.ee/betting/odds/football/matches"
_MYACCOUNT = "myaccount"

# The spending-limit interstitial. Estonian "Ei, aitäh" = "No, thanks" (declines the
# limit prompt — a benign dismissal, not a consent). We also accept the X/close.
_DISMISS_TEXT = re.compile(r"ei,?\s*aitäh|hiljem|sulge", re.I)
# A logged-in page shows the balance "Põhisaldo … 100,00 €"; the login page shows a
# password field. Either is a reliable state signal.
_BALANCE_RE = re.compile(r"Põhisaldo|Bonus\s*€|\d{1,3},\d{2}\s*€")


def _get_context(pw):
    """Connect to the operator's CDP-Chrome and return its default context, or raise."""
    browser = pw.chromium.connect_over_cdp(CDP_URL, timeout=10000)
    if not browser.contexts:
        raise RuntimeError(f"CDP at {CDP_URL} has no browser context")
    return browser.contexts[0]


def _unibet_page(ctx, *, navigate: bool = False):
    """Return an existing unibet.ee page, or the first page navigated to unibet.ee.
    Prefer the operator's already-open tab (it renders with the established session;
    a freshly spawned tab is degraded by DataDome — Phase-0 finding)."""
    for pg in ctx.pages:
        if "unibet" in (pg.url or "").lower():
            return pg
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    if navigate:
        pg.goto(SPORT_URL, wait_until="domcontentloaded", timeout=30000)
    return pg


def dismiss_limit_modal(page, *, tries: int = 3) -> bool:
    """Dismiss the "Panustamise limiit" interstitial if present. It can re-appear on
    navigation, so we retry a few times. Returns True if we dismissed at least once."""
    dismissed = False
    for _ in range(tries):
        try:
            btn = page.get_by_text(_DISMISS_TEXT).first
            if btn.count() > 0 and btn.is_visible(timeout=1500):
                btn.click(timeout=2000)
                dismissed = True
                page.wait_for_timeout(800)
                continue
        except Exception:
            pass
        # also try an explicit close (X) button inside a dialog
        try:
            x = page.locator('[role="dialog"] button[aria-label*="close" i], '
                             '[role="dialog"] button:has-text("×")').first
            if x.count() > 0 and x.is_visible(timeout=800):
                x.click(timeout=1500)
                dismissed = True
                page.wait_for_timeout(800)
                continue
        except Exception:
            pass
        break
    return dismissed


def is_logged_in(page) -> bool:
    """True if the page shows a logged-in signal (balance) and is not the login form."""
    try:
        if "/login" in (page.url or ""):
            # a password field means we are NOT logged in
            if page.locator('input[type="password"]').count() > 0:
                return False
        body = page.evaluate("() => document.body ? document.body.innerText.slice(0, 800) : ''")
        return bool(_BALANCE_RE.search(body or ""))
    except Exception:
        return False


def cdp_auto_login(*, max_wait_s: int = 180) -> int:
    """Ensure unibet.ee is logged in in the operator's CDP-Chrome.

    Idempotent: if already logged in, dismiss the limit modal and return 0. Otherwise
    fill the login form from UNIBET_EMAIL / UNIBET_PASSWORD and submit; the operator
    completes any 2FA/SMS in the browser. Polls until logged in or timeout.

    Returns 0 = logged in; 2 = missing credentials; 5 = could not confirm login.
    Credentials are read from the environment and never logged.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        log.error("unibet cdp_auto_login: playwright unavailable: %s", e)
        return 5
    with sync_playwright() as pw:
        try:
            ctx = _get_context(pw)
        except Exception as e:  # noqa: BLE001
            log.error("unibet cdp_auto_login: %s", e)
            return 5
        page = _unibet_page(ctx)
        # already logged in?
        try:
            if is_logged_in(page):
                dismiss_limit_modal(page)
                print("unibet-login: already logged in ✓")
                return 0
        except Exception:
            pass

        email = os.getenv("UNIBET_EMAIL")
        pw_val = os.getenv("UNIBET_PASSWORD")
        if not email or not pw_val:
            print("unibet-login: UNIBET_EMAIL / UNIBET_PASSWORD not set in .env — "
                  "cannot auto-login. (already-logged-in path still works.)")
            return 2

        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            dismiss_limit_modal(page)
            # Selectors are best-effort against unibet.ee's bespoke form; refined when
            # a real logged-out form is observed. Try common shapes, most specific first.
            user_sel = ('input[name="username" i], input[name="email" i], '
                        'input[type="email"], input[autocomplete="username"]')
            pass_sel = 'input[type="password"], input[name="password" i]'
            page.locator(user_sel).first.fill(email, timeout=8000)
            page.locator(pass_sel).first.fill(pw_val, timeout=8000)
            # submit: the login button, else Enter
            try:
                page.get_by_role("button", name=re.compile(r"logi sisse|sisene|login", re.I)).first.click(timeout=4000)
            except Exception:
                page.locator(pass_sel).first.press("Enter")
            print("unibet-login: submitted; watching for logged-in state "
                  f"(up to {max_wait_s}s; complete SMS/2FA in the browser if asked)")
        except Exception as e:  # noqa: BLE001
            log.warning("unibet-login: form fill failed (%s) — selectors may need "
                        "refinement against the live form", e)
            return 5

        deadline = time.time() + max_wait_s
        while time.time() < deadline:
            page.wait_for_timeout(2500)
            try:
                if is_logged_in(page):
                    dismiss_limit_modal(page)
                    print("unibet-login: logged in ✓")
                    return 0
            except Exception:
                pass
        print("unibet-login: could not confirm login within timeout")
        return 5


def diagnose() -> dict:
    """Read-only state probe for the CDP unibet session (for the smoke test / ops)."""
    out = {"connected": False, "logged_in": False, "url": None, "datadome": None}
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return out
    with sync_playwright() as pw:
        try:
            ctx = _get_context(pw)
        except Exception:
            return out
        out["connected"] = True
        page = _unibet_page(ctx)
        out["url"] = page.url
        try:
            out["logged_in"] = is_logged_in(page)
            out["datadome"] = bool(page.evaluate("() => document.cookie.includes('datadome')"))
        except Exception:
            pass
    return out


def main() -> int:
    import json
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(diagnose(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
