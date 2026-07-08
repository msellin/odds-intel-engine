"""
Coolbet browser sync (COOLBET-BROWSER-SYNC, 2026-06-12).

Drives a real persistent Chromium via Playwright to fetch the operator's
Coolbet bet history. This is the ONLY path that survives Coolbet's full
Imperva stack (TLS fingerprint + JS challenge + dynamic server cookies) —
FS-based plain-requests hits HTTP 500 with an _Incapsula_Resource JS
challenge at the /s/sbgate/bets/history endpoint specifically. A real
Chromium executes the JS challenge naturally.

ONE-TIME SETUP:
    python3 -m workers.automation.coolbet_browser_sync --login
    # → Opens a Chromium window, you log into Coolbet (SMS verify if asked)
    # → Press ENTER in terminal when logged in. Profile is persisted to
    #   ~/.config/oddsintel/coolbet-playwright-profile/ — survives forever.

RUNTIME (called from Mac daemon):
    bets = fetch_pending_bets()
    # → Returns list of {match_name, market, selection, stake, odds,
    #   placed_at, ticket_id} for everything in PENDING status.

Headless after first login: launch_persistent_context(headless=True). The
session cookies persist; no SMS needed again unless Coolbet invalidates
the trust marker (rare — usually months).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load .env eagerly so cdp_auto_login (and any helper that reads
# COOLBET_USER / COOLBET_PASS / FLARESOLVERR_URL) gets the right values
# regardless of how this module is invoked (`python -m ...` from the
# repo root, launchd plist, ad-hoc shell, etc.).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)

PROFILE_DIR = Path.home() / ".config" / "oddsintel" / "coolbet-playwright-profile"
HISTORY_PAGE = "https://www.coolbet.com/et/panuste-ajalugu/sport"
LOGIN_PAGE   = "https://www.coolbet.com/et/login"
API_HISTORY  = "https://www.coolbet.com/s/sbgate/bets/history"

# CDP attach mode: connect to the operator's REAL Chrome (launched with
# --remote-debugging-port=9222) instead of spawning our own. This is the
# only reliable path past Imperva: their Chrome is a long-warmed
# residential-IP browser that Imperva already trusts. Our spawned
# Chromium starts cold and gets challenged → API calls 403 → SPA never
# initialises. Confirmed via direct test 2026-06-12.
CDP_URL = os.getenv("COOLBET_CHROME_CDP_URL", "http://localhost:9222")


def _ensure_profile_dir() -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


def _sync_playwright_factory():
    """Try patchright first (Playwright fork with deeper anti-detection —
    patches navigator.webdriver at the binary level, removes --enable-
    automation entirely, fixes CDP leaks). Falls back to vanilla
    playwright if patchright not installed.

    Playwright-stealth (added via _apply_stealth) is layered on top to
    catch anything patchright doesn't already neutralise."""
    try:
        from patchright.sync_api import sync_playwright
        log.info("Using patchright (anti-detection fork)")
        return sync_playwright
    except ImportError:
        sync_playwright = _sync_playwright_factory()
        log.info("patchright unavailable — using vanilla playwright")
        return sync_playwright


def _launch_context(p, *, headless: bool):
    """Launch Chrome with anti-automation tweaks. Tries the system's real
    Chrome first (channel="chrome") — its fingerprint differs from the
    Playwright-bundled Chromium in ways Imperva probes for. Falls back
    to bundled Chromium with the standard AutomationControlled flag."""
    profile_dir = _ensure_profile_dir()
    common = dict(
        user_data_dir=str(profile_dir),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        # Strip Playwright's default --enable-automation so navigator.webdriver
        # stays false. patchright does this too but belt-and-suspenders.
        ignore_default_args=["--enable-automation"],
    )
    try:
        return p.chromium.launch_persistent_context(channel="chrome", **common)
    except Exception as e:
        log.info("System Chrome unavailable (%s) — falling back to bundled Chromium.", e)
        return p.chromium.launch_persistent_context(**common)


def _apply_stealth(ctx) -> None:
    """No-op. Was using playwright-stealth, but its non-configurable
    property patches (e.g. offsetHeight) conflicted with Coolbet's own
    JS — `utils.replaceProperty` would throw `Cannot redefine property`
    and the page would crash before the Imperva checkbox rendered.
    Patchright handles automation hiding at the binary level without
    breaking page JS, so no extra layer needed.

    Kept as a function so future stealth toggles have a clean hook."""
    return


def interactive_login() -> int:
    """One-time setup: open Chromium with the persistent profile so the
    operator can log into Coolbet. Profile cookies persist for next runs.

    Why not automate the login: Coolbet's SMS-2FA + occasional CAPTCHA
    is intentionally manual. Doing it once via real Chromium also seeds
    the localStorage with cbauth where Coolbet's frontend JS expects it
    — that's what makes subsequent headless XHRs work."""
    sync_playwright = _sync_playwright_factory()

    profile_dir = _ensure_profile_dir()
    print(f"  Profile dir: {profile_dir}")
    print(f"  Opening Coolbet login page. Sign in (incl. SMS if asked).")
    print(f"  When you're logged in and you see your account/balance, press ENTER here.")

    with sync_playwright() as p:
        ctx = _launch_context(p, headless=False)
        _apply_stealth(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_PAGE)
        try:
            input("  >>> Press ENTER when logged in <<<\n")
        except (EOFError, KeyboardInterrupt):
            pass
        # Snapshot what we learned for diagnostics.
        try:
            page.goto(HISTORY_PAGE, wait_until="domcontentloaded")
            time.sleep(2)
            cookies = ctx.cookies()
            print(f"  Profile now has {len(cookies)} cookies.")
            cb_cookies = [c["name"] for c in cookies if "incap" in c["name"] or c["name"] in ("reese84","cbauth","data")]
            print(f"  Imperva/session markers: {cb_cookies}")
        except Exception as e:
            print(f"  Warm-on-history-page failed: {e}")
        ctx.close()
    print("  ✓ Login flow done — profile saved.")
    return 0


def cdp_auto_login(*, max_wait_s: int = 300) -> int:
    """Fill the Coolbet login form in the CDP-Chrome window + click
    the login button automatically. If SMS is required, the operator
    completes that step in the browser; we poll until the URL leaves
    /login (success) or max_wait_s elapses.

    Idempotent: if already logged in (no login form found), we report
    success and return early."""
    sync_playwright = _sync_playwright_factory()
    email = os.getenv("COOLBET_USER") or os.getenv("COOLBET_EMAIL") or ""
    password = os.getenv("COOLBET_PASS") or os.getenv("COOLBET_PASSWORD") or ""
    if not email or not password:
        print("✗ COOLBET_USER / COOLBET_PASS not set in .env")
        return 2

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL, timeout=10000)
        except Exception as e:
            print(f"✗ Can't connect to CDP at {CDP_URL}: {e}")
            print("  Run: ./local/launch_chrome_for_sync.sh")
            return 3

        ctx = browser.contexts[0]
        # Reuse an existing tab if it's already on Coolbet, else open new.
        page = None
        for pg in ctx.pages:
            if "coolbet.com" in pg.url:
                page = pg
                print(f"  reusing existing Coolbet tab: {pg.url}")
                break
        if page is None:
            page = ctx.new_page()
        page.goto(LOGIN_PAGE, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)

        # If we're already logged in (no login form), report and exit.
        # Heuristic: the login page shows "Logi sisse" as a submit button;
        # if we don't find it after a brief wait, assume logged in.
        try:
            page.wait_for_selector("input[type='email'], input[name='email']", timeout=5000)
        except Exception:
            print("✓ Already logged in (no email field on page)")
            return 0

        print("  Filling login form…")
        try:
            # Coolbet's React form uses controlled inputs — type into them
            # rather than set value directly so the framework picks it up.
            page.fill("input[type='email'], input[name='email']", email)
            page.fill("input[type='password'], input[name='password']", password)
        except Exception as e:
            print(f"✗ Couldn't fill form: {e}")
            return 4

        # Click the green submit button inside the form. The header has a
        # "Logi sisse" link that we must NOT match — it opens the dialog
        # rather than submitting. Strategy: find the password field, then
        # the nearest submit button (always inside the same form).
        print("  Clicking Logi sisse…")
        try:
            # First, try the form-scoped submit button.
            submit = page.locator(
                "form button[type='submit'], "
                "button[type='submit']:has-text('LOGI SISSE'), "
                "button[type='submit']:has-text('Logi sisse')"
            ).first
            try:
                submit.click(timeout=8000)
            except Exception:
                # Fallback: type Enter inside the password field — Coolbet
                # supports that form submission shortcut.
                print("    submit click missed → trying Enter on password field")
                page.locator("input[type='password'], input[name='password']").first.press("Enter")
        except Exception as e:
            print(f"✗ Couldn't submit form: {e}")
            return 5

        print(f"  Submitted. Watching for login success (up to {max_wait_s}s).")
        print("  If SMS appears in the browser, enter the code there.")

        deadline = time.time() + max_wait_s
        while time.time() < deadline:
            try:
                url = page.url
                # Success signal: page navigates away from /login or balance appears.
                if "/login" not in url:
                    print(f"  ✓ logged in — page is now at {url}")
                    return 0
                # Some flows keep /login URL but render account info — check
                # for a logged-in marker (balance badge / account menu).
                has_balance = page.evaluate(
                    "() => !!document.querySelector('[data-test=\"balance\"], [data-testid=\"balance\"], [class*=\"Balance\"]')"
                )
                if has_balance:
                    print("  ✓ logged in — balance widget detected")
                    return 0
            except Exception:
                pass
            time.sleep(2)

        print(f"✗ Login didn't complete within {max_wait_s}s")
        return 6


# ── JWT-from-CDP (the auth seam) ─────────────────────────────────────────────
# CDP-JWT-EXTRACT (2026-06-12): the operator's CDP-Chrome holds a Coolbet
# session that Coolbet's frontend auto-renews every ~20 min via /s/auth/
# renew-token. The renewed JWT lands in localStorage['cbauth']. By reading
# that slot we get a continuously-fresh Bearer with zero operator touch and
# zero SMS — replacing the brittle "paste JWT from DevTools" workflow.

