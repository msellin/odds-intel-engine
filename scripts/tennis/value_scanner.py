#!/usr/bin/env python3
"""
Tennis value bet scanner — OddsPapi sharp-vs-soft strategy.

Strategy:
  1. Fetch today's ATP/WTA/Challenger fixtures from OddsPapi
  2. De-vig Pinnacle match-winner odds → fair probability
  3. Compare every soft book price to fair probability
  4. Log simulated bets where edge ≥ MIN_EDGE to tennis_value_bets table

Usage:
    export OP_KEY=your_oddspapi_key
    python3 scripts/tennis/value_scanner.py             # live run (writes to DB)
    python3 scripts/tennis/value_scanner.py --dry-run   # print only, no DB writes

Estimated OddsPapi requests per run: 3–6 (1 tournaments + 1–2 Pinnacle odds + 1–2 soft odds)
"""
from __future__ import annotations
import os, sys, json, time, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).parents[2]))
from workers.api_clients.db import execute_query, execute_write

BASE           = "https://api.oddspapi.io/v4"
TENNIS_SPORT   = 12
RECORD_MIN_EDGE = 0.0         # log all positive-edge observations for training data
DISPLAY_MIN_EDGE = 0.03       # only print/highlight ≥3% in console
KELLY_FRAC     = 0.25         # quarter Kelly for simulated stakes
MAX_STAKE      = 5.0          # cap per bet
DEFAULT_STAKE  = 1.0

# Sharp anchor
SHARP_BOOK = "pinnacle"

# Soft books to scan — liveOdds=True from OddsPapi bookmakers list
SOFT_BOOKS = [
    "bet365",
    "betway",
    "unibet",
    "bwin",
    "1xbet",
    "williamhill",
    "cloudbet",
]

# Batch size for tournament IDs per request (keep URL manageable)
TOURNEY_BATCH = 5   # OddsPapi max per /odds-by-tournaments call

_requests_remaining: int | None = None


def call(path: str, **params) -> dict | list | None:
    global _requests_remaining
    key = os.environ.get("OP_KEY", "")
    if not key:
        print("ERROR: OP_KEY env var not set. Run: export OP_KEY=your_key")
        sys.exit(1)
    params["apiKey"] = key
    url = f"{BASE}{path}"
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"  HTTP error {path}: {e}")
            return None
        if r.status_code == 429:
            wait = 2 ** attempt
            print(f"  Rate limited — waiting {wait}s (attempt {attempt+1}/4)")
            time.sleep(wait)
            continue
        rem = r.headers.get("x-requests-remaining") or r.headers.get("X-Requests-Remaining")
        if rem is not None:
            _requests_remaining = int(rem)
        if r.status_code != 200:
            print(f"  API error {r.status_code} on {path}: {r.text[:200]}")
            return None
        return r.json()
    print(f"  Giving up on {path} after repeated 429s")
    return None


def fetch_tennis_tournaments() -> list[dict]:
    data = call("/tournaments", sportId=TENNIS_SPORT)
    if not data:
        return []
    rows = data if isinstance(data, list) else data.get("data", data.get("tournaments", []))
    active = [t for t in rows if isinstance(t, dict) and int(t.get("upcomingFixtures") or 0) > 0]
    print(f"  Tennis tournaments with upcoming fixtures: {len(active)}")
    return active


def fetch_odds_bulk(tournament_ids: list[int | str], bookmaker: str) -> list[dict]:
    """Fetch fixtures+odds for a list of tournament IDs from one bookmaker."""
    all_fixtures: list[dict] = []
    for i in range(0, len(tournament_ids), TOURNEY_BATCH):
        batch = tournament_ids[i:i + TOURNEY_BATCH]
        tid_str = ",".join(str(t) for t in batch)
        data = call("/odds-by-tournaments", bookmaker=bookmaker, tournamentIds=tid_str)
        if not data:
            continue
        rows = data if isinstance(data, list) else data.get("data", data.get("fixtures", []))
        all_fixtures.extend(rows)
        time.sleep(1.5)
    return all_fixtures


