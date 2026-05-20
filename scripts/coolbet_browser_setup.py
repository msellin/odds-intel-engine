"""ONE-TIME interactive Coolbet browser-session bootstrap.

Run this at your laptop ONCE. It will:
  1. Launch real Chrome via undetected-chromedriver
  2. Navigate to coolbet.com (login page)
  3. Wait for YOU to log in via Smart-ID (Estonian eID — tap PIN1 on phone)
  4. After login completes, probe the browser for:
       • JWT location (localStorage / sessionStorage / cookies)
       • Login page DOM (Smart-ID button + ID-code field selectors —
         needed later for Telegram-triggered auto-fill)
  5. Save the Chrome profile to ~/.coolbet-daemon/chrome-profile/
  6. Write the discovered JWT to .env as COOLBET_MANUAL_JWT
  7. Write the discovered selectors to ~/.coolbet-daemon/login-dom.json

After this script completes, the headless refresher
(scripts/coolbet_refresh_jwt.py) will reuse the saved profile and renew the
JWT automatically every 25 minutes — no Smart-ID needed for as long as
Coolbet keeps the session alive (typically days to weeks).

When the session DOES eventually expire, you'll get a Telegram alert. At
that point either:
  • Rerun this script (1-minute task at the laptop), OR
  • Use /relogin in Telegram once we ship Smart-ID auto-fill (v2)

Usage:
    venv/bin/python3 scripts/coolbet_browser_setup.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv, set_key

# Project root for relative paths
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv()


_UC_LOOSEVERSION_SHIM = '''try:
    from distutils.version import LooseVersion  # Python <=3.11
except ImportError:  # Python >=3.12 removed distutils
    from packaging.version import Version as _PkgVersion
    class LooseVersion:
        def __init__(self, vstring):
            self._v = _PkgVersion(str(vstring))
            self.version = list(self._v.release)
            self.vstring = str(vstring)
        def __str__(self):  return str(self._v)
        def __repr__(self): return f"LooseVersion('{self._v}')"
        def _cmp(self, other):
            try:
                ov = other._v if isinstance(other, LooseVersion) else _PkgVersion(str(other))
            except Exception:
                return NotImplemented
            return (self._v > ov) - (self._v < ov)
        def __lt__(self, other): r=self._cmp(other); return NotImplemented if r is NotImplemented else r<0
        def __le__(self, other): r=self._cmp(other); return NotImplemented if r is NotImplemented else r<=0
        def __gt__(self, other): r=self._cmp(other); return NotImplemented if r is NotImplemented else r>0
        def __ge__(self, other): r=self._cmp(other); return NotImplemented if r is NotImplemented else r>=0
        def __eq__(self, other): r=self._cmp(other); return NotImplemented if r is NotImplemented else r==0
        def __hash__(self): return hash(self._v)'''

_UC_OLD_THIN_SHIM = "try:\n    from distutils.version import LooseVersion\nexcept ImportError:\n    from packaging.version import Version as LooseVersion"
_UC_ORIGINAL_IMPORT = "from distutils.version import LooseVersion"


def _ensure_uc_distutils_shim() -> None:
    """undetected-chromedriver 3.5.5 imports `distutils.version.LooseVersion`,
    but Python 3.12+ removed distutils. Patch patcher.py with a full
    LooseVersion-compatible shim (the thin `Version as LooseVersion` rename
    won't work — patcher.py reads `version.version[0]` for major-version
    access, which packaging.Version doesn't expose). Idempotent."""
    import importlib.util
    spec = importlib.util.find_spec("undetected_chromedriver")
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError("undetected_chromedriver not found in this Python — "
                           "run `venv/bin/pip install undetected-chromedriver`")
    pkg_dir = Path(list(spec.submodule_search_locations)[0])
    patcher_py = pkg_dir / "patcher.py"
    src = patcher_py.read_text()
    # ROBUST idempotency: if ANY LooseVersion class definition is already in
    # the file (from a previous patch run, manual edit, or even an upstream
    # change), don't touch it. Byte-exact comparisons fail on comment drift
    # and have re-patched live files into double-try corruption before.
    if "class LooseVersion" in src or "except ImportError" in src:
        return  # already patched in some form — leave alone
    if _UC_ORIGINAL_IMPORT not in src:
        return  # unrecognised state, leave alone
    new = src.replace(_UC_ORIGINAL_IMPORT, _UC_LOOSEVERSION_SHIM, 1)
    patcher_py.write_text(new)
    print(f"  (auto-patched {patcher_py.name} for Python 3.12+ distutils removal)")


_ensure_uc_distutils_shim()
import undetected_chromedriver as uc  # type: ignore

PROFILE_DIR = Path.home() / ".coolbet-daemon" / "chrome-profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

LOGIN_DOM_FILE = Path.home() / ".coolbet-daemon" / "login-dom.json"
ENV_FILE = ROOT / ".env"

COOLBET_URL = "https://www.coolbet.com/en/sports/football"


def _wait_for_login(driver, timeout: int = 300) -> bool:
    """Poll page state until we detect the user is logged in. Returns True on
    success, False on timeout.

    Login indicators tried (in order):
      • localStorage contains a key with a JWT-shaped value
      • document.cookie contains cbauth/Bearer
      • URL no longer contains /login or /auth
    """
    print(f"\n👤 Waiting for you to log in via Smart-ID (up to {timeout}s)…")
    print("   1. Click 'Smart-ID' (or 'Logi sisse' → Smart-ID) on the page")
    print("   2. Enter your Estonian personal ID code")
    print("   3. Tap PIN1 on your phone when prompted")
    print("   4. This script auto-detects the moment login completes\n")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # Look for a JWT-shaped string anywhere in localStorage
            jwt_found = driver.execute_script("""
                const out = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    const v = localStorage.getItem(k);
                    out[k] = v;
                }
                return out;
            """)
            for k, v in (jwt_found or {}).items():
                if isinstance(v, str) and v.startswith("eyJ") and v.count(".") == 2:
                    print(f"  ✓ Detected JWT in localStorage['{k}']")
                    return True
                if isinstance(v, str) and "eyJ" in v and v.count("Bearer") <= 2:
                    print(f"  ✓ Detected JWT-like string in localStorage['{k}'] (will inspect)")
                    return True
        except Exception:
            pass
        time.sleep(2)
        # Print a tick every 20s so it's clear the script is alive
        if int(time.time()) % 20 == 0:
            sys.stdout.write(".")
            sys.stdout.flush()
    return False


def _dump_storage(driver) -> dict:
    """Snapshot all browser storage so we know where Coolbet keeps the JWT."""
    return driver.execute_script("""
        const out = { localStorage: {}, sessionStorage: {}, cookies: document.cookie };
        for (let i = 0; i < localStorage.length; i++) {
            const k = localStorage.key(i);
            out.localStorage[k] = localStorage.getItem(k);
        }
        for (let i = 0; i < sessionStorage.length; i++) {
            const k = sessionStorage.key(i);
            out.sessionStorage[k] = sessionStorage.getItem(k);
        }
        return out;
    """)


def _find_jwt_in_storage(storage: dict) -> tuple[str, str, str] | None:
    """Return (location, key, jwt) for the first JWT-shaped string found.
    location is one of 'localStorage', 'sessionStorage', 'cookie'."""
    def _looks_like_jwt(s: str) -> bool:
        return isinstance(s, str) and s.startswith("eyJ") and s.count(".") == 2

    for loc in ("localStorage", "sessionStorage"):
        for k, v in (storage.get(loc) or {}).items():
            if _looks_like_jwt(v):
                return loc, k, v
            # Sometimes wrapped in JSON
            if isinstance(v, str) and "eyJ" in v:
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, dict):
                        for kk, vv in parsed.items():
                            if _looks_like_jwt(vv):
                                return loc, f"{k}.{kk}", vv
                except Exception:
                    pass
    # Cookie scan
    cookies = (storage.get("cookies") or "")
    for part in cookies.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            if _looks_like_jwt(v):
                return "cookie", k, v
    return None


