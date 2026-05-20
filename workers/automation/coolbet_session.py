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
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

_LOGIN_URL = "https://www.coolbet.com/s/auth/login"
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


class CoolbetSession:
    """Thread-safe(ish) Coolbet API session with auto JWT refresh."""

    def __init__(self):
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

        if not self._manual_jwt and (not self._email or not self._password):
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

        self._http = requests.Session()
        self._http.headers.update(_HEADERS_BASE)
        self._apply_imperva_cookies()

    # ── setup ────────────────────────────────────────────────────────────────

    def _apply_imperva_cookies(self) -> None:
        # Individual vars take priority; fall back to combined string
        individual = {k: v for k, v in self._imperva_cookies_individual.items() if v}
        if individual:
            for name, value in individual.items():
                self._http.cookies.set(name, value, domain="www.coolbet.com")
            return
        if not self._imperva_cookies_raw:
            log.warning("No Imperva cookies set — Imperva may block requests")
            return
        for part in self._imperva_cookies_raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                self._http.cookies.set(k.strip(), v.strip(), domain="www.coolbet.com")

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

    def _login(self) -> None:
        # Manual-JWT path takes priority when configured.
        if self._manual_jwt:
            self._adopt_manual_jwt()
            return

        log.info("Refreshing Coolbet JWT via /s/auth/login...")
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

    def _ensure_auth(self) -> None:
        if self._jwt is None or time.time() > self._jwt_exp - 120:
            self._login()
        self._http.headers.update({
            "cbauth": f"Bearer {self._jwt}",
            "login_session_id": self._login_session_id or "",
            "user_id": self._user_id or "",
        })

    def _throttle(self) -> None:
        """Sleep so the next request lands at a humanly-paced gap after the
        previous one. Adds jitter so consecutive calls aren't perfectly
        periodic (a periodic pattern is itself a scraper signature)."""
        target_gap = random.uniform(self._min_call_gap, self._max_call_gap)
        elapsed = time.time() - self._last_call_t
        if elapsed < target_gap:
            time.sleep(target_gap - elapsed)
        self._last_call_t = time.time()

    # ── public request helpers ────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> requests.Response:
        self._ensure_auth()
        self._throttle()
        return self._http.get(url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        self._ensure_auth()
        self._throttle()
        return self._http.post(url, **kwargs)

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
        """Heartbeat — does a lightweight authenticated GET that updates the
        server-side session's last-activity timestamp, preventing the
        idle-logout that fires after ~20-30 min of no traffic.

        KEEPALIVE-RESILIENCE (2026-05-20): tries two endpoints in order so
        a temporary Imperva block on one doesn't fail the whole heartbeat.
        Returns True if either succeeds.

        Both endpoints are real production calls that the daemon uses
        anyway — heartbeat just ensures we exercise them periodically.
        """
        # 1) fo-category for English Premier League (18975) — heavy use endpoint
        #    with the browser-matching params + slug referer.
        try:
            resp = self.get(
                "https://www.coolbet.com/s/sbgate/sports/fo-category/",
                params={"categoryId": 18975, "country": "EE", "isMobile": 0,
                        "language": "et", "layout": "EUROPEAN", "limit": 6},
                headers={"referer": "https://www.coolbet.com/et/sport/jalgpall/inglismaa/meistriliiga"},
            )
            if resp.status_code == 200:
                return True
            log.debug("keep_alive: fo-category %d, trying search fallback", resp.status_code)
        except Exception as e:
            log.debug("keep_alive: fo-category raised %s, trying search fallback", e)

        # 2) Fallback: search/v2 (lighter but more Imperva-sensitive)
        try:
            resp = self.get(
                "https://www.coolbet.com/s/sbgate/sports/search/v2",
                params={"search": "a", "country": "EE", "language": "en",
                        "layout": "EUROPEAN"},
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning("Coolbet keep_alive failed (both endpoints): %s", e)
            return False