def extract_odds_by_player(fixture: dict, bookmaker: str) -> dict[str, float]:
    """
    Return {participant1Id: price, participant2Id: price} for the match-winner market.
    Identifies match winner by bookmakerOutcomeId == 'home'/'away' — consistent across
    all bookmakers in OddsPapi. Takes lowest numeric market ID when multiple qualify.
    """
    bm_data = fixture.get("bookmakerOdds", {}).get(bookmaker)
    if not bm_data or not bm_data.get("bookmakerIsActive"):
        return {}
    if bm_data.get("suspended"):
        return {}

    p1_id = str(fixture.get("participant1Id", ""))
    p2_id = str(fixture.get("participant2Id", ""))
    if not p1_id or not p2_id:
        return {}

    markets: dict = bm_data.get("markets", {})
    best: tuple[float, float] | None = None
    best_mid = float("inf")

    for market_id, market in markets.items():
        if not market.get("marketActive"):
            continue
        outcomes: dict = market.get("outcomes", {})
        if len(outcomes) != 2:
            continue

        home_price: float | None = None
        away_price: float | None = None
        for oid, outcome_data in outcomes.items():
            player = outcome_data.get("players", {}).get("0", {})
            if not player.get("active"):
                continue
            price = player.get("price")
            if not price or price <= 1.0:
                continue
            bid = player.get("bookmakerOutcomeId", "")
            if bid == "home":
                home_price = price
            elif bid == "away":
                away_price = price

        if home_price and away_price:
            mid_num = int(market_id) if str(market_id).lstrip("-").isdigit() else float("inf")
            if mid_num < best_mid:
                best_mid = mid_num
                best = (home_price, away_price)

    if best:
        return {p1_id: best[0], p2_id: best[1]}
    return {}


def devig_two_way(home_raw: float, away_raw: float) -> tuple[float, float]:
    """Pinnacle de-vig: normalize implied probabilities to sum to 1."""
    h_prob = 1.0 / home_raw
    a_prob = 1.0 / away_raw
    total = h_prob + a_prob
    return h_prob / total, a_prob / total


def kelly_stake(edge: float, fair_prob: float, book_odds: float) -> float:
    """Quarter Kelly stake (units)."""
    b = book_odds - 1.0
    k = (b * fair_prob - (1 - fair_prob)) / b
    k = max(0.0, k)
    return round(min(k * KELLY_FRAC, MAX_STAKE), 3)


def upsert_fixture_today(fixture_id: str, tournament_name: str,
                         player_home: str, player_away: str,
                         kickoff_time: str, pin_raw_home: float, pin_raw_away: float,
                         threshold_home: float, threshold_away: float,
                         pin_margin_pct: float, dry_run: bool) -> None:
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
        "pin_raw_away": pin_raw_away, "threshold_home": round(threshold_home, 4),
        "threshold_away": round(threshold_away, 4), "pin_margin_pct": round(pin_margin_pct * 100, 2),
    })


def insert_value_bet(row: dict, dry_run: bool) -> None:
    if dry_run:
        return
    execute_write("""
        INSERT INTO tennis_value_bets
            (fixture_id, tournament_name, player_home, player_away, surface,
             kickoff_time, market, selection,
             pin_fair_odds, pin_raw_home, pin_raw_away,
             bookmaker, book_odds, edge_pct, kelly_fraction, stake, scan_date, notes)
        VALUES
            (%(fixture_id)s, %(tournament_name)s, %(player_home)s, %(player_away)s, %(surface)s,
             %(kickoff_time)s, %(market)s, %(selection)s,
             %(pin_fair_odds)s, %(pin_raw_home)s, %(pin_raw_away)s,
             %(bookmaker)s, %(book_odds)s, %(edge_pct)s, %(kelly_fraction)s, %(stake)s,
             CURRENT_DATE, %(notes)s)
        ON CONFLICT (fixture_id, bookmaker, selection, scan_date) DO UPDATE SET
            book_odds      = EXCLUDED.book_odds,
            edge_pct       = EXCLUDED.edge_pct,
            kelly_fraction = EXCLUDED.kelly_fraction,
            stake          = EXCLUDED.stake,
            logged_at      = now()
    """, row)