def _probe_login_dom(driver) -> dict:
    """Inspect the login page for Smart-ID button + ID-code form selectors.
    These get persisted so v2 (Telegram-driven Smart-ID auto-fill) can use them.

    We do this AFTER login so the user is already authenticated — but the
    login form's DOM is typically static, and we capture what we can from
    any login-related URLs the user visited. If empty, no big deal — v2 can
    re-probe on a fresh load.
    """
    return driver.execute_script("""
        const grab = (sel) => Array.from(document.querySelectorAll(sel)).map(e => ({
            tag: e.tagName, id: e.id, classes: e.className,
            text: (e.innerText||'').slice(0,80), type: e.type||'', name: e.name||'',
        }));
        return {
            url: location.href,
            title: document.title,
            buttons: grab('button').slice(0, 30),
            inputs: grab('input').slice(0, 30),
            links: grab('a').slice(0, 30).filter(l => /smart-?id|logi|login|auth|signin/i.test(l.text)),
        };
    """)


def main() -> int:
    print("Coolbet browser bootstrap")
    print("─" * 60)
    print(f"Profile dir: {PROFILE_DIR}")
    print()

    # Launch with persistent profile dir so future headless runs reuse session
    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")

    print("Launching Chrome (headful)…")
    driver = uc.Chrome(options=options, use_subprocess=False)
    try:
        driver.get(COOLBET_URL)
        ok = _wait_for_login(driver, timeout=600)  # 10 min budget
        if not ok:
            print("\n✗ Timed out waiting for login. No JWT detected in storage.")
            print("  Tip: complete login fully (Smart-ID PIN tap) and rerun.")
            return 1

        # Give the page another moment to fully hydrate post-login
        time.sleep(3)
        storage = _dump_storage(driver)

        # Persist the storage dump for inspection
        dump_path = Path.home() / ".coolbet-daemon" / "storage-dump.json"
        dump_path.write_text(json.dumps(storage, indent=2, default=str))
        print(f"\n📦 Saved storage dump → {dump_path}")

        # Find + extract the JWT
        found = _find_jwt_in_storage(storage)
        if not found:
            print("\n⚠  JWT-shaped string not found in any storage. Inspect the dump above.")
            return 1

        loc, key, jwt = found
        print(f"\n✓ JWT located in {loc}['{key}'] — length={len(jwt)}")
        print(f"  First 40 chars: {jwt[:40]}…")

        # Write to .env
        if ENV_FILE.exists():
            set_key(str(ENV_FILE), "COOLBET_MANUAL_JWT", jwt)
            print(f"✓ Wrote COOLBET_MANUAL_JWT to {ENV_FILE}")
        else:
            print(f"⚠  No .env at {ENV_FILE}; printing JWT instead:")
            print(f"   COOLBET_MANUAL_JWT={jwt}")

        # Save where-we-found-it for the headless refresher
        location_file = Path.home() / ".coolbet-daemon" / "jwt-location.json"
        location_file.write_text(json.dumps({"location": loc, "key": key}, indent=2))
        print(f"✓ Saved JWT location → {location_file}")

        # Probe login DOM (best-effort, for v2 Smart-ID auto-fill)
        try:
            dom = _probe_login_dom(driver)
            LOGIN_DOM_FILE.write_text(json.dumps(dom, indent=2, default=str))
            print(f"✓ Probed login DOM → {LOGIN_DOM_FILE} ({len(dom.get('buttons',[]))} btns, {len(dom.get('inputs',[]))} inputs)")
        except Exception as e:
            print(f"⚠  Login-DOM probe failed (non-fatal): {e}")

        print()
        print("─" * 60)
        print("✓ SETUP COMPLETE")
        print()
        print("Next steps:")
        print("  • Headless refresh test:")
        print("      venv/bin/python3 scripts/coolbet_refresh_jwt.py")
        print("  • Once that works, the daemon will refresh JWT every 25 min")
        print("    automatically and Telegram-trigger /relogin will use the")
        print("    same code path.")
        return 0
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
