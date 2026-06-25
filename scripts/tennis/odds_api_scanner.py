#!/usr/bin/env python3
"""
TENNIS-PAPER-BETS Phase 1.3 — tennis value scanner via The Odds API.

Replaces scripts/tennis/value_scanner.py (OddsPapi-based, deprecated 2026-06-25
when the 250 req/mo free cap was busted). The Odds API has 500 cred/mo free
and 100% Pinnacle coverage across active tour tournaments; one call per sport
returns all bookmakers bundled.

Flow:
  1. GET /sports → filter active tennis_* keys (typically 2-4 at any time)
  2. For each active sport: GET /sports/{key}/odds with Pinnacle + soft books
  3. For each event:
     - Extract Pinnacle prices → de-vig to fair probabilities
     - Upsert tennis_fixtures_today (full snapshot for admin page)
     - For each soft book: compute edge per selection, log to tennis_value_bets
       (RECORD_MIN_EDGE=0 → log all positive-edge observations for training)

Cost: 1 credit per active sport per scan (bookmakers bundled in one response).
Twice daily × ~3 active sports = ~6 credits/day = ~180/mo of 500 free.

Usage:
    python3 scripts/tennis/odds_api_scanner.py              # live, writes DB
    python3 scripts/tennis/odds_api_scanner.py --dry-run    # print only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parents[2] / ".env")

from workers.api_clients.db import execute_write  # noqa: E402
from scripts.tennis.bots_config import matching_bots  # noqa: E402

BASE = "https://api.the-odds-api.com/v4"

# ── Tunables (kept aligned with the retired value_scanner.py) ─────────────
RECORD_MIN_EDGE = 0.0       # log all positive-edge observations
DISPLAY_MIN_EDGE = 0.03     # highlight ≥3% in console
MAX_CREDIBLE_EDGE = 0.40    # > 40% is almost certainly stale data; drop
KELLY_FRAC = 0.25           # quarter Kelly
MAX_STAKE = 5.0
DEFAULT_STAKE = 1.0
FUTURE_CUTOFF_HOURS = 36    # scan today + next 36h kickoffs
START_FLOOR_MIN = 5         # require kickoff > now - 5 min (skip live/finished)

# ── Bookmakers ────────────────────────────────────────────────────────────
SHARP_BOOK = "pinnacle"
SOFT_BOOKS = [
    "bet365",
    "unibet_eu",
    "williamhill",
    "betway",
    "1xbet",
]
# coolbet intentionally excluded — scanned every 30 min via direct API path
# in scripts/tennis/place_coolbet_tennis.py; would just waste Odds API credits.
# betfair_ex_eu (Betfair Exchange) intentionally excluded — exchange prices
# naturally show 3-5% "edges" vs Pinnacle that get eaten by Betfair's 2-5%
# commission. Adding it would log synthetic edges that aren't real bettable
# opportunities. Reintroduce only with a commission-adjustment step.

ALL_BOOKS = [SHARP_BOOK, *SOFT_BOOKS]

_requests_remaining: int | None = None


def call(path: str, **params) -> tuple[int, dict | list | None]:
    """Wrapper around requests.get with Odds API auth + retry on 429."""
    global _requests_remaining
    key = os.environ.get("OA_KEY") or os.environ.get("ODDS_API_KEY", "")
    if not key:
        print("ERROR: OA_KEY / ODDS_API_KEY env var not set")
        sys.exit(1)
    params["apiKey"] = key
    url = f"{BASE}/{path.lstrip('/')}"
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"  HTTP error {path}: {e}")
            return -1, None
        if r.status_code == 429:
            wait = 2 ** attempt
            print(f"  Rate limited — waiting {wait}s (attempt {attempt+1}/4)")
            time.sleep(wait)
            continue
        rem = r.headers.get("x-requests-remaining")
        if rem is not None:
            _requests_remaining = int(rem)
        if r.status_code != 200:
            print(f"  API error {r.status_code} on {path}: {r.text[:200]}")
            return r.status_code, None
        return 200, r.json()
    print(f"  Giving up on {path} after repeated 429s")
    return 429, None


def list_active_tennis_sports() -> list[dict]:
    """Return active tennis sport entries: [{key, title, group}, ...]."""
    code, body = call("sports", all="true")
    if code != 200 or not isinstance(body, list):
        return []
    tennis = [
        s for s in body
        if isinstance(s, dict)
        and s.get("active")
        and "tennis" in (s.get("key") or "").lower()
    ]
    return tennis


def devig_two_way(home_raw: float, away_raw: float) -> tuple[float, float]:
    """Normalize implied probabilities to sum to 1 (proportional de-vig)."""
    h_prob = 1.0 / home_raw
    a_prob = 1.0 / away_raw
    total = h_prob + a_prob
    return h_prob / total, a_prob / total


def kelly_stake(edge: float, fair_prob: float, book_odds: float) -> float:
    """Quarter Kelly stake (units), capped at MAX_STAKE."""
    b = book_odds - 1.0
    if b <= 0:
        return 0.0
    k = (b * fair_prob - (1 - fair_prob)) / b
    k = max(0.0, k)
    return round(min(k * KELLY_FRAC, MAX_STAKE), 3)


def extract_book_prices(event: dict, bookmaker_key: str) -> tuple[float, float] | None:
    """
    Return (home_price, away_price) for the h2h market on this event, or None
    if the book isn't present or the market is suspended.
    """
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    if not home_team or not away_team:
        return None
    for bm in event.get("bookmakers") or []:
        if bm.get("key") != bookmaker_key:
            continue
        for mk in bm.get("markets") or []:
            if mk.get("key") != "h2h":
                continue
            outcomes = mk.get("outcomes") or []
            h_price = None
            a_price = None
            for o in outcomes:
                if o.get("name") == home_team:
                    h_price = o.get("price")
                elif o.get("name") == away_team:
                    a_price = o.get("price")
            if h_price and a_price and h_price > 1.0 and a_price > 1.0:
                return float(h_price), float(a_price)
    return None


def upsert_fixture_today(*, fixture_id: str, tournament_name: str,
                         player_home: str, player_away: str,
                         kickoff_time: str, pin_raw_home: float,
                         pin_raw_away: float, threshold_home: float,
                         threshold_away: float, pin_margin_pct: float,
                         dry_run: bool) -> None:
    if dry_run:
        return
    execute_write("""
        INSERT INTO tennis_fixtures_today
            (fixture_id, tournament_name, player_home, player_away,
             kickoff_time, pin_raw_home, pin_raw_away,
             threshold_home, threshold_away, pin_margin_pct, scanned_at)
        VALUES
            (%(fixture_id)s, %(tournament_name)s, %(player_home)s, %(player_away)s,
             %(kickoff_time)s, %(pin_raw_home)s, %(pin_raw_away)s,
             %(threshold_home)s, %(threshold_away)s, %(pin_margin_pct)s, now())
        ON CONFLICT (fixture_id) DO UPDATE SET
            tournament_name = EXCLUDED.tournament_name,
            player_home     = EXCLUDED.player_home,
            player_away     = EXCLUDED.player_away,
            kickoff_time    = EXCLUDED.kickoff_time,
            pin_raw_home    = EXCLUDED.pin_raw_home,
            pin_raw_away    = EXCLUDED.pin_raw_away,
            threshold_home  = EXCLUDED.threshold_home,
            threshold_away  = EXCLUDED.threshold_away,
            pin_margin_pct  = EXCLUDED.pin_margin_pct,
            scanned_at      = now()
    """, {
        "fixture_id": fixture_id, "tournament_name": tournament_name,
        "player_home": player_home, "player_away": player_away,
        "kickoff_time": kickoff_time, "pin_raw_home": pin_raw_home,
        "pin_raw_away": pin_raw_away,
        "threshold_home": round(threshold_home, 4),
        "threshold_away": round(threshold_away, 4),
        "pin_margin_pct": round(pin_margin_pct * 100, 2),
    })


def insert_value_bet(row: dict, dry_run: bool) -> None:
    """Insert one bot-segmented row. Row dict must include bot_id."""
    if dry_run:
        return
    execute_write("""
        INSERT INTO tennis_value_bets
            (fixture_id, tournament_name, player_home, player_away, surface,
             kickoff_time, market, selection,
             pin_fair_odds, pin_raw_home, pin_raw_away,
             bookmaker, book_odds, edge_pct, kelly_fraction, stake, scan_date,
             bot_id, notes)
        VALUES
            (%(fixture_id)s, %(tournament_name)s, %(player_home)s, %(player_away)s, %(surface)s,
             %(kickoff_time)s, %(market)s, %(selection)s,
             %(pin_fair_odds)s, %(pin_raw_home)s, %(pin_raw_away)s,
             %(bookmaker)s, %(book_odds)s, %(edge_pct)s, %(kelly_fraction)s, %(stake)s,
             CURRENT_DATE, %(bot_id)s, %(notes)s)
        ON CONFLICT (fixture_id, bookmaker, selection, scan_date, bot_id) DO UPDATE SET
            book_odds      = EXCLUDED.book_odds,
            edge_pct       = EXCLUDED.edge_pct,
            kelly_fraction = EXCLUDED.kelly_fraction,
            stake          = EXCLUDED.stake,
            logged_at      = now()
    """, row)


def scan_sport(sport: dict, dry_run: bool) -> tuple[int, int, int]:
    """
    Scan one tennis sport key. Returns (events_seen, events_with_pinnacle,
    value_bets_logged).
    """
    sport_key = sport["key"]
    sport_title = sport.get("title") or sport_key
    print(f"\n[scan] {sport_key}  ({sport_title})")

    code, body = call(
        f"sports/{sport_key}/odds",
        regions="eu",
        markets="h2h",
        bookmakers=",".join(ALL_BOOKS),
        oddsFormat="decimal",
        dateFormat="iso",
    )
    if code != 200 or not isinstance(body, list):
        print(f"  ⚠️  skipped — bad response (HTTP {code})")
        return 0, 0, 0

    now = datetime.now(timezone.utc)
    start_floor = now - timedelta(minutes=START_FLOOR_MIN)
    cutoff = now + timedelta(hours=FUTURE_CUTOFF_HOURS)

    events_seen = 0
    events_with_pin = 0
    bets_logged = 0
    bets_by_book: dict[str, int] = {}

    for ev in body:
        events_seen += 1
        commence = ev.get("commence_time")
        if not commence:
            continue
        try:
            start = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start < start_floor or start > cutoff:
            continue

        pin = extract_book_prices(ev, SHARP_BOOK)
        if not pin:
            continue
        events_with_pin += 1
        h_raw, a_raw = pin
        fair_h_prob, fair_a_prob = devig_two_way(h_raw, a_raw)
        fair_h_odds = 1.0 / fair_h_prob
        fair_a_odds = 1.0 / fair_a_prob
        pin_margin = (1.0 / h_raw + 1.0 / a_raw) - 1.0

        fixture_id = ev["id"]
        player_home = ev["home_team"]
        player_away = ev["away_team"]
        kickoff_iso = start.isoformat()

        upsert_fixture_today(
            fixture_id=fixture_id,
            tournament_name=sport_title,
            player_home=player_home, player_away=player_away,
            kickoff_time=kickoff_iso,
            pin_raw_home=h_raw, pin_raw_away=a_raw,
            threshold_home=fair_h_odds, threshold_away=fair_a_odds,
            pin_margin_pct=pin_margin, dry_run=dry_run,
        )

        for book in SOFT_BOOKS:
            soft = extract_book_prices(ev, book)
            if not soft:
                continue
            h_book, a_book = soft

            for selection, book_odds, fair_prob, fair_odds in [
                ("home", h_book, fair_h_prob, fair_h_odds),
                ("away", a_book, fair_a_prob, fair_a_odds),
            ]:
                edge = book_odds * fair_prob - 1.0
                if edge < RECORD_MIN_EDGE or edge > MAX_CREDIBLE_EDGE:
                    continue

                # Route observation through bot config — one observation may
                # land in multiple bot lanes (e.g. an edge of 0.06 qualifies
                # for both pin_broad ≥3% AND pin_selective ≥5%).
                matched = list(matching_bots(bookmaker=book, edge=edge))
                if not matched:
                    continue

                player = player_home if selection == "home" else player_away
                printed_value = False

                for bot_id, bot_cfg in matched:
                    bot_stake = (
                        kelly_stake(edge, fair_prob, book_odds)
                        if edge >= DISPLAY_MIN_EDGE
                        else 0.0
                    )
                    # Honour bot's stake unit if it overrides Kelly cap
                    bot_stake = min(bot_stake or 0.0, float(bot_cfg.get("stake", 1.0)))

                    row = {
                        "fixture_id":      fixture_id,
                        "tournament_name": sport_title,
                        "player_home":     player_home,
                        "player_away":     player_away,
                        "surface":         None,
                        "kickoff_time":    kickoff_iso,
                        "market":          "match_winner",
                        "selection":       selection,
                        "pin_fair_odds":   round(fair_odds, 4),
                        "pin_raw_home":    h_raw,
                        "pin_raw_away":    a_raw,
                        "bookmaker":       book,
                        "book_odds":       book_odds,
                        "edge_pct":        round(edge * 100, 2),
                        "kelly_fraction":  round(bot_stake / MAX_STAKE, 4) if bot_stake > 0 else 0.0,
                        "stake":           bot_stake,
                        "bot_id":          bot_id,
                        "notes":           f"pin_margin={round(pin_margin * 100, 2)}%  src=odds_api",
                    }

                    if edge >= DISPLAY_MIN_EDGE and not printed_value:
                        bot_labels = ",".join(b for b, _ in matched)
                        print(f"  ✅ VALUE  {sport_title[:24]:24s}  {player[:20]:20s}  "
                              f"{book:14s}  book={book_odds:.2f}  fair={fair_odds:.2f}  "
                              f"edge={edge*100:+.1f}%  → {bot_labels}")
                        printed_value = True

                    insert_value_bet(row, dry_run)
                    bets_logged += 1
                    bets_by_book[f"{book}/{bot_id}"] = bets_by_book.get(f"{book}/{bot_id}", 0) + 1

    print(f"  events: {events_seen} total → {events_with_pin} with Pinnacle  →  {bets_logged} value rows")
    for bk, n in bets_by_book.items():
        print(f"    {bk}: {n}")
    return events_seen, events_with_pin, bets_logged


def main(dry_run: bool = False) -> int:
    print("=" * 70)
    mode = "DRY RUN — no DB writes" if dry_run else "LIVE — logging to tennis_value_bets"
    print(f"TENNIS ODDS-API SCANNER  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}  ({mode})")
    print("=" * 70)

    sports = list_active_tennis_sports()
    if not sports:
        print("No active tennis sport keys — nothing to scan.")
        return 0
    print(f"Active tennis sports: {len(sports)}")
    for s in sports:
        print(f"  {s['key']:45s}  {s.get('title')}")

    totals = {"events": 0, "with_pin": 0, "value_bets": 0}
    for s in sports:
        ev, wp, vb = scan_sport(s, dry_run)
        totals["events"] += ev
        totals["with_pin"] += wp
        totals["value_bets"] += vb
        time.sleep(0.3)  # courtesy

    print("\n" + "=" * 70)
    print("SUMMARY")
    print(f"  sports scanned:           {len(sports)}")
    print(f"  events seen:              {totals['events']}")
    print(f"  events with Pinnacle:     {totals['with_pin']}")
    print(f"  value rows logged:        {totals['value_bets']}")
    if _requests_remaining is not None:
        print(f"  Odds API credits remaining this month: {_requests_remaining}")
    if not dry_run and totals["value_bets"]:
        print(f"  ✓ Wrote to tennis_value_bets (edge>0%; ≥3% highlighted)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print only; no DB writes")
    args = ap.parse_args()
    sys.exit(main(dry_run=args.dry_run))
