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


def _jwt_exp_or_zero(token: str | None) -> float:
    """Decode `exp` claim safely. Returns 0.0 for anything unparseable so
    _pick_freshest_jwt treats missing/malformed tokens as fully expired
    without raising."""
    if not token:
        return 0.0
    try:
        return float(_decode_jwt_payload(token).get("exp", 0))
    except Exception:
        return 0.0


def _pick_freshest_jwt(*candidates: str | None) -> str:
    """Pick the JWT with the latest `exp` claim out of the given candidates.
    Returns "" when all are empty/expired.

    Used by CoolbetSession.__init__ to pick between env-pasted JWT and the
    DB-persisted JWT on bootstrap. Whichever has the later expiry wins —
    typically DB after the first renewal, env on cold boot before DB is
    populated. An expired token is still returned if it's the latest one
    available, so _adopt_manual_jwt's expiry guard runs (and self-heal
    falls through to API login)."""
    best: str = ""
    best_exp: float = -1.0
    for c in candidates:
        if not c:
            continue
        exp = _jwt_exp_or_zero(c)
        if exp > best_exp:
            best = c
            best_exp = exp
    return best


# State writer — local import inside the methods that need it so module
# load doesn't trigger workers.api_clients.db connection.
def _state():
    from workers.automation import coolbet_state as _s
    return _s


def _load_fresh_imperva_cookies_from_db(*, max_age_hours: float = 2.0) -> dict[str, str] | None:
    """COOLBET-CDP-COOKIE-EXPORT (2026-07-08): read Imperva cookies that
    the Mac daemon harvests from CDP-Chrome every ~30 min. Returns the
    cookie dict if the snapshot is fresher than `max_age_hours`, else
    None so the caller falls back to env cookies.

    Silent-fail: any DB error returns None. Called at CoolbetSession
    __init__ time — DB flakiness must not block session construction.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT imperva_cookies_json,
                      EXTRACT(EPOCH FROM (NOW() - imperva_cookies_refreshed_at))
                        AS age_s
                 FROM coolbet_session_state WHERE id = 1"""
        )
    except Exception as e:
        log.debug("Imperva cookie DB read failed (falling back to env): %s", e)
        return None
    if not rows:
        return None
    row = rows[0]
    payload = row.get("imperva_cookies_json") or None
    age_s = row.get("age_s")
    if payload is None or age_s is None:
        return None
    if float(age_s) > max_age_hours * 3600:
        log.info("Imperva cookies in DB are %.1fh stale (> %.1fh) — using env fallback",
                 float(age_s) / 3600, max_age_hours)
        return None
    # Strip metadata keys before returning — only the actual cookie names.
    return {k: v for k, v in payload.items() if not k.startswith("_") and v}


# ── FlareSolverr proxy ───────────────────────────────────────────────────────
# Every call from this module to Coolbet goes through FS's named browser
# session (default: coolbet_prod) so Imperva sees real-Chrome TLS + headers.