# localStorage keys to probe, in order. Coolbet's SPA stored auth under
# `cbauth` as of 2026-06-12; the others are defensive fallbacks in case
# they rename. We also scan ALL keys for JWT-shaped values as last resort.
_JWT_LOCALSTORAGE_KEYS = ("cbauth", "auth-token", "access_token", "accessToken", "jwt")


def _looks_like_jwt(s: str) -> bool:
    """Cheap shape check — 3 base64url segments separated by dots."""
    if not s or not isinstance(s, str):
        return False
    if s.startswith("Bearer "):
        s = s[7:]
    parts = s.split(".")
    return len(parts) == 3 and all(len(p) > 4 for p in parts)


def _jwt_still_valid(token: str, *, min_ttl_s: int = 60) -> bool:
    """Decode the payload (no signature check — we trust the source) and
    confirm `exp` is at least min_ttl_s in the future. Returns False on
    any parse error so a malformed token isn't accidentally adopted."""
    try:
        import base64 as _b64, json as _json, time as _t
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))
        exp = float(payload.get("exp", 0))
        return exp > _t.time() + min_ttl_s
    except Exception:
        return False


def extract_jwt_from_cdp(*, allow_open_new_tab: bool = False,
                           timeout_ms: int = 15000) -> str | None:
    """Connect to CDP-Chrome and pull the live Coolbet JWT out of
    localStorage. Returns the bare JWT string (no "Bearer " prefix) or
    None if no valid token was found.

    Why this exists: Coolbet's frontend auto-renews the JWT every ~20 min
    while a coolbet.com tab is open. As long as the operator's CDP-Chrome
    has even one Coolbet tab loaded, localStorage['cbauth'] is always
    fresh. Reading it here replaces:
      - paste-from-DevTools (COOLBET_MANUAL_JWT) → manual + stale fast
      - /s/auth/login + SMS                       → triggers SMS storms

    allow_open_new_tab=False (default): only reuses an existing coolbet.com
    tab. Returns None if none open. Safe for unattended use — never
    flashes the CDP-Chrome window to foreground.

    allow_open_new_tab=True: if no Coolbet tab exists, opens one to
    www.coolbet.com and reads its localStorage. Triggers a brief
    background page load; the operator may notice a new tab. Use for the
    explicit --refresh-jwt CLI.

    Implementation note: when the operator's Chrome has many active
    targets (iframes navigating, ads loading), `connect_over_cdp` can
    fail mid-handshake with `Frame was detached`. We retry the whole
    connect+read sequence a few times with short backoff — by the time
    of the retry, the racing frame is usually settled."""
    import time as _t

    last_err: Exception | None = None
    for attempt in range(3):
        snapshot = _try_read_localStorage_via_cdp(
            allow_open_new_tab=allow_open_new_tab,
            timeout_ms=timeout_ms,
        )
        if isinstance(snapshot, Exception):
            last_err = snapshot
            log.info("CDP localStorage read attempt %d failed: %s",
                      attempt + 1, snapshot)
            _t.sleep(1.5 + attempt)  # 1.5s, 2.5s, 3.5s
            continue
        if snapshot is None:
            return None  # explicit "nothing here" signal — don't retry

        probed = snapshot.get("probed") or {}
        # Pass 1: probe known keys.
        for k in _JWT_LOCALSTORAGE_KEYS:
            v = probed.get(k)
            if not v:
                continue
            token = v[7:] if isinstance(v, str) and v.startswith("Bearer ") else v
            if _looks_like_jwt(token) and _jwt_still_valid(token):
                log.info("JWT extracted from CDP localStorage['%s'] (ttl>=60s).", k)
                return token

        # Pass 2: any JWT-shaped value in the whole storage.
        for v in (snapshot.get("all_values") or []):
            if not v:
                continue
            token = v[7:] if isinstance(v, str) and v.startswith("Bearer ") else v
            if _looks_like_jwt(token) and _jwt_still_valid(token):
                log.info("JWT extracted from CDP localStorage (scan, key unknown).")
                return token

        log.info("No valid JWT found in CDP-Chrome localStorage — keys present: %s",
                  snapshot.get("all_keys") or [])
        return None

    log.warning("extract_jwt_from_cdp failed after retries: %s", last_err)
    return None


# COOLBET-CDP-COOKIE-EXPORT (2026-07-08): the 6 cookie names Imperva actually
# fingerprints on Coolbet. reese84 + visid_incap_* are the two must-haves; the
# rest correlate with reese84 and Imperva may notice if they're missing. Names
# stay in sync with `_imperva_cookies_individual` in coolbet_session.py.
_IMPERVA_COOKIE_NAMES = (
    "reese84",
    "visid_incap_723517",
    "nlbi_723517",
    "nlbi_723517_2147483392",
    "incap_ses_1099_723517",
    "uuid",
)


def extract_imperva_cookies_from_cdp(*, timeout_ms: int = 15000) -> dict[str, str] | None:
    """Read Imperva cookies from CDP-Chrome via CDP `Network.getAllCookies`.
    Returns {cookie_name: value} for the 6 Imperva-critical names present
    on www.coolbet.com, or None if CDP is unavailable / no Coolbet tab.

    Why this exists: FS-Docker Chrome fails Imperva challenges (different
    fingerprint from the operator's real desktop Chrome). CDP-Chrome IS
    the operator's real Chrome — Imperva trusts it. Harvesting the cookies
    here lets other Coolbet-HTTP jobs (odds-snapshot, cs2-coolbet-scanner)
    run in COOLBET_NO_FS=true mode with plain-requests + fresh cookies,
    bypassing FS entirely.

    Uses Network.getAllCookies (not `document.cookie`) so HttpOnly cookies
    are included — reese84 is often HttpOnly.

    Returns None (not raise) on any CDP failure; caller decides what to do
    (keep using stale DB cookies, fall back to env, alert, etc.).
    """
    import time as _t

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            result = _try_get_all_cookies_via_cdp(timeout_ms=timeout_ms)
        except Exception as e:
            last_err = e
            log.info("CDP getAllCookies attempt %d failed: %s",
                     attempt + 1, e)
            _t.sleep(1.5 + attempt)
            continue
        if result is None:
            return None  # explicit "no coolbet tab" — don't retry
        if isinstance(result, Exception):
            last_err = result
            _t.sleep(1.5 + attempt)
            continue

        harvested = {
            c["name"]: c["value"]
            for c in result
            if c.get("name") in _IMPERVA_COOKIE_NAMES
            and c.get("value")
            and "coolbet.com" in (c.get("domain") or "")
        }
        # reese84 + at least one visid_incap_* is the minimum Imperva accepts.
        # If both are missing, the cookies aren't useful — return None so the
        # caller falls back cleanly rather than persisting a bad snapshot.
        if not harvested.get("reese84"):
            log.info("CDP cookies harvested but reese84 missing "
                     "(keys=%s) — treating as empty.",
                     sorted(harvested.keys()))
            return None
        log.info("Harvested %d Imperva cookies from CDP-Chrome (keys=%s).",
                 len(harvested), sorted(harvested.keys()))
        return harvested

    log.warning("extract_imperva_cookies_from_cdp failed after retries: %s", last_err)
    return None


def _try_get_all_cookies_via_cdp(*, timeout_ms: int) -> list | None:
    """One attempt at CDP Network.getAllCookies. Returns list of cookie
    dicts on success, None if no Coolbet tab. Exceptions propagate; the
    public wrapper catches + retries."""
    import asyncio
    return asyncio.run(
        _async_get_all_cookies(timeout_s=timeout_ms / 1000.0)
    )


async def _async_get_all_cookies(*, timeout_s: float) -> list | None:
    """Async CDP handshake: find Coolbet tab, open WS, send
    Network.getAllCookies, return cookie list. Mirrors
    _async_read_localStorage's connection pattern for consistency."""
    import asyncio
    import websockets

    try:
        targets = await asyncio.wait_for(
            _http_get_json(f"{CDP_URL}/json/list"),
            timeout=timeout_s,
        )
    except Exception as e:
        log.warning("CDP /json/list failed: %s", e)
        raise

    coolbet_target = None
    for t in targets or []:
        if t.get("type") != "page":
            continue
        if "coolbet.com" in (t.get("url") or ""):
            coolbet_target = t
            break

    if coolbet_target is None:
        # No coolbet tab — we DON'T open one here (harvest is a passive
        # read; opening a fresh tab would trigger a page load without an
        # authenticated context, which defeats the whole point).
        log.info("No coolbet.com tab in CDP-Chrome — skipping cookie harvest.")
        return None

    ws_url = coolbet_target.get("webSocketDebuggerUrl")
    if not ws_url:
        log.warning("Coolbet target has no webSocketDebuggerUrl: %s", coolbet_target)
        return None

    async with websockets.connect(ws_url,
                                     open_timeout=timeout_s,
                                     max_size=10_000_000) as ws:
        # Network domain doesn't strictly need enabling for getAllCookies
        # on modern Chrome, but doing so is harmless + defensive.
        await ws.send(_json_dumps({
            "id": 1,
            "method": "Network.enable",
            "params": {},
        }))
        await ws.send(_json_dumps({
            "id": 2,
            "method": "Network.getAllCookies",
            "params": {},
        }))
        deadline = asyncio.get_event_loop().time() + timeout_s
        seen_ids: set[int] = set()
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError("CDP Network.getAllCookies response timed out")
            msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            resp = _json_loads(msg)
            rid = resp.get("id")
            if rid not in (1, 2):
                continue  # ignore Network events / other traffic
            seen_ids.add(rid)
            if rid == 2:
                if "error" in resp:
                    raise RuntimeError(f"CDP error: {resp['error']}")
                cookies = ((resp.get("result") or {}).get("cookies")) or []
                return cookies


