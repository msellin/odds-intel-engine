"""
Forebet — settled-history scraper.

Forebet publishes mathematical predictions and statistics for football matches.
Each day's results are reachable via:

    https://www.forebet.com/en/football-predictions/predictions-1x2/YYYY-MM-DD
    https://www.forebet.com/en/football-predictions/under-over-25-goals/YYYY-MM-DD

Important coverage limit (verified 2026-06-24):
  - Historical date URLs work for roughly the last ~40 days; older dates
    silently fall back to "today" content. Their UI exposes a date strip that
    only goes back ~30 days, then forward ~14 days. So a backfill from May 4
    (~51 days ago) is impossible — earliest reachable settled date is ~38 days
    back. The scraper validates that the rows returned actually match the
    requested date (via the <time datetime="..."> attr) and drops anything
    that doesn't.

Each row is a div.rcnt with:
  * <time datetime="YYYY-MM-DD"> kickoff date
  * itemprop="name" meta = "Home vs Away"
  * div.fprc spans = implied 1/X/2 percentages (Forebet's own model)
  * div.predict_y / div.predict_no = settlement: y=correct, no=incorrect
  * div.forepr span = predicted outcome (1 / X / 2 / Over / Under)
  * div.haodd spans = bookmaker odds (1, X, 2) or (Over, Under) for OU page
  * div.lscr_td b.l_scr = final score "H - A"
  * Page caps at ~44 matches/day (Forebet's own priority filter)

The scraper emits dev/active/forebet_raw.json — one row per (date × market × match).

About 44 picks/day × ~38 reachable days × 2 markets ≈ ~3300 rows; trim to the
audit window in audit_vs_forebet.py.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dev" / "active" / "forebet_raw.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0 Safari/537.36 "
    "OddsIntel-Audit/1.0 (+https://oddsintel.app; competitor ROI audit)"
)

# Two markets we mirror in our own production model. BTTS / HT etc. exist on
# Forebet but we don't trade them, so excluded.
MARKETS = {
    "1x2": "predictions-1x2",
    "over_under_25": "under-over-25-goals",
}

PAGE_CAP_PER_DAY = 44   # observed cap; sanity guard


@dataclass
class ForebetPick:
    requested_date: str          # YYYY-MM-DD we asked for
    match_date: Optional[str]    # YYYY-MM-DD from <time datetime=...>
    market: str                  # "1x2" | "over_under_25"
    match_name: Optional[str]    # "Home vs Away"
    home_team: Optional[str]
    away_team: Optional[str]
    league_short: Optional[str]  # e.g. "Uy2"
    pick: Optional[str]          # "1" | "X" | "2" | "Over" | "Under"
    settled: bool                # True if predict_y / predict_no was found
    correct: Optional[bool]      # True for predict_y, False for predict_no
    score_home: Optional[int]
    score_away: Optional[int]
    odds_home: Optional[float]   # only set on 1x2 rows
    odds_draw: Optional[float]
    odds_away: Optional[float]
    odds_over: Optional[float]   # only set on OU rows
    odds_under: Optional[float]
    pick_odds: Optional[float]   # the bookmaker odds AT the side we picked


def _classes(el: Tag) -> list[str]:
    cls = el.get("class")
    if not cls:
        return []
    return list(cls) if isinstance(cls, (list, tuple)) else [cls]


def _text(el: Optional[Tag]) -> Optional[str]:
    if el is None:
        return None
    t = el.get_text(strip=True)
    return t or None


def _parse_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    s = s.strip()
    if s in ("-", "no", "yes", ""):
        return None
    try:
        v = float(s)
        if 1.01 <= v <= 1000:
            return v
    except ValueError:
        pass
    return None


def _parse_score(s: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not s:
        return None, None
    import re
    m = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def parse_row(row: Tag, market: str, requested_date: str) -> Optional[ForebetPick]:
    # Date
    t = row.find("time")
    match_date = t.get("datetime") if t else None

    # Match name
    nm = row.find("meta", attrs={"itemprop": "name"})
    match_name = nm.get("content") if nm else None
    home = _text(row.select_one('span.homeTeam span[itemprop="name"]'))
    away = _text(row.select_one('span.awayTeam span[itemprop="name"]'))

    # League short tag (e.g. "Uy2"); falls back to alt text on the flag image
    league_short = _text(row.select_one("span.shortTag"))

    # Settlement: predict_y = correct, predict_no = incorrect, neither = open
    pred_div = row.select_one("div.predict_y, div.predict_no")
    settled = False
    correct: Optional[bool] = None
    if pred_div is not None:
        cls = _classes(pred_div)
        if "predict_y" in cls:
            settled, correct = True, True
        elif "predict_no" in cls:
            settled, correct = True, False

    # Pick label
    pick = None
    forepr = row.select_one(".forepr")
    if forepr is not None:
        pick = forepr.get_text(strip=True) or None

    # Score
    score_home, score_away = _parse_score(_text(row.select_one(".lscr_td .l_scr")))

    # Odds — haodd spans differ between markets
    odds_home = odds_draw = odds_away = odds_over = odds_under = None
    haodd_spans = row.select(".bigOnly.prmod .haodd > span")
    odds_vals = [_parse_float(s.get_text(strip=True)) for s in haodd_spans]
    if market == "1x2":
        if len(odds_vals) >= 3:
            odds_home, odds_draw, odds_away = odds_vals[0], odds_vals[1], odds_vals[2]
    else:  # over_under_25
        if len(odds_vals) >= 2:
            odds_over, odds_under = odds_vals[0], odds_vals[1]

    # Map pick to the bookmaker odds for the chosen side
    pick_odds = None
    if pick:
        p = pick.strip()
        if market == "1x2":
            pick_odds = {"1": odds_home, "X": odds_draw, "2": odds_away}.get(p)
        else:
            pick_odds = {"Over": odds_over, "Under": odds_under}.get(p)

    return ForebetPick(
        requested_date=requested_date,
        match_date=match_date,
        market=market,
        match_name=match_name,
        home_team=home,
        away_team=away,
        league_short=league_short,
        pick=pick,
        settled=settled,
        correct=correct,
        score_home=score_home,
        score_away=score_away,
        odds_home=odds_home,
        odds_draw=odds_draw,
        odds_away=odds_away,
        odds_over=odds_over,
        odds_under=odds_under,
        pick_odds=pick_odds,
    )


def fetch_day(session: requests.Session, d: str, market_slug: str,
              *, retries: int = 3) -> Optional[str]:
    url = f"https://www.forebet.com/en/football-predictions/{market_slug}/{d}"
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"  warn: {d}/{market_slug} HTTP {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"  warn: {d}/{market_slug} {e}", file=sys.stderr)
        time.sleep(1.5 ** attempt)
    return None


def parse_page(html: str, market: str, requested_date: str) -> list[ForebetPick]:
    soup = BeautifulSoup(html, "lxml")
    rows = soup.select("div.rcnt")
    out: list[ForebetPick] = []
    for r in rows:
        try:
            p = parse_row(r, market, requested_date)
            if p is None:
                continue
            # Drop rows whose match_date doesn't match the requested date.
            # Forebet silently serves today's data when an old date is requested
            # — these mismatches reveal that the date was out of range.
            if p.match_date and p.match_date != requested_date:
                continue
            out.append(p)
        except Exception as e:
            print(f"  warn: row parse error on {requested_date}/{market}: {e}",
                  file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default=None,
                    help="YYYY-MM-DD (default: today - 40 days)")
    ap.add_argument("--end", default=None,
                    help="YYYY-MM-DD inclusive (default: yesterday)")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    today = date.today()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end \
        else (today - timedelta(days=1))
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start \
        else (today - timedelta(days=40))

    if start > end:
        print(f"FATAL: start {start} after end {end}", file=sys.stderr)
        return 2

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
    })

    all_rows: list[dict] = []
    day = start
    days_walked = 0
    while day <= end:
        d_str = day.isoformat()
        for mkt_key, mkt_slug in MARKETS.items():
            html = fetch_day(session, d_str, mkt_slug)
            if html is None:
                print(f"  ERROR: {d_str}/{mkt_key} bail")
                continue
            picks = parse_page(html, mkt_key, d_str)
            if len(picks) > PAGE_CAP_PER_DAY * 1.5:
                print(f"  warn: {d_str}/{mkt_key} returned {len(picks)} > "
                      f"expected cap {PAGE_CAP_PER_DAY} — verifying date filter")
            for p in picks:
                all_rows.append(asdict(p))
            settled = sum(1 for p in picks if p.settled)
            print(f"  {d_str}/{mkt_key}: {len(picks)} rows ({settled} settled)")
            time.sleep(random.uniform(0.8, 1.3))
        days_walked += 1
        day += timedelta(days=1)

    # COMPETITOR-SCRAPES-WEEKLY-2026-08-01: Forebet's public history strip
    # currently exposes only ~7 days back (was ~30-40 in the doc-comment above),
    # so a fresh scrape alone loses historical breadth every week. Merge into
    # any existing snapshot, deduped on (match_date, market, match_name), to
    # accumulate coverage across weekly workflow runs.
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    merged: dict[tuple, dict] = {}
    if out.exists() and out.stat().st_size > 0:
        try:
            existing = json.loads(out.read_text())
            if isinstance(existing, list):
                for row in existing:
                    key = (row.get("match_date"), row.get("market"), row.get("match_name"))
                    merged[key] = row
                print(f"Merging with existing {len(merged)} rows from {out.name}")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  warn: couldn't merge existing snapshot ({e}); overwriting")
    for row in all_rows:
        key = (row.get("match_date"), row.get("market"), row.get("match_name"))
        merged[key] = row
    out.write_text(json.dumps(list(merged.values()), indent=2, ensure_ascii=False))
    print(f"\nDone. days walked: {days_walked}, fresh rows: {len(all_rows)}, "
          f"total after merge: {len(merged)}")
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
