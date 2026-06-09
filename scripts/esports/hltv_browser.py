"""
Reusable headless-Chromium fetcher for HLTV /stats/* URLs.

Why: Cloudflare's bot management on HLTV's /stats/* pages rejects standard
Python `requests` clients even with valid cookies. It requires JS execution,
canvas fingerprinting, and other browser-only features. Playwright solves
this by running a real Chromium that automatically handles CF challenges.

Usage:
    with HltvBrowser() as br:
        html = br.fetch('https://www.hltv.org/stats/teams/pistols?...')
        html2 = br.fetch('https://www.hltv.org/stats/teams/maps/9565/vitality')

Single browser instance is reused across requests — solving CF once gives
us a clearance cookie that persists for subsequent requests in the same
session. Net: ~5-10s for first request (CF challenge), <2s thereafter.

Stealth notes: we don't use undetected-chromedriver. Playwright launches
real Chromium with standard fingerprint; no monkey-patching. We only set
viewport + UA. This is "I'm a real headless browser doing legitimate
scraping at human pace" — not "I'm pretending to be desktop Chrome."
"""

from __future__ import annotations

import time
from typing import Optional

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

try:
    from playwright_stealth import Stealth
    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False


CHROMIUM_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class HltvBrowser:
    """Context-managed headless browser for HLTV scraping."""

    def __init__(self, headless: bool = True, wait_cf_seconds: int = 15):
        self.headless = headless
        self.wait_cf_seconds = wait_cf_seconds
        self._pw = None
        self._browser: Optional[Browser] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def __enter__(self) -> "HltvBrowser":
        self._pw = sync_playwright().start()
        # Use a persistent profile so cf_clearance survives across runs
        # and the JS environment matches a "returning visitor".
        from pathlib import Path
        profile_dir = Path.home() / ".cache" / "hltv_browser_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        self._ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=self.headless,
            user_agent=CHROMIUM_UA,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Europe/Tallinn",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        # Hide common automation flags
        self._ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = { runtime: {} };
        """)
        self._page = self._ctx.new_page()
        # Apply stealth patches to evade common automation detection.
        if _HAS_STEALTH:
            try:
                Stealth().apply_stealth_sync(self._page)
                print(f"  [stealth] applied")
            except Exception as e:
                print(f"  [!] stealth apply failed: {e}")
        # Warm-up: visit homepage so we pass any first-visit CF challenge
        # before hitting /stats/* which has stricter rules.
        print(f"  [cf] warming session via https://www.hltv.org/ ...")
        try:
            self._page.goto("https://www.hltv.org/", wait_until="domcontentloaded", timeout=30000)
            self._wait_for_no_challenge()
        except Exception as e:
            print(f"  [!] warmup failed: {e}")
        return self

    def _wait_for_no_challenge(self) -> bool:
        """Wait until page title is NOT 'Just a moment...'. Returns True if resolved."""
        for i in range(self.wait_cf_seconds):
            time.sleep(1)
            try:
                t = self._page.title() if self._page else ""
            except Exception:
                continue
            if "Just a moment" not in t and "challenge" not in t.lower():
                if i > 0:
                    print(f"  [cf] resolved after {i+1}s")
                return True
        return False

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def fetch(self, url: str, polite_sleep: float = 2.0) -> str | None:
        if self._page is None:
            raise RuntimeError("HltvBrowser must be used as context manager")
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  [!] goto failed for {url}: {e}")
            return None

        title = self._page.title()
        if "Just a moment" in title or "challenge" in title.lower():
            print(f"  [cf] waiting for challenge to resolve...")
            if not self._wait_for_no_challenge():
                print(f"  [!] CF challenge did not resolve after {self.wait_cf_seconds}s")
                return self._page.content()

        if polite_sleep > 0:
            time.sleep(polite_sleep)
        return self._page.content()
