#!/usr/bin/env python3
"""
HLTV match-page odds scraper — populates the bookie-market reference.

Each HLTV match page (https://www.hltv.org/matches/{id}/...) lists ~30-40
bookmakers' odds in one table. Taking the MEDIAN across all listed books
gives a robust market consensus (immune to single-book outliers — some
crypto books at the long-tail end can be wildly off).

Why median over mean:
- One bookmaker quoting 1.05 vs market 1.20 distorts mean by ~1.5pp.
- Median is unmoved by up to half the books being wrong.
- For betting edge calc, "what does the market think" matters more than
  "what does any specific book think" — median is the cleaner question.

Population columns on cs2_upcoming_matches:
- bookie_odds1 / bookie_odds2 — median across all HLTV-listed books.
  These are what the existing bot edge calc reads.

Scope:
- HLTV-sourced rows only (bo3gg_id < 0, so we can recover hltv_match_id
  as abs(bo3gg_id)). bo3.gg-sourced rows would need a separate slug lookup
  to find their HLTV equivalent — out of scope here.

Usage:
    python3 scripts/esports/cs2_hltv_match_odds.py            # dry-run
    python3 scripts/esports/cs2_hltv_match_odds.py --record   # write DB

Polite: 1.5s pacing between page fetches. Plain requests with browser UA;
FlareSolverr fallback when plain GET gets 403'd.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests",
                            "--break-system-packages", "-q"])
    import requests

from dotenv import load_dotenv

load_dotenv()

from workers.api_clients.db import execute_query, execute_write  # noqa: E402


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

POLITE_DELAY_S = 1.5  # pacing between requests, avoids HLTV rate-limit
WINDOW_HOURS = 48  # only fetch odds for matches in the next 48h

# Pattern: a bookmaker row in the odds table. Anchors on title="<BookName>"
# (each row has a logo with the title attr) followed by two odds-cell <td>s.
# The {0,2000} bound prevents the regex from spanning multiple rows.
_BOOKIE_ROW_RE = re.compile(
    r'title="([^"]+)".{0,2000}?'
    r'<td[^>]*class="odds-cell[^"]*"[^>]*>.*?>(\d+\.\d+)</a>.*?'
    r'<td[^>]*class="odds-cell[^"]*"[^>]*>.*?>(\d+\.\d+)</a>',
    re.DOTALL,
)

# HLTV requires the URL slug (`/matches/{id}/{slug}`); plain `/matches/{id}`
# returns 404. We harvest the slug from the /matches list page in one shot
# and build an id → slug map for the run.
_SLUG_RE = re.compile(r'/matches/(\d+)/([a-z0-9\-]+)"')


def fetch_url(url: str, session_name: str = "hltv_match_odds") -> str | None:
    """Plain GET with browser UA; FlareSolverr fallback on 4xx/5xx."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        if r.status_code == 200:
            return r.text
        print(f"  [info] HTTP {r.status_code} on {url[-60:]} — trying FlareSolverr")
    except Exception as e:
        print(f"  [info] plain GET failed ({e}) — trying FlareSolverr")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        if is_available():
            text = fs_fetch(url, session=session_name)
            if text:
                return text
    except Exception as e:
        print(f"  [warn] FlareSolverr fallback failed: {e}")
    return None


def build_slug_map() -> dict[int, str]:
    """One fetch of /matches → {hltv_match_id: 'team1-vs-team2-event-slug'}.

    HLTV 404's on /matches/{id} alone — needs the full slugged URL.
    Building this map once costs 1 request and unlocks all downstream odds
    fetches in the same run.
    """
    html = fetch_url("https://www.hltv.org/matches", session_name="hltv_matches_list")
    if not html:
        return {}
    out: dict[int, str] = {}
    for mid_str, slug in _SLUG_RE.findall(html):
        try:
            out[int(mid_str)] = slug
        except ValueError:
            continue
    return out


def fetch_match_page(hltv_match_id: int, slug_map: dict[int, str]) -> str | None:
    slug = slug_map.get(hltv_match_id)
    if not slug:
        print(f"  [skip] no slug for /matches/{hltv_match_id} in /matches list")
        return None
    return fetch_url(f"https://www.hltv.org/matches/{hltv_match_id}/{slug}")


