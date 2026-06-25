#!/usr/bin/env python3
"""
TENNIS-PAPER-BETS Phase 1 probe — does OddsPapi v4 expose tennis match results?

Walks a list of candidate endpoints + likely field names. Goal is a yes/no on
"can we settle `tennis_value_bets` rows by calling OddsPapi with a fixture_id".

Run once, read the printed report, then decide settlement source.

Usage:
    OP_KEY=<key> python3 scripts/tennis/probe_oddspapi_results.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parents[2] / ".env")

BASE = "https://api.oddspapi.io/v4"
TENNIS_SPORT = 12

# Candidate result-bearing endpoints to probe. Some are guesses based on the
# v4 conventions seen in value_scanner.py — the 404s are also informative.
PROBE_PATHS = [
    "/results",
    "/results-by-tournaments",
    "/fixtures",
    "/fixtures-by-tournaments",
    "/events",
    "/events-by-tournaments",
    "/scores",
    "/matches",
    "/finished-fixtures",
    "/historical-results",
]

# Keys that, if present in a fixture/event payload, tell us a result is in there
RESULT_FIELD_HINTS = (
    "winnerId", "winner", "score", "scores", "result", "finalScore",
    "status", "matchStatus", "isFinished", "finished", "completed",
    "homeScore", "awayScore", "participant1Score", "participant2Score",
)


def call(path: str, **params) -> tuple[int, dict | list | None, dict[str, str]]:
    import time
    key = os.environ.get("OP_KEY", "")
    if not key:
        print("ERROR: OP_KEY env var not set.")
        sys.exit(1)
    params["apiKey"] = key
    url = f"{BASE}{path}"
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            return -1, None, {"error": str(e)}
        if r.status_code == 429:
            wait = 2 + attempt * 3   # 2, 5, 8, 11, 14s
            print(f"  429 — backing off {wait}s (attempt {attempt+1}/5)")
            time.sleep(wait)
            continue
        body: dict | list | None
        try:
            body = r.json()
        except Exception:
            body = r.text[:300]  # type: ignore[assignment]
        headers = {k: v for k, v in r.headers.items() if "request" in k.lower() or "rate" in k.lower()}
        return r.status_code, body, headers
    return 429, None, {"error": "giving up after 5 retries"}


def pretty_keys(obj, depth=0, max_depth=3) -> list[str]:
    """Walk a nested dict and return a flat list of dotted key paths."""
    out: list[str] = []
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(k)
            out.extend(f"{k}.{sub}" for sub in pretty_keys(v, depth + 1, max_depth))
    elif isinstance(obj, list) and obj:
        out.extend(pretty_keys(obj[0], depth, max_depth))
    return out


def first_finished_sample(rows) -> dict | None:
    """Find a row that looks finished, if any."""
    if not isinstance(rows, list):
        return None
    for r in rows:
        if not isinstance(r, dict):
            continue
        status = (r.get("status") or r.get("matchStatus") or "").lower()
        if status in ("finished", "ended", "completed", "ft"):
            return r
        if r.get("isFinished") or r.get("finished") or r.get("completed"):
            return r
        if r.get("winnerId") or r.get("winner"):
            return r
    return None


def report_endpoint(path: str, params: dict) -> None:
    print(f"\n=== GET {path} {params} ===")
    code, body, hdrs = call(path, **params)
    print(f"  HTTP {code}   headers: {hdrs}")
    if code != 200:
        if isinstance(body, str):
            print(f"  body: {body[:200]}")
        elif isinstance(body, dict):
            print(f"  body: {json.dumps(body)[:300]}")
        return

    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        rows = body.get("data") or body.get("fixtures") or body.get("events") or body.get("results") or []
        if not rows and not any(k in body for k in ("data", "fixtures", "events", "results")):
            rows = [body]
    else:
        rows = []

    print(f"  row count: {len(rows) if isinstance(rows, list) else 'n/a'}")
    if isinstance(rows, list) and rows:
        sample_keys = pretty_keys(rows[0], max_depth=2)
        hinted = [k for k in sample_keys if any(h.lower() in k.lower() for h in RESULT_FIELD_HINTS)]
        print(f"  top-level keys (first row): {sorted(set(k.split('.')[0] for k in sample_keys))[:25]}")
        if hinted:
            print(f"  ✅ RESULT-LIKE FIELDS FOUND: {sorted(set(hinted))[:15]}")
        else:
            print("  ⚠️  no result-like fields in first row")

        finished = first_finished_sample(rows)
        if finished:
            print("  ✅ FOUND A FINISHED-LOOKING ROW — full dump:")
            print("  " + json.dumps(finished, indent=2)[:2000].replace("\n", "\n  "))
        else:
            print("  (no finished row in this sample — try a yesterday-window probe)")


def main() -> None:
    print("=" * 70)
    print(f"OddsPapi v4 — tennis results probe   {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print("=" * 70)

    # First, get a current tournament id we can use as a parameter.
    code, body, _ = call("/tournaments", sportId=TENNIS_SPORT)
    if code != 200:
        print(f"FATAL: /tournaments returned {code}")
        return
    tourneys = body if isinstance(body, list) else body.get("data", [])
    if not tourneys:
        print("FATAL: no tennis tournaments returned")
        return
    sample_tournament = int(tourneys[0].get("tournamentId") or tourneys[0].get("id"))
    print(f"\nUsing sample tournament id: {sample_tournament}  ({tourneys[0].get('tournamentName')})")

    # Probe each candidate endpoint with several parameter shapes.
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    probes = [
        ("/results", {"sportId": TENNIS_SPORT, "date": yesterday}),
        ("/results", {"sportId": TENNIS_SPORT}),
        ("/results-by-tournaments", {"tournamentIds": sample_tournament}),
        ("/fixtures", {"sportId": TENNIS_SPORT, "date": yesterday}),
        ("/fixtures", {"sportId": TENNIS_SPORT, "status": "finished"}),
        ("/fixtures-by-tournaments", {"tournamentIds": sample_tournament}),
        ("/events", {"sportId": TENNIS_SPORT, "date": yesterday}),
        ("/events-by-tournaments", {"tournamentIds": sample_tournament}),
        ("/scores", {"sportId": TENNIS_SPORT, "date": yesterday}),
        ("/matches", {"sportId": TENNIS_SPORT, "date": yesterday}),
        ("/finished-fixtures", {"sportId": TENNIS_SPORT}),
        ("/historical-results", {"sportId": TENNIS_SPORT, "from": yesterday, "to": today}),
    ]

    for path, params in probes:
        try:
            report_endpoint(path, params)
        except Exception as e:
            print(f"  probe crashed: {e}")

    # Also check whether the existing /odds-by-tournaments call itself surfaces
    # post-match status on yesterday's fixtures — that would be the cheapest
    # settlement path (no new endpoint, just inspect the same response).
    print("\n=== Bonus probe: /odds-by-tournaments status field on yesterday's tournaments ===")
    code, body, _ = call("/odds-by-tournaments",
                         tournamentIds=sample_tournament, bookmaker="Pinnacle")
    rows = body if isinstance(body, list) else (body or {}).get("data", [])
    if rows and isinstance(rows, list):
        sample = rows[0] if isinstance(rows[0], dict) else None
        if sample:
            top_keys = list(sample.keys())
            print(f"  top-level fixture keys: {top_keys[:25]}")
            for k in ("status", "matchStatus", "isFinished", "finished", "result",
                      "winnerId", "score", "finalScore"):
                if k in sample:
                    print(f"  ✅ {k} = {sample[k]!r}")


if __name__ == "__main__":
    main()