def _chrome_at_profile_picker(*, timeout_s: float = 3.0) -> bool:
    """Return True if Chrome's only page-type targets are chrome://profile-picker/.
    When this is the case, CDP `Target.createTarget` (used by /json/new to
    open a coolbet.com tab) silently fails because no profile is loaded —
    so `open_coolbet_tab` returns ok=False and the daemon spins forever on
    state=no_coolbet_tab. The operator must click a profile in the running
    Chrome window; daemon can't drive chrome:// UI via CDP."""
    import asyncio

    async def _check() -> bool:
        try:
            targets = await asyncio.wait_for(
                _http_get_json(f"{CDP_URL}/json/list"), timeout=timeout_s
            )
        except Exception:
            return False
        pages = [t for t in (targets or []) if t.get("type") == "page"]
        if not pages:
            return False
        return all(
            (t.get("url") or "").startswith("chrome://profile-picker/")
            for t in pages
        )

    try:
        return asyncio.run(_check())
    except Exception:
        return False


def diagnose_cdp_jwt_state(*, timeout_ms: int = 8000) -> dict:
    """Classify the CDP-Chrome JWT state so daemon alerts can tell the
    operator exactly what to fix. Returns a dict:

        {"state": <one of chrome_down/chrome_at_profile_picker/no_coolbet_tab/
                          logged_out/jwt_expired/valid/unknown>,
         "detail": <short human-readable string>,
         "ttl_s":  <int|None — remaining seconds when state=valid>}

    Distinct from `extract_jwt_from_cdp()` (which returns the JWT string
    or None) because alerts need the *why*, not the JWT itself. Read-only,
    never opens a new tab — diagnosis must not flash the Chrome window.

    States in priority order (first match wins):
      chrome_down     — CDP unreachable (Chrome not running with --remote-debugging-port,
                        OR FlareSolverr-side mid-restart, OR no network to localhost).
                        Recovery: `./local/launch_chrome_for_sync.sh`.
      chrome_at_profile_picker — CDP reachable, only tab is chrome://profile-picker/.
                        Chrome restarted into multi-profile picker; `/json/new` can't
                        open a real tab until a profile is loaded. Daemon can't click
                        chrome:// UI via CDP — operator must select the profile in the
                        running Chrome window.
      no_coolbet_tab  — Chrome reachable but no coolbet.com page target.
                        Recovery: open a Coolbet tab in CDP-Chrome.
      logged_out      — Coolbet tab found but `cbauth` key absent from localStorage
                        AND no JWT-shaped value anywhere. This is what today's outage
                        looks like — browser running, tab open, but user signed out.
                        Recovery: log into coolbet.com in CDP-Chrome.
      jwt_expired     — `cbauth` present but exp <= now. Coolbet's renew-token loop
                        usually keeps this fresh; expiry means the renew loop also
                        stopped (e.g. tab backgrounded for hours OR session forced out).
                        Recovery: refresh the coolbet.com tab or re-login.
      valid           — JWT present with TTL >= 60s. ttl_s gives the remaining window.
    """
    snapshot = _try_read_localStorage_via_cdp(
        allow_open_new_tab=False,
        timeout_ms=timeout_ms,
    )
    if isinstance(snapshot, Exception):
        return {"state": "chrome_down",
                "detail": f"CDP unreachable at {CDP_URL}: {snapshot}",
                "ttl_s": None}
    if snapshot is None:
        if _chrome_at_profile_picker():
            return {"state": "chrome_at_profile_picker",
                    "detail": (f"CDP-Chrome is at chrome://profile-picker/ "
                               f"({CDP_URL}) — no profile loaded, /json/new "
                               "cannot open a real tab."),
                    "ttl_s": None}
        return {"state": "no_coolbet_tab",
                "detail": f"No coolbet.com tab open in CDP-Chrome ({CDP_URL}).",
                "ttl_s": None}

    probed = snapshot.get("probed") or {}
    all_keys = snapshot.get("all_keys") or []
    all_values = snapshot.get("all_values") or []

    # B2 (2026-06-16): two-pass classification so `jwt_expired` only
    # fires when a JWT-shape value lives in a KNOWN AUTH KEY (cbauth +
    # legacy fallbacks). Any other JWT-shape value (Coolbet's analytics
    # cookies, third-party scripts) is noise — treating its expiry as
    # "session lapsed" would route B4 self-heal toward a Page.reload
    # that can't actually recover anything. Logged-out + an unrelated
    # JWT-shape value is still logged-out.

    def _decode_ttl(raw: str) -> int | None:
        token = raw[7:] if isinstance(raw, str) and raw.startswith("Bearer ") else raw
        if not _looks_like_jwt(token):
            return None
        if _jwt_still_valid(token):
            try:
                import base64 as _b64, json as _json, time as _t
                payload_b64 = token.split(".")[1]
                payload_b64 += "=" * (4 - len(payload_b64) % 4)
                payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))
                exp = float(payload.get("exp", 0))
                return int(exp - _t.time())
            except Exception:
                return None
        return 0  # JWT-shaped but expired

    # Pass 1: KNOWN AUTH KEYS only. This is the only place that can
    # legitimately produce `valid` or `jwt_expired` — those states are
    # claims about Coolbet's auth state specifically.
    for k in _JWT_LOCALSTORAGE_KEYS:
        v = probed.get(k)
        if not v:
            continue
        ttl = _decode_ttl(v)
        if ttl is None:
            continue  # value exists in auth key but isn't JWT-shaped — ignore
        if ttl > 0:
            return {"state": "valid",
                    "detail": f"Fresh JWT in localStorage['{k}'] (ttl ~{ttl}s).",
                    "ttl_s": ttl}
        # ttl == 0 means JWT-shape present in auth key but expired.
        return {"state": "jwt_expired",
                "detail": (f"JWT in localStorage['{k}'] is expired. "
                            "Refresh the coolbet.com tab or log in again."),
                "ttl_s": 0}

    # Pass 2: defensive full-scan for a renamed auth slot. Only treat
    # `valid` as actionable here — finding an EXPIRED JWT-shape in a
    # non-auth key tells us nothing about the session, so we fall through
    # to logged_out rather than misclassifying as jwt_expired.
    for v in all_values:
        if not v:
            continue
        ttl = _decode_ttl(v)
        if ttl and ttl > 0:
            return {"state": "valid",
                    "detail": f"Fresh JWT in localStorage (scan, key unknown — ttl ~{ttl}s).",
                    "ttl_s": ttl}

    # Logged-out: tab is open, localStorage exists, but no cbauth and no JWT-shaped
    # value anywhere. This is the failure mode that hit 2026-06-15/16: 33 keys
    # present, none of them auth.
    return {"state": "logged_out",
            "detail": ("Coolbet tab open but no JWT in localStorage "
                       f"({len(all_keys)} keys present, cbauth missing). "
                       "Log into coolbet.com in CDP-Chrome."),
            "ttl_s": None}


def proactive_jwt_refresh(*, min_ttl_s: int = 300) -> dict:
    """Pull a fresh JWT from CDP-Chrome and persist it to DB when the
    currently-persisted token's TTL is below `min_ttl_s`. No-op when the
    DB JWT is comfortably fresh.

    Designed for the Mac daemon to call at the start of every tick BEFORE
    the placer touches Coolbet — so by the time `place_all_bets` opens a
    session, the DB JWT is fresh and `CoolbetSession.__init__` adopts it
    cleanly. Eliminates the race where a tick starts with a JWT that
    expires mid-request.

    This complements (does not replace) the reactive path in
    `CoolbetSession._login` → `_try_cdp_jwt`. That path runs at the moment
    of expiry; this one runs preemptively so expiry never lands inside a
    placement attempt.

    Returns a status dict:
        {"refreshed": bool,
         "ttl_before_s": int | None,
         "ttl_after_s":  int | None,
         "reason": one of fresh / refreshed / cdp_unavailable /
                          cdp_returned_expired / persist_failed / no_db_jwt}

    Cost: 1 short DB SELECT every call; CDP probe + UPDATE only when stale."""
    import time as _t
    from workers.automation.coolbet_state import (
        read_persisted_jwt, persist_jwt, mark_login_success,
    )

    db_jwt, db_session_id = read_persisted_jwt()
    ttl_before: int | None = None
    if db_jwt:
        # Cheap local decode — no network call.
        exp = _jwt_exp_seconds(db_jwt)
        if exp is not None:
            ttl_before = int(exp - _t.time())
            if ttl_before > min_ttl_s:
                return {"refreshed": False, "ttl_before_s": ttl_before,
                        "ttl_after_s": ttl_before, "reason": "fresh"}

    # Stale, missing exp, or DB has no JWT — try the CDP source of truth.
    try:
        fresh = extract_jwt_from_cdp(allow_open_new_tab=False)
    except Exception as e:
        log.warning("proactive_jwt_refresh: CDP probe raised: %s", e)
        return {"refreshed": False, "ttl_before_s": ttl_before,
                "ttl_after_s": ttl_before, "reason": "cdp_unavailable"}
    if not fresh:
        # No Coolbet tab, Chrome down, or logged out. The catch-net + the
        # daemon's consecutive-error alert will handle escalation. Don't
        # mark_error here — that's the reactive path's job.
        return {"refreshed": False, "ttl_before_s": ttl_before,
                "ttl_after_s": ttl_before, "reason": "cdp_unavailable"}

    exp_after = _jwt_exp_seconds(fresh)
    if exp_after is None or exp_after <= _t.time():
        return {"refreshed": False, "ttl_before_s": ttl_before,
                "ttl_after_s": None, "reason": "cdp_returned_expired"}
    ttl_after = int(exp_after - _t.time())

    # Persist to DB. Mirror `CoolbetSession._adopt_manual_jwt`'s pattern:
    # persist_jwt (canonical token store) + mark_login_success (sets
    # jwt_exp_at + clears last_error). Without mark_login_success, the
    # `/status` row would still show a stale jwt_exp_at after refresh.
    try:
        import base64 as _b64, json as _json
        from datetime import datetime as _dt, timezone as _tz
        payload_b64 = fresh.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))
        user_id = payload.get("user_id") or payload.get("sub")
        login_session_id = payload.get("login_session_id") or db_session_id
        exp_dt = _dt.fromtimestamp(float(payload["exp"]), tz=_tz.utc)
        persist_jwt(fresh, login_session_id=login_session_id,
                    set_by="proactive_refresh")
        mark_login_success(method="proactive_cdp", user_id=user_id,
                           jwt_exp_at=exp_dt)
    except Exception as e:
        log.warning("proactive_jwt_refresh: persist failed: %s", e)
        return {"refreshed": False, "ttl_before_s": ttl_before,
                "ttl_after_s": ttl_after, "reason": "persist_failed"}

    log.info("proactive_jwt_refresh: TTL %ss → %ds (refreshed via CDP)",
             ttl_before, ttl_after)
    return {"refreshed": True, "ttl_before_s": ttl_before,
            "ttl_after_s": ttl_after, "reason": "refreshed"}


