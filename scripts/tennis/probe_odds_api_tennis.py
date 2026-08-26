#!/usr/bin/env python3
"""
TENNIS-PAPER-BETS Phase 1 — probe The Odds API for tennis coverage.

We already have OA_KEY/ODDS_API_KEY on the VPS (used by WC sweep). Goal:
  1. List tennis sport keys the API exposes
  2. For each: fetch upcoming events, check Pinnacle coverage + odds payload shape
  3. Try a /scores call to see if results are queryable (== settlement source)

Run once, read the printed report, then decide whether to swap from OddsPapi.

Usage:
    python3 scripts/tennis/probe_odds_api_tennis.py
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

BASE = "https://api.the-odds-api.com/v4"
KEY = os.environ.get("OA_KEY") or os.environ.get("ODDS_API_KEY", "")


def call(path: str, **params) -> tuple[int, dict | list | None, dict[str, str]]:
    if not KEY:
        print("ERROR: OA_KEY / ODDS_API_KEY not set.")
        sys.exit(1)
    params["apiKey"] = KEY
    url = f"{BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, params=params, timeout=30)
    except requests.RequestException as e:
        return -1, None, {"error": str(e)}
    body: dict | list | None
    try:
        body = r.json()
    except Exception:
        body = r.text[:300]  # type: ignore[assignment]
    hdrs = {k: v for k, v in r.headers.items()
            if any(x in k.lower() for x in ("request", "rate", "retry", "quota"))}
    return r.status_code, body, hdrs


def main() -> None:
    print("=" * 70)
    print("The Odds API — tennis probe")
    print("=" * 70)

    # 1. List all sports — pick out the tennis ones
    print("\n[1] GET /sports")
    code, body, hdrs = call("sports", all="true")
    print(f"  HTTP {code}  headers: {hdrs}")
    if code != 200 or not isinstance(body, list):
        print(f"  unexpected body: {body!r}")
        return

    tennis = [s for s in body if isinstance(s, dict)
              and ("tennis" in (s.get("group") or "").lower()
                   or "tennis" in (s.get("key") or "").lower()
                   or "tennis" in (s.get("title") or "").lower())]
    print(f"\n  Tennis-related sport keys: {len(tennis)}")
    for s in tennis:
        marker = "ACTIVE" if s.get("active") else "inactive"
        print(f"    {s.get('key'):45s}  {marker:8s}  {s.get('title')}")

    if not tennis:
        print("  ⚠️  NO tennis sport keys found")
        return

    # 2. For each ACTIVE tennis sport, fetch events + odds with Pinnacle
    active_tennis = [s for s in tennis if s.get("active")]
    if not active_tennis:
        print("\n  ⚠️  All tennis sport keys are inactive — no live tournaments to probe")
        return

    sample_key = active_tennis[0]["key"]
    print(f"\n[2] GET /sports/{sample_key}/odds  (pinnacle, h2h market)")
    code, body, hdrs = call(f"sports/{sample_key}/odds",
                            regions="eu", markets="h2h", bookmakers="pinnacle")
    print(f"  HTTP {code}  headers: {hdrs}")
    if code == 200 and isinstance(body, list):
        print(f"  events returned: {len(body)}")
        if body:
            print("  first event (full dump):")
            print("  " + json.dumps(body[0], indent=2)[:1500].replace("\n", "\n  "))
            # Count pinnacle coverage
            pin_events = [e for e in body
                          if any(bm.get("key") == "pinnacle"
                                 for bm in (e.get("bookmakers") or []))]
            print(f"\n  ✅ Pinnacle coverage: {len(pin_events)}/{len(body)} events")
    else:
        print(f"  body: {body!r}")

    # Try other tennis keys to see if any have richer coverage
    for s in active_tennis[1:4]:
        k = s["key"]
        print(f"\n[2b] /sports/{k}/odds  pinnacle h2h")
        code, body, hdrs = call(f"sports/{k}/odds",
                                regions="eu", markets="h2h", bookmakers="pinnacle")
        print(f"  HTTP {code}  events: {len(body) if isinstance(body,list) else 'n/a'}  headers: {hdrs}")
        if isinstance(body, list) and body:
            pin = sum(1 for e in body if any(bm.get("key") == "pinnacle"
                                             for bm in (e.get("bookmakers") or [])))
            print(f"  Pinnacle coverage: {pin}/{len(body)}")

    # 3. Try /scores for settlement
    print(f"\n[3] GET /sports/{sample_key}/scores  (results — settlement source)")
    code, body, hdrs = call(f"sports/{sample_key}/scores", daysFrom=2)
    print(f"  HTTP {code}  headers: {hdrs}")
    if code == 200 and isinstance(body, list):
        print(f"  results returned: {len(body)}")
        completed = [r for r in body if r.get("completed")]
        print(f"  ✅ Completed events: {len(completed)}/{len(body)}")
        if completed:
            print("  sample completed event:")
            print("  " + json.dumps(completed[0], indent=2)[:1000].replace("\n", "\n  "))
        elif body:
            print("  sample (incomplete) event:")
            print("  " + json.dumps(body[0], indent=2)[:1000].replace("\n", "\n  "))
    elif code == 422:
        print(f"  /scores not supported on this sport: {body!r}")
    else:
        print(f"  body: {body!r}")


if __name__ == "__main__":
    main()
