#!/usr/bin/env python3
"""
TENNIS-PAPER-BETS Phase 4.6 settlement-source probe — api-tennis.com via RapidAPI.

Why this matters: 260 daily Coolbet-only observations need a result-backfill
source. Sackmann is license-blocked, tennis-data.co.uk is dead. api-tennis.com
covers ATP/WTA/ITF/Challenger via RapidAPI; this probe answers three questions:

  1. Does the free tier cover ITF + Challenger events?
  2. What's the actual free-tier quota?
  3. Are match results (winner) directly queryable, or only odds?

Setup (one-time, ~5 min):
  1. Sign up at https://rapidapi.com (free, no credit card)
  2. Subscribe to: https://rapidapi.com/jjrm365-kIFr3Nx_odV/api/tennis-api-atp-wta-itf  (free tier)
  3. Copy the X-RapidAPI-Key from the dashboard
  4. Add to .env: APITENNIS_KEY=<your_key>

Then run:
    python3 scripts/tennis/probe_api_tennis.py

Output: per-endpoint pass/fail + sample row + rate-limit headers. The script
makes ~5 calls total; well within any reasonable free quota.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parents[2] / ".env")

KEY = os.environ.get("APITENNIS_KEY") or os.environ.get("RAPIDAPI_KEY", "")
# RapidAPI uses host + key headers. This API's host:
HOST = "tennis-api-atp-wta-itf.p.rapidapi.com"
BASE = f"https://{HOST}"

# Endpoints to probe — names guessed from docs.tennis-api.com pattern, will
# adjust based on actual response shapes.
PROBES = [
    ("/tennis/v2/atp/tournament/calendar/2026", "ATP 2026 calendar"),
    ("/tennis/v2/wta/tournament/calendar/2026", "WTA 2026 calendar"),
    ("/tennis/v2/itf/tournament/calendar/2026", "ITF 2026 calendar — covers Futures?"),
    ("/tennis/v2/atp/fixtures/2026-06-25", "ATP fixtures for today"),
    ("/tennis/v2/wta/fixtures/2026-06-25", "WTA fixtures for today"),
    ("/tennis/v2/itf/fixtures/2026-06-25", "ITF fixtures for today (Challenger + Futures)"),
]


def call(path: str) -> tuple[int, dict | list | str, dict]:
    headers = {
        "X-RapidAPI-Key":  KEY,
        "X-RapidAPI-Host": HOST,
    }
    url = f"{BASE}{path}"
    try:
        r = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as e:
        return -1, str(e), {}
    body: dict | list | str
    try:
        body = r.json()
    except Exception:
        body = r.text[:300]
    rate_headers = {
        k: v for k, v in r.headers.items()
        if any(x in k.lower() for x in ("rate", "quota", "limit", "remain"))
    }
    return r.status_code, body, rate_headers


def summary(label: str, code: int, body, headers: dict) -> None:
    print(f"\n=== {label} ===")
    print(f"  HTTP {code}")
    if headers:
        print(f"  rate headers: {headers}")
    if code == 200 and isinstance(body, (list, dict)):
        if isinstance(body, list):
            print(f"  list len: {len(body)}")
            if body:
                print(f"  first row keys: {sorted((body[0] or {}).keys()) if isinstance(body[0], dict) else type(body[0]).__name__}")
                print(f"  sample (truncated):")
                print("  " + json.dumps(body[0], indent=2)[:600].replace("\n", "\n  "))
        else:
            keys = list(body.keys())
            print(f"  dict keys: {keys[:15]}")
            inner = body.get("data") or body.get("results") or body.get("fixtures") or body.get("tournaments")
            if isinstance(inner, list):
                print(f"  inner list len: {len(inner)}")
                if inner:
                    print(f"  first item keys: {sorted((inner[0] or {}).keys()) if isinstance(inner[0], dict) else type(inner[0]).__name__}")
    elif code == 200:
        print(f"  body: {str(body)[:300]}")
    elif code == 403:
        print("  → not subscribed to this endpoint on the free tier (most likely)")
    elif code == 404:
        print(f"  → endpoint path probably wrong; check docs at https://docs.tennis-api.com/")
    elif code == 429:
        print("  → rate-limited (or monthly quota exhausted)")
    else:
        print(f"  body: {str(body)[:300]}")


def main() -> int:
    print("=" * 70)
    print("api-tennis.com probe — RapidAPI free tier")
    print("=" * 70)

    if not KEY:
        print("\nERROR: APITENNIS_KEY / RAPIDAPI_KEY not set.")
        print("Setup:")
        print("  1. Sign up: https://rapidapi.com")
        print("  2. Subscribe (free): https://rapidapi.com/jjrm365-kIFr3Nx_odV/api/tennis-api-atp-wta-itf")
        print("  3. Add to .env: APITENNIS_KEY=<key from dashboard>")
        return 1

    print(f"\nKey prefix: {KEY[:8]}…")
    print(f"Host:       {HOST}")

    for path, label in PROBES:
        code, body, headers = call(path)
        summary(label, code, body, headers)

    print("\n" + "=" * 70)
    print("Decisions to make based on output:")
    print("  - 403 on ITF endpoint → free tier excludes lower tiers, won't solve our gap")
    print("  - 200 with results in body → green light to build the backfill job")
    print("  - 429 immediately → free quota too tight, need paid tier or different source")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
