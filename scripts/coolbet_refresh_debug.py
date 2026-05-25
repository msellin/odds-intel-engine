"""Diagnostic for headless-refresh failure. Dumps URL + all localStorage +
sessionStorage + cookies so we can see what state the headless browser
actually loads into.

Usage:
    venv/bin/python3 scripts/coolbet_refresh_debug.py
    venv/bin/python3 scripts/coolbet_refresh_debug.py --headful   # compare
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()

# Reuse the same shim helper
from coolbet_refresh_jwt import _ensure_uc_distutils_shim  # type: ignore
_ensure_uc_distutils_shim()
import undetected_chromedriver as uc  # type: ignore

PROFILE_DIR = Path.home() / ".coolbet-daemon" / "chrome-profile"
COOLBET_URL = "https://www.coolbet.com/en/sports/football"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headful", action="store_true",
                    help="Run with visible Chrome (compare against headless)")
    ap.add_argument("--wait", type=int, default=15,
                    help="Seconds to wait after page load before dumping (default 15)")
    args = ap.parse_args()

    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    if not args.headful:
        options.add_argument("--headless=new")

    print(f"Launching Chrome ({'headful' if args.headful else 'headless'})…")
    driver = uc.Chrome(options=options, use_subprocess=False)
    try:
        driver.get(COOLBET_URL)
        print(f"Waiting {args.wait}s for hydration…")
        time.sleep(args.wait)

        # URL + title
        url = driver.current_url
        title = driver.title
        print(f"\nLanded URL: {url}")
        print(f"Page title: {title}")

        # Dump storage
        storage = driver.execute_script("""
            const out = { localStorage: {}, sessionStorage: {} };
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                out.localStorage[k] = (localStorage.getItem(k) || '').slice(0, 80);
            }
            for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i);
                out.sessionStorage[k] = (sessionStorage.getItem(k) || '').slice(0, 80);
            }
            return out;
        """) or {}

        print(f"\nlocalStorage keys ({len(storage.get('localStorage', {}))}):")
        for k, v in (storage.get("localStorage") or {}).items():
            print(f"  {k} = {v}")

        print(f"\nsessionStorage keys ({len(storage.get('sessionStorage', {}))}):")
        for k, v in (storage.get("sessionStorage") or {}).items():
            print(f"  {k} = {v}")

        cookies = driver.get_cookies()
        print(f"\nCookies ({len(cookies)}):")
        for c in cookies[:25]:
            n = c.get("name", "")
            v = (c.get("value", "") or "")[:40]
            d = c.get("domain", "")
            print(f"  {n} = {v}…  [{d}]")
        if len(cookies) > 25:
            print(f"  … +{len(cookies)-25} more")

        # Page-level sniff: are we on a challenge page?
        body_text = driver.execute_script(
            "return (document.body && document.body.innerText || '').slice(0, 300);"
        )
        print(f"\nFirst 300 chars of body:\n  {body_text!r}")

        # Save the full dump
        dump_path = Path.home() / ".coolbet-daemon" / "headless-debug.json"
        dump_path.write_text(json.dumps({
            "url": url, "title": title,
            "localStorage": driver.execute_script("""
                const out={}; for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i); out[k]=localStorage.getItem(k);} return out;
            """),
            "sessionStorage": driver.execute_script("""
                const out={}; for(let i=0;i<sessionStorage.length;i++){const k=sessionStorage.key(i); out[k]=sessionStorage.getItem(k);} return out;
            """),
            "cookies": cookies,
            "body_first_300": body_text,
        }, indent=2, default=str))
        print(f"\nFull dump → {dump_path}")
        return 0
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
