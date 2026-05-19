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
        # Falls back to the legacy combined COOLBET_IMPERVA_COOKIES string
        self._imperva_cookies_individual = {
            "reese84":                 os.getenv("COOLBET_COOKIE_REESE84", ""),
            "visid_incap_723517":      os.getenv("COOLBET_COOKIE_VISID_INCAP", ""),
            "nlbi_723517":             os.getenv("COOLBET_COOKIE_NLBI", ""),
            "nlbi_723517_2147483392":  os.getenv("COOLBET_COOKIE_NLBI2", ""),
            "incap_ses_1099_723517":   os.getenv("COOLBET_COOKIE_INCAP_SES", ""),
        }
        self._imperva_cookies_raw = os.getenv("COOLBET_IMPERVA_COOKIES", "")

        if not self._email or not self._password:
            raise RuntimeError("COOLBET_USER and COOLBET_PASS must be set in .env")

        self._jwt: str | None = None
        self._jwt_exp: float = 0.0
        self._login_session_id: str | None = None
        self._user_id: str | None = None

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

    def _login(self) -> None:
        log.info("Refreshing Coolbet JWT...")
        resp = self._http.post(_LOGIN_URL, json={
            "email": self._email,
            "password": self._password,
        })
        if resp.status_code == 403:
            raise RuntimeError(
                "Coolbet login blocked (403) — Imperva cookies likely expired. "
                "Re-login in your browser and update COOLBET_IMPERVA_COOKIES in .env."
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

    # ── public request helpers ────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> requests.Response:
        self._ensure_auth()
        return self._http.get(url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        self._ensure_auth()
        return self._http.post(url, **kwargs)

    @property
    def user_id(self) -> str | None:
        return self._user_id
