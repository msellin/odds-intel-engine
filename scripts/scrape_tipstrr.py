"""
Tipstrr — public stats scraper.

Tipstrr is a tipster proofing platform. Per-bet selection / odds / stake for
each tip is paywalled behind a subscription. The PUBLIC stats endpoint exposes
aggregated monthly performance per tipster — this is enough to publish ROI in
our matched window without paying for any tipster.

URL pattern:
    https://tipstrr.com/tipster/<slug>/stats

The page returns server-rendered HTML with a JS payload (HTML-encoded JSON,
&q; = "). The payload's STATS_MONTH[<slug>_6] field contains a list of monthly
buckets:

    {
      "date": "2026-06-01T00:00:00Z",
      "tips": 47,                 # number of settled tips that month
      "win": 14, "lose": 33,
      "averageOdds": 2.979,
      "profit": -4.825,           # PnL in their proprietary point unit
      "staked": 47,               # always == tips × 1.0 unit
      "levelStakeROI": -10.27,    # the per-month "flat 1-unit" ROI %
      ...
    }

Cloudflare bot challenge is served on naive requests, so we route through
cloudscraper.

Note: this aggregate covers ALL bet types in the tipster's portfolio (football
1X2, OU, AH, BTTS, etc.). Tipstrr does NOT expose per-market monthly stats
publicly. Audit script documents this in scope_notes — comparison is still
fair as long as we pick FOOTBALL-only tipsters and stay within the same window.

Output: dev/active/tipstrr_raw.json with one entry per (tipster_slug × month).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Optional

import cloudscraper
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dev" / "active" / "tipstrr_raw.json"

# Football tipsters that are visibly active on /football. Audit script will
# drop ones that don't have ≥ MIN_SAMPLE bets in the matched window.
DEFAULT_SLUGS = [
    "star-tips",
    "master-football-tipster",
    "mls-value",
    "main-draws-model-top-euros",
    "the-football-analyst",
    "football-laboratory",
    "soccer-bettor",
    "kingofbets",
]


def decode_payload(text: str) -> Optional[dict]:
    """Tipstrr embeds the page payload as HTML-encoded JSON inside a <script>
    tag. Decode &q; / &l; / &g; / &a; back to literal " < > & and parse."""
    soup = BeautifulSoup(text, "lxml")
    for sc in soup.find_all("script"):
        body = sc.string or ""
        if "PORTFOLIO" not in body:
            continue
        decoded = (body.replace("&q;", '"')
                       .replace("&l;", "<")
                       .replace("&g;", ">")
                       .replace("&a;", "&"))
        try:
            return json.loads(decoded)
        except json.JSONDecodeError as e:
            print(f"  warn: payload parse error: {e}", file=sys.stderr)
            return None
    return None


def extract_tipster(payload: dict, slug: str) -> dict:
    """Pull the bits we need from one tipster's payload."""
    portfolio = payload.get("PORTFOLIO", {}) or {}
    # Keys look like "<slug>_"
    pf_key = next((k for k in portfolio if k.startswith(slug)), None)
    pf = portfolio.get(pf_key) if pf_key else {}
    overview = pf.get("overview") if isinstance(pf, dict) else None

    stats_month = payload.get("STATS_MONTH", {}) or {}
    # Keys look like "<slug>_<period>" where period 6 = "all time"
    sm_key = next((k for k in stats_month if k.startswith(slug)), None)
    months = stats_month.get(sm_key, []) if sm_key else []

    stats_sport = payload.get("STATS_SPORT", {}) or {}
    sp_key = next((k for k in stats_sport if k.startswith(slug)), None)
    sports = stats_sport.get(sp_key, []) if sp_key else []
    football_only = bool(sports) and all(
        (s.get("sport", {}) or {}).get("reference") == "football" for s in sports
    )

    return {
        "slug": slug,
        "name": (pf.get("name") if isinstance(pf, dict) else None) or slug,
        "active": pf.get("active") if isinstance(pf, dict) else None,
        "football_only": football_only,
        "overview": overview,
        "monthly": months,
        "sports": sports,
    }


def fetch_tipster(scraper, slug: str, *, retries: int = 3) -> Optional[dict]:
    url = f"https://tipstrr.com/tipster/{slug}/stats"
    last_err = None
    for attempt in range(retries):
        try:
            r = scraper.get(url, timeout=45)
            if r.status_code == 200 and r.text:
                payload = decode_payload(r.text)
                if payload is None:
                    last_err = "payload-not-found"
                else:
                    return extract_tipster(payload, slug)
            else:
                last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = repr(e)
        wait = 1.5 ** attempt
        print(f"  warn: {slug} fetch attempt {attempt + 1} failed "
              f"({last_err}); retry in {wait:.1f}s", file=sys.stderr)
        time.sleep(wait)
    print(f"  ERROR: {slug} bailed: {last_err}", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slugs", nargs="+", default=DEFAULT_SLUGS,
                    help="Tipstrr tipster slugs to scrape")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "mobile": False}
    )

    all_tipsters: list[dict] = []
    for slug in args.slugs:
        print(f"Fetching {slug} ...")
        info = fetch_tipster(scraper, slug)
        if info is None:
            continue
        n_months = len(info.get("monthly") or [])
        ov = info.get("overview") or {}
        print(f"  {slug}: football_only={info['football_only']} "
              f"total_tips={ov.get('tips')} ROI={ov.get('roi')} "
              f"months={n_months}")
        all_tipsters.append(info)
        time.sleep(random.uniform(0.8, 1.4))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_tipsters, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(all_tipsters)} tipsters to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
