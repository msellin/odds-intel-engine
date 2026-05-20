"""Headless JWT refresh — opens Chrome with the saved profile, extracts a
fresh JWT, writes to .env.

Prereq: scripts/coolbet_browser_setup.py must have been run once to:
  • Create the persistent profile at ~/.coolbet-daemon/chrome-profile/
  • Save the JWT location to ~/.coolbet-daemon/jwt-location.json

This script is invoked:
  • Manually:  venv/bin/python3 scripts/coolbet_refresh_jwt.py
  • By daemon: every 25 min (subprocess) + on Telegram /relogin command

Exit codes:
  0 — fresh JWT extracted and written to .env
  2 — session expired in Chrome profile (user must rerun setup script
      OR — once shipped — use v2 Telegram-triggered Smart-ID auto-fill)
  3 — any other error (Chrome failed to launch, etc.)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv, set_key

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
    """Patch undetected-chromedriver's distutils import for Python 3.12+ with a
    full LooseVersion-compatible shim (not just rename). See
    scripts/coolbet_browser_setup.py for explanation. Idempotent."""
    import importlib.util
    spec = importlib.util.find_spec("undetected_chromedriver")
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError("undetected_chromedriver not installed in this Python")
    pkg_dir = Path(list(spec.submodule_search_locations)[0])
    patcher_py = pkg_dir / "patcher.py"
    src = patcher_py.read_text()
    if _UC_LOOSEVERSION_SHIM in src:
        return
    if _UC_OLD_THIN_SHIM in src:
        patcher_py.write_text(src.replace(_UC_OLD_THIN_SHIM, _UC_LOOSEVERSION_SHIM, 1))
    elif _UC_ORIGINAL_IMPORT in src:
        patcher_py.write_text(src.replace(_UC_ORIGINAL_IMPORT, _UC_LOOSEVERSION_SHIM, 1))


_ensure_uc_distutils_shim()
import undetected_chromedriver as uc  # type: ignore

PROFILE_DIR = Path.home() / ".coolbet-daemon" / "chrome-profile"
LOCATION_FILE = Path.home() / ".coolbet-daemon" / "jwt-location.json"
ENV_FILE = ROOT / ".env"

COOLBET_URL = "https://www.coolbet.com/en/sports/football"


def _read_location() -> tuple[str, str]:
    if not LOCATION_FILE.exists():
        raise FileNotFoundError(
            f"{LOCATION_FILE} missing — run scripts/coolbet_browser_setup.py first"
        )
    data = json.loads(LOCATION_FILE.read_text())
    return data["location"], data["key"]


def _extract_jwt(driver, location: str, key: str) -> str | None:
    """Pull the JWT from the configured storage location."""
    if location == "localStorage":
        # Handle nested keys like "session.token"
        if "." in key:
            outer, inner = key.split(".", 1)
            raw = driver.execute_script(f"return localStorage.getItem({json.dumps(outer)});")
            if not raw:
                return None
            try:
                return json.loads(raw).get(inner)
            except Exception:
                return None
        return driver.execute_script(f"return localStorage.getItem({json.dumps(key)});")
    if location == "sessionStorage":
        if "." in key:
            outer, inner = key.split(".", 1)
            raw = driver.execute_script(f"return sessionStorage.getItem({json.dumps(outer)});")
            if not raw:
                return None
            try:
                return json.loads(raw).get(inner)
            except Exception:
                return None
        return driver.execute_script(f"return sessionStorage.getItem({json.dumps(key)});")
    if location == "cookie":
        cookies = driver.get_cookies()
        for c in cookies:
            if c.get("name") == key:
                return c.get("value")
    return None


def _is_logged_in(jwt: str | None) -> bool:
    return isinstance(jwt, str) and jwt.startswith("eyJ") and jwt.count(".") == 2


def main() -> int:
    try:
        location, key = _read_location()
    except Exception as e:
        print(f"✗ {e}", file=sys.stderr)
        return 3

    if not PROFILE_DIR.exists():
        print(f"✗ Profile dir missing: {PROFILE_DIR} — run setup script first", file=sys.stderr)
        return 3

    options = uc.ChromeOptions()
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--headless=new")  # headless mode for unattended refresh

    try:
        driver = uc.Chrome(options=options, use_subprocess=False)
    except Exception as e:
        print(f"✗ Chrome launch failed: {e}", file=sys.stderr)
        return 3

    try:
        driver.get(COOLBET_URL)
        # Let JS hydrate + refresh JWT
        time.sleep(5)
        jwt = _extract_jwt(driver, location, key)
        if not _is_logged_in(jwt):
            # Try again after a longer wait — JWT may refresh lazily
            time.sleep(5)
            jwt = _extract_jwt(driver, location, key)
        if not _is_logged_in(jwt):
            print("✗ Session expired in Chrome profile — JWT not found. "
                  "Rerun scripts/coolbet_browser_setup.py to re-authenticate.",
                  file=sys.stderr)
            return 2

        # Write to .env
        set_key(str(ENV_FILE), "COOLBET_MANUAL_JWT", jwt)
        print(f"OK refreshed JWT (len={len(jwt)})")
        return 0
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
