#!/usr/bin/env python3
"""
Coolbet tennis odds recorder — silent --record mode.

Flow:
  1. Fetch all Coolbet tennis matches via fo-category (public, no JWT)
  2. Batch-fetch current odds via sb-odds endpoint (no JWT)
  3. Cross-reference by kickoff time against tennis_fixtures_today (Pinnacle thresholds)
  4. Record observations where Pinnacle fixture has resolved player names
     — edge > 0%: logged for training data
     — edge >= 3%: highlighted as action signal

Usage:
    python3 scripts/tennis/place_coolbet_tennis.py              # dry run, print only
    python3 scripts/tennis/place_coolbet_tennis.py --record     # write to DB

Requires: COOLBET_COOKIE_REESE84 (+ optional other Imperva cookies) in .env.
No JWT needed — these are public read endpoints.
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

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)

_CATEGORY_URL = "https://www.coolbet.com/s/sbgate/sports/fo-category/"
_LEAGUES_URL  = "https://www.coolbet.com/s/sports/category/order/explicit/category-page-leagues"
_ODDS_URL     = "https://www.coolbet.com/s/sb-odds/odds/current/fo"

TENNIS_SPORT_CATEGORY_ID = 72
_DISPLAY_MIN_EDGE        = 0.03
_MAX_CREDIBLE_EDGE       = 0.40   # above this → fixture mismatch or bad data, skip
KICKOFF_MATCH_WINDOW_MIN = 20
ODDS_BATCH_SIZE          = 50   # market IDs per sb-odds call


def fetch_tennis_leagues(session: CoolbetSession) -> list[dict]:
    resp = session.post(_LEAGUES_URL, json={
        "sportCategoryId": TENNIS_SPORT_CATEGORY_ID,
        "country": "EE",
        "locale":  "en",
    })
    if resp.ok:
        payload = resp.json()
        if isinstance(payload, list) and payload:
            return [
                {"id": int(e["id"]), "name": e.get("name") or "",
                 "fullSlug": e.get("fullSlug") or ""}
                for e in payload if e.get("id")
            ]
    return []


def fetch_league_matches(session: CoolbetSession, league_id: int, slug: str) -> list[dict]:
    """
    Fetch all matches in a league via fo-category.
    Returns list of {id, home, away, start, status, markets: [{id, outcomes: [...]}]}.
    """
    extra = {"referer": f"https://www.coolbet.com/et/sport/{slug}"} if slug else {}
    resp = session.get(_CATEGORY_URL, params={
        "categoryId": league_id,
        "country": "EE", "isMobile": 0, "language": "et",
        "layout": "EUROPEAN", "limit": 6,
    }, headers=extra or None)
    if not resp.ok:
        return []
    data = resp.json()
    cats = data if isinstance(data, list) else [data]
    matches = []
    for cat in cats:
        for m in cat.get("matches") or []:
            if not m.get("id"):
                continue
            matches.append({
                "id":     int(m["id"]),
                "home":   (m.get("home_team_name") or "").strip(),
                "away":   (m.get("away_team_name") or "").strip(),
                "start":  m.get("match_start") or m.get("start"),
                "status": m.get("status"),
                "markets": m.get("markets") or [],
            })
    return matches


def find_match_winner_market(markets: list[dict]) -> dict | None:
    """Return the Match Result (2-outcome) market only — never fall back to handicap/totals."""
    for m in markets:
        name = (m.get("name") or "").lower()
        if "match result" in name and len(m.get("outcomes") or []) == 2:
            return m
    return None


def fetch_odds_batch(session: CoolbetSession, market_ids: list[int]) -> dict[int, dict[int, float]]:
    """
    Batch-fetch current odds for multiple markets.
    Returns {market_id: {outcome_id: decimal_odds}}.
    """
    result: dict[int, dict[int, float]] = {}
    for i in range(0, len(market_ids), ODDS_BATCH_SIZE):
        batch = market_ids[i:i + ODDS_BATCH_SIZE]
        resp = session.post(_ODDS_URL, json={"where": {"market_id": {"in": batch}}})
        if not resp.ok:
            continue
        data = resp.json()
        # Response: {str(outcome_id): {outcome_id, value, market_id, status, ...}}
        for oid_str, row in data.items():
            if not isinstance(row, dict):
                continue
            market_id = row.get("market_id")
            outcome_id = row.get("outcome_id")
            raw = row.get("value") or row.get("odds") or 0
            price = float(raw) if raw else 0.0
            if market_id and outcome_id and price > 1.0:
                if market_id not in result:
                    result[market_id] = {}
                result[market_id][outcome_id] = price
        time.sleep(0.3)
    return result


def load_pin_fixtures() -> list[dict]:
    now = datetime.now(timezone.utc)
    # Exclude matches that started more than 5 minutes ago (already live)
    start_floor = now - timedelta(minutes=5)
    cutoff = now + timedelta(hours=36)
    # Deduplicate: for the same player pair + kickoff_time, keep the sharpest line
    # (lowest Pinnacle margin). DISTINCT ON orders by margin ASC, picks the first.
    return execute_query("""
        SELECT DISTINCT ON (
            LEAST(player_home, player_away),
            GREATEST(player_home, player_away),
            date_trunc('minute', kickoff_time)
        )
            fixture_id, tournament_name, player_home, player_away,
            kickoff_time, threshold_home, threshold_away,
            pin_raw_home, pin_raw_away, pin_margin_pct
        FROM tennis_fixtures_today
        WHERE kickoff_time >= %s AND kickoff_time <= %s
        ORDER BY
            LEAST(player_home, player_away),
            GREATEST(player_home, player_away),
            date_trunc('minute', kickoff_time),
            pin_margin_pct ASC NULLS LAST
    """, (start_floor.isoformat(), cutoff.isoformat()))


def clear_todays_coolbet_bets(dry_run: bool) -> int:
    """Delete today's Coolbet rows before re-scanning so stale mis-matched entries don't persist."""
    if dry_run:
        return 0
    result = execute_write(
        "DELETE FROM tennis_value_bets WHERE bookmaker = 'coolbet' AND scan_date = CURRENT_DATE",
        {},
    )
    return result or 0


def _last_name(name: str) -> str:
    """Extract last name for fuzzy matching. Handles 'Last, F.' and 'First Last' formats."""
    name = name.strip()
    if "," in name:
        return name.split(",")[0].strip().lower()
    parts = name.split()
    return parts[-1].lower() if parts else name.lower()


def match_to_fixture(coolbet_start: str, cb_home: str, cb_away: str,
                     fixtures: list[dict]) -> dict | None:
    """Match a Coolbet match to a Pinnacle fixture by kickoff time + player name overlap.
    Requires both player last names to appear in the fixture (order-independent) to avoid
    cross-gender and cross-tournament mismatches."""
    try:
        cb_dt = datetime.fromisoformat(coolbet_start.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

    cb_ln_h = _last_name(cb_home)
    cb_ln_a = _last_name(cb_away)
    cb_lns = {cb_ln_h, cb_ln_a} - {""}

    window = timedelta(minutes=KICKOFF_MATCH_WINDOW_MIN)
    best: dict | None = None
    best_score = -1

    for fix in fixtures:
        try:
            kt = fix["kickoff_time"]
            fix_dt = kt if isinstance(kt, datetime) else datetime.fromisoformat(str(kt))
        except (ValueError, KeyError, TypeError):
            continue
        if abs(cb_dt - fix_dt) > window:
            continue

        # Name overlap: count how many Coolbet last names appear in either fixture name
        fix_ln_h = _last_name(str(fix.get("player_home") or ""))
        fix_ln_a = _last_name(str(fix.get("player_away") or ""))
        fix_lns = {fix_ln_h, fix_ln_a} - {""}

        overlap = len(cb_lns & fix_lns)
        # Require both players to match; a single-name match is too ambiguous
        if overlap < 2 and cb_lns:
            continue

        time_score = 1.0 - abs(cb_dt - fix_dt).total_seconds() / window.total_seconds()
        score = overlap + time_score
        if score > best_score:
            best_score = score
            best = fix

    return best


def _looks_like_id(s: str) -> bool:
    return s.strip().lstrip("-").isdigit()


def _has_real_names(fixture: dict) -> bool:
    """True if both players have real names (not numeric OddsPapi IDs)."""
    return (
        not _looks_like_id(str(fixture.get("player_home") or "0"))
        and not _looks_like_id(str(fixture.get("player_away") or "0"))
    )


def record_observation(
    fixture: dict | None,
    match: dict,
    h_odds: float, a_odds: float,
    dry_run: bool,
) -> tuple[int, int]:
    cb_id   = str(match.get("id", ""))
    cb_home = match.get("home") or "?"
    cb_away = match.get("away") or "?"
    cb_start = match.get("start", "")

    logged = 0
    value  = 0

    for selection, cb_odds, thr_key in [
        ("home", h_odds, "threshold_home"),
        ("away", a_odds, "threshold_away"),
    ]:
        if not cb_odds or cb_odds <= 1.0:
            continue

        threshold: float | None = fixture.get(thr_key) if fixture else None
        edge_pct: float | None = None
        if threshold and float(threshold) > 1.0:
            threshold = float(threshold)
            fair_prob = 1.0 / threshold
            edge_pct = round((cb_odds * fair_prob - 1.0) * 100, 2)

        # Sanity cap: >40% edge vs Pinnacle is a fixture mismatch / bad data — skip entirely
        if edge_pct is not None and edge_pct > _MAX_CREDIBLE_EDGE * 100:
            continue

        is_value = edge_pct is not None and edge_pct > 0

        if edge_pct is not None and edge_pct >= _DISPLAY_MIN_EDGE * 100:
            marker = "✅ VALUE"
            value += 1
        elif is_value:
            marker = "   edge"
        else:
            marker = "      "

        pin_info = f"thr={threshold:.3f}  edge={edge_pct:+.1f}%" if edge_pct is not None else "no Pinnacle match"
        player_label = cb_home if selection == "home" else cb_away
        print(f"  {marker}  {player_label[:24]:24s}  cb={cb_odds:.2f}  {pin_info}")

        if not dry_run and fixture and _has_real_names(fixture):
            fix_id = fixture["fixture_id"]
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
                fixture["tournament_name"],
                fixture["player_home"],
                fixture["player_away"],
                cb_start,
                selection,
                threshold,
                fixture.get("pin_raw_home"),
                fixture.get("pin_raw_away"),
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

    session = CoolbetSession(require_auth=False)

    # ── 0. Clear stale Coolbet rows from today ────────────────────────
    # Must run before inserting so mis-matched entries from a prior run don't persist.
    deleted = clear_todays_coolbet_bets(dry_run)
    if not dry_run:
        print(f"\n[0] Cleared {deleted} stale Coolbet rows from today")

    # ── 1. Load Pinnacle thresholds ───────────────────────────────────
    print("\n[1] Loading Pinnacle thresholds...")
    pin_fixtures = load_pin_fixtures()
    print(f"  Fixtures: {len(pin_fixtures)}")

    # ── 2. Fetch leagues ──────────────────────────────────────────────
    print("\n[2] Fetching Coolbet tennis leagues (sportCategoryId=72)...")
    leagues = fetch_tennis_leagues(session)
    print(f"  Leagues: {len(leagues)}")
    if not leagues:
        print("  No leagues found. Check Imperva cookies in .env.")
        return

    # ── 3. Fetch matches per league ───────────────────────────────────
    print("\n[3] Fetching matches...")
    all_matches: list[dict] = []
    for league in leagues:
        matches = fetch_league_matches(session, league["id"], league.get("fullSlug", ""))
        singles = [
            m for m in matches
            if m.get("status") == "OPEN"
            and find_match_winner_market(m.get("markets") or []) is not None
        ]
        all_matches.extend(singles)
        if singles:
            print(f"    {league['name'][:40]:40s}: {len(singles)}")
        time.sleep(0.2)

    print(f"  Total singles matches: {len(all_matches)}")
    if not all_matches:
        print("  No matches. Done.")
        return

    # ── 4. Batch-fetch odds ───────────────────────────────────────────
    print("\n[4] Fetching current odds...")
    market_info: dict[int, tuple[dict, dict]] = {}  # market_id → (match, market)
    market_ids: list[int] = []
    for match in all_matches:
        mkt = find_match_winner_market(match.get("markets") or [])
        if mkt:
            mid = int(mkt["id"])
            market_ids.append(mid)
            market_info[mid] = (match, mkt)

    odds_map = fetch_odds_batch(session, market_ids)
    print(f"  Odds fetched for {len(odds_map)} markets")

    # ── 5. Record observations ─────────────────────────────────────────
    print("\n[5] Checking odds vs Pinnacle thresholds...")
    total_logged = 0
    total_value  = 0

    for market_id, (match, mkt) in market_info.items():
        odds = odds_map.get(market_id)
        if not odds:
            continue

        outcomes = mkt.get("outcomes") or []
        if len(outcomes) < 2:
            continue

        # Match first/second outcome to home/away by order
        o1, o2 = outcomes[0], outcomes[1]
        h_odds = odds.get(o1["id"])
        a_odds = odds.get(o2["id"])
        if not h_odds or not a_odds:
            continue

        # Player names from outcome labels (more reliable than home/away_team_name for tennis)
        match["home"] = match.get("home") or (o1.get("name") or "").strip()
        match["away"] = match.get("away") or (o2.get("name") or "").strip()

        fixture = match_to_fixture(
            match.get("start", ""),
            match.get("home", ""),
            match.get("away", ""),
            pin_fixtures,
        )
        logged, value = record_observation(fixture, match, h_odds, a_odds, dry_run)
        total_logged += logged
        total_value  += value

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"  Matches processed: {len(market_info)}")
    print(f"  Observations:      {total_logged}")
    print(f"  Value bets ≥3%:   {total_value}")
    if not dry_run and total_logged:
        print(f"  ✓ Written to tennis_value_bets")
    print()


if __name__ == "__main__":
    main()