def _fs_call(body: dict, *, timeout_s: int = 90) -> dict:
    """Low-level FlareSolverr proxy call. Raises if FLARESOLVERR_URL unset
    or the FS instance is unreachable.

    Returns the parsed JSON envelope: { status: 'ok'|'error', solution: {...},
    message, version, startTimestamp, endTimestamp }.

    COOLBET_FS_LOCAL_URL (2026-06-26): Coolbet-specific override. When set
    AND reachable, used in preference to FLARESOLVERR_URL — covers ad-hoc
    Mac CLI runs (smoke tests, manual placer dry-runs) that otherwise pick
    up a stale remote URL from .env and 500. Mac-only opt-in by being set;
    VPS-hosted callers don't see it.

    DEFENSIVE-FS-URL (2026-06-17): legacy fallback for the Mac daemon path
    — if FLARESOLVERR_URL points to a remote host AND COOLBET_MAC_POLL_S
    is set (daemon-only signal from launchd plist), prefer localhost too.
    Retained for safety; COOLBET_FS_LOCAL_URL supersedes it when set."""
    raw_fs_url = os.getenv("FLARESOLVERR_URL") or _FS_URL_DEFAULT
    local_override = os.getenv("COOLBET_FS_LOCAL_URL")
    if local_override:
        try:
            import urllib.request as _u
            with _u.urlopen(local_override.rstrip("/") + "/", timeout=2) as r:
                if r.status == 200:
                    raw_fs_url = local_override
        except Exception:
            pass  # fall through to FLARESOLVERR_URL if local unreachable
    elif (raw_fs_url
            and "localhost" not in raw_fs_url
            and "127.0.0.1" not in raw_fs_url
            and os.getenv("COOLBET_MAC_POLL_S")):
        # Mac-daemon path. Cheap reachability check — fail-closed.
        try:
            import urllib.request as _u
            with _u.urlopen(_FS_URL_DEFAULT + "/", timeout=2) as r:
                if r.status == 200:
                    raw_fs_url = _FS_URL_DEFAULT
        except Exception:
            pass

    fs_url = raw_fs_url.rstrip("/")
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

    def __init__(self, *, require_auth: bool = True,
                 allow_api_login: bool | None = None):
        self._require_auth = require_auth

        # COOLBET-NO-AUTO-LOGIN (2026-06-12): /s/auth/login triggers SMS 2FA
        # every call from any IP that hasn't been device-trusted in the
        # current browser session — this is how an expired-JWT scenario
        # spammed 100+ SMS overnight (heartbeat cron retrying every ~5min
        # × 9hrs). Default: refuse API login. The ONLY path that should
        # mint a fresh JWT is scripts/coolbet/flaresolverr_login_enroll.py
        # (which talks to Coolbet directly, not via CoolbetSession). All
        # other contexts — heartbeat, placement, scanner — read DB JWT and
        # fail fast if it's expired, so the operator gets a clean
        # "re-enroll" signal instead of a midnight SMS storm.
        if allow_api_login is None:
            allow_api_login = os.getenv("COOLBET_ALLOW_API_LOGIN", "").lower() in (
                "true", "1", "yes",
            )
        self._allow_api_login = allow_api_login
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

        # D2 (2026-06-16): COOLBET_MANUAL_JWT env-bootstrap path retired.
        # The DB-backed jwt_current (COOLBET-JWT-DB-BACKED 2026-06-12) is
        # the single source of truth. The env var was the root cause of
        # the 2026-06-15/16 incident: env JWT expired 15:54 UTC the day
        # before, _pick_freshest_jwt(env, db) returned the stale env value
        # rather than failing loudly, and the daemon looped on expired-
        # JWT errors for 24h+. Removing the env read forces the system
        # to always read the freshest DB value AND surfaces the
        # "DB has no JWT" state honestly instead of masking with stale env.
        db_jwt: str | None = None
        try:
            from workers.automation.coolbet_state import read_persisted_jwt
            db_jwt, _ = read_persisted_jwt()
        except Exception:
            db_jwt = None
        self._manual_jwt = db_jwt

        if require_auth and not self._manual_jwt and (not self._email or not self._password):
            raise RuntimeError(
                "Auth misconfigured: no JWT in coolbet_session_state. "
                "Run `python3 -m workers.automation.coolbet_browser_sync --full-heal` "
                "(daemon-side, with CDP-Chrome logged into Coolbet) OR "
                "`python3 scripts/coolbet/flaresolverr_login_enroll.py start` "
                "for SMS-based cold-start enrollment."
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

        # ANON-READ-NO-FS (2026-06-25): when COOLBET_NO_FS=true, bootstrap
        # cookies from env vars + skip FS refresh entirely. Used for public-
        # read scanners (e.g. scripts/tennis/place_coolbet_tennis.py) that
        # only need Imperva clearance, never JWT auth. Also lets the scanner
        # run on environments without a FlareSolverr instance (local dev,
        # VPS scheduler) — production Coolbet daemon on the Mac keeps
        # using FS as today. require_auth=True implicitly disables this
        # since JWT-bearing calls need fresh FS cookies to look authentic.
        #
        # COOLBET-CDP-COOKIE-EXPORT (2026-07-08): NO_FS mode now prefers
        # DB cookies (harvested from CDP-Chrome every 30 min by the Mac
        # daemon) over the static env-var cookies. Env cookies stale as
        # Imperva rotates; DB cookies stay fresh as long as the daemon
        # tick lands. Fallback order: DB (if refreshed within 2h) → env.
        self._no_fs = (
            not require_auth
            and os.getenv("COOLBET_NO_FS", "").lower() in ("1", "true", "yes")
        )
        if self._no_fs:
            db_cookies = _load_fresh_imperva_cookies_from_db(max_age_hours=2.0)
            source_cookies = db_cookies if db_cookies else self._imperva_cookies_individual
            _cookie_source_label = "db" if db_cookies else "env"
            for name, value in source_cookies.items():
                if value:
                    self._http.cookies.set(name, value, domain="www.coolbet.com")
            # Match the User-Agent Imperva fingerprinted when the env cookie
            # was minted. Standard Chrome desktop UA works for the cookies
            # the operator ships in .env from a real browser session.
            self._http.headers["User-Agent"] = os.getenv(
                "COOLBET_NO_FS_UA",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
            )
            self._cookies_fresh = True
            log.info("CoolbetSession NO_FS mode — %d cookies from %s",
                     sum(1 for v in source_cookies.values() if v),
                     _cookie_source_label)

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

        ANON-READ-NO-FS (2026-06-25): no-op when COOLBET_NO_FS=true.
        Env-supplied cookies were loaded at __init__ time; we trust they're
        valid and don't try to refresh. A 401/403 retry path will still
        attempt a refresh, but it'll hit this no-op and surface the error.
        """
        if self._no_fs:
            return len(self._http.cookies)
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
                f"Persisted JWT is expired (exp={datetime.fromtimestamp(self._jwt_exp, tz=timezone.utc).isoformat()}). "
                "Run `--full-heal` to attempt auto-recovery, or "
                "`flaresolverr_login_enroll.py start` for SMS cold-start."
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
        # Persist to DB so a process on the other side (the VPS when this
        # ran locally, or vice versa) can bootstrap from the same token
        # without an env-var sync.
        try:
            _state().persist_jwt(token, login_session_id=self._login_session_id,
                                  set_by="adopt_manual_jwt")
        except Exception as e:
            log.warning("persist_jwt after adopt failed (non-fatal): %s", e)

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

        # Adopt the new JWT — populates self._jwt + recomputes _jwt_exp.
        # _adopt_manual_jwt persists to DB (the canonical store). The
        # env+.env writebacks that used to live here were retired in D2
        # (2026-06-16) along with the env READ on bootstrap — DB is the
        # only source of truth now, no second path to drift from.
        self._manual_jwt = new_jwt
        self._adopt_manual_jwt()

        return self.jwt_seconds_remaining

    def _try_cdp_jwt(self) -> bool:
        """Pull a fresh JWT from CDP-Chrome's localStorage and adopt it.
        Returns True on success.

        Why this is the preferred refresh path (CDP-JWT-EXTRACT, 2026-06-12):
        CDP-Chrome's logged-in tab auto-renews via /s/auth/renew-token every
        ~20 min, so localStorage['cbauth'] is always fresh. Reading it
        replaces both stale env-paste AND SMS-triggering /s/auth/login.

        Tried before the SMS-blocked fallback in _login(). Silently no-ops
        on the VPS / any env without a CDP-Chrome reachable at
        COOLBET_CHROME_CDP_URL — the operator-side daemon is the only
        process where this path is meaningful."""
        try:
            from workers.automation.coolbet_browser_sync import extract_jwt_from_cdp
        except Exception as e:
            # INFO-level — silent on the VPS (no CDP-Chrome there) but
            # surfaces import failures locally so we can diagnose.
            log.info("CDP JWT helper unavailable (likely the VPS env): %s", e)
            return False
        try:
            cdp_jwt = extract_jwt_from_cdp(allow_open_new_tab=False)
        except Exception as e:
            # WARNING-level — an exception here means the CDP path is
            # mis-wired in a way the operator should see, not silenced.
            log.warning("CDP JWT extract raised: %s", e)
            return False
        if not cdp_jwt:
            log.info("CDP JWT extract returned None (no Coolbet tab, "
                      "no valid JWT in localStorage, or CDP unreachable).")
            return False
        try:
            self._manual_jwt = cdp_jwt
            self._adopt_manual_jwt()  # decodes, validates, persists to DB
            return True
        except Exception as e:
            log.warning("CDP JWT adopt failed: %s", e)
            return False

    def _login(self) -> None:
        # Preferred path: DB/env JWT is still valid → adopt and return.
        # If expired, try CDP-Chrome localStorage (auto-fresh, no SMS).
        # Only if that fails AND allow_api_login=True do we POST
        # /s/auth/login — which triggers SMS 2FA every time.
        if self._manual_jwt:
            try:
                self._adopt_manual_jwt()
                return
            except RuntimeError as e:
                if "expired" not in str(e).lower():
                    raise
                # CDP-Chrome path — silent self-heal when operator's
                # Chrome window is up.
                if self._try_cdp_jwt():
                    log.info("Adopted fresh JWT from CDP-Chrome (self-heal).")
                    return
                if not self._allow_api_login:
                    _state().mark_error(
                        "JWT expired, CDP refresh unavailable, api_login disabled — run --refresh-jwt or enrollment"
                    )
                    raise RuntimeError(
                        "Coolbet JWT expired AND CDP-Chrome JWT refresh "
                        "unavailable (Chrome not running / no coolbet.com "
                        "tab open / Chrome logged out) AND api_login is "
                        "disabled to prevent SMS-2FA spam. Either: open "
                        "CDP-Chrome with a coolbet.com tab + run "
                        "`python3 -m workers.automation.coolbet_browser_sync --refresh-jwt` "
                        "OR (last resort) run "
                        "`python3 scripts/coolbet/flaresolverr_login_enroll.py start` "
                        "to enrol via SMS."
                    )
                if not (self._email and self._password):
                    raise
                log.warning("Manual JWT expired, CDP refresh unavailable, allow_api_login=True — calling /s/auth/login (may trigger SMS)")

        # No JWT in DB/env at all. Same precedence: CDP first, SMS path
        # only with explicit opt-in.
        if self._try_cdp_jwt():
            log.info("Bootstrapped JWT from CDP-Chrome (no prior token).")
            return

        if not self._allow_api_login:
            _state().mark_error(
                "No JWT, CDP refresh unavailable, api_login disabled — run --refresh-jwt or enrollment"
            )
            raise RuntimeError(
                "No Coolbet JWT available (DB jwt_current is NULL, env "
                "COOLBET_MANUAL_JWT empty), CDP-Chrome JWT refresh "
                "unavailable, AND api_login is disabled to prevent SMS-2FA "
                "spam. Open CDP-Chrome with a coolbet.com tab + run "
                "`python3 -m workers.automation.coolbet_browser_sync --refresh-jwt`."
            )

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
            # Imperva 403's /s/auth/login from cloud IPs (the VPS) even when
            # the same code + cookies succeed from a residential IP. The
            # self-heal answer is to run the one-time enrollment locally
            # — that persists a fresh JWT to coolbet_session_state.jwt_current
            # in DB which this process will pick up on next CoolbetSession()
            # construction (or on the next placement run that constructs one).
            raise RuntimeError(
                "Coolbet login blocked (403) — Imperva blocks /s/auth/login from "
                "this IP (typical for cloud datacenter origins like the VPS). "
                "Run `python3 scripts/coolbet/flaresolverr_login_enroll.py start` "
                "from a residential IP one time; the fresh JWT lands in "
                "coolbet_session_state.jwt_current and this process will inherit "
                "it on next CoolbetSession() construction. After that, "
                "/s/auth/renew-token keeps it alive forever (accepted from any IP)."
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
        # Persist so the OTHER side (the VPS after a local bootstrap, or
        # local after a the VPS renewal — wherever the password login
        # actually worked) can pick up this token from DB.
        try:
            _state().persist_jwt(token, login_session_id=self._login_session_id,
                                  set_by="api_login")
        except Exception as e:
            log.warning("persist_jwt after api_login failed (non-fatal): %s", e)

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
        with .status_code/.text/.json()/.ok like requests.Response.

        ANON-READ-NO-FS (2026-06-25): when COOLBET_NO_FS=true, route through
        plain requests with the env cookies loaded at init. Public-read
        scanners (tennis fixture/odds lookups) work this way; only the
        placement / JWT-authenticated paths require FS-fingerprinted Chrome.
        """
        self._ensure_auth()
        self._throttle()
        headers = self._build_auth_headers(kwargs.pop("headers", None))
        params = kwargs.pop("params", None)
        if self._no_fs:
            resp = self._http.get(url, params=params, headers=headers, **kwargs)
            return _FSResponse({
                "solution": {
                    "status": resp.status_code,
                    "response": resp.text,
                    "headers": dict(resp.headers),
                }
            })
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

        # COOLBET-POST-TIMEOUT (2026-07-05): Imperva sometimes tarpits
        # the connection instead of returning a status code — the plain
        # requests.Session inherits no default timeout, so `_http.post`
        # can block forever. On 2026-07-04/05 this hung the cs2 scanner
        # for 4+ min per fire, blocking the launchd slot. Default to 30s
        # unless the caller explicitly sets its own timeout.
        kwargs.setdefault("timeout", 30)

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
        `headers` parameter from request bodies (confirmed in FS the VPS
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