def auto_start_docker(*, timeout_s: int = 60) -> dict:
    """Detect when Docker daemon is unreachable and `open -a Docker` to start
    Docker Desktop. Polls until `docker ps` exits 0 OR timeout. Then waits
    for the FlareSolverr container to be healthy (containers with
    restart=always come up automatically once Docker is ready).

    Returns {"ok": bool, "elapsed_s": float, "message": str}. Idempotent —
    re-running when Docker is already up is a fast no-op.

    Why this exists: today's incident chain showed Docker can be down
    independently of any Coolbet/CDP state. Without auto-start, the
    operator has to manually `open -a Docker` and wait — defeats the
    self-healing premise. macOS Docker Desktop is a GUI app; `open -a`
    is the right way to launch it (vs `docker daemon` which doesn't work
    on macOS)."""
    import subprocess as _sp
    import time as _t

    started = _t.time()

    def _docker_up() -> bool:
        try:
            r = _sp.run(["docker", "ps"], capture_output=True,
                        text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    if _docker_up():
        return {"ok": True, "elapsed_s": 0.0,
                "message": "Docker already running"}

    try:
        _sp.run(["open", "-a", "Docker"], capture_output=True,
                text=True, timeout=10)
    except Exception as e:
        return {"ok": False, "elapsed_s": _t.time() - started,
                "message": f"`open -a Docker` failed: {e}"}

    # Poll until docker daemon responds — typically 20-40s cold-start.
    while _t.time() - started < timeout_s:
        if _docker_up():
            elapsed = _t.time() - started
            # Wait briefly for FlareSolverr container (restart:always) to
            # come up alongside Docker. We don't BLOCK on FS health here —
            # the daemon's next FS call will surface the issue if it's
            # not ready, and proactive heal will run again.
            return {"ok": True, "elapsed_s": elapsed,
                    "message": f"Docker ready after {elapsed:.1f}s"}
        _t.sleep(3)

    return {"ok": False, "elapsed_s": _t.time() - started,
            "message": f"Docker didn't respond within {timeout_s}s"}


def _flaresolverr_reachable() -> bool:
    """Cheap reachability check on the LOCAL FlareSolverr endpoint.
    Always checks localhost:8191 — not the configured URL — because the
    gate is "should we run `open -a Docker` to recover the local FS
    container?" A configured remote URL being unreachable can't be
    fixed by starting local Docker, so checking it would give a false
    negative for the auto-start gate."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8191/", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def auto_launch_cdp_chrome(*, timeout_s: int = 45) -> dict:
    """Subprocess `local/launch_chrome_for_sync.sh` to bring CDP-Chrome up.
    The script handles the one-time profile copy, kills any prior CDP
    Chrome instance, and polls the CDP port until ready. Returns:

        {"ok": bool, "elapsed_s": float, "stdout": str, "message": str}

    Idempotent — re-launching when Chrome is already up is safe (script
    pkills the old instance first). Cost: ~3-5s on warm cache, ~30s on
    first-ever profile copy. The daemon SHOULD NOT call this on the hot
    path — only from the self-heal escalation path."""
    import subprocess as _sp
    import time as _t
    from pathlib import Path as _Path

    script = _Path(__file__).resolve().parents[2] / "local" / "launch_chrome_for_sync.sh"
    if not script.exists():
        return {"ok": False, "elapsed_s": 0.0, "stdout": "",
                "message": f"launch script not found at {script}"}

    started = _t.time()
    try:
        proc = _sp.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=timeout_s,
        )
    except _sp.TimeoutExpired:
        return {"ok": False, "elapsed_s": _t.time() - started,
                "stdout": "",
                "message": f"launch script timed out after {timeout_s}s"}
    except Exception as e:
        return {"ok": False, "elapsed_s": _t.time() - started,
                "stdout": "",
                "message": f"launch script raised: {e}"}

    return {
        "ok": proc.returncode == 0,
        "elapsed_s": _t.time() - started,
        "stdout": (proc.stdout or "")[-400:],
        "message": ("CDP-Chrome launched" if proc.returncode == 0
                    else f"launch script exit {proc.returncode}: "
                         f"{(proc.stderr or '')[-200:]}"),
    }


def cdp_reload_coolbet_tab(*, timeout_s: int = 10,
                             post_reload_wait_s: float = 3.0) -> dict:
    """Find an existing coolbet.com tab in CDP-Chrome and send Page.reload.
    Coolbet's frontend then fires `/s/auth/renew-token` using the session
    cookie — that endpoint does NOT require SMS — and writes a fresh JWT
    to `localStorage['cbauth']`.

    Returns {"ok": bool, "message": str}. Only acts when the diagnose
    state is `jwt_expired` (precondition checked by caller). For
    `logged_out`, the renew-token call would itself 401 and there'd be no
    recovery — login is the only path and it goes through SMS, which is
    the operator's choice.

    Why this exists: the cheap "JWT lapsed because Coolbet's auto-renew
    didn't fire while tab was backgrounded" case is far more common than
    "session genuinely expired server-side". A Page.reload is the gentlest
    recovery action available — no SMS, no login UI, no operator touch."""
    import asyncio as _aio
    import time as _t

    async def _do_reload() -> dict:
        import websockets
        # 1. Find the Coolbet tab via /json/list.
        try:
            targets = await _aio.wait_for(
                _http_get_json(f"{CDP_URL}/json/list"),
                timeout=timeout_s,
            )
        except Exception as e:
            return {"ok": False, "message": f"CDP /json/list failed: {e}"}

        coolbet_target = None
        for t in (targets or []):
            if t.get("type") != "page":
                continue
            if "coolbet.com" in (t.get("url") or ""):
                coolbet_target = t
                break
        if coolbet_target is None:
            return {"ok": False,
                    "message": "No coolbet.com tab to reload (auto_self_heal "
                               "should open one via allow_open_new_tab=True instead)"}

        ws_url = coolbet_target.get("webSocketDebuggerUrl")
        if not ws_url:
            return {"ok": False, "message": "tab has no webSocketDebuggerUrl"}

        # 2. Open WS, send Page.reload.
        try:
            async with websockets.connect(ws_url, max_size=2_000_000) as ws:
                await ws.send(_json_dumps({"id": 1, "method": "Page.reload",
                                            "params": {"ignoreCache": False}}))
                # Wait briefly for the response (CDP echoes the id on completion).
                try:
                    await _aio.wait_for(ws.recv(), timeout=timeout_s)
                except Exception:
                    pass  # response is best-effort; reload itself proceeds
        except Exception as e:
            return {"ok": False, "message": f"CDP Page.reload failed: {e}"}

        # 3. Let the SPA fire renew-token + write the new JWT to localStorage.
        await _aio.sleep(post_reload_wait_s)
        return {"ok": True, "message": f"reloaded coolbet.com tab + waited {post_reload_wait_s}s"}

    try:
        return _aio.run(_do_reload())
    except Exception as e:
        return {"ok": False, "message": f"reload orchestration failed: {e}"}


def auto_self_heal(*, dry_run: bool = False,
                     triggered_by: str = "auto") -> dict:
    """Orchestrate the full CDP-JWT recovery chain. Goal: get from any
    state to `state=valid` with as little operator action as possible.

    Steps (each gated on the previous probe's outcome):
      1. Probe via `diagnose_cdp_jwt_state`. If already `valid`, exit.
      2. If `chrome_down`, run `auto_launch_cdp_chrome` (~5-30s), re-probe.
      3. If `no_coolbet_tab`, open one via
         `extract_jwt_from_cdp(allow_open_new_tab=True)`. That call ALSO
         persists if a fresh JWT is present. Re-probe.
      4. If `jwt_expired`, reload the Coolbet tab via
         `cdp_reload_coolbet_tab`. The SPA's renew-token cycle picks it
         up (no SMS). Re-probe.
      5. If `logged_out` after all that, alert — only SMS-enroll can
         recover, and that's the operator's deliberate choice.
      6. Final probe. If `valid`, persist + clear placement_paused (if it
         was set by daemon self-pause).

    Returns:
        {"recovered": bool,
         "state_before": str,
         "state_after":  str,
         "actions":      list[str],   # human-readable trail
         "message":      str}

    Idempotent. dry_run=True prints the action plan without executing.
    Designed to be called from:
      • daemon's consecutive-error escalation BEFORE alerting
      • operator's `--full-heal` CLI
      • a future Telegram inline-button callback
    """
    import time as _t
    from workers.automation.coolbet_state import (
        set_placement_paused, is_placement_paused,
    )

    import time as _heal_t
    _heal_started = _heal_t.time()

    actions: list[str] = []

    def _probe() -> dict:
        try:
            return diagnose_cdp_jwt_state()
        except Exception as e:
            return {"state": "unknown", "detail": str(e), "ttl_s": None}

    def _finish(result: dict) -> dict:
        """Write the heal attempt to coolbet_heal_log + return. Centralised
        so every exit path (including the early state=valid return) gets
        audited. Best-effort — never crash the heal on a log failure."""
        if not dry_run:
            try:
                from workers.automation.coolbet_state import log_heal_attempt
                log_heal_attempt(
                    triggered_by=triggered_by,
                    result=result,
                    duration_s=_heal_t.time() - _heal_started,
                )
            except Exception as e:
                log.debug("heal-log write failed (non-fatal): %s", e)
        return result

    # DOCKER-AUTO-START (2026-06-17 followup): the daemon's hot path goes
    # through FlareSolverr (Docker). If Docker is down, every placement
    # tick errors regardless of JWT state. Detect + start before touching
    # CDP/JWT. Skipped on dry_run + skipped when FS is already reachable.
    if not dry_run and not _flaresolverr_reachable():
        if dry_run:
            actions.append("would: open -a Docker to bring FlareSolverr up")
        else:
            docker = auto_start_docker()
            actions.append(f"docker_start: ok={docker['ok']} ({docker['message']})")

    # FORCE-PERSIST (2026-06-17 followup): sync DB from CDP at the very
    # start. Handles the "CDP has fresh JWT but DB is stale" case that
    # happens after a manual login — without this, auto_self_heal would
    # see state=valid and return without ever writing to DB, leaving
    # the daemon's first tick to discover the staleness.
    # proactive_jwt_refresh is idempotent and cheap: it no-ops when DB
    # is already fresh, persists when CDP has a fresher token.
    if not dry_run:
        try:
            r = proactive_jwt_refresh()
            if r.get("refreshed"):
                actions.append(
                    f"proactive_refresh: persisted CDP→DB "
                    f"(TTL {r.get('ttl_before_s')}s → {r.get('ttl_after_s')}s)"
                )
        except Exception as e:
            actions.append(f"proactive_refresh failed (non-fatal): {e}")

    state_before = _probe()
    state = state_before.get("state")
    if state == "valid":
        msg = f"JWT TTL ~{state_before.get('ttl_s')}s, nothing to heal."
        if not actions:
            actions = ["no-op (already valid)"]
        return _finish({"recovered": True,
                "state_before": state, "state_after": state,
                "actions": actions, "message": msg})

    # Step 2: launch Chrome if it's not reachable.
    if state == "chrome_down":
        if dry_run:
            actions.append("would: launch CDP-Chrome via launch_chrome_for_sync.sh")
        else:
            launch = auto_launch_cdp_chrome()
            actions.append(f"launch_chrome: ok={launch['ok']} ({launch['message']})")
            if not launch["ok"]:
                return _finish({"recovered": False,
                        "state_before": state_before.get("state"),
                        "state_after": "chrome_down",
                        "actions": actions, "message": launch["message"]})
            _t.sleep(2.0)  # let CDP settle
            state_before = _probe()
            state = state_before.get("state")
            actions.append(f"after_launch_probe: state={state}")

    # Step 2b: profile-picker bailout. CDP can't click chrome:// UI, so
    # `open_coolbet_tab` silently fails (verified 2026-06-19/21: 9 consecutive
    # auto-heal attempts stalled at no_coolbet_tab while Chrome sat at
    # chrome://profile-picker/). Return with an actionable operator message
    # — the daemon's _notify_consecutive_failures will surface it on Telegram.
    if state == "chrome_at_profile_picker":
        return _finish({"recovered": False,
                "state_before": state_before.get("state"),
                "state_after": "chrome_at_profile_picker",
                "actions": actions,
                "message": ("CDP-Chrome is at chrome://profile-picker/ — "
                            "daemon can't click chrome:// UI via CDP. Bring "
                            "the running Chrome window to the front, click "
                            "your profile, then open https://www.coolbet.com. "
                            "Next daemon tick will pick up the fresh JWT.")})

    # Step 3: open a Coolbet tab if missing — extract_jwt_from_cdp also
    # adopts a JWT in the same call if one's present.
    if state == "no_coolbet_tab":
        if dry_run:
            actions.append("would: open coolbet.com tab via CDP /json/new")
        else:
            try:
                jwt = extract_jwt_from_cdp(allow_open_new_tab=True)
                actions.append(f"open_coolbet_tab: jwt_obtained={bool(jwt)}")
            except Exception as e:
                actions.append(f"open_coolbet_tab failed: {e}")
            _t.sleep(1.0)
            state_before = _probe()
            state = state_before.get("state")
            actions.append(f"after_open_probe: state={state}")

    # Step 4: reload the Coolbet tab to trigger renew-token.
    if state == "jwt_expired":
        if dry_run:
            actions.append("would: Page.reload coolbet.com tab")
        else:
            r = cdp_reload_coolbet_tab()
            actions.append(f"page_reload: ok={r['ok']} ({r['message']})")
            if r["ok"]:
                _t.sleep(1.0)
                state_before = _probe()
                state = state_before.get("state")
                actions.append(f"after_reload_probe: state={state}")

    # Step 5a: if logged_out AND COOLBET_AUTO_LOGIN_ON_HEAL is set, try
    # cdp_auto_login as the last-resort recovery (verified 2026-06-17/18:
    # browser form submit, no SMS while CDP profile retains device trust).
    # Rate-limited to once per hour to bound SMS exposure if Coolbet ever
    # rotates device trust and starts demanding SMS. Opt-in by env so
    # operators have to deliberately enable it after reading the safety
    # contract — default-off keeps original "alert, don't auto-login"
    # behaviour for any unattended deployment.
    if state == "logged_out" and os.getenv("COOLBET_AUTO_LOGIN_ON_HEAL", "").lower() in ("true", "1", "yes"):
        from workers.automation.coolbet_state import (
            auto_login_recently_attempted, record_auto_login_attempt,
        )
        if auto_login_recently_attempted(min_gap_min=60):
            actions.append("auto_login: skipped — rate-limited (last attempt <60min ago)")
            record_auto_login_attempt(outcome="rate_limited")
        elif dry_run:
            actions.append("would: cdp_auto_login() to recover logged_out")
        else:
            actions.append("logged_out → cdp_auto_login()…")
            record_auto_login_attempt(outcome="attempted")  # stamp before so a hang still records
            try:
                rc = cdp_auto_login(max_wait_s=300)
                if rc == 0:
                    actions.append("auto_login: success")
                    record_auto_login_attempt(outcome="success")
                    # Pull the freshly-minted JWT from CDP → DB. The early
                    # FORCE-PERSIST at the top of this function already ran
                    # (and no-op'd because CDP was logged_out then), so we
                    # MUST call proactive_jwt_refresh AGAIN here to actually
                    # write the post-login JWT to coolbet_session_state.
                    # Without this, the daemon's next tick re-discovers
                    # logged_out via stale DB JWT and re-fires auto_login,
                    # tripping the rate limit.
                    try:
                        rf = proactive_jwt_refresh()
                        if rf.get("refreshed"):
                            actions.append(
                                f"post-login proactive_refresh: persisted "
                                f"CDP→DB (TTL {rf.get('ttl_before_s')}s → "
                                f"{rf.get('ttl_after_s')}s)"
                            )
                        else:
                            actions.append(
                                f"post-login proactive_refresh: "
                                f"{rf.get('reason')}"
                            )
                    except Exception as e:
                        actions.append(f"post-login proactive_refresh raised: {e}")
                    # Re-probe and fall through to the final probe + persist.
                    state_before = _probe()
                    state = state_before.get("state")
                elif rc == 6:
                    actions.append("auto_login: timed out (SMS likely required — check browser)")
                    record_auto_login_attempt(outcome="sms_timeout")
                    return _finish({"recovered": False,
                            "state_before": "logged_out",
                            "state_after": "logged_out",
                            "actions": actions,
                            "message": ("cdp_auto_login form-submitted but page "
                                        "didn't leave /login within 5min — Coolbet "
                                        "likely sent SMS. Check the CDP-Chrome "
                                        "browser, enter the code, then the next "
                                        "daemon tick self-heals.")})
                else:
                    actions.append(f"auto_login: error (rc={rc})")
                    record_auto_login_attempt(outcome="error")
            except Exception as e:
                actions.append(f"auto_login raised: {e}")
                record_auto_login_attempt(outcome="error")

    # Step 5b: if still logged_out (auto-login disabled, rate-limited, or
    # failed), bail out with the actionable hint. Only SMS-enroll can
    # recover, and that's the operator's deliberate choice.
    if state == "logged_out":
        return _finish({"recovered": False,
                "state_before": state_before.get("state"),
                "state_after": "logged_out",
                "actions": actions,
                "message": ("Coolbet session expired in CDP-Chrome — operator "
                            "must log in (open the CDP-Chrome window, sign in "
                            "to coolbet.com). After that, the next daemon tick "
                            "will self-heal via proactive_jwt_refresh.")})

    # Step 6: final probe + persist + clear placement_paused if recovered.
    final = _probe()
    state_after = final.get("state")
    recovered = (state_after == "valid")
    if recovered and not dry_run:
        # The extract_jwt_from_cdp / refresh_jwt_via_cdp chain in steps 3-4
        # already persisted; here we just make sure placement isn't blocked
        # by a stale kill-switch we (or the daemon's self-pause) might have set.
        try:
            paused, reason = is_placement_paused()
            if paused and reason and "daemon" in (reason or "").lower():
                set_placement_paused(False)
                actions.append(f"cleared placement_paused (was: {reason})")
        except Exception as e:
            actions.append(f"placement_paused clear failed (non-fatal): {e}")

    return _finish({"recovered": recovered,
            "state_before": state_before.get("state"),
            "state_after": state_after,
            "actions": actions,
            "message": (f"recovered to {state_after}" if recovered
                        else f"stalled at {state_after}")})


def _jwt_exp_seconds(token: str) -> float | None:
    """Decode JWT payload (no signature check) and return `exp` as epoch
    seconds. Returns None on any parse error so callers can treat parse
    failure the same as "missing exp"."""
    try:
        import base64 as _b64, json as _json
        if token.startswith("Bearer "):
            token = token[7:]
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))
        return float(payload.get("exp", 0)) or None
    except Exception:
        return None


def _try_read_localStorage_via_cdp(*, allow_open_new_tab: bool,
                                      timeout_ms: int) -> dict | None | Exception:
    """One attempt at: discover Coolbet tab via /json/list, open a raw
    CDP WebSocket to its debugger URL, send Runtime.evaluate to read
    localStorage. Returns:
      - dict snapshot on success
      - None if there's nothing to read (no Coolbet tab and
        allow_open_new_tab=False), so the caller knows not to retry
      - Exception object on transient failure (caller may retry)

    Why raw CDP over websockets instead of Playwright: the operator's
    Chrome has 60+ active targets (gmail iframes, maps, ads). Playwright
    tries to enumerate them all on connect and crashes with
    'Frame was detached' when one races a navigation. CDP Runtime.evaluate
    is a single targeted call to one tab's debugger session — none of
    that enumeration happens. Verified more robust against the operator's
    busy daily-driver Chrome 2026-06-12."""
    import asyncio
    try:
        return asyncio.run(
            _async_read_localStorage(allow_open_new_tab=allow_open_new_tab,
                                       timeout_s=timeout_ms / 1000.0)
        )
    except Exception as e:
        return e


async def _async_read_localStorage(*, allow_open_new_tab: bool,
                                       timeout_s: float) -> dict | None:
    """Async implementation of the CDP read. Sync caller wraps with
    asyncio.run() — there's no live loop in any process that calls this
    (daemon tick, --refresh-jwt CLI), so a per-call event loop is fine."""
    import asyncio
    import websockets

    # 1) Discover the Coolbet tab. /json/list returns all targets; we
    #    filter to type=page on coolbet.com.
    try:
        targets = await asyncio.wait_for(
            _http_get_json(f"{CDP_URL}/json/list"),
            timeout=timeout_s,
        )
    except Exception as e:
        log.warning("CDP /json/list failed: %s", e)
        raise

    coolbet_target = None
    for t in targets or []:
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        if "coolbet.com" in url:
            coolbet_target = t
            break

    if coolbet_target is None:
        if not allow_open_new_tab:
            log.info("No coolbet.com tab open in CDP-Chrome — "
                     "skipping JWT extract (allow_open_new_tab=False).")
            return None
        # Open via the /json/new convenience endpoint instead of opening
        # a Playwright page. /json/new respects --remote-debugging-port
        # and gives us back a fresh page target with its own ws URL.
        #
        # CDP-NEW-METHOD-PUT (2026-06-24): Chrome 124+ requires PUT instead
        # of GET on `/json/new` (CSRF mitigation — the GET form let any
        # webpage open arbitrary tabs in CDP-Chrome). Three overnight
        # cdp_auto_login failures on 2026-06-21 → 23 surfaced this:
        # `HTTP Error 405: Method Not Allowed`. PUT works on both new and
        # old Chrome, so use it unconditionally.
        log.info("No coolbet.com tab — opening one via CDP /json/new (PUT).")
        try:
            new_target = await asyncio.wait_for(
                _http_put_json(f"{CDP_URL}/json/new?https://www.coolbet.com/"),
                timeout=timeout_s,
            )
            coolbet_target = new_target
            # Give the SPA a moment to populate localStorage on first init.
            await asyncio.sleep(3.0)
        except Exception as e:
            log.warning("CDP /json/new failed: %s", e)
            raise

    ws_url = coolbet_target.get("webSocketDebuggerUrl")
    if not ws_url:
        log.warning("Coolbet target has no webSocketDebuggerUrl: %s", coolbet_target)
        return None

    # 2) Connect WS, send Runtime.evaluate. CDP's protocol is simple
    #    JSON-RPC; we send one request and wait for the matching id.
    js = (
        "(() => {"
        "  const keys = " + repr(list(_JWT_LOCALSTORAGE_KEYS)) + ";"
        "  const probed = {};"
        "  for (const k of keys) { try { probed[k] = localStorage.getItem(k); } catch (e) { probed[k] = null; } }"
        "  const all_keys = [];"
        "  const all_values = [];"
        "  try {"
        "    for (let i = 0; i < localStorage.length; i++) {"
        "      const k = localStorage.key(i);"
        "      all_keys.push(k);"
        "      all_values.push(localStorage.getItem(k));"
        "    }"
        "  } catch (e) {}"
        "  return JSON.stringify({ probed, all_keys, all_values });"
        "})()"
    )

    try:
        async with websockets.connect(ws_url,
                                         open_timeout=timeout_s,
                                         max_size=10_000_000) as ws:
            req_id = 1
            await ws.send(_json_dumps({
                "id": req_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js,
                    "returnByValue": True,
                    "awaitPromise": False,
                },
            }))
            # Some responses (e.g. Network events) arrive ahead of ours
            # — loop until we see our request id.
            deadline = asyncio.get_event_loop().time() + timeout_s
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise TimeoutError("CDP Runtime.evaluate response timed out")
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                resp = _json_loads(msg)
                if resp.get("id") != req_id:
                    continue
                if "error" in resp:
                    raise RuntimeError(f"CDP error: {resp['error']}")
                result = (resp.get("result") or {}).get("result") or {}
                if result.get("type") != "string":
                    raise RuntimeError(f"Unexpected CDP result shape: {result}")
                return _json_loads(result.get("value") or "{}")
    except Exception as e:
        log.warning("CDP Runtime.evaluate failed: %s", e)
        raise


async def _http_get_json(url: str) -> object:
    """Tiny async HTTP GET that decodes JSON. Avoids pulling aiohttp
    just for two calls — uses the urllib request via a thread executor."""
    import asyncio, urllib.request, json as _json
    def _fetch():
        with urllib.request.urlopen(url, timeout=5) as resp:
            return _json.loads(resp.read())
    return await asyncio.to_thread(_fetch)


async def _http_put_json(url: str) -> object:
    """PUT request for Chrome DevTools Protocol endpoints. Chrome 124+
    requires PUT instead of GET for `/json/new` (the GET form was a CSRF
    risk — any web page could open arbitrary tabs in CDP-Chrome by hitting
    localhost:9222 from a fetch()). Older Chrome (≤123) still accepts PUT
    on the same endpoints, so this is always the safer choice now."""
    import asyncio, urllib.request, json as _json
    def _fetch():
        req = urllib.request.Request(url, method="PUT")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return _json.loads(resp.read())
    return await asyncio.to_thread(_fetch)


def _json_dumps(o) -> str:
    import json as _json
    return _json.dumps(o)


def _json_loads(s) -> dict:
    import json as _json
    if isinstance(s, (bytes, bytearray)):
        s = s.decode("utf-8")
    return _json.loads(s)


def refresh_jwt_via_cdp(*, allow_open_new_tab: bool = True,
                          clear_placement_paused: bool = False) -> dict:
    """Operator entrypoint — extract JWT from CDP, persist to
    coolbet_session_state, optionally clear the placement_paused kill
    switch. Returns a result dict for the CLI to print.

    Why optional clear: the daemon refuses to place when paused, even
    after a JWT refresh. Operator must explicitly opt in to resuming —
    forces a conscious "yes, the underlying issue is fixed" decision."""
    result = {"ok": False, "jwt_obtained": False, "ttl_s": None,
              "user_id": None, "placement_paused_before": None,
              "placement_paused_after": None, "message": ""}
    jwt = extract_jwt_from_cdp(allow_open_new_tab=allow_open_new_tab)
    if not jwt:
        result["message"] = (
            "No JWT found. Check that CDP-Chrome is running and you're "
            "logged into Coolbet (have at least one coolbet.com tab open)."
        )
        return result

    result["jwt_obtained"] = True
    try:
        import base64 as _b64, json as _json, time as _t
        payload_b64 = jwt.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))
        result["user_id"] = payload.get("sub") or ""
        result["ttl_s"] = int(float(payload.get("exp", 0)) - _t.time())
        login_session_id = payload.get("login_session_id") or ""
    except Exception as e:
        result["message"] = f"JWT extracted but payload parse failed: {e}"
        return result

    try:
        from datetime import datetime as _dt, timezone as _tz
        from workers.automation.coolbet_state import (
            persist_jwt, set_placement_paused, is_placement_paused,
            mark_login_success,
        )
        was_paused, paused_reason = is_placement_paused()
        result["placement_paused_before"] = was_paused
        persist_jwt(jwt, login_session_id=login_session_id,
                     set_by="cdp_refresh")
        # Mirror the post-_adopt_manual_jwt pattern: also stamp the
        # observability fields (jwt_exp_at, last_login_at, session_healthy).
        # Without this, /status reads stale data even after a successful
        # refresh — exactly what surfaced 2026-06-12 when --refresh-jwt
        # landed a JWT but the row still showed the old expiry.
        mark_login_success(
            method="cdp_refresh",
            user_id=result["user_id"],
            jwt_exp_at=_dt.fromtimestamp(
                float(payload.get("exp", 0)), tz=_tz.utc),
        )
        if clear_placement_paused and was_paused:
            set_placement_paused(False)
            result["placement_paused_after"] = False
        else:
            result["placement_paused_after"] = was_paused
    except Exception as e:
        result["message"] = f"DB persist failed: {e}"
        return result

    result["ok"] = True
    result["message"] = (
        f"JWT refreshed from CDP (user={result['user_id']}, "
        f"ttl={result['ttl_s']}s). placement_paused: "
        f"{result['placement_paused_before']} → {result['placement_paused_after']}."
    )
    return result


def fetch_pending_bets_via_cdp(*, timeout_ms: int = 30000) -> list[dict]:
    """Fetch pending bets from the operator's logged-in Coolbet session.

    CDP-FETCH-RAW (2026-06-12): rewritten to use raw CDP over websockets
    rather than Playwright/patchright. The Playwright path crashed with
    'Frame was detached' on the operator's daily-driver Chrome (60+
    active targets — gmail iframes, maps, ads — racing the page
    enumeration). Same root cause as extract_jwt_from_cdp's migration.

    No tab navigation: instead of redirecting the operator's Coolbet
    tab to /panuste-ajalugu/sport and intercepting the XHR, we run a
    single Runtime.evaluate that calls `fetch('/s/sbgate/bets/history',
    {headers: {cbauth, ...}})` from the page context. The page's
    cookies + Imperva session are reused; no navigation, no focus
    event, no window flash.

    Returns [] on any failure — caller (Mac daemon) treats empty as
    'no CDP sync this tick, fall back to user_placed_at + Telegram-
    button dedup'."""
    try:
        import asyncio
        body = asyncio.run(_async_fetch_pending_bets(timeout_s=timeout_ms / 1000.0))
    except Exception as e:
        log.warning("CDP fetch_pending_bets failed: %s", e)
        return []
    if body is None:
        return []
    if isinstance(body, dict):
        items = (body.get("tickets") or body.get("data")
                  or body.get("results") or body.get("items") or [])
    else:
        items = body if isinstance(body, list) else []
    if isinstance(items, list):
        log.info("CDP-fetched %d pending bet(s) from Coolbet history", len(items))
        return items
    return []


