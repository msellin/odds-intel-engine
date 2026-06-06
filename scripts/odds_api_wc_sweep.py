"""ODDS-API-WC — daily WC 2026 odds sweep.

One sport-level call to The Odds API returns all 72 WC fixtures × all bookmakers
for one cost = (markets × regions) per call. Default: h2h+totals+spreads × eu = 3 credits.

Rotates through bookmaker names from Odds API convention into our `odds_snapshots`
convention (Pinnacle, Bet365, Betfair Exchange, Coolbet, Unibet, etc.) and inserts
one row per (match, market, selection, bookmaker, timestamp).

Idempotency: each call writes a new row with the current timestamp — duplicate runs
just accumulate snapshots, which is what we want for drift/CLV tracking.

Match identification: matches Odds API `home_team`/`away_team` + `commence_time`
against our matches table by fuzzy name + ±6h time window.

Run modes:
    --dry-run        Don't write to DB, just print what would be inserted
    --markets h2h    Override default h2h,totals,spreads
    --regions eu,uk  Override default eu (multiplies credit cost)

Cost: default = 3 credits/call. Adding regions = 3 × N regions.
"""
from __future__ import annotations
import argparse, json, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from rapidfuzz import fuzz

sys.path.insert(0, "/Users/margussellin/www/odds-intel-engine")
from workers.api_clients.db import get_conn

KEY = os.environ.get("OA_KEY") or os.environ.get("ODDS_API_KEY")
BASE = "https://api.the-odds-api.com/v4"
SPORT = "soccer_fifa_world_cup"

# Odds API bookmaker key → our `odds_snapshots.bookmaker` convention.
# Only books we'd recognize are mapped; everything else gets a Title-Cased fallback.
BOOKMAKER_MAP = {
    "pinnacle":      "Pinnacle",
    "bet365":        "Bet365",
    "betfair_ex_eu": "Betfair Exchange",
    "betfair_ex_uk": "Betfair Exchange",
    "coolbet":       "Coolbet",
    "unibet":        "Unibet",
    "unibet_eu":     "Unibet",
    "unibet_fr":     "Unibet",
    "unibet_nl":     "Unibet",
    "unibet_se":     "Unibet",
    "unibet_dk":     "Unibet",
    "unibet_be":     "Unibet",
    "unibet_ie":     "Unibet",
    "unibet_com_au": "Unibet",
    "williamhill":   "William Hill",
    "marathonbet":   "Marathonbet",
    "1xbet":         "1xBet",
    "onexbet":       "1xBet",
    "betano":        "Betano",
    "betvictor":     "BetVictor",
    "10bet":         "10Bet",
    "dafabet":       "Dafabet",
    "betfair":       "Betfair",
}

def _normalize_bookmaker(key: str, title: str) -> str:
    if key in BOOKMAKER_MAP:
        return BOOKMAKER_MAP[key]
    # Fallback: prefer human title, else cleaned key
    return title or key.replace("_", " ").title()

def fetch_odds(markets: str, regions: str) -> list[dict]:
    if not KEY:
        sys.exit("set OA_KEY (or ODDS_API_KEY) env var")
    r = requests.get(
        f"{BASE}/sports/{SPORT}/odds",
        params={"apiKey": KEY, "markets": markets, "regions": regions, "oddsFormat": "decimal"},
        timeout=30,
    )
    rem = r.headers.get("x-requests-remaining")
    used = r.headers.get("x-requests-used")
    print(f"  Odds API: HTTP {r.status_code}  remaining={rem}  used={used}  bytes={len(r.content)}")
    if r.status_code != 200:
        sys.exit(f"Odds API error: {r.text[:300]}")
    return r.json()

def load_our_wc_matches() -> dict:
    """Return dict mapping (home_lo, away_lo, ko_date_iso) → match_id for our WC fixtures."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT m.id::text, m.date,
                     ht.name AS home, at2.name AS away
              FROM matches m
              JOIN teams ht ON ht.id = m.home_team_id
              JOIN teams at2 ON at2.id = m.away_team_id
              JOIN leagues l ON l.id = m.league_id
              WHERE l.name = 'World Cup' AND m.date BETWEEN %s AND %s
            """, ("2026-06-01", "2026-08-01"))
            return [{"id": r[0], "date": r[1], "home": r[2], "away": r[3]} for r in cur.fetchall()]

def match_event_to_db(event: dict, our_matches: list[dict]) -> str | None:
    """Find our match_id for an Odds API event."""
    op_home = (event.get("home_team") or "").lower()
    op_away = (event.get("away_team") or "").lower()
    try:
        op_ko = datetime.fromisoformat(event["commence_time"].replace("Z","+00:00"))
    except Exception:
        return None
    best = None; best_score = 0
    for m in our_matches:
        if abs((m["date"] - op_ko).total_seconds()) > 6*3600: continue
        h, a = m["home"].lower(), m["away"].lower()
        s1 = (fuzz.token_set_ratio(op_home, h) + fuzz.token_set_ratio(op_away, a)) / 2
        s2 = (fuzz.token_set_ratio(op_home, a) + fuzz.token_set_ratio(op_away, h)) / 2
        score = max(s1, s2)
        if score > best_score: best_score = score; best = m
    return best["id"] if best and best_score >= 70 else None

