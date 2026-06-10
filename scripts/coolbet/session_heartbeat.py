"""
Coolbet session heartbeat — routes authenticated calls through FlareSolverr
to bypass Imperva, exactly like the login flow.

Rotates through 5 endpoints to mimic real browsing pattern:
  - bets_history  : "user checking open bets"
  - transactions  : "user viewing wallet"
  - maintenance   : "browser background ping"
  - search        : "user typing in search"
  - renew_token   : "browser background refresh"

Single-shot or loop modes:
  python3 scripts/coolbet/session_heartbeat.py            # one fire, rotation
  python3 scripts/coolbet/session_heartbeat.py --loop 12  # 12 fires, 5min apart
  python3 scripts/coolbet/session_heartbeat.py --all      # all 5 endpoints in one go

Log: dev/active/coolbet_heartbeat.log (one JSON line per call).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
# Reload env every time the script starts — picks up freshest JWT.
load_dotenv(ROOT / ".env", override=True)

LOG_FILE = ROOT / "dev/active/coolbet_heartbeat.log"
FS_URL = os.getenv("FLARESOLVERR_URL", "").rstrip("/")
FS_SESSION = os.getenv("COOLBET_FLARE_SESSION", "coolbet_dev")


# ----- FlareSolverr helpers (mirror flaresolverr_login_enroll.py) -----

def _fs_call(body: dict, *, timeout_s: int = 120) -> dict:
    if not FS_URL:
        raise RuntimeError("FLARESOLVERR_URL is unset")
    req = urllib.request.Request(
        f"{FS_URL}/v1",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read())


def fs_session_create(name: str) -> None:
    """Idempotent create — silently no-ops if session already exists."""
    try:
        _fs_call({"cmd": "sessions.create", "session": name}, timeout_s=30)
    except Exception:
        pass  # Likely already exists


def fs_request(method: str, url: str, *, headers: dict | None = None,
               post_data: str | None = None, session: str = FS_SESSION) -> dict:
    """Make a request through FlareSolverr's Chrome. Returns the wrapped
    response: status (in solution.status), url, response body (HTML/JSON in
    solution.response), cookies (solution.cookies)."""
    body = {
        "cmd": "request.post" if method == "POST" else "request.get",
        "url": url,
        "session": session,
        "maxTimeout": 60_000,
    }
    if post_data is not None:
        body["postData"] = post_data
    if headers:
        # FlareSolverr accepts a flat dict
        body["headers"] = headers
    return _fs_call(body)


# ----- JWT + endpoint setup -----

def _decode_jwt(jwt: str) -> dict:
    try:
        payload_b64 = jwt.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


SEARCH_URL = "https://www.coolbet.com/s/sbgate/sports/search/v2"
BETS_HISTORY_URL = (
    "https://www.coolbet.com/s/sbgate/bets/history"
    "?isCampaign=false&isCashout=false&language=et&layout=EUROPEAN"
    "&pageNumber=1&pageSize=10&ticketStatus=PENDING"
)
TRANSACTIONS_URL = (
    "https://www.coolbet.com/s/wallet/transactions"
    "?isGrouped=true&order_by=-created&page=1&page_size=10&product=&type__ni="
)
MAINTENANCE_URL = "https://www.coolbet.com/s/casino/fo/maintenance"
RENEW_URL = "https://www.coolbet.com/s/auth/renew-token"

# (name, method, url-or-builder, post_data)
# search URL built dynamically with country/language params
def _search_url() -> str:
    qs = urllib.parse.urlencode({
        "country": "EE", "language": "et", "layout": "EUROPEAN", "search": "counter"})
    return f"{SEARCH_URL}?{qs}"


USER_ENDPOINTS = [
    ("bets_history", "GET",  BETS_HISTORY_URL, None),
    ("transactions", "GET",  TRANSACTIONS_URL, None),
    ("maintenance",  "GET",  MAINTENANCE_URL,  None),
    ("search",       "GET",  _search_url(),    None),
    ("renew_token",  "POST", RENEW_URL,        "{}"),
]


def _auth_headers(jwt: str) -> dict:
    claims = _decode_jwt(jwt)
    return {
        "accept": "*/*",
        "accept-language": "en-GB,en-US;q=0.9",
        "cbauth": f"Bearer {jwt}",
        "content-type": "application/json; charset=utf-8",
        "login_session_id": str(claims.get("login_session_id", "")),
        "user_id": str(claims.get("sub", "")),
        "x-device": "DESKTOP",
        "origin": "https://www.coolbet.com",
        "referer": "https://www.coolbet.com/et/sport",
    }


# ----- One heartbeat fire -----

def one_fire(endpoint_name: str | None = None) -> dict:
    """Make one heartbeat call. Returns dict with status + log line.
    If endpoint_name is None, picks one via rotation (minute-of-hour ± jitter)."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = {"ts": ts, "ep": None, "status": None, "ok": False, "ttl_s": None,
            "note": []}

    jwt = os.getenv("COOLBET_MANUAL_JWT", "")
    if not jwt:
        line["note"].append("no_jwt_in_env")
        return line

    claims = _decode_jwt(jwt)
    exp = float(claims.get("exp", 0))
    ttl = exp - time.time()
    line["ttl_s"] = round(ttl)

    # Pick endpoint
    if endpoint_name:
        eps = [ep for ep in USER_ENDPOINTS if ep[0] == endpoint_name]
    else:
        minute = datetime.now(timezone.utc).minute
        idx = (minute // 5 + random.randint(0, 1)) % len(USER_ENDPOINTS)
        eps = [USER_ENDPOINTS[idx]]

    if not eps:
        line["note"].append("no_endpoint_picked")
        return line
    name, method, url, post_data = eps[0]
    line["ep"] = name

    headers = _auth_headers(jwt)
    try:
        result = fs_request(method, url, headers=headers, post_data=post_data)
    except Exception as e:
        line["note"].append(f"fs_call_err:{type(e).__name__}:{str(e)[:80]}")
        return line

    sol = result.get("solution") or {}
    status = sol.get("status")
    line["status"] = status
    body = sol.get("response") or ""
    line["note"].append(f"body_len={len(body)}")
    if status == 200:
        line["ok"] = True
    elif status in (500, 502, 503):
        # Probably Imperva block
        if "Imperva" in body or "/de-" in body[:200]:
            line["note"].append("imperva_block")
    elif status == 401:
        line["note"].append("jwt_rejected")
    elif status == 403:
        line["note"].append("forbidden_or_imperva")
    return line


def _log_line(line: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(line) + "\n")
    emoji = "✓" if line.get("ok") else "✗"
    note = " ".join(line.get("note") or [])
    print(f"{emoji} {line['ts']}  ep={line['ep']}  status={line['status']}  ttl={line['ttl_s']}s  {note}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", choices=[ep[0] for ep in USER_ENDPOINTS] + ["rotate"],
                    default="rotate",
                    help="One specific endpoint or 'rotate' (default)")
    ap.add_argument("--all", action="store_true",
                    help="Hit ALL 5 endpoints in one fire (for full smoke test)")
    ap.add_argument("--loop", type=int, default=0,
                    help="Run N iterations 5 min apart (with ±60s jitter)")
    args = ap.parse_args()

    fs_session_create(FS_SESSION)

    if args.all:
        for name, _, _, _ in USER_ENDPOINTS:
            _log_line(one_fire(name))
            time.sleep(2)  # small gap
        return 0

    iterations = max(args.loop, 1)
    for i in range(iterations):
        ep = None if args.endpoint == "rotate" else args.endpoint
        _log_line(one_fire(ep))
        if i + 1 < iterations:
            jitter = random.randint(-60, 60)
            wait_s = 300 + jitter
            print(f"  sleeping {wait_s}s until next fire…")
            time.sleep(wait_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
