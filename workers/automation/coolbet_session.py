"""
Coolbet API session manager.

Handles JWT authentication via /s/auth/login.  The JWT expires every ~30 min;
this module auto-refreshes it transparently.

Required .env keys:
    COOLBET_USER               — email address
    COOLBET_PASS               — password
    COOLBET_IMPERVA_COOKIES    — raw cookie string copied from browser DevTools
                                 (the reese84 / visid_incap_* tokens that prove
                                 the browser passed Imperva's challenge)

The Imperva cookies are the only part that requires a human interaction (solving
the challenge once in a real browser).  They last for days to weeks.  The JWT
is refreshed automatically from credentials.
"""

import base64
import json
import logging
import os
import random
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz

load_dotenv()

log = logging.getLogger(__name__)

# COOLBET-FS-SESSION-STABLE (2026-06-11): route every HTTP call through
# FlareSolverr instead of plain requests.Session. Imperva accepts FS's real
# Chrome TLS fingerprint + cookie jar; the plain-requests path was getting
# 403/500'd at the search/v2 endpoint (the symptom that left the placer
# with no bets to bet on). FS keeps the Imperva cookies + login session in
# the named browser session — we no longer need to sync them to .env.
_FS_URL_DEFAULT = "http://localhost:8191"
_FS_SESSION_NAME = os.getenv("COOLBET_FLARE_SESSION", "coolbet_prod")
_FS_TIMEOUT_MS = int(os.getenv("COOLBET_FS_TIMEOUT_MS", "60000"))

_LOGIN_URL = "https://www.coolbet.com/s/auth/login"
_RENEW_URL = "https://www.coolbet.com/s/auth/renew-token"
_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "content-type": "application/json; charset=utf-8",
    "accept": "*/*",
    "x-device": "DESKTOP",
    "origin": "https://www.coolbet.com",
    "referer": "https://www.coolbet.com/en/sports/football",
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}


def _decode_jwt_payload(token: str) -> dict:
    payload_b64 = token.split(".")[1]
    # Add padding
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.b64decode(payload_b64))


# State writer — local import inside the methods that need it so module
# load doesn't trigger workers.api_clients.db connection.
def _state():
    from workers.automation import coolbet_state as _s
    return _s


# ── FlareSolverr proxy ───────────────────────────────────────────────────────
# Mirrors the proven scripts/coolbet/session_heartbeat.py pattern. Every call
# from this module to Coolbet goes through FS's named browser session
# (default: coolbet_prod) so Imperva sees real-Chrome TLS + headers.


