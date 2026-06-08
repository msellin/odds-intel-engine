#!/usr/bin/env python3
"""
Coolbet tennis odds recorder — silent --record mode.

Flow:
  1. Fetch all Coolbet tennis matches + match-winner odds (public API, no JWT)
  2. Cross-reference by kickoff time against tennis_fixtures_today (Pinnacle thresholds)
  3. Record ALL Coolbet tennis observations to tennis_value_bets (bookmaker='coolbet')
  4. Mark as value bet if Coolbet odds > Pinnacle fair price (edge > 0%)

Usage:
    python3 scripts/tennis/place_coolbet_tennis.py              # dry run, print only
    python3 scripts/tennis/place_coolbet_tennis.py --record     # write to DB

Requires only COOLBET_IMPERVA_COOKIES (or individual COOLBET_COOKIE_* vars) in .env.
No JWT needed — tennis odds are a public read endpoint.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parents[2] / ".env")

from workers.api_clients.db import execute_query, execute_write
from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import (
    fetch_events_for_league,
    fetch_main_markets,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)

_CATEGORY_URL = "https://www.coolbet.com/s/sbgate/sports/fo-category/"
_LEAGUES_URL  = "https://www.coolbet.com/s/sports/category/order/explicit/category-page-leagues"

TENNIS_SPORT_CATEGORY_ID = 72          # confirmed from Coolbet network tab
_DISPLAY_MIN_EDGE        = 0.03        # print highlight threshold
KICKOFF_MATCH_WINDOW_MIN = 20          # minutes of tolerance for kickoff matching


def fetch_tennis_leagues(session: CoolbetSession) -> list[dict]:
    """Return all Coolbet tennis leagues under sportCategoryId=72."""
    resp = session.post(_LEAGUES_URL, json={
        "sportCategoryId": TENNIS_SPORT_CATEGORY_ID,
        "country": "EE",
        "locale":  "en",
    })
    if resp.ok:
        payload = resp.json()
        if isinstance(payload, list) and payload:
            leagues = [
                {"id": int(e["id"]), "name": e.get("name") or "",
                 "fullSlug": e.get("fullSlug") or ""}
                for e in payload if e.get("id")
            ]
            return leagues
    # Fallback: fetch the tennis root category directly — returns one big group
    resp2 = session.get(_CATEGORY_URL, params={
        "categoryId": TENNIS_SPORT_CATEGORY_ID,
        "country": "EE", "isMobile": 0, "language": "et",
        "layout": "EUROPEAN", "limit": 50,
    }, headers={"referer": "https://www.coolbet.com/et/sport/tennis"})
    if resp2.ok:
        data = resp2.json()
        leagues = []
        cats = data if isinstance(data, list) else [data]
        for cat in cats:
            lid = cat.get("id")
            if lid:
                leagues.append({
                    "id":       int(lid),
                    "name":     cat.get("name") or "",
                    "fullSlug": cat.get("fullSlug") or cat.get("slug") or "",
                })
        return leagues
    return []


def extract_match_winner(bet_offers: list[dict]) -> tuple[float | None, float | None, str, str]:
    """
    Find 2-outcome match-winner market. Returns (home_odds, away_odds, h_name, a_name).
    Tennis match winner: look for criterion containing 'winner' or 'match'.
    Falls back to first 2-outcome market.
    """
    for offer in bet_offers:
        label = offer.get("criterion_label", "").lower()
        outcomes = offer.get("outcomes", [])
        active = [o for o in outcomes if o.get("odds_decimal", 0) > 1.01]
        if len(active) != 2:
            continue
        if "winner" in label or "match" in label or label in ("1x2", ""):
            return (
                active[0]["odds_decimal"], active[1]["odds_decimal"],
                active[0].get("label", ""), active[1].get("label", ""),
            )
    # Fallback: any 2-outcome market
    for offer in bet_offers:
        outcomes = offer.get("outcomes", [])
        active = [o for o in outcomes if o.get("odds_decimal", 0) > 1.01]
        if len(active) == 2:
            return (
                active[0]["odds_decimal"], active[1]["odds_decimal"],
                active[0].get("label", ""), active[1].get("label", ""),
            )
    return None, None, "", ""


def load_pin_fixtures() -> list[dict]:
    """Read today's fixtures with Pinnacle thresholds from tennis_fixtures_today."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=36)
    return execute_query("""
        SELECT fixture_id, tournament_name, player_home, player_away,
               kickoff_time, threshold_home, threshold_away,
               pin_raw_home, pin_raw_away
        FROM tennis_fixtures_today
        WHERE kickoff_time >= %s AND kickoff_time <= %s
        ORDER BY kickoff_time
    """, (now.isoformat(), cutoff.isoformat()))


