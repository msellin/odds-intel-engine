#!/usr/bin/env python3
"""
Phase 1b — One-time SMS-trust enrollment for the FlareSolverr Chrome
session against Coolbet. Run twice:

  1) `start`    — triggers Coolbet to send the SMS code, saves the codeId
                  to /tmp/coolbet_2fa_state.json, leaves the FlareSolverr
                  session "coolbet_dev" alive so the same Chrome instance
                  completes verification.
  2) `verify N` — submits the 6-digit code (paste it as arg N), confirms
                  trust, saves the fresh JWT to .env (COOLBET_MANUAL_JWT)
                  and destroys the FlareSolverr session on exit.

Hard rules:
  * Never hits /s/bets/* (placement) endpoints.
  * Redacts JWT / cookie values from anything printed to stdout.
  * Bails on CAPTCHA / lockout / unexpected response shapes.

Run:
  FLARESOLVERR_URL=https://flaresolverr-cf-production.up.railway.app \\
  python3 scripts/coolbet/flaresolverr_login_enroll.py start

  # ... wait for SMS, paste code in chat ...

  FLARESOLVERR_URL=https://flaresolverr-cf-production.up.railway.app \\
  python3 scripts/coolbet/flaresolverr_login_enroll.py verify 123456
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

FLARESOLVERR_URL = (os.getenv("FLARESOLVERR_URL") or "").rstrip("/")
COOLBET_USER = os.getenv("COOLBET_USER") or os.getenv("COOLBET_EMAIL") or ""
COOLBET_PASS = os.getenv("COOLBET_PASS") or os.getenv("COOLBET_PASSWORD") or ""
SESSION_NAME = os.getenv("COOLBET_FLARE_SESSION", "coolbet_dev")
STATE_FILE = Path("/tmp/coolbet_2fa_state.json")

LOGIN_API = "https://www.coolbet.com/s/auth/login"
HOMEPAGE = "https://www.coolbet.com/en/"

# Found via probe (only one that 400's instead of 404'ing).
VERIFY_CANDIDATES = [
    "https://www.coolbet.com/s/auth/2fa/verify",
    # Some XHR-auth APIs re-use the login endpoint with extra fields.
    "https://www.coolbet.com/s/auth/login",
    "https://www.coolbet.com/s/auth/sms-verify",
    "https://www.coolbet.com/s/auth/login-2fa",
]

DEFAULT_FS_TIMEOUT_MS = 90_000


def _redact(s: str | None, keep: int = 4) -> str:
    if not s:
        return "<empty>"
    return f"<{len(s)}ch:{s[:keep]}…>"


def _scrub(body: str) -> str:
    body = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "<JWT-redacted>", body)
    body = re.sub(r"\"(token|jwt|accessToken|cbauth)\"\s*:\s*\"[^\"]+\"", r'"\1":"<redacted>"', body)
    return body


def _fs_call(body: dict, *, timeout_s: int = 120) -> dict:
    if not FLARESOLVERR_URL:
        raise RuntimeError("FLARESOLVERR_URL is unset.")
    req = urllib.request.Request(
        f"{FLARESOLVERR_URL}/v1",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def fs_session_create(name: str) -> bool:
    data = _fs_call({"cmd": "sessions.create", "session": name})
    return data.get("status") == "ok"


def fs_session_destroy(name: str) -> None:
    try:
        _fs_call({"cmd": "sessions.destroy", "session": name}, timeout_s=15)
    except Exception as e:
        print(f"  [fs] destroy({name}) error (ignored): {e}")


def fs_get(url: str, *, session: str) -> dict:
    data = _fs_call(
        {"cmd": "request.get", "url": url, "session": session, "maxTimeout": DEFAULT_FS_TIMEOUT_MS},
        timeout_s=DEFAULT_FS_TIMEOUT_MS // 1000 + 30,
    )
    if data.get("status") != "ok":
        print(f"  [fs] GET {url[:60]} non-ok: {data.get('message')}")
        return {}
    return data.get("solution") or {}


def harvest_session(session_name: str) -> tuple[dict, str]:
    """Warm the FlareSolverr session and return (cookies_dict, user_agent).

    Two-step warmup: homepage first, then a deeper Imperva-gated page
    (sports football). Empirically Coolbet's homepage often doesn't yield
    `reese84` on a fresh Chrome — but loading any Sports page does, because
    that's where Imperva's "real challenge" sits. We need reese84 for the
    XHR login POST or Imperva 403's us with its NOINDEX boilerplate.
    """
    warmup_urls = [
        HOMEPAGE,
        "https://www.coolbet.com/en/sports/football",
        "https://www.coolbet.com/en/sports/esports",
    ]
    cookies: dict[str, str] = {}
    ua = ""
    for url in warmup_urls:
        sol = fs_get(url, session=session_name)
        if not sol:
            continue
        for c in (sol.get("cookies") or []):
            cookies[c["name"]] = c["value"]
        ua = sol.get("userAgent") or ua
        # Early-out as soon as we have BOTH reese84 and a visid_incap_*
        has_reese = "reese84" in cookies
        has_visid = any(k.startswith("visid_incap") for k in cookies)
        print(f"  warmup {url.split('/en/')[-1] or '/'}: {len(cookies)} cookies "
              f"reese84={has_reese} visid={has_visid}")
        if has_reese and has_visid:
            break
    if not ua:
        raise RuntimeError("FlareSolverr returned empty userAgent")
    if not any(k.startswith("visid_incap") for k in cookies):
        raise RuntimeError(f"no visid_incap_* after warmup — got {sorted(cookies)}")
    if "reese84" not in cookies:
        # Some Imperva configs grant reese84 only on first real XHR, not
        # navigation. Continue — login may still work, will surface 403 cleanly.
        print(f"  [warn] no reese84 after {len(warmup_urls)} warmups — login may 403")
    return cookies, ua


def login_xhr(cookies: dict, ua: str, body: dict) -> requests.Response:
    """Plain-requests JSON POST to /s/auth/login using FS-harvested cookies."""
    return requests.post(
        LOGIN_API,
        json=body,
        cookies=cookies,
        headers={
            "User-Agent": ua,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "*/*",
            "Origin": "https://www.coolbet.com",
            "Referer": "https://www.coolbet.com/en/",
            "x-device": "DESKTOP",
        },
        timeout=30,
    )


def cmd_start() -> int:
    print("=== Coolbet 2FA enrollment — STEP 1 (trigger SMS) ===")
    # COOLBET-NO-AUTO-LOGIN (2026-06-12): this script is the ONLY context
    # allowed to call /s/auth/login. It's a deliberate, operator-initiated
    # action — opposite of an unattended cron retrying it every 5 min.
    os.environ["COOLBET_ALLOW_API_LOGIN"] = "true"
    if not (FLARESOLVERR_URL and COOLBET_USER and COOLBET_PASS):
        print("✗ missing env: need FLARESOLVERR_URL, COOLBET_USER, COOLBET_PASS")
        return 2

    if not fs_session_create(SESSION_NAME):
        # Maybe already exists from a previous abandoned run — destroy + retry.
        fs_session_destroy(SESSION_NAME)
        if not fs_session_create(SESSION_NAME):
            print("✗ could not create FlareSolverr session")
            return 3

    try:
        print(f"  warming FlareSolverr session {SESSION_NAME!r}…")
        cookies, ua = harvest_session(SESSION_NAME)
        print(f"  ✓ {len(cookies)} cookies; UA len {len(ua)}")

        print("  triggering /s/auth/login (expect 401 + 2FA challenge)…")
        r = login_xhr(cookies, ua, {"email": COOLBET_USER, "password": COOLBET_PASS})
        if r.status_code == 200:
            # Server unexpectedly didn't ask for 2FA → device already trusted.
            data = r.json()
            jwt = (data.get("token") or data.get("jwt") or "").lstrip("Bearer ").strip()
            if jwt:
                print("  ✓ login succeeded WITHOUT 2FA — device already trusted.")
                _persist_jwt(jwt, data, cookies, ua)
                fs_session_destroy(SESSION_NAME)
                STATE_FILE.unlink(missing_ok=True)
                return 0
            print(f"✗ 200 but no token in response: {_scrub(r.text)[:200]!r}")
            return 4

        if r.status_code != 401:
            print(f"✗ unexpected status {r.status_code}: {_scrub(r.text)[:200]!r}")
            return 4

        try:
            data = r.json()
        except Exception:
            print(f"✗ non-JSON 401 body: {_scrub(r.text)[:200]!r}")
            return 4

        name = data.get("name", "")
        tf = data.get("twoFactor") or {}
        code_id = tf.get("codeId") or data.get("codeId")
        method = tf.get("method") or tf.get("defaultMethod") or "?"
        safe_phone = tf.get("safePhone") or "?"
        reason = tf.get("reason") or "?"

        # Bail on anything that's NOT the SMS path — CAPTCHA, lockout, Smart-ID.
        if name not in ("TwoFactorRequiredSmsError", "TwoFactorRequiredError"):
            print(f"✗ 401 but not the expected 2FA challenge: name={name!r}")
            print(f"  body: {_scrub(r.text)[:400]!r}")
            return 5
        if method != "sms":
            print(f"✗ 2FA method is {method!r} — only 'sms' is supported by this script.")
            return 5
        if not code_id:
            print(f"✗ no codeId in 2FA response: {_scrub(r.text)[:400]!r}")
            return 4

        STATE_FILE.write_text(json.dumps({
            "codeId": code_id,
            "method": method,
            "reason": reason,
            "session_name": SESSION_NAME,
            "cookies": cookies,
            "ua": ua,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }))
        os.chmod(STATE_FILE, 0o600)
        print(f"\n  ✓ SMS sent to phone ending in …{safe_phone}")
        print(f"  reason: {reason}  method: {method}  codeId: {code_id[:8]}…")
        print(f"  state saved to {STATE_FILE} (0600)")
        print(f"\n  When the SMS arrives, run:")
        print(f"    FLARESOLVERR_URL={FLARESOLVERR_URL} \\")
        print(f"      python3 scripts/coolbet/flaresolverr_login_enroll.py verify <CODE>")
        print(f"\n  (FlareSolverr session {SESSION_NAME!r} kept alive for follow-up.)")
        return 0
    except Exception as e:
        print(f"✗ start failed: {e}")
        fs_session_destroy(SESSION_NAME)
        return 9


def _persist_jwt(jwt: str, login_payload: dict, cookies: dict, ua: str) -> None:
    """Write the fresh JWT + Imperva cookies into .env, report identity.

    Also persists the six COOLBET_COOKIE_* env vars that workers/automation/
    coolbet_session.py reads — without this sync the placer falls back to
    stale (or empty) cookies and Imperva blocks its requests with HTTP 500
    even when the JWT is fresh (the COOLBET-PLACER-FLARESOLVERR-WIRE gap
    we hit on 2026-06-10).
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        print(f"  [warn] .env not at {env_path} — JWT NOT persisted, will print summary only.")
    try:
        payload_b64 = jwt.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        user_id = payload.get("sub") or "?"
        exp = float(payload.get("exp", 0))
        ttl_s = exp - time.time()
        renewal = payload.get("renewal_date") or ""
        print(f"  JWT — sub={user_id}  exp_in={ttl_s:.0f}s  renewal_date={renewal[:14]}")
    except Exception as e:
        print(f"  [warn] could not decode JWT payload: {e}")

    if env_path.exists():
        set_key(str(env_path), "COOLBET_MANUAL_JWT", jwt)
        print(f"  ✓ wrote COOLBET_MANUAL_JWT to {env_path}")
        _persist_imperva_cookies(env_path, cookies)

    # COOLBET-JWT-DB-BACKED (2026-06-12): also persist to coolbet_session_state
    # so Railway picks up this JWT on its next run without any env-var push.
    # This is the whole point of the DB-backed architecture — local enrolls,
    # Railway inherits, renew-token keeps it alive forever from either side.
    try:
        # Path manipulation so we can import workers.* when this script runs
        # from anywhere (it's a stand-alone CLI, no relative imports).
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from workers.automation.coolbet_state import persist_jwt as _db_persist_jwt
        lsid = (login_payload.get("loginSessionId")
                or login_payload.get("login_session_id"))
        _db_persist_jwt(jwt, login_session_id=lsid, set_by="local_enroll")
        print("  ✓ persisted JWT to coolbet_session_state.jwt_current "
              "(Railway will inherit on next CoolbetSession init)")
    except Exception as e:
        print(f"  [warn] DB persist failed (non-fatal): {e}")

    # Audit non-Imperva cookies in case Coolbet sets a device-trust marker.
    non_imperva = {
        k: v for k, v in cookies.items()
        if not (k.startswith("visid_incap") or k.startswith("incap_") or k.startswith("nlbi_") or k == "reese84")
    }
    print(f"  device cookies (non-Imperva, candidates for trust marker): {sorted(non_imperva)}")