def parse_event_to_rows(event: dict, match_id: str, now_iso: str) -> list[tuple]:
    """Convert one Odds API event to odds_snapshots rows.
    Returns list of (match_id, bookmaker, market, selection, odds, timestamp,
                     is_closing, minutes_to_kickoff, handicap_line)."""
    try:
        ko_dt = datetime.fromisoformat(event["commence_time"].replace("Z","+00:00"))
    except Exception:
        return []
    now_dt = datetime.now(timezone.utc)
    mins_to_ko = int((ko_dt - now_dt).total_seconds() / 60)
    is_closing = mins_to_ko is not None and abs(mins_to_ko) <= 5

    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    rows = []
    for bm in event.get("bookmakers", []):
        bk_name = _normalize_bookmaker(bm.get("key", ""), bm.get("title", ""))
        bm_last_update = bm.get("last_update") or now_iso  # ISO from API
        for market in bm.get("markets", []):
            mkey = market.get("key")
            outcomes = market.get("outcomes", [])
            if mkey == "h2h":
                # 1x2: outcomes are by team name + "Draw"
                for o in outcomes:
                    name = o.get("name", "")
                    if name == home_team: sel = "home"
                    elif name == away_team: sel = "away"
                    elif name.lower() == "draw": sel = "draw"
                    else: continue
                    rows.append((match_id, bk_name, "1x2", sel, float(o["price"]),
                                 bm_last_update, is_closing, mins_to_ko, None))
            elif mkey == "totals":
                # one snapshot per (line, side). Only half-lines fit our schema.
                for o in outcomes:
                    name = (o.get("name") or "").lower()
                    line = o.get("point")
                    if line is None: continue
                    if line not in (0.5, 1.5, 2.5, 3.5, 4.5): continue
                    cents = int(round(line * 10))
                    market_name = f"over_under_{cents:02d}"
                    side = "over" if name == "over" else "under" if name == "under" else None
                    if side is None: continue
                    rows.append((match_id, bk_name, market_name, side, float(o["price"]),
                                 bm_last_update, is_closing, mins_to_ko, None))
            elif mkey == "spreads":
                # Asian Handicap. outcomes are by team name + point.
                # Our convention: handicap_line is HOME-perspective.
                for o in outcomes:
                    name = o.get("name", "")
                    point = o.get("point")
                    if point is None: continue
                    if name == home_team:
                        sel = "home"; hline = float(point)
                    elif name == away_team:
                        sel = "away"; hline = -float(point)  # flip to home-perspective
                    else: continue
                    rows.append((match_id, bk_name, "asian_handicap", sel, float(o["price"]),
                                 bm_last_update, is_closing, mins_to_ko, hline))
    return rows

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--markets", default="h2h,totals,spreads")
    ap.add_argument("--regions", default="eu")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    n_markets = len([m for m in args.markets.split(",") if m])
    n_regions = len([r for r in args.regions.split(",") if r])
    print(f"ODDS-API-WC sweep — markets={args.markets} regions={args.regions} "
          f"=> credit cost: {n_markets * n_regions}")

    events = fetch_odds(args.markets, args.regions)
    print(f"  → {len(events)} WC events returned")
    if not events:
        sys.exit("no events — bailing")

    our_matches = load_our_wc_matches()
    print(f"  our DB has {len(our_matches)} WC matches in window")

    all_rows = []
    unmatched = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for ev in events:
        mid = match_event_to_db(ev, our_matches)
        if not mid:
            unmatched.append(f"{ev.get('home_team')} vs {ev.get('away_team')}")
            continue
        all_rows.extend(parse_event_to_rows(ev, mid, now_iso))

    print(f"\n  matched: {len(events) - len(unmatched)}/{len(events)} events")
    if unmatched:
        print(f"  unmatched (showing first 5): {unmatched[:5]}")

    print(f"  rows to insert: {len(all_rows)}")
    if all_rows:
        from collections import Counter
        mkt_count = Counter(r[2] for r in all_rows)
        bk_count = Counter(r[1] for r in all_rows)
        print(f"  market breakdown: {dict(mkt_count)}")
        print(f"  top bookmakers: {dict(bk_count.most_common(10))}")
        print(f"  sample row: {all_rows[0]}")

    if args.dry_run:
        print("\nDRY-RUN — no DB write."); return

    if not all_rows:
        print("nothing to insert."); return

    print(f"\ninserting {len(all_rows)} rows…")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO odds_snapshots
                   (match_id, bookmaker, market, selection, odds, timestamp,
                    is_closing, minutes_to_kickoff, handicap_line)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                all_rows,
            )
            conn.commit()
    print(f"✓ inserted {len(all_rows)} rows")

if __name__ == "__main__":
    main()
