"""
SignalOdds — soccer past-predictions scraper.

Walks https://signalodds.com/predictions/past?sport=soccer&page=N from page 1
until the response stops returning prediction cards. Parses each card directly
from server-rendered HTML (no JS execution needed) and writes deterministic
rows to dev/active/signalodds_soccer.json.

About the page content:
  - 12 cards per page (sometimes fewer on the last page)
  - Two card flavours: "open" (full pick + odds + bookmaker + EV + confidence
    + model) and "premium/PRO" (pick, odds and model exact value hidden;
    confidence and EV exposed only as a band like "80-90% / +0-2%"). Premium
    cards still expose league, date, status (Correct/Incorrect/Void), score,
    and that the pick belongs to "The Oracle".
  - Each card sits inside a div.grid.md:grid-cols-2.lg:grid-cols-3.gap-4 with
    exactly 12 direct children — that's how we anchor the parser.

The scraper writes:
  dev/active/signalodds_soccer.json    list[dict]  — final corpus (deduped on event_url + model)
  dev/active/signalodds_soccer.cursor  text        — last successfully written page

Resume behaviour: on startup the cursor file is read; scraping continues from
cursor+1. Use --restart to wipe and start over.

Politeness: a randomised 600–1100 ms gap between requests; gzip; descriptive
User-Agent; 3 retries with exponential backoff on transient network errors.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import requests
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dev" / "active" / "signalodds_soccer.json"
CURSOR_PATH = ROOT / "dev" / "active" / "signalodds_soccer.cursor"

BASE_URL = "https://signalodds.com/predictions/past"
SPORT = "soccer"
USER_AGENT = (
    "OddsIntel-Audit/1.0 (+https://oddsintel.com; competitor ROI audit; "
    "Mozilla/5.0 compatible)"
)
PAGE_SIZE = 12


@dataclass
class Prediction:
    page: int                                # source page number
    is_premium: bool                         # True if pick details are paywalled
    league: Optional[str]
    league_url: Optional[str]
    event_url: Optional[str]
    detail_url: Optional[str]                # /predictions/{model}/{event}/{selection}
    kickoff_text: Optional[str]              # raw label like "Today · 2:00 AM" / "Jun 23 · 11:00 PM"
    status: Optional[str]                    # Correct | Incorrect | Void
    home_team: Optional[str]
    away_team: Optional[str]
    score_home: Optional[int]
    score_away: Optional[int]
    market: Optional[str]                    # e.g. "Match Result"
    pick: Optional[str]                      # selection text, e.g. "Colombia Win"
    ev_pct: Optional[float]                  # +0.4  (open only; premium gives a range band)
    ev_band: Optional[str]                   # "+0-2%" for premium
    confidence_pct: Optional[float]          # 90  (open only)
    confidence_band: Optional[str]           # "70-80%" for premium
    model_name: Optional[str]
    model_url: Optional[str]
    odds: Optional[float]
    bookmaker: Optional[str]


def _classes(el: Tag) -> list[str]:
    cls = el.get("class")
    if not cls:
        return []
    return list(cls) if isinstance(cls, (list, tuple)) else [cls]


def _has_classes(el: Tag, *needed: str) -> bool:
    cls = set(_classes(el))
    return all(n in cls for n in needed)


def _has_substr(el: Tag, substr: str) -> bool:
    """True if any class on `el` contains the substring."""
    return any(substr in c for c in _classes(el))


def _find_grids(soup: BeautifulSoup) -> list[Tag]:
    """Return all grids whose direct-child count equals PAGE_SIZE."""
    out: list[Tag] = []
    for div in soup.find_all("div"):
        if _has_classes(div, "grid", "md:grid-cols-2", "lg:grid-cols-3", "gap-4"):
            kids = div.find_all(recursive=False)
            if 1 <= len(kids) <= PAGE_SIZE:
                out.append(div)
    return out


def _pick_cards_grid(soup: BeautifulSoup) -> Optional[Tag]:
    """The page renders the prediction list as ONE grid. There are smaller
    grids elsewhere (filters, footer sections). We prefer the FIRST grid with
    PAGE_SIZE children; fall back to the largest grid we saw if everything is
    short (last page).
    """
    grids = _find_grids(soup)
    if not grids:
        return None
    full = [g for g in grids if len(g.find_all(recursive=False)) == PAGE_SIZE]
    if full:
        return full[0]
    # last page may have fewer items
    grids.sort(key=lambda g: len(g.find_all(recursive=False)), reverse=True)
    return grids[0]


def _text_or_none(el: Optional[Tag]) -> Optional[str]:
    if el is None:
        return None
    return el.get_text(strip=True) or None


def _parse_int(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    s = s.strip()
    try:
        return int(s)
    except ValueError:
        return None


def _parse_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.replace("+", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_card(card: Tag, page: int) -> Optional[Prediction]:
    """Parse a single card div. Tolerant: missing fields return None.

    The card layout (after stripping the SVG/icon noise):

        [league-link] [event-link "Today · 2:00 AM"] [status-badge]
        H2: [home] [score] [away]
        Body block:
          AI Pick · {market}             OR  AI Pick · Premium
          {pick text}                        ███ ███ ███           (blurred)
          [EV badge +0.4%] [Conf badge 90%]  Confidence 70-80% · EV +0-2%
          {model link}                       {model link} PRO
          {odds @ bookmaker}                 ▓.▓▓ ▓▓▓▓▓▓▓          (blurred)
        Footer: [Prediction Details] [Bet at X]  OR  [Event Details] [Get this pick]
    """
    import re as _re
    is_premium = any(_has_substr(el, "blur-sm") for el in card.find_all("div"))

    # Header row
    header = next((d for d in card.find_all("div") if _has_substr(d, "border-b")), None)
    league_a = header.find("a", href=lambda h: h and "/leagues/" in h) if header else None
    league = _text_or_none(league_a)
    league_url = league_a.get("href") if league_a else None

    event_a = header.find("a", href=lambda h: h and "/events/" in h) if header else None
    event_url = event_a.get("href") if event_a else None
    kickoff_text = event_a.get_text(separator=" ", strip=True) if event_a else None

    # Status: a "rounded-full" pill with "Correct"/"Incorrect"/"Void"
    status = None
    for label in ("Correct", "Incorrect", "Void"):
        if card.find(string=label):
            status = label
            break

    # Teams + score (H2 element)
    h2 = card.find("h2")
    home_team = away_team = None
    score_home = score_away = None
    if h2:
        team_links = h2.find_all("a", href=lambda h: h and "/teams/" in h)
        if len(team_links) >= 2:
            home_team = _text_or_none(team_links[0])
            away_team = _text_or_none(team_links[1])
        score_span = next(
            (sp for sp in h2.find_all("span") if _has_substr(sp, "tabular-nums")),
            None,
        )
        if score_span is not None:
            # Inner text is e.g. "0 - 1" with HTML comments between — strip them
            txt = score_span.get_text(separator=" ", strip=True)
            m = _re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", txt)
            if m:
                score_home, score_away = int(m.group(1)), int(m.group(2))

    # Body block — the colored left-border container that holds "AI Pick"
    market = pick = ev_pct = ev_band = conf_pct = conf_band = None
    model_name = model_url = odds = bookmaker = None

    body = None
    for div in card.find_all("div"):
        if "border-l-2" in _classes(div) and (
            _has_substr(div, "match-border") or _has_substr(div, "premium")
        ):
            body = div
            break

    if body is not None:
        # The first child div carries "AI Pick · {market}" or "AI Pick · Premium"
        ai_pick_label = next(
            (d for d in body.find_all("div") if _has_substr(d, "uppercase")
             and _has_substr(d, "tracking-wide")),
            None,
        )
        ai_pick_txt = ai_pick_label.get_text(separator=" ", strip=True) if ai_pick_label else ""
        if "·" in ai_pick_txt:
            market = ai_pick_txt.split("·", 1)[1].strip()
        elif is_premium:
            market = "Premium"

        # Pick line — bold base text right after the label
        pick_div = next(
            (d for d in body.find_all("div")
             if "font-bold" in _classes(d) and "leading-tight" in _classes(d)),
            None,
        )
        if pick_div and not is_premium:
            pick = pick_div.get_text(strip=True) or None

        # EV / confidence
        if not is_premium:
            # Two coloured pill spans: one starts with "EV", the other ends with "%"
            for sp in body.find_all("span"):
                t = sp.get_text(separator=" ", strip=True)
                if t.startswith("EV") and ev_pct is None:
                    ev_pct = _parse_float(t.replace("EV", "").strip())
                elif t.endswith("%") and not t.startswith("EV") and "+" not in t and conf_pct is None:
                    conf_pct = _parse_float(t)
        else:
            p = body.find("p")
            if p:
                txt = p.get_text(separator=" ", strip=True)
                m = _re.search(r"Confidence\s*([\d\-+%]+)", txt)
                if m:
                    conf_band = m.group(1)
                m = _re.search(r"EV\s*([\d\-+%]+)", txt)
                if m:
                    ev_band = m.group(1)

        # Model name + URL
        model_a = body.find("a", href=lambda h: h and "/models/" in h)
        if model_a is not None:
            model_name = _text_or_none(model_a)
            model_url = model_a.get("href")

        # Odds (open only): the bookmaker badge sits in a small pill with
        # text like "1.59" followed by a bookmaker link.
        if not is_premium:
            book_a = body.find("a", href=lambda h: h and "/bookmakers/" in h)
            if book_a is not None:
                bookmaker = _text_or_none(book_a)
                # The numeric "1.59" is the first child of the span that wraps
                # book_a. NextJS splits text via HTML comments so we walk the
                # parent span's child strings.
                parent_span = book_a.parent
                if parent_span is not None:
                    for ch in parent_span.children:
                        if isinstance(ch, str):
                            txt = ch.strip()
                            try:
                                v = float(txt)
                                if 1.01 <= v <= 1000:
                                    odds = v
                                    break
                            except ValueError:
                                continue
            # Fallback: any span whose direct string is parseable as a decimal
            if odds is None:
                for sp in body.find_all("span"):
                    if sp.find("a"):
                        for ch in sp.children:
                            if isinstance(ch, str):
                                txt = ch.strip()
                                try:
                                    v = float(txt)
                                    if 1.01 <= v <= 1000:
                                        odds = v
                                        break
                                except ValueError:
                                    pass
                        if odds is not None:
                            break

    # Detail URL (Prediction Details footer link)
    detail_a = card.find(
        "a",
        href=lambda h: h and h.startswith("/predictions/") and h.count("/") >= 4,
    )
    detail_url = detail_a.get("href") if detail_a else None

    return Prediction(
        page=page,
        is_premium=is_premium,
        league=league,
        league_url=league_url,
        event_url=event_url,
        detail_url=detail_url,
        kickoff_text=kickoff_text,
        status=status,
        home_team=home_team,
        away_team=away_team,
        score_home=score_home,
        score_away=score_away,
        market=market,
        pick=pick,
        ev_pct=ev_pct,
        ev_band=ev_band,
        confidence_pct=conf_pct,
        confidence_band=conf_band,
        model_name=model_name,
        model_url=model_url,
        odds=odds,
        bookmaker=bookmaker,
    )


def parse_page(html: str, page: int) -> list[Prediction]:
    soup = BeautifulSoup(html, "lxml")
    grid = _pick_cards_grid(soup)
    if grid is None:
        return []
    out: list[Prediction] = []
    for card in grid.find_all(recursive=False):
        try:
            p = parse_card(card, page=page)
            if p is not None:
                out.append(p)
        except Exception as e:  # parser-tolerant
            print(f"  warn: card parse error on page {page}: {e}", file=sys.stderr)
    return out


def _row_key(p: Prediction) -> str:
    """Deterministic dedup key. event_url + model_name uniquely identifies a
    (match, model) row across paging refreshes; detail_url is a fallback."""
    return (p.detail_url
            or (p.event_url or "") + "::" + (p.model_name or ""))


def load_existing() -> tuple[list[dict], int]:
    rows: list[dict] = []
    cursor = 0
    if OUT_PATH.exists():
        try:
            rows = json.loads(OUT_PATH.read_text())
            if not isinstance(rows, list):
                rows = []
        except Exception:
            rows = []
    if CURSOR_PATH.exists():
        try:
            cursor = int(CURSOR_PATH.read_text().strip() or "0")
        except Exception:
            cursor = 0
    return rows, cursor


def save_progress(rows: list[dict], cursor: int) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    tmp.replace(OUT_PATH)
    CURSOR_PATH.write_text(str(cursor))


def fetch_page(session: requests.Session, page: int, *, retries: int = 3) -> Optional[str]:
    url = f"{BASE_URL}?sport={SPORT}&page={page}"
    last_err: Optional[str] = None
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = repr(e)
        # backoff
        wait = 1.5 ** attempt
        print(f"  warn: page {page} fetch failed ({last_err}); retry in {wait:.1f}s",
              file=sys.stderr)
        time.sleep(wait)
    print(f"  ERROR: page {page} gave up after {retries} attempts ({last_err})",
          file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-pages", type=int, default=10_000,
                    help="hard upper bound on pages to walk (default 10000)")
    ap.add_argument("--restart", action="store_true",
                    help="ignore cursor and start from page 1")
    ap.add_argument("--end-page", type=int, default=None,
                    help="explicit stop page (for testing)")
    args = ap.parse_args()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows, cursor = load_existing()
    if args.restart:
        rows, cursor = [], 0

    by_key: dict[str, dict] = {_row_key(Prediction(**r)): r for r in rows} if False else {}
    # Build dedup index from existing rows (which are dicts, not Prediction objects)
    by_key = {}
    for r in rows:
        # build the same key from a dict
        key = (r.get("detail_url")
               or (r.get("event_url") or "") + "::" + (r.get("model_name") or ""))
        by_key[key] = r

    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en-US,en;q=0.9",
    })

    start_page = cursor + 1
    print(f"Resuming from page {start_page} (existing rows: {len(rows)})")
    consecutive_empty = 0
    pages_walked = 0
    new_rows_added = 0

    page = start_page
    while page <= args.max_pages:
        if args.end_page is not None and page > args.end_page:
            break
        html = fetch_page(session, page)
        if html is None:
            print(f"page {page}: bailing — repeated fetch failures", file=sys.stderr)
            break
        preds = parse_page(html, page)
        pages_walked += 1
        if not preds:
            consecutive_empty += 1
            print(f"page {page}: 0 cards (consecutive empty: {consecutive_empty})")
            if consecutive_empty >= 3:
                print("Stopping — 3 consecutive empty pages.")
                break
        else:
            consecutive_empty = 0
            added = 0
            for p in preds:
                key = _row_key(p)
                if key in by_key:
                    continue
                d = asdict(p)
                by_key[key] = d
                rows.append(d)
                added += 1
            new_rows_added += added
            print(f"page {page}: parsed {len(preds)} (added {added} new) | "
                  f"corpus={len(rows)}")

        # Persist every 5 pages or at the end
        if pages_walked % 5 == 0:
            save_progress(rows, page)

        # politeness
        time.sleep(random.uniform(0.6, 1.1))
        page += 1

    save_progress(rows, page - 1)
    print(f"\nDone. pages walked: {pages_walked}, new rows: {new_rows_added}, "
          f"corpus total: {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