def _persist_imperva_cookies(env_path: Path, cookies: dict[str, str]) -> None:
    """Sync the placer's COOLBET_COOKIE_* env vars from FlareSolverr-harvested
    cookies. Mirrors the six individual vars that
    workers/automation/coolbet_session.py reads at startup:

        COOLBET_COOKIE_REESE84       ← reese84
        COOLBET_COOKIE_VISID_INCAP   ← visid_incap_<site_id>
        COOLBET_COOKIE_NLBI          ← nlbi_<site_id>     (the short variant)
        COOLBET_COOKIE_NLBI2         ← nlbi_<site_id>_<wp_id>   (the suffixed variant)
        COOLBET_COOKIE_INCAP_SES     ← incap_ses_<wp_id>_<site_id>
        COOLBET_COOKIE_UUID          ← uuid

    Without this sync the placer's direct requests.Session() falls back to
    whatever stale values exist (or empty) and Imperva returns HTTP 500 on
    search/v2 — the failure mode hit on 2026-06-10 when the placer was first
    exercised post-COOLBET-HEARTBEAT shipping. The heartbeat works fine
    because it routes through FlareSolverr; this bridges the placer to the
    same auth state without rewriting its HTTP layer.
    """
    # (env_var, matcher) — matcher returns True for the cookie name to use.
    # Order matters: the more-specific nlbi variant is matched first so the
    # general "nlbi_" matcher doesn't claim it for NLBI before NLBI2 sees it.
    env_map: list[tuple[str, callable]] = [
        ("COOLBET_COOKIE_REESE84",     lambda k: k == "reese84"),
        ("COOLBET_COOKIE_VISID_INCAP", lambda k: k.startswith("visid_incap")),
        ("COOLBET_COOKIE_NLBI2",       lambda k: k.startswith("nlbi_") and k.count("_") >= 2),
        ("COOLBET_COOKIE_NLBI",        lambda k: k.startswith("nlbi_") and k.count("_") == 1),
        ("COOLBET_COOKIE_INCAP_SES",   lambda k: k.startswith("incap_ses_")),
        ("COOLBET_COOKIE_UUID",        lambda k: k == "uuid"),
    ]

    written: list[str] = []
    missing: list[str] = []
    for env_var, matcher in env_map:
        match_value = None
        for name, value in cookies.items():
            if matcher(name):
                match_value = value
                break
        if match_value:
            set_key(str(env_path), env_var, match_value)
            written.append(env_var)
        else:
            missing.append(env_var)

    if written:
        print(f"  ✓ wrote {len(written)} Imperva cookies to {env_path}: "
              f"{', '.join(v.replace('COOLBET_COOKIE_', '') for v in written)}")
    if missing:
        # Missing UUID is normal on a fresh session — Coolbet sets it post-login.
        # Missing reese84/visid is a problem — Imperva won't accept us without them.
        critical = [m for m in missing if m in ("COOLBET_COOKIE_REESE84", "COOLBET_COOKIE_VISID_INCAP")]
        if critical:
            print(f"  [warn] missing critical Imperva cookies: "
                  f"{', '.join(v.replace('COOLBET_COOKIE_', '') for v in critical)}")
        else:
            print(f"  [info] no value for: "
                  f"{', '.join(v.replace('COOLBET_COOKIE_', '') for v in missing)}")


