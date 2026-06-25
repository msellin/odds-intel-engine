#!/usr/bin/env python3
"""
TENNIS-PAPER-BETS Phase 1.5 — capture Pinnacle closing odds + compute CLV.

Runs frequently in the pre-kickoff window. For each tennis fixture in
tennis_fixtures_today with kickoff in the next CAPTURE_WINDOW_MIN minutes,
fetches the latest Pinnacle h2h price and writes it to tennis_value_bets
as `closing_odds` (overwriting earlier captures so the final stored value
is the closest-to-kickoff Pinnacle snap). Computes `clv = (book_odds /
closing_odds) - 1` in the same write.

We don't have sport_key on the rows yet (would need a migration), so each
run does a single /sports call to resolve {sport_title → sport_key} and
groups imminent fixtures by sport. Cheap pre-check skips everything if
no fixture is imminent.

Budget:
  - /sports call:  1 credit per run if anything imminent, else 0
  - /odds call:    1 credit per unique active sport with an imminent fixture
  - Worst case at 30-min cadence × 17h tennis day = 34 fires × 4 credits =
    ~136 credits/day. In practice many fires will be skipped (no imminent
    fixtures), so actual usage is far lower.

Usage:
    python3 scripts/tennis/capture_closing_odds.py                # live
    python3 scripts/tennis/capture_closing_odds.py --dry-run      # no DB writes
    python3 scripts/tennis/capture_closing_odds.py --window 60    # widen window
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

from workers.api_clients.db import execute_query, execute_write  # noqa: E402

BASE = "https://api.the-odds-api.com/v4"
CAPTURE_WINDOW_MIN_DEFAULT = 45  # capture fixtures kicking off in next 45 min

_requests_remaining: int | None = None


def call(path: str, **params) -> tuple[int, list | dict | None]:
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
            print(f"  429 — waiting {wait}s (attempt {attempt+1}/4)")
            time.sleep(wait)
            continue
        rem = r.headers.get("x-requests-remaining")
        if rem is not None:
            _requests_remaining = int(rem)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code} on {path}: {r.text[:200]}")
            return r.status_code, None
        return 200, r.json()
    return 429, None


def list_active_tennis_sport_map() -> dict[str, str]:
    """Return {sport_title: sport_key} for active tennis sports."""
    code, body = call("sports", all="true")
    if code != 200 or not isinstance(body, list):
        return {}
    return {
        s["title"]: s["key"]
        for s in body
        if isinstance(s, dict)
        and s.get("active")
        and "tennis" in (s.get("key") or "").lower()
        and s.get("title") and s.get("key")
    }


def find_imminent_fixtures(window_min: int) -> list[dict]:
    """
    Distinct fixtures that have at least one unsettled value-bet row kicking
    off within window_min minutes. We deliberately do NOT filter on
    `closing_odds IS NULL` here — every cron run within the window
    re-captures and overwrites, so the final stored value is the
    closest-to-kickoff Pinnacle snap (the actual "close"), not the earliest
    capture. Credit cost is per-sport per-fire, not per-row, so re-capturing
    doesn't cost extra.

    Reading from tennis_value_bets (not tennis_fixtures_today) means we never
    burn credits on a sport that has imminent fixtures but no rows needing CLV.
    """
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=window_min)
    rows = execute_query(
        """
        SELECT DISTINCT fixture_id, tournament_name, player_home, player_away,
                        kickoff_time
          FROM tennis_value_bets
         WHERE result IS NULL
           AND kickoff_time > now()
           AND kickoff_time <= %s
           AND fair_source = 'odds_api_pinnacle'
         ORDER BY kickoff_time ASC
        """,
        (cutoff,),
    )
    return rows or []


def extract_pinnacle_h2h(event: dict) -> tuple[float, float] | None:
    """Return (home_price, away_price) for Pinnacle h2h on this event, or None."""
    home = event.get("home_team")
    away = event.get("away_team")
    if not home or not away:
        return None
    for bm in event.get("bookmakers") or []:
        if bm.get("key") != "pinnacle":
            continue
        for mk in bm.get("markets") or []:
            if mk.get("key") != "h2h":
                continue
            h_price = None
            a_price = None
            for o in mk.get("outcomes") or []:
                if o.get("name") == home:
                    h_price = o.get("price")
                elif o.get("name") == away:
                    a_price = o.get("price")
            if h_price and a_price and h_price > 1.0 and a_price > 1.0:
                return float(h_price), float(a_price)
    return None


def update_closing_for_fixture(fixture_id: str, home_price: float,
                               away_price: float, dry_run: bool) -> int:
    """
    Write closing_odds + clv to ALL unsettled rows of this fixture. Returns
    the number of rows updated. closing_odds is overwritten on each call so
    later runs (closer to kickoff) replace earlier captures naturally.
    """
    if dry_run:
        rows = execute_query(
            "SELECT id, selection, book_odds FROM tennis_value_bets "
            "WHERE fixture_id = %s AND result IS NULL",
            (fixture_id,),
        )
        for r in rows or []:
            close = home_price if r["selection"] == "home" else away_price
            clv = (float(r["book_odds"]) / close) - 1.0
            print(f"    [dry] row {r['id']}  sel={r['selection']}  "
                  f"close={close:.3f}  clv={clv*100:+.2f}%")
        return len(rows or [])

    # Use CASE to set closing_odds + clv per row based on selection
    affected = execute_write(
        """
        UPDATE tennis_value_bets
           SET closing_odds = CASE selection
                                WHEN 'home' THEN %s::numeric
                                WHEN 'away' THEN %s::numeric
                              END,
               clv = CASE selection
                       WHEN 'home' THEN (book_odds / %s::numeric) - 1
                       WHEN 'away' THEN (book_odds / %s::numeric) - 1
                     END
         WHERE fixture_id = %s
           AND result IS NULL
        """,
        (home_price, away_price, home_price, away_price, fixture_id),
    )
    return affected or 0


def main(window_min: int, dry_run: bool) -> int:
    print("=" * 70)
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"TENNIS CLOSING-ODDS CAPTURE  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}  "
          f"({mode}, window={window_min}min)")
    print("=" * 70)

    imminent = find_imminent_fixtures(window_min)
    print(f"Imminent fixtures (next {window_min} min): {len(imminent)}")
    if not imminent:
        print("Nothing imminent. Skipping API calls.")
        return 0

    # Resolve sport_title → sport_key. One /sports call regardless of how many.
    sport_map = list_active_tennis_sport_map()
    if not sport_map:
        print("No active tennis sports returned — can't resolve sport keys.")
        return 0

    # Group imminent fixtures by sport_key (skip ones we can't resolve)
    by_sport: dict[str, list[dict]] = {}
    unresolved: list[str] = []
    for fx in imminent:
        tname = fx.get("tournament_name") or ""
        sport_key = sport_map.get(tname)
        if not sport_key:
            unresolved.append(tname)
            continue
        by_sport.setdefault(sport_key, []).append(fx)

    if unresolved:
        # Often a tournament has just finished and the sport went inactive;
        # not a failure mode — just log it.
        uniq = sorted(set(unresolved))
        print(f"  unresolved tournament titles (skipped): {uniq[:5]}"
              f"{' …' if len(uniq) > 5 else ''}")

    if not by_sport:
        print("No imminent fixtures map to active sport keys. Skipping.")
        return 0

    total_rows_updated = 0
    captured_events = 0
    for sport_key, fxs in by_sport.items():
        wanted_ids = {fx["fixture_id"] for fx in fxs}
        print(f"\n[capture] {sport_key}  (imminent: {len(wanted_ids)})")
        code, body = call(
            f"sports/{sport_key}/odds",
            regions="eu", markets="h2h", bookmakers="pinnacle",
            oddsFormat="decimal", dateFormat="iso",
        )
        if code != 200 or not isinstance(body, list):
            print(f"  ⚠️  /odds returned HTTP {code} — skipping this sport")
            continue

        for ev in body:
            if ev.get("id") not in wanted_ids:
                continue
            prices = extract_pinnacle_h2h(ev)
            if not prices:
                continue
            h_close, a_close = prices
            updated = update_closing_for_fixture(
                fixture_id=ev["id"],
                home_price=h_close,
                away_price=a_close,
                dry_run=dry_run,
            )
            if updated:
                captured_events += 1
                total_rows_updated += updated
                print(f"  ✓ {ev.get('home_team')} vs {ev.get('away_team')}  "
                      f"Pin {h_close:.2f} / {a_close:.2f}  → {updated} row(s)")
        time.sleep(0.3)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print(f"  imminent fixtures:  {len(imminent)}")
    print(f"  sports queried:     {len(by_sport)}")
    print(f"  events captured:    {captured_events}")
    print(f"  rows updated:       {total_rows_updated}")
    if _requests_remaining is not None:
        print(f"  Odds API credits remaining this month: {_requests_remaining}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--window", type=int, default=CAPTURE_WINDOW_MIN_DEFAULT,
                    help=f"lookahead minutes (default {CAPTURE_WINDOW_MIN_DEFAULT})")
    args = ap.parse_args()
    sys.exit(main(window_min=args.window, dry_run=args.dry_run))