async def _async_fetch_pending_bets(*, timeout_s: float) -> dict | list | None:
    """Find Coolbet tab → reload it via CDP → intercept the /s/sbgate/bets/history
    XHR Coolbet's React app fires on load → return parsed JSON.

    Why Page.reload + Network interception rather than a direct fetch():
    Imperva specifically challenges /s/sbgate/bets/history at the API
    layer — a bare fetch() from page context returns HTTP 500 with
    `<script src="/de-Macd-thats-...">` (the Imperva JS challenge).
    The trick that DOES work is to navigate the page, which executes
    the challenge JS natively, then capture the XHR that Coolbet's own
    React app fires — that XHR is now trusted. Confirmed empirically
    2026-06-12 by trying both paths.

    Page reload is mildly disruptive (the operator's tab refreshes)
    but matches the existing Playwright behavior — the SILENT-SYNC
    optimisation (reuse-existing-tab-if-on-history) is preserved."""
    import asyncio
    import websockets

    try:
        targets = await asyncio.wait_for(
            _http_get_json(f"{CDP_URL}/json/list"),
            timeout=timeout_s,
        )
    except Exception as e:
        log.warning("CDP /json/list failed: %s", e)
        return None

    coolbet_target = None
    for t in targets or []:
        if t.get("type") != "page":
            continue
        if "coolbet.com" in (t.get("url") or ""):
            coolbet_target = t
            break
    if coolbet_target is None:
        log.info("No coolbet.com tab open in CDP-Chrome — skipping bet-history sync.")
        return None

    ws_url = coolbet_target.get("webSocketDebuggerUrl")
    if not ws_url:
        log.warning("Coolbet target has no webSocketDebuggerUrl: %s", coolbet_target)
        return None

    current_url = coolbet_target.get("url") or ""
    # NON-DISRUPTIVE-SYNC (2026-06-12): only sync when the operator's
    # tab is ALREADY on the history page. Navigating their tab away
    # from wherever they're browsing is rude; the dedup is a nice-to-
    # have, not safety-critical (user_placed_at + Telegram ✅ button is
    # the canonical kill switch). Operator who wants the sync just
    # leaves a /panuste-ajalugu/sport tab open.
    if HISTORY_PAGE not in current_url and "/panuste-ajalugu" not in current_url:
        log.info("CDP fetch_pending_bets: Coolbet tab on %s, not history — "
                  "skipping sync (open /panuste-ajalugu/sport to enable).",
                  current_url[:80])
        return None

    try:
        async with websockets.connect(ws_url,
                                         open_timeout=timeout_s,
                                         max_size=10_000_000) as ws:
            next_id = [0]

            async def send_cmd(method: str, params: dict | None = None) -> int:
                next_id[0] += 1
                rid = next_id[0]
                await ws.send(_json_dumps({
                    "id": rid, "method": method, "params": params or {},
                }))
                return rid

            # Enable the domains we need. We don't care about the ack
            # responses (their ids are tracked but skipped in the loop).
            await send_cmd("Network.enable")
            await send_cmd("Page.enable")

            # Reload the existing history tab to retrigger the XHR.
            # We pre-validated the tab is already on history (above),
            # so Page.reload is safe — no navigation away.
            await send_cmd("Page.reload", {"ignoreCache": False})

            # Collect requestIds for /s/sbgate/bets/history responses
            # that look like the real API call (status 200, JSON-ish).
            # The Imperva challenge first returns 500 with HTML; the
            # real call follows after the challenge JS solves it.
            history_request_ids: list[str] = []
            history_seen_at: dict[str, float] = {}
            deadline = asyncio.get_event_loop().time() + timeout_s
            # Also stop early once we've seen the response AND given
            # Coolbet a beat to deliver the body — typical lag <500ms.
            settle_after_first_hit_s = 2.5
            first_hit_at: float | None = None

            while True:
                now = asyncio.get_event_loop().time()
                remaining = deadline - now
                if remaining <= 0:
                    break
                if first_hit_at is not None and (now - first_hit_at) > settle_after_first_hit_s:
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 1.0))
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    log.warning("WS recv failed: %s", e)
                    break

                evt = _json_loads(msg)
                method = evt.get("method")
                if method != "Network.responseReceived":
                    continue
                params = evt.get("params") or {}
                resp = params.get("response") or {}
                url = resp.get("url") or ""
                if "/s/sbgate/bets/history" not in url:
                    continue
                # Skip the Imperva 500 challenge responses.
                if resp.get("status") != 200:
                    continue
                req_id = params.get("requestId")
                if not req_id or req_id in history_seen_at:
                    continue
                history_seen_at[req_id] = now
                history_request_ids.append(req_id)
                if first_hit_at is None:
                    first_hit_at = now

            # No history XHR seen → bail. Caller falls back to
            # user_placed_at-only dedup.
            if not history_request_ids:
                log.info("CDP fetch_pending_bets: no /bets/history 200 response seen "
                          "within %.0fs", timeout_s)
                return None

            # Fetch each response body via Network.getResponseBody.
            all_items: list[dict] = []
            merged: dict = {}
            for rid in history_request_ids:
                body_cmd_id = await send_cmd(
                    "Network.getResponseBody", {"requestId": rid},
                )
                # Drain WS until we see the matching response.
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    except asyncio.TimeoutError:
                        log.warning("Network.getResponseBody timed out for %s", rid)
                        break
                    evt = _json_loads(msg)
                    if evt.get("id") != body_cmd_id:
                        continue
                    if "error" in evt:
                        log.warning("getResponseBody error for %s: %s", rid, evt["error"])
                        break
                    result = evt.get("result") or {}
                    body_text = result.get("body") or ""
                    if result.get("base64Encoded"):
                        import base64 as _b64
                        try:
                            body_text = _b64.b64decode(body_text).decode("utf-8")
                        except Exception as e:
                            log.warning("base64 decode failed: %s", e)
                            break
                    try:
                        parsed = _json_loads(body_text)
                    except Exception as e:
                        log.warning("response not JSON: %s body=%s", e, body_text[:200])
                        break
                    if isinstance(parsed, dict):
                        tickets = (parsed.get("tickets") or parsed.get("data")
                                    or parsed.get("results") or parsed.get("items"))
                        if isinstance(tickets, list):
                            all_items.extend(tickets)
                        merged = parsed  # keep the last full envelope for caller
                    elif isinstance(parsed, list):
                        all_items.extend(parsed)
                    break

            # If we collected tickets across responses, hand the
            # caller the merged envelope; else return whatever we
            # have. Caller already handles both shapes.
            if all_items:
                if merged and isinstance(merged, dict):
                    merged["tickets"] = all_items
                    return merged
                return all_items
            return merged or None
    except Exception as e:
        log.warning("CDP fetch_pending_bets: WS failed: %s", e)
        return None