def main(dry_run: bool = False) -> None:
    print("=" * 65)
    print(f"TENNIS VALUE SCANNER  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'DRY RUN — no DB writes' if dry_run else 'LIVE — logging to tennis_value_bets'}")
    print("=" * 65)

    # ── 1. Discover active tennis tournaments ──────────────────────────
    print("\n[1] Fetching tennis tournaments...")
    tournaments = fetch_tennis_tournaments()
    if not tournaments:
        print("  No active tournaments found.")
        return

    # Build lookup: tournamentId → name
    tourney_names: dict[int, str] = {}
    for t in tournaments:
        tid = t.get("tournamentId") or t.get("id")
        name = t.get("tournamentName") or t.get("name") or str(tid)
        tourney_names[int(tid)] = name

    tournament_ids = list(tourney_names.keys())
    print(f"  Active tournament IDs: {len(tournament_ids)}")

    # ── 2. Fetch Pinnacle odds ─────────────────────────────────────────
    print(f"\n[2] Fetching Pinnacle odds for {len(tournament_ids)} tournaments...")
    pin_fixtures = fetch_odds_bulk(tournament_ids, SHARP_BOOK)
    print(f"  Pinnacle fixtures returned: {len(pin_fixtures)}")

    # Filter to today + next 24h and build fair-odds index
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=36)

    pin_index: dict[str, dict] = {}  # fixture_id → {home_raw, away_raw, fair_home, fair_away, ...}
    for fix in pin_fixtures:
        fid = fix.get("fixtureId") or fix.get("id")
        start_str = fix.get("startTime") or fix.get("trueStartTime")
        if not fid or not start_str:
            continue
        try:
            start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start < now or start > cutoff:
            continue

        pin_odds = extract_odds_by_player(fix, SHARP_BOOK)
        p1_id = str(fix.get("participant1Id", ""))
        p2_id = str(fix.get("participant2Id", ""))
        if p1_id not in pin_odds or p2_id not in pin_odds:
            continue
        h_raw = pin_odds[p1_id]
        a_raw = pin_odds[p2_id]

        fair_h, fair_a = devig_two_way(h_raw, a_raw)
        pin_index[fid] = {
            "fixture": fix,
            "tournament_id": fix.get("tournamentId"),
            "start": start,
            "p1_id": p1_id, "p2_id": p2_id,
            "h_raw": h_raw, "a_raw": a_raw,
            "fair_h": fair_h, "fair_a": fair_a,
            "fair_h_odds": 1.0 / fair_h,
            "fair_a_odds": 1.0 / fair_a,
        }

    print(f"  Fixtures with Pinnacle match-winner odds (today+36h): {len(pin_index)}")
    if not pin_index:
        print("  Nothing to compare. Done.")
        return

    # ── 2b. Upsert ALL fixtures + thresholds (for admin page) ─────────
    for fid, pin in pin_index.items():
        fix = pin["fixture"]
        p1 = pin["p1_id"]
        p2 = pin["p2_id"]
        tid = int(pin["tournament_id"] or 0)
        tourney_name = tourney_names.get(tid, "Unknown")
        margin = 1.0 / pin["h_raw"] + 1.0 / pin["a_raw"] - 1.0
        upsert_fixture_today(
            fixture_id=fid, tournament_name=tourney_name,
            player_home=p1, player_away=p2,
            kickoff_time=pin["start"].isoformat(),
            pin_raw_home=pin["h_raw"], pin_raw_away=pin["a_raw"],
            threshold_home=pin["fair_h_odds"], threshold_away=pin["fair_a_odds"],
            pin_margin_pct=margin, dry_run=dry_run,
        )

    # ── 3. Scan soft books ─────────────────────────────────────────────
    total_bets = 0
    bets_by_book: dict[str, int] = {}

    for book in SOFT_BOOKS:
        print(f"\n[3] Scanning {book}...")
        soft_fixtures = fetch_odds_bulk(tournament_ids, book)
        soft_index = {(f.get("fixtureId") or f.get("id")): f for f in soft_fixtures}

        book_bets = 0
        for fid, pin in pin_index.items():
            soft_fix = soft_index.get(fid)
            if not soft_fix:
                continue
            soft_odds = extract_odds_by_player(soft_fix, book)
            p1_id = pin["p1_id"]
            p2_id = pin["p2_id"]
            if p1_id not in soft_odds or p2_id not in soft_odds:
                continue
            h_book = soft_odds[p1_id]
            a_book = soft_odds[p2_id]

            fix = pin["fixture"]
            p1 = fix.get("participant1Id")
            p2 = fix.get("participant2Id")
            tid = int(pin["tournament_id"] or 0)
            tourney_name = tourney_names.get(tid, "Unknown")
            kickoff = pin["start"].isoformat()

            for selection, book_odds, fair_prob, fair_odds, pin_raw in [
                ("home", h_book, pin["fair_h"], pin["fair_h_odds"], pin["h_raw"]),
                ("away", a_book, pin["fair_a"], pin["fair_a_odds"], pin["a_raw"]),
            ]:
                edge = book_odds * fair_prob - 1.0
                if edge < RECORD_MIN_EDGE:
                    continue

                stake = kelly_stake(edge, fair_prob, book_odds) if edge >= DISPLAY_MIN_EDGE else 0.0

                row = {
                    "fixture_id":      fid,
                    "tournament_name": tourney_name,
                    "player_home":     str(p1),
                    "player_away":     str(p2),
                    "surface":         None,
                    "kickoff_time":    kickoff,
                    "market":          "match_winner",
                    "selection":       selection,
                    "pin_fair_odds":   round(fair_odds, 4),
                    "pin_raw_home":    pin["h_raw"],
                    "pin_raw_away":    pin["a_raw"],
                    "bookmaker":       book,
                    "book_odds":       book_odds,
                    "edge_pct":        round(edge * 100, 2),
                    "kelly_fraction":  round(stake / MAX_STAKE, 4) if stake > 0 else 0.0,
                    "stake":           stake,
                    "notes":           f"pin_margin={round((1/pin['h_raw']+1/pin['a_raw']-1)*100,2)}%",
                }

                if edge >= DISPLAY_MIN_EDGE:
                    player_label = f"p{p1}" if selection == "home" else f"p{p2}"
                    print(f"  ✅ VALUE  {tourney_name[:30]:30s}  "
                          f"{player_label}  "
                          f"book={book_odds:.2f}  fair={fair_odds:.2f}  edge={edge*100:+.1f}%  "
                          f"stake={stake:.2f}u")

                insert_value_bet(row, dry_run)
                book_bets += 1
                total_bets += 1

        if book_bets:
            bets_by_book[book] = book_bets
        time.sleep(0.3)

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"SUMMARY")
    print(f"  Pinnacle fixtures scanned: {len(pin_index)}")
    print(f"  Observations logged (edge>0): {total_bets}")
    for bk, n in bets_by_book.items():
        print(f"    {bk}: {n}")
    if _requests_remaining is not None:
        print(f"  OddsPapi requests remaining this month: {_requests_remaining}")
    if not dry_run and total_bets:
        print(f"  ✓ Logged to tennis_value_bets table (edge>0%; ≥3% highlighted)")
    print()

    # Note: player IDs from OddsPapi are numeric — next step is building
    # a player name lookup (call /participants?sportId=12 for names)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print only, no DB writes")
    args = ap.parse_args()
    main(dry_run=args.dry_run)