def cmd_verify(code: str) -> int:
    print("=== Coolbet 2FA enrollment — STEP 2 (submit SMS code) ===")
    if not STATE_FILE.exists():
        print(f"✗ no state at {STATE_FILE} — run `start` first.")
        return 2
    state = json.loads(STATE_FILE.read_text())
    code_id = state["codeId"]
    cookies = state["cookies"]
    ua = state["ua"]
    sess = state.get("session_name", SESSION_NAME)
    print(f"  codeId: {code_id[:8]}…  fs_session: {sess}  state_age: "
          f"{(datetime.now(timezone.utc) - datetime.fromisoformat(state['created_at'])).total_seconds():.0f}s")

    # Refresh cookies from FlareSolverr in case Imperva rotated them. Best-effort.
    try:
        fresh = fs_get(HOMEPAGE, session=sess)
        if fresh:
            new_cookies = {c["name"]: c["value"] for c in (fresh.get("cookies") or [])}
            if new_cookies:
                cookies.update(new_cookies)
                print(f"  refreshed cookies from FlareSolverr ({len(new_cookies)} keys)")
    except Exception as e:
        print(f"  [warn] could not refresh cookies — proceeding with stored ones: {e}")

    # Verified payload shape captured from Coolbet's web client DevTools:
    #   POST /s/auth/2fa/verify  {"code","codeId","isTrustedDevice"}
    # We send isTrustedDevice=true to claim trust enrollment — but Coolbet's
    # actual trust marker is tied to the `uuid` cookie (Coolbet-side device id),
    # which the FlareSolverr Chrome instance owns and preserves across runs.
    payload_shapes = [
        {"code": code, "codeId": code_id, "isTrustedDevice": True},
        # If the True flag is rejected, fall back to the value the real web
        # client sent (false) — Coolbet probably enrolls trust regardless of
        # the flag, based on uuid.
        {"code": code, "codeId": code_id, "isTrustedDevice": False},
    ]

    headers = {
        "User-Agent": ua,
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "*/*",
        "Origin": "https://www.coolbet.com",
        "Referer": "https://www.coolbet.com/en/",
        "x-device": "DESKTOP",
    }

    last_err = None
    for url in VERIFY_CANDIDATES:
        for shape_idx, body in enumerate(payload_shapes):
            r = requests.post(url, json=body, cookies=cookies, headers=headers, timeout=30)
            short = url.split('/s/')[1]
            keys = sorted(body.keys())
            preview = _scrub(r.text)[:200]
            print(f"  {short:24}  shape#{shape_idx} keys={keys}")
            print(f"    → HTTP {r.status_code}, len={len(r.text)}  body={preview!r}")
            if r.status_code in (404, 405):
                # endpoint doesn't exist — move on (don't try other shapes here)
                last_err = f"{r.status_code} on {url}"
                break
            if r.status_code == 400 and shape_idx < len(payload_shapes) - 1:
                # endpoint exists, payload wrong shape — try next shape
                last_err = f"400 on {url}: {_scrub(r.text)[:120]!r}"
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    print(f"  ✗ 200 but non-JSON: {_scrub(r.text)[:200]!r}")
                    return 4
                jwt = (data.get("token") or data.get("jwt") or data.get("accessToken") or "").lstrip("Bearer ").strip()
                if not jwt:
                    print(f"  ✗ 200 but no token: {_scrub(json.dumps(data))[:200]!r}")
                    return 4
                # Pull any Set-Cookie that Coolbet attached on success — that's
                # where a device-trust marker would live.
                cookies.update(r.cookies.get_dict())
                print(f"\n  ✓ verified! endpoint = {url}")
                _persist_jwt(jwt, data, cookies, ua)
                # Quick sanity check: hit balance with the fresh JWT.
                print("\n  sanity-check: GET /s/account/info with fresh JWT…")
                lsid = data.get("loginSessionId") or data.get("login_session_id") or ""
                uid = data.get("userId") or data.get("user_id") or ""
                br = requests.get(
                    "https://www.coolbet.com/s/account/info",
                    cookies=cookies,
                    headers={**headers, "cbauth": f"Bearer {jwt}",
                             "login_session_id": str(lsid), "user_id": str(uid)},
                    timeout=20,
                )
                print(f"    /s/account/info → HTTP {br.status_code}, len={len(br.text)}")
                # Don't print the body — it's PII-rich.
                # Destroy the FlareSolverr session — Imperva cookies stay valid
                # for hours; we don't need the Chrome instance idling.
                fs_session_destroy(sess)
                STATE_FILE.unlink(missing_ok=True)
                print(f"\n  done. fs session destroyed; {STATE_FILE} removed.")
                return 0
            # 401/403/422 — surface the body for the report; redact tokens.
            print(f"  body: {_scrub(r.text)[:280]!r}")
            last_err = f"{r.status_code} on {url} shape#{shape_idx}"
            # If it's a clear "wrong code" we should stop early so the user can re-run start.
            try:
                err = r.json()
                if (err.get("name") or "").lower().endswith("invalidcodeerror") \
                   or "invalid" in (err.get("message") or "").lower():
                    print("  ⚠ code looks invalid/expired — re-run `start` and re-enter SMS.")
                    return 6
            except Exception:
                pass

    print(f"\n✗ all endpoint/shape combinations failed. last: {last_err}")
    return 7


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    pv = sub.add_parser("verify")
    pv.add_argument("code", help="6-digit SMS code")
    args = p.parse_args()
    if args.cmd == "start":
        return cmd_start()
    if args.cmd == "verify":
        return cmd_verify(args.code.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