def fetch_pending_bets(*, headless: bool = True,
                        timeout_ms: int = 30000) -> list[dict]:
    """Open the persistent profile in headless Chromium, navigate to the
    bet-history page, intercept the /s/sbgate/bets/history XHR fired by
    Coolbet's frontend, return the parsed JSON.

    Returns a list of bet dicts as Coolbet renders them — each entry has
    at minimum:
      - id (Coolbet's internal ticket id)
      - status ('PENDING' here)
      - selections: array of {event_name, market_name, selection_name, odds, ...}
      - stake, currency, placed_at

    Returns empty list on any failure — caller treats that as "couldn't
    sync this tick, fall back to user_placed_at-only dedup".

    headless=False is for debugging — shows the browser, you can see what
    Coolbet renders. Set via env COOLBET_BROWSER_HEADFUL=1."""
    sync_playwright = _sync_playwright_factory()

    if os.getenv("COOLBET_BROWSER_HEADFUL"):
        headless = False

    profile_dir = _ensure_profile_dir()
    if not (profile_dir / "Default").exists() and not any(profile_dir.iterdir()):
        log.error("Empty profile dir — run `python3 -m workers.automation.coolbet_browser_sync --login` first.")
        return []

    captured: list[dict] = []
    with sync_playwright() as p:
        ctx = _launch_context(p, headless=headless)
        _apply_stealth(ctx)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        # Hook the response BEFORE navigating so we don't miss the XHR.
        def _on_response(resp):
            try:
                if "/s/sbgate/bets/history" not in resp.url:
                    return
                if "ticketStatus=PENDING" not in resp.url and "ticketStatus=all" not in resp.url:
                    return
                body = resp.json()
                # Coolbet returns either a list directly or a paginated envelope.
                # Normalise to a list so callers don't care.
                if isinstance(body, dict):
                    items = body.get("data") or body.get("results") or body.get("items") or []
                else:
                    items = body
                if isinstance(items, list):
                    captured.extend(items)
                    log.info("captured %d bets from %s", len(items), resp.url)
            except Exception as e:
                log.debug("response intercept failed (non-fatal): %s", e)

        page.on("response", _on_response)
        try:
            page.goto(HISTORY_PAGE, wait_until="domcontentloaded",
                       timeout=timeout_ms)
            # XHR fires after DOM ready. Wait briefly for capture.
            page.wait_for_timeout(5000)
        except Exception as e:
            log.warning("page load failed: %s", e)
        ctx.close()

    return captured


