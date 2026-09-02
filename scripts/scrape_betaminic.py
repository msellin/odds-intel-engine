#!/usr/bin/env python3
"""Betaminic — ShootingBets settled-results scraper.

BETAMINIC-PUBLIC-TABLE-2026-09-02. This script used to write an
`auth_required` stub. That was investigated on 2026-06-24 against
/betamin-builder/public-strategies/, which genuinely is behind a free-signup
wall, and the conclusion — "don't scrape paywalled content, document and
skip" — was right for that page.

It was wrong about the site. /shootingbets/results/ publishes the same
outfit's settled bets through a public wpDataTables AJAX endpoint:

    POST /wp-admin/admin-ajax.php?action=get_wdtable&table_id=1246

No account, no session cookie. The only gate is `wdtNonce`, which the page
prints in plain sight:

    <input type="hidden" id="wdtNonceFrontendServerSide_1246" value="..." />

That nonce is shared across anonymous visitors, so fetching the page and
reading the input is the whole authentication story. Verified 2026-09-02:
115,792 rows total, 27,178 after the Soccer filter, returned as per-bet rows
with result and P/L — richer than the aggregate strategy stats the original
attempt was chasing.

Columns, in the order the endpoint returns them:

    0 starts            "09-02-2024 07:00 PM"   (d-m-Y, per wpdatatables_settings)
    1 sport             "Soccer"
    2 league            "England - Premier League 2 U21"
    3 home / 4 away
    5 market            "Moneyline" | "Totals" | ...
    6 selection         "Away" | "Under" | ...
    7 value_odds        the price Betaminic claims
    8 b365_line         "-" for moneyline, "2.5" for totals
    9 b365_value_odds   Bet365's price
   10 b365_points       final score, "0-2"
   11 b365_result       "W" | "L" | (void/push variants)
   12 PL                per-100 stake profit, "150.00" / "-50.00"
   13 date              "09-02-2024"

Writes dev/active/betaminic_raw.json for audit_vs_betaminic.py.

    python3 scripts/scrape_betaminic.py                 # last 120 days
    python3 scripts/scrape_betaminic.py --days 400
    python3 scripts/scrape_betaminic.py --max-rows 5000
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dev" / "active" / "betaminic_raw.json"

RESULTS_URL = "https://www.betaminic.com/shootingbets/results/"
AJAX_URL = ("https://www.betaminic.com/wp-admin/admin-ajax.php"
            "?action=get_wdtable&table_id=1246")
TABLE_ID = 1246
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

COLUMNS = ["starts", "sport", "league", "home", "away", "market", "selection",
           "value_odds", "b365_line", "b365_value_odds", "b365_points",
           "b365_result", "PL", "date"]
PAGE_SIZE = 200      # the UI asks for 10; 200 is well inside what it will serve


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-GB,en;q=0.9",
    })
    return s


def fetch_nonce(sess: requests.Session) -> str:
    """Read the server-side nonce the results page prints for this table."""
    r = sess.get(RESULTS_URL, timeout=30)
    r.raise_for_status()
    m = re.search(
        rf'id="wdtNonceFrontendServerSide_{TABLE_ID}"[^>]*value="([a-f0-9]+)"',
        r.text)
    if not m:
        # Fail loudly. Silently returning None here would produce an empty
        # 200 from the AJAX endpoint, which is exactly how a dead scraper
        # looks like a working one.
        raise RuntimeError(
            f"wdtNonceFrontendServerSide_{TABLE_ID} not found on {RESULTS_URL} — "
            "the table id or the page layout changed")
    return m.group(1)


def _payload(nonce: str, start: int, length: int, draw: int) -> dict:
    d = {
        "draw": str(draw), "start": str(start), "length": str(length),
        "search[value]": "", "search[regex]": "false",
        "order[0][column]": "0", "order[0][dir]": "desc",   # newest first
        "wdtNonce": nonce, "sRangeSeparator": "|",
    }
    for i, c in enumerate(COLUMNS):
        d[f"columns[{i}][data]"] = str(i)
        d[f"columns[{i}][name]"] = c
        d[f"columns[{i}][searchable]"] = "true"
        d[f"columns[{i}][orderable]"] = "true"
        # The site's own UI sends "|" for the two odds columns (a range filter
        # with both bounds empty). Mirror it — omitting it changes the filter.
        d[f"columns[{i}][search][value]"] = (
            "Soccer" if c == "sport" else ("|" if c.endswith("value_odds") else ""))
        d[f"columns[{i}][search][regex]"] = "true" if c == "sport" else "false"
    return d


def fetch_page(sess: requests.Session, nonce: str, start: int, draw: int) -> dict:
    r = sess.post(AJAX_URL, data=_payload(nonce, start, PAGE_SIZE, draw), timeout=45,
                  headers={
                      "X-Requested-With": "XMLHttpRequest",
                      "Referer": RESULTS_URL,
                      "Origin": "https://www.betaminic.com",
                      "Accept": "application/json, text/javascript, */*; q=0.01",
                  })
    r.raise_for_status()
    if not r.text.strip():
        raise RuntimeError(
            "empty 200 from the wdtable endpoint — usually a stale or wrong "
            "wdtNonce. Re-fetch the page and retry.")
    return r.json()


def _parse_date(s: str) -> str | None:
    """'09-02-2024' -> '2024-02-09'. wpdatatables_settings declares d-m-Y."""
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", (s or "").strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def to_row(raw: list) -> dict | None:
    if not raw or len(raw) < len(COLUMNS):
        return None
    r = dict(zip(COLUMNS, raw))
    d = _parse_date(r.get("date"))
    if d is None:
        return None
    return {
        "kickoff_date": d,
        "starts": r.get("starts"),
        "league": (r.get("league") or "").strip(),
        "home_team": (r.get("home") or "").strip(),
        "away_team": (r.get("away") or "").strip(),
        "market": (r.get("market") or "").strip(),
        "selection": (r.get("selection") or "").strip(),
        "line": None if (r.get("b365_line") or "-").strip() == "-" else (r.get("b365_line") or "").strip(),
        "odds": _f(r.get("b365_value_odds")) or _f(r.get("value_odds")),
        "value_odds": _f(r.get("value_odds")),
        "score": (r.get("b365_points") or "").strip(),
        "result": (r.get("b365_result") or "").strip(),
        "pl_per_100": _f(r.get("PL")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=120,
                    help="stop once rows are older than this (default 120)")
    ap.add_argument("--max-rows", type=int, default=20000)
    args = ap.parse_args()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).date().isoformat()
    sess = _session()
    nonce = fetch_nonce(sess)
    print(f"nonce {nonce} · walking newest-first back to {cutoff}")

    rows: list[dict] = []
    start = 0
    draw = 1
    total = None
    while len(rows) < args.max_rows:
        page = fetch_page(sess, nonce, start, draw)
        if total is None:
            # The endpoint returns this as a STRING; comparing it to an int
            # start offset raises rather than terminating the walk.
            try:
                total = int(page.get("recordsFiltered"))
            except (TypeError, ValueError):
                total = None
            print(f"  recordsFiltered (Soccer): {total}")
        data = page.get("data") or []
        if not data:
            break
        parsed = [p for p in (to_row(x) for x in data) if p]
        rows.extend(parsed)
        oldest = min((p["kickoff_date"] for p in parsed), default=None)
        print(f"  start={start}: {len(parsed)} rows (oldest {oldest}) · total={len(rows)}")
        if oldest and oldest < cutoff:
            break
        start += PAGE_SIZE
        draw += 1
        if total is not None and start >= total:
            break
        time.sleep(random.uniform(0.6, 1.1))   # same politeness as the other scrapers

    rows = [r for r in rows if r["kickoff_date"] >= cutoff]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(rows)} rows (>= {cutoff}) to {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