def _fs_call(body: dict, *, timeout_s: int = 90) -> dict:
    """Low-level FlareSolverr proxy call. Raises if FLARESOLVERR_URL unset
    or the FS instance is unreachable.

    Returns the parsed JSON envelope: { status: 'ok'|'error', solution: {...},
    message, version, startTimestamp, endTimestamp }."""
    fs_url = (os.getenv("FLARESOLVERR_URL") or _FS_URL_DEFAULT).rstrip("/")
    if not fs_url:
        raise RuntimeError("FLARESOLVERR_URL is unset — required for Coolbet session.")
    req = urllib.request.Request(
        f"{fs_url}/v1",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def _fs_session_ensure(name: str) -> None:
    """Idempotent — silently no-ops if session already exists. We don't
    distinguish creation from existence on purpose; FS doesn't expose a
    clean 'exists?' check, only sessions.list which is heavier."""
    try:
        _fs_call({"cmd": "sessions.create", "session": name}, timeout_s=30)
    except Exception:
        pass  # Already exists, or FS slow to respond — either way subsequent
              # request.* calls will surface the real error.


class _FSResponse:
    """Adapter wrapping a FlareSolverr response dict in a requests.Response-
    shaped interface. Lets the existing CoolbetSession callers (placer,
    scanner, explorer, inplay) keep using `.status_code`, `.text`, `.json()`,
    `.ok`, `.cookies`, `.raise_for_status()` without code changes.

    Why an adapter and not subclassing requests.Response: the latter wants
    a real urllib3 connection object behind it. We're feeding from a dict.
    Cleaner to expose only the shape we actually use."""

    def __init__(self, fs_envelope: dict):
        sol = fs_envelope.get("solution") or {}
        self._envelope = fs_envelope
        self._solution = sol
        self.status_code: int = int(sol.get("status") or 0)
        self.text: str = sol.get("response") or ""
        self.url: str = sol.get("url") or ""
        # cookies: FS returns a list of {name, value, ...}; expose as dict
        self.cookies: dict[str, str] = {
            c.get("name"): c.get("value")
            for c in (sol.get("cookies") or [])
            if c.get("name")
        }
        self.headers: dict = sol.get("headers") or {}
        # FS sometimes wraps the JSON body in HTML (<pre>...</pre>) when
        # navigating to an API endpoint that returns text/plain. Strip the
        # wrapper before .json() parses.
        self._stripped_text: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def _clean_text(self) -> str:
        if self._stripped_text is not None:
            return self._stripped_text
        t = self.text or ""
        # FlareSolverr wraps non-HTML responses inside a <pre> block when the
        # server returned text/json. Strip the surrounding HTML if present.
        if "<pre>" in t and "</pre>" in t:
            start = t.index("<pre>") + len("<pre>")
            end = t.index("</pre>", start)
            t = t[start:end]
        # Defensive — some FS versions also wrap in <html><body>
        for prefix in ("<!DOCTYPE html>", "<html>", "<body>"):
            if t.lstrip().startswith(prefix):
                # If we can't find <pre>, fall back to text as-is; let json()
                # surface the parse error so callers see a real diagnostic.
                break
        self._stripped_text = t.strip()
        return self._stripped_text

    def json(self):
        return json.loads(self._clean_text())

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(
                f"HTTP {self.status_code} on {self.url} via FlareSolverr — "
                f"body[:200]={self._clean_text()[:200]!r}"
            )


class CoolbetSession:
    """Thread-safe(ish) Coolbet API session with auto JWT refresh.

    require_auth=False (ANON-READ mode): skips JWT entirely — only Imperva
    cookies are applied. Sufficient for all public read endpoints (search/v2,
    fo-match, sidebets, odds). Use for --record mode so a valid Imperva cookie
    set is enough without a COOLBET_MANUAL_JWT.
    """

    def __init__(self, *, require_auth: bool = True):
        self._require_auth = require_auth
        self._email = os.getenv("COOLBET_USER", os.getenv("COOLBET_EMAIL", ""))
        self._password = os.getenv("COOLBET_PASS", os.getenv("COOLBET_PASSWORD", ""))
        # Individual cookie vars (preferred — easier to update when one expires)
        # Falls back to the legacy combined COOLBET_IMPERVA_COOKIES string.
        # `uuid` (deviceId) is a Coolbet-side cookie, not Imperva — required
        # in the bet placement payload (POST /s/bets/bets sends it as
        # `deviceId`). Without it Coolbet 400's the bet.
        self._imperva_cookies_individual = {
            "reese84":                 os.getenv("COOLBET_COOKIE_REESE84", ""),
            "visid_incap_723517":      os.getenv("COOLBET_COOKIE_VISID_INCAP", ""),
            "nlbi_723517":             os.getenv("COOLBET_COOKIE_NLBI", ""),
            "nlbi_723517_2147483392":  os.getenv("COOLBET_COOKIE_NLBI2", ""),
            "incap_ses_1099_723517":   os.getenv("COOLBET_COOKIE_INCAP_SES", ""),
            "uuid":                    os.getenv("COOLBET_COOKIE_UUID", ""),
        }
        self._imperva_cookies_raw = os.getenv("COOLBET_IMPERVA_COOKIES", "")

        # Manual-JWT mode (MANUAL-JWT, 2026-05-20): when COOLBET_MANUAL_JWT is
        # set, skip /s/auth/login entirely and use the pasted token. Use this
        # whenever Coolbet's email/password endpoint is rate-limited or blocked
        # (eg user logged in via Smart-ID and password login is now disabled).
        # Token lifetime is the JWT's `exp` (~30 min). On expiry we raise a
        # clear error so the operator pastes a fresh `cbauth` from browser
        # DevTools. This is the exact seam a future headless-Chrome refresher
        # would write into — same code path, just automated capture.
        self._manual_jwt = os.getenv("COOLBET_MANUAL_JWT", "").strip()
        if self._manual_jwt.startswith("Bearer "):
            self._manual_jwt = self._manual_jwt[7:]

        if require_auth and not self._manual_jwt and (not self._email or not self._password):
            raise RuntimeError(
                "Auth misconfigured: set COOLBET_MANUAL_JWT (pasted from browser) "
                "OR set COOLBET_USER + COOLBET_PASS for API login."
            )

        self._jwt: str | None = None
        self._jwt_exp: float = 0.0
        self._login_session_id: str | None = None
        self._user_id: str | None = None

        # COOLBET-HUMAN-PACED (2026-05-20): every authenticated call goes
        # through _throttle() which enforces a randomised gap so our request
        # pattern doesn't look like a scraper to Imperva's anti-bot stack.
        # Defaults: 0.8–2.0s between calls + small jitter on the floor.
        # Override via env if a workload needs faster (CI tests) or slower
        # (deeper paranoia). The minimum is still enforced — set both equal
        # for a constant gap.
        self._min_call_gap = float(os.getenv("COOLBET_MIN_CALL_GAP_S", "0.8"))
        self._max_call_gap = float(os.getenv("COOLBET_MAX_CALL_GAP_S", "2.0"))
        self._last_call_t  = 0.0
        self._throttle_lock = __import__("threading").Lock()

        # HYBRID TRANSPORT (2026-06-11):
        # - GET ........ via FlareSolverr (real Chrome TLS, full Imperva pass)
        # - POST (JSON). via plain requests.Session() WITH cookies harvested
        #                from a FS GET. The reason: FlareSolverr force-encodes
        #                all POST bodies as application/x-www-form-urlencoded
        #                and TRUNCATES JSON bodies at the first separator.
        #                Imperva accepts our plain-requests POST because the
        #                cookies are real (FS-issued, fresh).
        # See COOLBET-FS-SESSION-STABLE diagnostic logs 2026-06-11 for the
        # httpbin echo that proved FS truncates JSON to 4 bytes.
        self._fs_session_name = _FS_SESSION_NAME
        _fs_session_ensure(self._fs_session_name)

        # The real transport for POST (and any legacy plain-requests path).
        # Cookies will be populated by _refresh_cookies_from_fs() on first use.
        self._http = requests.Session()
        self._http.headers.update(_HEADERS_BASE)
        # Tracks whether we've done at least one FS-cookie harvest. Set to
        # False on init AND on any 401/403 to force re-harvest on next call.
        self._cookies_fresh: bool = False

    # ── setup ────────────────────────────────────────────────────────────────

    def _apply_imperva_cookies(self) -> None:
        """No-op since 2026-06-11 (HYBRID-FS architecture). FlareSolverr's
        browser holds the canonical Imperva cookies. We harvest them into
        self._http on demand via _refresh_cookies_from_fs() instead of reading
        from .env (which is stale by the time the next process restarts).

        Kept as a method (not removed) so callers that explicitly call
        _apply_imperva_cookies() — typically test setup — don't break."""
        return

    def _refresh_cookies_from_fs(self) -> int:
        """Navigate via FlareSolverr through Coolbet warmup pages to capture
        fresh Imperva cookies (reese84, visid_incap_*, nlbi_*, incap_ses_*),
        then copy them into self._http for plain-requests POSTs to use.

        WARMUP-LOOP-FIX (2026-06-11): single-page nav doesn't always trigger
        Imperva's reese84 issuance (the "deep challenge" token). Mirrors the
        proven pattern from flaresolverr_login_enroll.py — visits multiple
        pages in sequence until BOTH reese84 AND visid_incap_* are present.
        Early-exits as soon as both land to avoid extra navigations.

        Also synchronises the User-Agent so plain-requests calls look like
        the same browser that earned the cookies (Imperva fingerprints UA +
        cookies together — mismatch can trigger a re-challenge).

        Returns the number of cookies harvested. Raises on FS unreachable
        OR if Coolbet's response didn't include the critical Imperva markers
        after all warmup attempts — callers should treat that as a hard
        auth failure.
        """
        warmup_urls = [
            "https://www.coolbet.com/",                       # homepage (often gets reese84)
            "https://www.coolbet.com/en/sports/football",      # deep page (gets visid_incap_*)
            "https://www.coolbet.com/en/sports/esports",       # tertiary fallback
        ]
        cookies: list = []
        ua: str | None = None
        sol: dict = {}
        for url in warmup_urls:
            body = {
                "cmd": "request.get",
                "url": url,
                "session": self._fs_session_name,
                "maxTimeout": _FS_TIMEOUT_MS,
            }
            raw = _fs_call(body)
            sol = raw.get("solution") or {}
            new_cookies = sol.get("cookies") or []
            if new_cookies:
                # Merge by name — later navigations refresh earlier values.
                by_name = {c.get("name"): c for c in cookies if c.get("name")}
                for c in new_cookies:
                    if c.get("name"):
                        by_name[c["name"]] = c
                cookies = list(by_name.values())
            ua = sol.get("userAgent") or ua
            # Early-out as soon as both critical Imperva markers are present.
            names = {c.get("name") for c in cookies if c.get("name")}
            if "reese84" in names and any(n.startswith("visid_incap") for n in names):
                break
        if not cookies:
            raise RuntimeError(
                f"FS cookie harvest empty after warmup loop — FS status="
                f"{raw.get('status')!r}, message={raw.get('message')!r}. "
                f"Try `python3 scripts/diagnose/flaresolverr.py` to diagnose."
            )

        # Wipe stale cookies first so a fresh harvest doesn't accidentally
        # preserve expired ones (Imperva rotates fast).
        self._http.cookies.clear()
        copied = 0
        for c in cookies:
            name = c.get("name")
            value = c.get("value")
            if name and value:
                # Domain quirk: some Imperva cookies are issued for the bare
                # domain ('.coolbet.com'), some for the host ('www.coolbet.com').
                # FS returns the exact domain; preserve it. Default to www if
                # missing (Coolbet's docs anchor on www).
                domain = c.get("domain") or "www.coolbet.com"
                self._http.cookies.set(name, value, domain=domain)
                copied += 1

        # UA sync — without this the post-harvest plain-requests calls look
        # like a different browser to Imperva and may get re-challenged.
        # `ua` was populated inside the warmup loop from the last navigation
        # that returned one.
        if ua:
            self._http.headers["User-Agent"] = ua

        # Sanity check: did we get the critical Imperva markers?
        names = {c.get("name") for c in cookies if c.get("name")}
        has_reese = "reese84" in names
        has_visid = any(n.startswith("visid_incap") for n in names)
        if not (has_reese and has_visid):
            log.warning(
                "FS cookie harvest may be incomplete — reese84=%s visid_incap_*=%s. "
                "Imperva-protected POSTs may 403.", has_reese, has_visid,
            )

        self._cookies_fresh = True
        log.info("Refreshed %d Imperva cookies from FS (reese84=%s, visid=%s)",
                 copied, has_reese, has_visid)
        # Observability — record this in the session_state row so /status
        # can show "cookies refreshed N minutes ago".
        _state().mark_cookies_refreshed(copied)
        return copied

    # ── auth ─────────────────────────────────────────────────────────────────

    def _adopt_manual_jwt(self) -> None:
        """Use the JWT pasted into COOLBET_MANUAL_JWT instead of calling
        /s/auth/login. user_id + login_session_id are extracted from the JWT
        payload itself (`sub` + `login_session_id`)."""
        token = self._manual_jwt
        payload = _decode_jwt_payload(token)
        self._jwt = token
        self._user_id = payload.get("sub") or ""
        self._login_session_id = payload.get("login_session_id") or ""
        self._jwt_exp = float(payload.get("exp", 0))
        # renewal_date is "YYYYMMDDHHMMSSffffff" UTC — Coolbet rejects with 401
        # "Token has expired" once past this, even though `exp` is later.
        # Parse to epoch seconds so _ensure_auth can renew proactively.
        self._jwt_renewal_ts = 0.0
        rd = payload.get("renewal_date") or ""
        if isinstance(rd, str) and len(rd) >= 14 and rd[:14].isdigit():
            try:
                self._jwt_renewal_ts = datetime(
                    int(rd[0:4]), int(rd[4:6]), int(rd[6:8]),
                    int(rd[8:10]), int(rd[10:12]), int(rd[12:14]),
                    tzinfo=timezone.utc,
                ).timestamp()
            except Exception:
                self._jwt_renewal_ts = 0.0
        ttl = self._jwt_exp - time.time()
        if ttl <= 0:
            raise RuntimeError(
                f"COOLBET_MANUAL_JWT is expired (exp={datetime.fromtimestamp(self._jwt_exp, tz=timezone.utc).isoformat()}). "
                "Paste a fresh `cbauth` Bearer from browser DevTools and restart."
            )
        log.info(
            "Using manual JWT — user=%s ttl=%.0fs (exp=%s)",
            self._user_id, ttl,
            datetime.fromtimestamp(self._jwt_exp, tz=timezone.utc).isoformat(),
        )
        # Observability: record this login event in the state row.
        _state().mark_login_success(
            method="manual_jwt",
            user_id=self._user_id,
            jwt_exp_at=datetime.fromtimestamp(self._jwt_exp, tz=timezone.utc),
            fs_url=os.getenv("FLARESOLVERR_URL"),
            fs_session_name=self._fs_session_name,
        )

    def renew_jwt_via_api(self) -> float:
        """Call POST /s/auth/renew-token to get a fresh JWT — no browser, no
        Smart-ID, no Imperva challenge. Coolbet's frontend uses this same
        endpoint every ~20 min while a user is browsing. Authenticated by
        the *current* (possibly soon-to-expire) JWT in `cbauth`.

        Updates self._jwt + writes the new JWT to .env so daemon restarts
        and the manual-JWT preflight check both see the latest. Returns the
        new TTL in seconds. Raises if renewal fails (401/403 = current JWT
        is dead, operator must Smart-ID again and paste a fresh manual JWT).
        """
        if not self._jwt:
            raise RuntimeError("No current JWT to renew — call _ensure_auth first")
        # Set auth headers directly — do NOT route through _ensure_auth, which
        # would refuse a JWT within the 120s TTL safety margin and call
        # _login() / _adopt_manual_jwt() which would re-raise "JWT expired".
        # The renewal endpoint accepts JWTs that are past the safety margin
        # as long as they're not server-side-rejected (Coolbet's grace window).
        if not self._cookies_fresh:
            self._refresh_cookies_from_fs()
        self._http.headers.update({
            "cbauth": f"Bearer {self._jwt}",
            "login_session_id": self._login_session_id or "",
            "user_id": self._user_id or "",
        })
        self._throttle()
        resp = self._http.post(_RENEW_URL, json={})
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"renew-token refused ({resp.status_code}): current JWT is "
                f"dead. Body: {resp.text[:200]}. Re-Smart-ID in browser, "
                f"paste fresh `cbauth` into COOLBET_MANUAL_JWT."
            )
        resp.raise_for_status()

        # Response shape unknown until we observe one — try common keys.
        # Coolbet's login endpoint returns `{token, loginSessionId}`-style
        # so we expect similar here. Fall back to scanning for JWT shape.
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        new_jwt = (
            data.get("token") or data.get("jwt")
            or data.get("accessToken") or data.get("access_token")
            or data.get("cbauth")
        )
        if not new_jwt:
            # Maybe response is bare string, or nested — scan stringified body
            body = resp.text or ""
            import re as _re
            m = _re.search(r"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", body)
            if m:
                new_jwt = m.group(1)
        if not new_jwt:
            raise RuntimeError(
                f"renew-token succeeded but no JWT in response. "
                f"Status={resp.status_code} body={resp.text[:300]}"
            )
        if new_jwt.startswith("Bearer "):
            new_jwt = new_jwt[7:]

        # Adopt the new JWT — populates self._jwt + recomputes _jwt_exp
        self._manual_jwt = new_jwt
        self._adopt_manual_jwt()

        # Persist to .env so daemon restart, preflight, and place_one_real_bet
        # all see the freshest token. Best-effort — if it fails we still have
        # the in-memory swap.
        # Update os.environ immediately so any CoolbetSession() created in the
        # same process after this renewal sees the fresh token (not just the
        # in-memory session that called renew). set_key writes the file but
        # doesn't touch os.environ — that gap is what caused the odds sweep to
        # keep failing after renewal.
        os.environ["COOLBET_MANUAL_JWT"] = new_jwt

        try:
            from dotenv import set_key as _set_key
            env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", ".env")
            env_file = os.path.normpath(env_file)
            if os.path.exists(env_file):
                _set_key(env_file, "COOLBET_MANUAL_JWT", new_jwt)
        except Exception as e:
            log.warning("Failed to persist renewed JWT to .env: %s", e)

        return self.jwt_seconds_remaining

    def reload_manual_jwt(self) -> float:
        """Re-read COOLBET_MANUAL_JWT from .env and adopt it without restart.
        Used by the daemon's headless-browser JWT refresher: refresher writes
        fresh JWT → calls this → session swaps token transparently.
        Returns the new TTL in seconds. Raises if no JWT in env or expired."""
        from dotenv import load_dotenv as _ld  # local import to dodge global cache
        _ld(override=True)
        fresh = os.getenv("COOLBET_MANUAL_JWT", "").strip()
        if fresh.startswith("Bearer "):
            fresh = fresh[7:]
        if not fresh:
            raise RuntimeError("COOLBET_MANUAL_JWT empty after reload — refresher likely failed.")
        self._manual_jwt = fresh
        self._adopt_manual_jwt()
        return self.jwt_seconds_remaining

    def _login(self) -> None:
        # Manual-JWT path is preferred when the env-pasted token is still
        # valid. If it's expired AND we have API credentials, fall through
        # to API login instead of raising — that's the self-heal behaviour
        # the operator wants (2026-06-11). The old "raise on expired manual
        # JWT" was a safety hatch from the era when API login was unreliable.
        if self._manual_jwt:
            try:
                self._adopt_manual_jwt()
                return
            except RuntimeError as e:
                if "expired" in str(e).lower() and self._email and self._password:
                    log.info("Manual JWT expired — falling through to API login")
                else:
                    raise

        log.info("Refreshing Coolbet JWT via /s/auth/login (plain-requests + FS cookies)...")
        if not self._cookies_fresh:
            self._refresh_cookies_from_fs()
        # Login does NOT carry cbauth (we're earning it). Strip any auth
        # headers that may have been merged from a prior call.
        for k in ("cbauth", "login_session_id", "user_id"):
            self._http.headers.pop(k, None)
        resp = self._http.post(_LOGIN_URL, json={
            "email": self._email,
            "password": self._password,
        })
        if resp.status_code == 403:
            raise RuntimeError(
                "Coolbet login blocked (403) — Imperva cookies likely expired. "
                "Re-login in your browser and update COOLBET_IMPERVA_COOKIES in .env."
            )
        if resp.status_code in (401, 423) or (
            resp.status_code == 400 and "banned" in resp.text.lower()
        ):
            raise RuntimeError(
                f"Coolbet API login refused ({resp.status_code}): {resp.text[:200]}. "
                "If you're on Smart-ID / 2FA, password login may be disabled — "
                "set COOLBET_MANUAL_JWT in .env (paste `cbauth` Bearer from browser) "
                "to bypass /s/auth/login entirely."
            )
        resp.raise_for_status()

        data = resp.json()
        log.debug("Login response keys: %s", list(data.keys()))

        # Coolbet returns the token in various possible field names; try all.
        token = (
            data.get("token")
            or data.get("jwt")
            or data.get("accessToken")
            or data.get("access_token")
        )
        if not token:
            raise RuntimeError(f"Login succeeded but no token in response: {data}")
        if token.startswith("Bearer "):
            token = token[7:]

        self._jwt = token
        self._login_session_id = (
            data.get("loginSessionId")
            or data.get("login_session_id")
        )

        payload = _decode_jwt_payload(token)
        self._user_id = (
            data.get("userId")
            or data.get("user_id")
            or payload.get("sub")
        )
        self._jwt_exp = float(payload.get("exp", time.time() + 1800))

        log.info(
            "JWT obtained — user=%s expires=%s",
            self._user_id,
            datetime.fromtimestamp(self._jwt_exp, tz=timezone.utc).isoformat(),
        )
        # Observability: record this api_login event.
        _state().mark_login_success(
            method="api_login",
            user_id=self._user_id,
            jwt_exp_at=datetime.fromtimestamp(self._jwt_exp, tz=timezone.utc),
            fs_url=os.getenv("FLARESOLVERR_URL"),
            fs_session_name=self._fs_session_name,
        )

    def _ensure_auth(self) -> None:
        if not self._require_auth:
            return  # anon-read mode: Imperva cookies only, no JWT needed
        if self._jwt is None or time.time() > self._jwt_exp - 120:
            self._login()
        # Coolbet rejects JWTs once past their `renewal_date` (~5–6 min after
        # issue) with 401 "Token has expired", even though `exp` is far out.
        # Pre-emptively renew when within 30s of renewal_date.
        rts = getattr(self, "_jwt_renewal_ts", 0.0) or 0.0
        if rts and time.time() > rts - 30 and self._manual_jwt:
            try:
                self.renew_jwt_via_api()
            except Exception as e:
                log.warning("Pre-emptive JWT renewal failed: %s — proceeding with current token", e)
        self._http.headers.update({
            "cbauth": f"Bearer {self._jwt}",
            "login_session_id": self._login_session_id or "",
            "user_id": self._user_id or "",
        })

    def _throttle(self) -> None:
        """Sleep so the next request lands at a humanly-paced gap after the
        previous one. Adds jitter so consecutive calls aren't perfectly
        periodic (a periodic pattern is itself a scraper signature).
        Lock ensures sweep thread + main thread sharing one session can't
        both pass the gap check simultaneously and fire concurrent requests."""
        with self._throttle_lock:
            target_gap = random.uniform(self._min_call_gap, self._max_call_gap)
            elapsed = time.time() - self._last_call_t
            if elapsed < target_gap:
                time.sleep(target_gap - elapsed)
            self._last_call_t = time.time()

    # ── FlareSolverr-routed transport ────────────────────────────────────────

    def _build_auth_headers(self, extra: dict | None = None) -> dict:
        """Auth + browser-sim headers for any FS-routed request. Includes the
        cbauth Bearer + login_session_id + user_id when in auth mode, plus the
        sec-* + accept fluff that distinguishes Chrome XHRs from naked fetches."""
        h = {
            "accept": "*/*",
            "accept-language": "en-GB,en-US;q=0.9",
            "content-type": "application/json; charset=utf-8",
            "x-device": "DESKTOP",
            "origin": "https://www.coolbet.com",
            "referer": "https://www.coolbet.com/en/sports/football",
        }
        if self._require_auth and self._jwt:
            h["cbauth"] = f"Bearer {self._jwt}"
            h["login_session_id"] = self._login_session_id or ""
            h["user_id"] = self._user_id or ""
        if extra:
            h.update(extra)
        return h

    def _fs_get(self, url: str, *, headers: dict | None = None,
                params: dict | None = None) -> _FSResponse:
        """GET via FlareSolverr's named browser session. URL-encodes params
        into the URL string (FS request.get doesn't accept a separate params
        field). Returns _FSResponse with a requests-shaped interface."""
        if params:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(params)}"
        body = {
            "cmd": "request.get",
            "url": url,
            "session": self._fs_session_name,
            "maxTimeout": _FS_TIMEOUT_MS,
        }
        if headers:
            body["headers"] = headers
        return _FSResponse(_fs_call(body))

    def _fs_post(self, url: str, *, headers: dict | None = None,
                 json_body: dict | None = None,
                 raw_body: str | None = None) -> _FSResponse:
        """POST via FlareSolverr. json_body wins over raw_body when both set.
        FS expects postData as a URL-encoded string or a JSON-stringified blob
        depending on Content-Type — we pass JSON-stringified and rely on the
        headers (set by _build_auth_headers) to declare content-type."""
        body = {
            "cmd": "request.post",
            "url": url,
            "session": self._fs_session_name,
            "maxTimeout": _FS_TIMEOUT_MS,
        }
        if json_body is not None:
            body["postData"] = json.dumps(json_body)
        elif raw_body is not None:
            body["postData"] = raw_body
        if headers:
            body["headers"] = headers
        return _FSResponse(_fs_call(body))

    # ── public request helpers ────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> _FSResponse:
        """GET via FlareSolverr-routed Chrome. Drop-in compatible with the
        previous requests.Session-backed behaviour — returns a _FSResponse
        with .status_code/.text/.json()/.ok like requests.Response."""
        self._ensure_auth()
        self._throttle()
        headers = self._build_auth_headers(kwargs.pop("headers", None))
        params = kwargs.pop("params", None)
        return self._fs_get(url, headers=headers, params=params)

    def post(self, url: str, **kwargs) -> requests.Response:
        """POST via plain requests.Session() WITH cookies harvested from FS.

        Why not FlareSolverr: FS force-encodes POST bodies as application/x-www-
        form-urlencoded and truncates JSON to ~4 bytes (verified via httpbin
        echo 2026-06-11). Coolbet's APIs require proper JSON bodies. The
        cookies we get from a FS GET pass Imperva's challenge, and Imperva
        validates the SAME cookies + UA on subsequent POSTs — so plain
        requests with those cookies sails through.

        Auto-retries once on 401/403 after a fresh cookie harvest (Imperva
        cookies expire fast — typically minutes to hours)."""
        self._ensure_auth()
        if not self._cookies_fresh:
            self._refresh_cookies_from_fs()
        self._throttle()

        # _ensure_auth already merged cbauth/login_session_id/user_id into
        # self._http.headers. kwargs.pop("headers") layers additional ones.
        resp = self._http.post(url, **kwargs)
        if resp.status_code in (401, 403):
            # Refresh cookies + retry ONCE. If still 401/403, surface the
            # error to caller — likely JWT expired (caller should renew) or
            # Coolbet upstream issue.
            log.info("Coolbet POST got %d — refreshing FS cookies and retrying once",
                      resp.status_code)
            self._cookies_fresh = False
            self._refresh_cookies_from_fs()
            self._throttle()
            resp = self._http.post(url, **kwargs)
        return resp

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def jwt_seconds_remaining(self) -> float:
        """Seconds until the JWT expires. Negative if expired / no JWT yet.
        Coolbet issues 30-min JWTs; renewal_date sits at the 20-min mark."""
        if self._jwt is None:
            return -1.0
        return self._jwt_exp - time.time()

    def keep_alive(self) -> bool:
        """Heartbeat — pings Coolbet's casino-maintenance status endpoint.

        KEEPALIVE-FS-HEADER-STRIP-FIX (2026-06-11): goes via PLAIN requests,
        not session.get(). Reason: FlareSolverr v2 silently strips the
        `headers` parameter from request bodies (confirmed in FS Railway
        logs — `WARNING Request parameter 'headers' was removed in
        FlareSolverr v2.`). So FS-routed auth-required GETs lose their
        cbauth Bearer + login_session_id headers and Coolbet returns 401.
        The bug surfaced as "heartbeat: FAIL" in /status even though bet
        placement (which uses plain requests) was succeeding.

        Plain requests with FS-harvested cookies passes Imperva (cookies
        are real, just-issued) and carries our auth headers properly.
        Same transport pattern as `.post()` uses for bet placement.

        /s/casino/fo/maintenance is the exact endpoint Coolbet's frontend
        hits every 5 min while browsing, so traffic looks normal.
        Returns True if either probe succeeds.
        """
        # Make sure auth + cookies are ready before any plain-requests call.
        try:
            self._ensure_auth()
            if not self._cookies_fresh:
                self._refresh_cookies_from_fs()
        except Exception as e:
            log.warning("keep_alive: auth/cookies refresh failed: %s", e)
            return False

        self._throttle()

        # 1) /s/casino/fo/maintenance — what the browser pings every 5 min.
        try:
            resp = self._http.get(
                "https://www.coolbet.com/s/casino/fo/maintenance",
                params={"licence": "EE"},
                timeout=15,
            )
            if resp.status_code == 200:
                return True
            log.debug("keep_alive: maintenance %d, trying fo-category fallback", resp.status_code)
        except Exception as e:
            log.debug("keep_alive: maintenance raised %s, trying fo-category fallback", e)

        # 2) Fallback: fo-category (heavier but production-tested).
        try:
            self._throttle()
            resp = self._http.get(
                "https://www.coolbet.com/s/sbgate/sports/fo-category/",
                params={"categoryId": 18975, "country": "EE", "isMobile": 0,
                        "language": "et", "layout": "EUROPEAN", "limit": 6},
                headers={"referer": "https://www.coolbet.com/et/sport/jalgpall/inglismaa/meistriliiga"},
                timeout=15,
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning("Coolbet keep_alive failed (both endpoints): %s", e)
            return False


_SEARCH_URL = "https://www.coolbet.com/s/sbgate/sports/search/v2"


def coolbet_match_url(home: str, away: str) -> str | None:
    """Search Coolbet for a match and return its URL. Returns None on any failure.

    Uses Imperva cookies from env — no JWT required. Safe to call from any
    context; failures are swallowed so a missing/expired cookie never breaks
    the caller.
    """
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            "x-device": "DESKTOP",
            "accept": "*/*",
            "sec-fetch-site": "same-origin",
            "sec-fetch-mode": "cors",
            "referer": "https://www.coolbet.com/et/sport/recommendations",
            "origin": "https://www.coolbet.com",
        })
        # Prefer split env vars (same as CoolbetSession.__init__); fall back to
        # the legacy combined COOLBET_IMPERVA_COOKIES string.
        imperva = {
            "reese84":                 os.getenv("COOLBET_COOKIE_REESE84", ""),
            "visid_incap_723517":      os.getenv("COOLBET_COOKIE_VISID_INCAP", ""),
            "nlbi_723517":             os.getenv("COOLBET_COOKIE_NLBI", ""),
            "nlbi_723517_2147483392":  os.getenv("COOLBET_COOKIE_NLBI2", ""),
            "incap_ses_1099_723517":   os.getenv("COOLBET_COOKIE_INCAP_SES", ""),
        }
        individual = {k: v for k, v in imperva.items() if v}
        if individual:
            for name, value in individual.items():
                session.cookies.set(name, value, domain="www.coolbet.com")
        else:
            raw = os.getenv("COOLBET_IMPERVA_COOKIES", "")
            if raw:
                for part in raw.split(";"):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        session.cookies.set(k.strip(), v.strip(), domain="www.coolbet.com")

        resp = session.get(
            _SEARCH_URL,
            params={"country": "EE", "language": "et", "layout": "EUROPEAN",
                    "search": home.split()[0]},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        target = f"{home} - {away}".lower()
        best_id, best_score = None, 0
        for ev in resp.json():
            if ev.get("sport_icon") != "football":
                continue
            score = fuzz.token_set_ratio(target, (ev.get("name") or "").lower())
            if score > best_score:
                best_score, best_id = score, ev.get("id")
        if best_id and best_score >= 60:
            return f"https://www.coolbet.com/et/sport/match/{best_id}"
    except Exception as e:
        log.debug("coolbet_match_url failed: %s", e)
    return None