def normalize_for_dedup(raw_bet: dict) -> dict | None:
    """Reduce a raw Coolbet ticket to a (match_name, market, selection,
    placed_at, ticket_id) tuple suitable for fuzzy-matching against our
    simulated_bets queue.

    Coolbet's ticket shape (verified 2026-06-12):
      { id, status, total_stake, ticket_type ('single' | 'combo'),
        total_matches, created_at, currency,
        first_match: { sport_name, match_name, league_name,
                       market_name, outcome_name, sport_icon, sport_category_id },
        first_bet_odds, ... }

    For combos (ticket_type != 'single' OR total_matches > 1) we mark
    is_combo=True so the daemon's combo-single dedup logic can treat
    them separately. We use first_match for the canonical text — combos
    are matched at the ticket level, not per-leg.

    Returns None when the bet shape isn't what we expect — caller falls
    back to user_placed_at dedup for that one."""
    try:
        first = raw_bet.get("first_match") or {}
        if not first:
            return None
        match_name = first.get("match_name") or ""
        market = first.get("market_name") or ""
        selection = first.get("outcome_name") or ""
        if not (match_name and market and selection):
            return None
        ticket_id = raw_bet.get("id")
        placed_at = raw_bet.get("created_at")
        is_combo = (raw_bet.get("ticket_type") != "single"
                     or (raw_bet.get("total_matches") or 1) > 1)
        return {
            "match_name": match_name,
            "market": market,
            "selection": selection,
            "placed_at": placed_at,
            "ticket_id": ticket_id,
            "is_combo": is_combo,
            "status": raw_bet.get("status"),
            "stake": raw_bet.get("total_stake"),
            "odds": raw_bet.get("first_bet_odds"),
            "sport_icon": first.get("sport_icon"),
        }
    except Exception as e:
        log.debug("normalize failed: %s", e)
        return None


