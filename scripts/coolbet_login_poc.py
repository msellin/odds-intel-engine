"""
Coolbet Playwright login PoC — tests whether we can get past Cloudflare
and log in without human help.

Two strategies tried in order:
  1. Existing Chrome profile — Coolbet already trusts this browser; may
     already be logged in. Lowest detection risk.
  2. Stealth chromium — fresh browser with fingerprint patches to pass CF.

Run:
    venv/bin/python scripts/coolbet_login_poc.py
    venv/bin/python scripts/coolbet_login_poc.py --strategy stealth
    venv/bin/python scripts/coolbet_login_poc.py --headless  # for Railway

Credentials from .env: COOLBET_EMAIL, COOLBET_PASSWORD
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

COOLBET_EMAIL = os.getenv("COOLBET_EMAIL", "")
COOLBET_PASSWORD = os.getenv("COOLBET_PASSWORD", "")
CHROME_PROFILE = Path.home() / "Library/Application Support/Google/Chrome"

SPORTS_URL = "https://www.coolbet.com/en/sports/football"
LOGIN_URL  = "https://www.coolbet.com/en/login"


async def _check_logged_in(page) -> bool:
    """Return True if already authenticated (looks for account/balance indicator)."""
    try:
        # Coolbet shows a balance or username element when logged in
        await page.wait_for_selector(
            "[data-testid='account-balance'], [class*='balance'], [class*='userName'], "
            "[data-cy='user-balance'], nav [class*='logged']",
            timeout=5000,
        )
        return True
    except Exception:
        return False


async def _do_login(page) -> bool:
    """Fill and submit login form. Returns True on apparent success."""
    if not COOLBET_EMAIL or not COOLBET_PASSWORD:
        print("  ✗ COOLBET_EMAIL / COOLBET_PASSWORD not set in .env")
        return False

    print(f"  → Navigating to login page...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    # Accept cookies if dialog is present
    try:
        await page.click(
            "button[id*='accept'], button[class*='accept'], "
            "button:has-text('Accept'), button:has-text('Accept all')",
            timeout=4000,
        )
        print("  ✓ Cookie consent accepted")
        await asyncio.sleep(1)
    except Exception:
        pass

    # Fill credentials
    try:
        await page.fill("input[type='email'], input[name='email'], input[id*='email']",
                        COOLBET_EMAIL, timeout=8000)
        await page.fill("input[type='password'], input[name='password'], input[id*='password']",
                        COOLBET_PASSWORD, timeout=5000)
        print("  ✓ Credentials filled")
    except Exception as e:
        print(f"  ✗ Could not fill login form: {e}")
        return False

    await asyncio.sleep(0.5)
    await page.click(
        "button[type='submit'], button:has-text('Log in'), button:has-text('Sign in')",
        timeout=5000,
    )
    print("  ✓ Submit clicked, waiting for redirect...")
    await page.wait_for_load_state("networkidle", timeout=20000)
    await asyncio.sleep(2)

    return await _check_logged_in(page)


async def strategy_existing_profile(headless: bool) -> bool:
    """Launch with user's real Chrome profile — inherits existing Coolbet session."""
    from playwright.async_api import async_playwright

    print(f"\n[Strategy 1] Using existing Chrome profile: {CHROME_PROFILE}")
    if not CHROME_PROFILE.exists():
        print("  Chrome profile not found, skipping.")
        return False

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(CHROME_PROFILE),
            channel="chrome",      # use real Chrome, not Playwright's Chromium
            headless=headless,
            args=["--profile-directory=Default"],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print(f"  → Navigating to {SPORTS_URL} ...")
        try:
            await page.goto(SPORTS_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  ✗ Navigation failed: {e}")
            await ctx.close()
            return False

        await asyncio.sleep(3)

        # Check for Cloudflare challenge
        title = await page.title()
        print(f"  Page title: {title!r}")
        if "just a moment" in title.lower() or "cloudflare" in title.lower():
            print("  ✗ Cloudflare challenge detected")
            await ctx.close()
            return False
        print("  ✓ Cloudflare passed")

        if await _check_logged_in(page):
            print("  ✓ Already logged in!")
            await ctx.close()
            return True

        print("  → Not logged in, attempting login...")
        success = await _do_login(page)
        print(f"  {'✓ Login successful' if success else '✗ Login failed'}")

        if not headless:
            print("  (browser stays open 10s for inspection)")
            await asyncio.sleep(10)

        await ctx.close()
        return success


async def strategy_stealth(headless: bool) -> bool:
    """Fresh Chromium browser with playwright-stealth fingerprint patches."""
    from playwright.async_api import async_playwright
    try:
        from playwright_stealth import stealth_async
    except ImportError:
        print("  playwright-stealth not installed — run: venv/bin/pip install playwright-stealth")
        return False

    print("\n[Strategy 2] Stealth Chromium (fresh browser, patched fingerprints)")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
            ],
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            timezone_id="Europe/London",
        )
        page = await ctx.new_page()
        await stealth_async(page)

        print(f"  → Navigating to {SPORTS_URL} ...")
        try:
            await page.goto(SPORTS_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  ✗ Navigation failed: {e}")
            await browser.close()
            return False

        await asyncio.sleep(3)

        title = await page.title()
        print(f"  Page title: {title!r}")
        if "just a moment" in title.lower() or "cloudflare" in title.lower():
            print("  ✗ Cloudflare challenge detected")
            await browser.close()
            return False
        print("  ✓ Cloudflare passed")

        if await _check_logged_in(page):
            print("  ✓ Already logged in (unexpected for fresh browser)")
            await browser.close()
            return True

        success = await _do_login(page)
        print(f"  {'✓ Login successful' if success else '✗ Login failed'}")

        if not headless:
            await asyncio.sleep(10)

        await browser.close()
        return success


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", choices=["profile", "stealth", "both"], default="both")
    p.add_argument("--headless", action="store_true", help="Run headless (no visible window)")
    args = p.parse_args()

    print("=" * 60)
    print("Coolbet Playwright login PoC")
    print("=" * 60)

    if not COOLBET_EMAIL:
        print("\nWARNING: COOLBET_EMAIL not in .env — login attempt will fail.")
        print("Add COOLBET_EMAIL and COOLBET_PASSWORD to .env first.\n")

    results = {}

    if args.strategy in ("profile", "both"):
        results["profile"] = await strategy_existing_profile(args.headless)

    if args.strategy in ("stealth", "both"):
        if args.strategy == "both" and results.get("profile"):
            print("\nProfile strategy succeeded — skipping stealth.")
        else:
            results["stealth"] = await strategy_stealth(args.headless)

    print("\n" + "=" * 60)
    print("Results:")
    for strat, ok in results.items():
        print(f"  {strat:10s}  {'✓ SUCCESS' if ok else '✗ FAILED'}")

    any_ok = any(results.values())
    if any_ok:
        print("\n→ Playwright can log in. Ready to build bet placement automation.")
    else:
        print("\n→ Login blocked. Options:")
        print("   • Add COOLBET_EMAIL + COOLBET_PASSWORD to .env and retry")
        print("   • Run headed (remove --headless) so you can solve any manual challenge once")
        print("   • Try from home IP if running on a server")
    print("=" * 60)

    sys.exit(0 if any_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