def parse_bookmaker_odds(html: str) -> list[tuple[str, float, float]]:
    """Return list of (bookmaker_name, odds_home, odds_away). Dedupes by
    (bookie, odds_home, odds_away) tuple — the regex can match the same
    row twice if HLTV nests promo wrappers around the logo cell."""
    seen: set[tuple[str, str, str]] = set()
    rows: list[tuple[str, float, float]] = []
    for bk, o1, o2 in _BOOKIE_ROW_RE.findall(html):
        key = (bk, o1, o2)
        if key in seen:
            continue
        seen.add(key)
        try:
            f1, f2 = float(o1), float(o2)
        except ValueError:
            continue
        if f1 <= 1.0 or f2 <= 1.0:
            continue  # garbage / illegal odds
        rows.append((bk, f1, f2))
    return rows


def median_odds(rows: list[tuple[str, float, float]]) -> tuple[float, float] | None:
    """Median across all listed books. Filters out implausible vig:
    if sum of inverse odds < 1.0 (positive expected value market = book error
    or partial row) we discard. Standard CS2 books have ~5-8% vig so the
    implied sum should be ~1.05 to ~1.15."""
    if len(rows) < 3:
        return None  # too thin — median needs reasonable sample
    o1 = sorted(r[1] for r in rows)
    o2 = sorted(r[2] for r in rows)
    m1, m2 = median(o1), median(o2)
    if 1/m1 + 1/m2 < 0.98:  # arbitrage opportunity = parse error, not real
        return None
    return (m1, m2)


def load_targets(only_missing: bool = True) -> list[dict]:
    """Upcoming HLTV-sourced rows within the window. bo3gg_id < 0 means
    HLTV-sourced; abs(bo3gg_id) recovers the hltv_match_id."""
    where_extra = " AND (bookie_odds1 IS NULL OR bookie_odds2 IS NULL)" if only_missing else ""
    return execute_query(
        f"""SELECT id, bo3gg_id AS bo3gg_id_signed,
                  team1, team2, kickoff_time, league
           FROM cs2_upcoming_matches
           WHERE bo3gg_id < 0
             AND kickoff_time >= NOW()
             AND kickoff_time < NOW() + INTERVAL '{WINDOW_HOURS} hours'
             {where_extra}
           ORDER BY kickoff_time"""
    )


def write_odds(row_id: int, m1: float, m2: float, n_books: int) -> None:
    execute_write(
        """UPDATE cs2_upcoming_matches
           SET bookie_odds1 = %s, bookie_odds2 = %s,
               scanned_at = NOW()
           WHERE id = %s""",
        (round(m1, 3), round(m2, 3), row_id),
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--record", action="store_true",
                   help="Write median odds to cs2_upcoming_matches (default: dry-run)")
    p.add_argument("--refresh-all", action="store_true",
                   help="Also re-fetch matches that already have bookie_odds (default: only NULL)")
    args = p.parse_args()

    targets = load_targets(only_missing=not args.refresh_all)
    print(f"\n=== HLTV match-page odds scrape  ({len(targets)} target rows, next {WINDOW_HOURS}h) ===")
    if not targets:
        print("  No HLTV-sourced rows in window with missing odds.")
        return 0

    print("  building HLTV match-id → URL-slug map (one /matches fetch)…")
    slug_map = build_slug_map()
    print(f"  {len(slug_map)} match slugs available")
    if not slug_map:
        print("  [fatal] could not fetch /matches list — abort.")
        return 1

    written = 0
    skipped_no_odds = 0
    skipped_thin = 0
    for t in targets:
        hltv_id = abs(int(t["bo3gg_id_signed"]))
        html = fetch_match_page(hltv_id, slug_map)
        if not html:
            skipped_no_odds += 1
            continue
        rows = parse_bookmaker_odds(html)
        meds = median_odds(rows)
        if meds is None:
            print(f"  [thin] {t['team1']} vs {t['team2']:<25} only {len(rows)} books — skipped")
            skipped_thin += 1
            time.sleep(POLITE_DELAY_S)
            continue
        m1, m2 = meds
        implied_sum = 1/m1 + 1/m2
        print(f"  {t['team1'][:18]:18} vs {t['team2'][:18]:18}  "
              f"median {m1:.2f}/{m2:.2f}  ({len(rows)} books, vig {(implied_sum-1)*100:.1f}%)")
        if args.record:
            write_odds(t["id"], m1, m2, len(rows))
            written += 1
        time.sleep(POLITE_DELAY_S)

    print(f"\n  parsed {len(targets) - skipped_no_odds - skipped_thin}/{len(targets)} matches")
    print(f"  {skipped_no_odds} skipped (page fetch failed)")
    print(f"  {skipped_thin} skipped (< 3 books listed)")
    if args.record:
        print(f"  ✓ wrote {written} rows to cs2_upcoming_matches.bookie_odds1/2")
    else:
        print("  [DRY-RUN] no DB writes. Re-run with --record to persist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