# Market-name translation: Coolbet's Estonian/English labels → our DB enum.
# Add new variants here as you spot them; unknown markets resolve to None
# and that ticket falls through to user_placed_at-only dedup.
_MARKET_MAP_PARTIALS: list[tuple[str, str]] = [
    ("lõpptulemus",          "1x2"),
    ("match result",          "1x2"),
    ("aasia händikäp",        "asian_handicap"),
    ("asian handicap",        "asian_handicap"),
    ("väravate arv",          "o/u"),
    ("total goals",           "o/u"),
    ("mõlemad meeskonnad",    "btts"),
    ("both teams to score",   "btts"),
    ("topelt võimalus",       "double_chance"),
    ("double chance",         "double_chance"),
    ("loobu loosist",         "draw_no_bet"),
    ("draw no bet",           "draw_no_bet"),
]


def _coolbet_market_to_key(coolbet_market: str) -> str | None:
    """Coolbet's market name → our market enum value. Case-insensitive
    substring match because Coolbet appends line values to some markets
    (e.g. 'Väravate arv (Üle/Alla) 2.5')."""
    m = (coolbet_market or "").lower()
    for needle, key in _MARKET_MAP_PARTIALS:
        if needle in m:
            return key
    return None


def _coolbet_selection_to_key(market_key: str, cb_selection: str,
                                cb_home: str, cb_away: str) -> str | None:
    """Coolbet outcome name → our selection enum.

    1X2 / DC / DNB / AH: outcome_name is the team name (or 'Viik' for
    draw). Match against cb_home/cb_away with fuzzy comparison to handle
    'FC Dinamo Batumi' vs 'Dinamo Batumi' style differences.
    O/U: outcome_name is 'Üle 2.5' or 'Alla 2.5' → 'over X.Y' / 'under X.Y'."""
    sel = (cb_selection or "").strip()
    sel_lower = sel.lower()
    if market_key == "o/u":
        # Estonian Üle/Alla → English over/under, then preserve the number.
        # Coolbet doesn't always include the line in outcome_name — pull from
        # market name if absent.
        if "üle" in sel_lower or sel_lower.startswith("over"):
            base = "over"
        elif "alla" in sel_lower or sel_lower.startswith("under"):
            base = "under"
        else:
            return None
        # Extract the X.Y line from the selection itself.
        import re
        m = re.search(r"(\d+(?:\.\d+)?)", sel)
        if m:
            return f"{base} {m.group(1)}"
        return base
    # For team-based markets, fuzzy-match selection to home/away
    from rapidfuzz import fuzz
    home_score = fuzz.token_set_ratio(sel.lower(), (cb_home or "").lower())
    away_score = fuzz.token_set_ratio(sel.lower(), (cb_away or "").lower())
    if "viik" in sel_lower or "draw" in sel_lower:
        return "draw"
    if home_score >= 75 and home_score >= away_score:
        return "home"
    if away_score >= 75:
        return "away"
    return None


def match_coolbet_to_simulated(norm_bet: dict, simulated_bets: list[dict]) -> dict | None:
    """Given a normalised Coolbet ticket and our candidates from
    simulated_bets, find the simulated_bet that corresponds to it.

    Strategy:
      - Parse Coolbet's 'match_name' as 'Home - Away'
      - Map Coolbet's market + selection labels to our enum values
      - Fuzzy-match the team names against each simulated_bet's home/away

    Returns the matched simulated_bet dict, or None on no match. We want
    to be CONSERVATIVE — a false positive marks a legit edge as placed
    and the daemon would skip placing it (losing the edge). False
    negatives just mean we re-attempt placement which our existing dedup
    catches (real_bets row prevents duplicate at next tick)."""
    from rapidfuzz import fuzz
    cb_match = norm_bet.get("match_name") or ""
    if " - " not in cb_match:
        return None
    parts = cb_match.split(" - ", 1)
    cb_home = parts[0].strip()
    cb_away = parts[1].strip()

    market_key = _coolbet_market_to_key(norm_bet.get("market", ""))
    if not market_key:
        return None
    selection_key = _coolbet_selection_to_key(
        market_key, norm_bet.get("selection", ""), cb_home, cb_away,
    )
    if not selection_key:
        return None

    best = None
    best_score = 0
    for sb in simulated_bets:
        if sb.get("market") != market_key:
            continue
        # Selection comparison: for AH the selection includes a handicap
        # number that may not match exactly. Loose-equal: starts-with.
        sb_sel = (sb.get("selection") or "").lower()
        sk_lower = (selection_key or "").lower()
        if not (sb_sel.startswith(sk_lower.split()[0]) or sk_lower.startswith(sb_sel.split()[0])):
            continue
        home_score = fuzz.token_set_ratio(cb_home.lower(), (sb.get("home_team") or "").lower())
        away_score = fuzz.token_set_ratio(cb_away.lower(), (sb.get("away_team") or "").lower())
        avg = (home_score + away_score) / 2
        if avg >= 70 and avg > best_score:
            best = sb
            best_score = avg
    return best


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--login", action="store_true", help="One-time interactive login (spawned Chromium — deprecated)")
    p.add_argument("--fetch", action="store_true", help="Fetch + print pending bets (spawned Chromium — deprecated)")
    p.add_argument("--cdp-fetch", action="store_true", help="Fetch via CDP attach to user's real Chrome (requires --remote-debugging-port=9222)")
    p.add_argument("--cdp-login", action="store_true", help="Open the CDP-Chrome on Coolbet login + wait for you to finish logging in")
    p.add_argument("--cdp-auto-login", action="store_true", help="Auto-fill + click login in CDP-Chrome (uses COOLBET_USER/PASS); waits for SMS if asked")
    p.add_argument("--refresh-jwt", action="store_true",
                   help="Extract fresh JWT from CDP-Chrome localStorage, persist to coolbet_session_state, optionally clear placement_paused.")
    p.add_argument("--resume-placement", action="store_true",
                   help="Combine with --refresh-jwt to ALSO clear the placement_paused kill switch after the JWT lands.")
    p.add_argument("--full-heal", action="store_true",
                   help="One-command operator recovery (B4): probe CDP state, "
                        "auto-launch Chrome if down, open Coolbet tab if missing, "
                        "Page.reload if JWT expired, persist JWT, clear placement_paused. "
                        "Only `logged_out` requires manual login (auto-heal can't bypass SMS).")
    p.add_argument("--full-heal-dry-run", action="store_true",
                   help="Combine with --full-heal to print the action plan without executing.")
    p.add_argument("--headful", action="store_true", help="Visible browser (debug)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(name)s — %(message)s")

    if args.refresh_jwt:
        res = refresh_jwt_via_cdp(
            allow_open_new_tab=True,
            clear_placement_paused=args.resume_placement,
        )
        print(f"  ok                = {res['ok']}")
        print(f"  jwt_obtained      = {res['jwt_obtained']}")
        print(f"  ttl_s             = {res['ttl_s']}")
        print(f"  user_id           = {res['user_id']}")
        print(f"  placement_paused  = {res['placement_paused_before']} → {res['placement_paused_after']}")
        print(f"  message           = {res['message']}")
        return 0 if res["ok"] else 1
    if args.full_heal:
        res = auto_self_heal(dry_run=args.full_heal_dry_run,
                             triggered_by="operator_cli")
        print(f"  recovered     = {res['recovered']}")
        print(f"  state         = {res['state_before']} → {res['state_after']}")
        print(f"  actions:")
        for a in res['actions']:
            print(f"    • {a}")
        print(f"  message       = {res['message']}")
        return 0 if res["recovered"] else 1
    if args.login:
        return interactive_login()
    if args.cdp_auto_login:
        return cdp_auto_login()
    if args.cdp_fetch:
        bets = fetch_pending_bets_via_cdp()
        print(f"  fetched {len(bets)} bets via CDP:")
        for b in bets[:25]:
            norm = normalize_for_dedup(b)
            keys_preview = sorted(b.keys())[:8] if isinstance(b, dict) else type(b).__name__
            print(f"    raw_keys={keys_preview}…")
            if norm:
                print(f"    → {norm['match_name'][:40]:40s}  {norm['market']:20s}  {norm['selection']:18s}  ticket={norm['ticket_id']}")
        return 0
    if args.fetch:
        if args.headful:
            os.environ["COOLBET_BROWSER_HEADFUL"] = "1"
        bets = fetch_pending_bets()
        print(f"  fetched {len(bets)} bets:")
        for b in bets[:25]:
            norm = normalize_for_dedup(b)
            print(f"    raw_keys={sorted(b.keys())[:8]}…")
            if norm:
                print(f"    → {norm['match_name'][:40]:40s}  {norm['market']:20s}  {norm['selection']:18s}  ticket={norm['ticket_id']}")
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