def match_to_fixture(coolbet_start: str, fixtures: list[dict]) -> dict | None:
    """Find best Pinnacle fixture for a Coolbet match by kickoff time (within ±20min)."""
    try:
        cb_dt = datetime.fromisoformat(coolbet_start.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    window = timedelta(minutes=KICKOFF_MATCH_WINDOW_MIN)
    best: dict | None = None
    best_delta = window
    for fix in fixtures:
        try:
            fix_dt = datetime.fromisoformat(fix["kickoff_time"])
        except (ValueError, KeyError):
            continue
        delta = abs(cb_dt - fix_dt)
        if delta < best_delta:
            best_delta = delta
            best = fix
    return best


def _looks_like_id(s: str) -> bool:
    """True if the string is just a numeric OddsPapi participant ID, not a real name."""
    return s.strip().lstrip("-").isdigit()


def backfill_player_names(fixture: dict, cb_home: str, cb_away: str, dry_run: bool) -> None:
    """If tennis_fixtures_today still has numeric IDs, overwrite with Coolbet player names."""
    if not fixture:
        return
    p_home = str(fixture.get("player_home") or "")
    p_away = str(fixture.get("player_away") or "")
    if _looks_like_id(p_home) and cb_home and not _looks_like_id(cb_home):
        if not dry_run:
            execute_write(
                "UPDATE tennis_fixtures_today SET player_home=%s WHERE fixture_id=%s",
                (cb_home, fixture["fixture_id"]),
            )
        print(f"    → name resolved: {p_home} → {cb_home}")
        fixture["player_home"] = cb_home  # update in-memory for display
    if _looks_like_id(p_away) and cb_away and not _looks_like_id(cb_away):
        if not dry_run:
            execute_write(
                "UPDATE tennis_fixtures_today SET player_away=%s WHERE fixture_id=%s",
                (cb_away, fixture["fixture_id"]),
            )
        print(f"    → name resolved: {p_away} → {cb_away}")
        fixture["player_away"] = cb_away


def record_observation(
    fixture: dict | None,
    coolbet_match: dict,
    h_odds: float, a_odds: float,
    h_name: str, a_name: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Write Coolbet observation to tennis_value_bets. Returns (logged, value_bets)."""
    cb_id   = str(coolbet_match.get("id", ""))
    cb_home = coolbet_match.get("home") or h_name or "?"
    cb_away = coolbet_match.get("away") or a_name or "?"
    cb_start = coolbet_match.get("start", "")

    # Backfill player names into tennis_fixtures_today when they're still numeric IDs
    backfill_player_names(fixture, cb_home, cb_away, dry_run)

    logged = 0
    value  = 0

    for selection, cb_odds, threshold_key, raw_pin in [
        ("home", h_odds, "threshold_home", "pin_raw_home"),
        ("away", a_odds, "threshold_away",  "pin_raw_away"),
    ]:
        if not cb_odds or cb_odds <= 1.0:
            continue

        # Compute edge vs Pinnacle fair price
        edge_pct: float | None = None
        threshold: float | None = fixture.get(threshold_key) if fixture else None
        if threshold and threshold > 1.0:
            fair_prob = 1.0 / threshold
            edge_pct = round((cb_odds * fair_prob - 1.0) * 100, 2)

        is_value = edge_pct is not None and edge_pct > 0

        if edge_pct is not None and edge_pct >= _DISPLAY_MIN_EDGE * 100:
            marker = "✅ VALUE"
            value += 1
        elif is_value:
            marker = "   edge"
        else:
            marker = "      "

        pin_info = f"threshold={threshold:.3f}  edge={edge_pct:+.1f}%" if edge_pct is not None else "no Pinnacle match"
        player_label = cb_home if selection == "home" else cb_away
        print(f"  {marker}  {player_label[:22]:22s}  cb={cb_odds:.2f}  {pin_info}")

        if not dry_run:
            # Also backfill names in existing tennis_value_bets rows for this fixture
            if fixture and _looks_like_id(str(fixture.get("player_home") or "")):
                pass  # already updated in-memory; the INSERT below uses resolved names
            fix_id = fixture["fixture_id"] if fixture else f"cb_{cb_id}"
            if fixture:
                execute_write(
                    """UPDATE tennis_value_bets
                       SET player_home=%s, player_away=%s
                       WHERE fixture_id=%s AND player_home ~ '^[0-9]+$'""",
                    (fixture["player_home"], fixture["player_away"], fixture["fixture_id"]),
                )
            execute_write("""
                INSERT INTO tennis_value_bets
                    (fixture_id, tournament_name, player_home, player_away, surface,
                     kickoff_time, market, selection,
                     pin_fair_odds, pin_raw_home, pin_raw_away,
                     bookmaker, book_odds, edge_pct, kelly_fraction, stake,
                     scan_date, notes)
                VALUES
                    (%s, %s, %s, %s, NULL,
                     %s, 'match_winner', %s,
                     %s, %s, %s,
                     'coolbet', %s, %s, 0, 0,
                     CURRENT_DATE, %s)
                ON CONFLICT (fixture_id, bookmaker, selection, scan_date) DO UPDATE SET
                    book_odds  = EXCLUDED.book_odds,
                    edge_pct   = EXCLUDED.edge_pct,
                    logged_at  = now()
            """, (
                fix_id,
                fixture["tournament_name"] if fixture else cb_home[:60],
                fixture["player_home"] if fixture else cb_home,
                fixture["player_away"] if fixture else cb_away,
                cb_start,
                selection,
                threshold,
                fixture.get("pin_raw_home") if fixture else None,
                fixture.get("pin_raw_away") if fixture else None,
                cb_odds,
                edge_pct,
                f"coolbet_match_id={cb_id}",
            ))
        logged += 1

    return logged, value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="Write to DB (default: dry run)")
    args = ap.parse_args()

    dry_run = not args.record
    print("=" * 65)
    print(f"COOLBET TENNIS  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'DRY RUN — no DB writes' if dry_run else 'LIVE — logging to tennis_value_bets'}")
    print("=" * 65)

    # No JWT needed for public read endpoints
    session = CoolbetSession(require_auth=False)

    # ── 1. Load Pinnacle thresholds ───────────────────────────────────
    print("\n[1] Loading Pinnacle thresholds from tennis_fixtures_today...")
    pin_fixtures = load_pin_fixtures()
    print(f"  Fixtures with Pinnacle thresholds: {len(pin_fixtures)}")

    # ── 2. Fetch Coolbet tennis leagues ───────────────────────────────
    print("\n[2] Fetching Coolbet tennis leagues (sportCategoryId=72)...")
    leagues = fetch_tennis_leagues(session)
    print(f"  Leagues found: {len(leagues)}")
    if not leagues:
        print("  No leagues returned. Check Imperva cookies (COOLBET_COOKIE_REESE84 etc. in .env).")
        return

    # ── 3. Fetch events per league ────────────────────────────────────
    print(f"\n[3] Fetching matches from {len(leagues)} leagues...")
    all_matches: list[dict] = []
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=36)

    for league in leagues:
        events = fetch_events_for_league(
            session, league["id"], league.get("fullSlug")
        )
        upcoming = [e for e in events if e.get("start") and e.get("status") in ("OPEN", None, "")]
        all_matches.extend(upcoming)
        if upcoming:
            print(f"    {league['name'][:45]:45s}: {len(upcoming)}")
        time.sleep(0.3)

    print(f"  Total upcoming tennis matches: {len(all_matches)}")
    if not all_matches:
        print("  No matches found.")
        return

    # ── 4. Fetch main markets ─────────────────────────────────────────
    print(f"\n[4] Fetching match-winner markets...")
    match_ids = [m["id"] for m in all_matches]
    markets_by_id = fetch_main_markets(session, match_ids)
    print(f"  Markets fetched for {len(markets_by_id)} matches")

    # ── 5. Record all observations ─────────────────────────────────────
    print(f"\n[5] Checking odds vs Pinnacle thresholds...")
    total_logged = 0
    total_value  = 0

    for match in all_matches:
        mid      = match["id"]
        offers   = markets_by_id.get(mid, [])
        if not offers:
            continue
        h_odds, a_odds, h_name, a_name = extract_match_winner(offers)
        if not h_odds or not a_odds:
            continue

        fixture = match_to_fixture(match.get("start", ""), pin_fixtures)
        logged, value = record_observation(
            fixture, match, h_odds, a_odds, h_name, a_name, dry_run
        )
        total_logged += logged
        total_value  += value

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Coolbet matches processed: {len(all_matches)}")
    print(f"  Observations logged:       {total_logged}")
    print(f"  Value bets (edge ≥ 3%):   {total_value}")
    if not dry_run and total_logged:
        print(f"  ✓ Written to tennis_value_bets (bookmaker=coolbet)")
    print()


if __name__ == "__main__":
    main()
