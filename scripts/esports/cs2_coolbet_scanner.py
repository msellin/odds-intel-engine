#!/usr/bin/env python3
"""
Coolbet CS2 odds scanner.

Fetches CS2 match_winner odds from Coolbet (no auth — public anon-read API),
matches to rows in cs2_upcoming_matches by team name + kickoff time, writes
coolbet_odds1/coolbet_odds2.

Usage:
    python3 scripts/esports/cs2_coolbet_scanner.py            # dry-run, print only
    python3 scripts/esports/cs2_coolbet_scanner.py --record   # write to DB

Coolbet anon-read uses Imperva cookies (no JWT) — same pattern as the tennis
scanner. Sport category 65035 = esports root; we filter by fullSlug containing
"Counter-Strike-2".
"""
import argparse
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.automation.coolbet_session import CoolbetSession
from workers.api_clients.db import execute_query, execute_write

_LEAGUES_URL  = "https://www.coolbet.com/s/sports/category/order/explicit/category-page-leagues"
_CATEGORY_URL = "https://www.coolbet.com/s/sbgate/sports/fo-category/"
_ODDS_URL     = "https://www.coolbet.com/s/sb-odds/odds/current/fo"

ESPORTS_SPORT_CATEGORY_ID = 65035
KICKOFF_MATCH_WINDOW_MIN  = 30   # CS2 matches sometimes drift; wider than tennis
ODDS_BATCH_SIZE           = 50


def fetch_cs2_leagues(session: CoolbetSession) -> list[dict]:
    """Return CS2-only leagues (filtered by fullSlug 'Counter-Strike-2')."""
    resp = session.post(_LEAGUES_URL, json={
        "sportCategoryId": ESPORTS_SPORT_CATEGORY_ID,
        "country": "EE",
        "locale": "en",
    })
    if not resp.ok:
        print(f"[!] leagues fetch failed: {resp.status_code}", file=sys.stderr)
        return []
    raw = resp.json()
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        full_slug = e.get("fullSlug") or ""
        if "Counter-Strike-2" not in full_slug:
            continue
        if not e.get("id"):
            continue
        out.append({
            "id":       int(e["id"]),
            "name":     e.get("name") or "",
            "fullSlug": full_slug,
        })
    return out


def fetch_league_matches(session: CoolbetSession, league_id: int, slug: str) -> list[dict]:
    """Match list with markets — same shape as tennis scanner."""
    extra = {"referer": f"https://www.coolbet.com/et/sport/{slug}"} if slug else {}
    resp = session.get(_CATEGORY_URL, params={
        "categoryId": league_id,
        "country": "EE", "isMobile": 0, "language": "en",
        "layout": "EUROPEAN", "limit": 6,
    }, headers=extra or None)
    if not resp.ok:
        return []
    data = resp.json()
    # Coolbet response shapes encountered (2026-06-11):
    #   - {"categories": [{"id":..., "matches":[...]}]}   (current, observed)
    #   - [{"id":..., "matches":[...]}]                    (legacy, kept compatible)
    #   - {"id":..., "matches":[...]}                      (singleton — wrap)
    if isinstance(data, dict) and "categories" in data:
        cats = data["categories"] or []
    elif isinstance(data, list):
        cats = data
    else:
        cats = [data]
    matches = []
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        for m in cat.get("matches") or []:
            if not m.get("id"):
                continue
            # Coolbet match shape: name = "Team A - Team B" + outcomes have
            # result_key "[Home]"/"[Away]"; no home_team_name/away_team_name
            # field on the match itself anymore. Split the name as fallback
            # so legacy callers that rely on .home/.away still work.
            home = (m.get("home_team_name") or "").strip()
            away = (m.get("away_team_name") or "").strip()
            if not (home and away):
                # Split "Team A - Team B" on the first " - " separator.
                name = (m.get("name") or "").strip()
                if " - " in name:
                    a, b = name.split(" - ", 1)
                    home = home or a.strip()
                    away = away or b.strip()
            matches.append({
                "id":      int(m["id"]),
                "home":    home,
                "away":    away,
                "start":   m.get("match_start") or m.get("start"),
                "status":  m.get("status"),
                "markets": m.get("markets") or [],
            })
    return matches


def find_match_winner_market(markets: list[dict]) -> dict | None:
    """Return the 2-outcome Match Result (head-to-head) market."""
    for m in markets:
        name = (m.get("name") or "").lower()
        if "match result" in name and len(m.get("outcomes") or []) == 2:
            return m
    # Fallback: any 2-outcome market with "winner" in the name
    for m in markets:
        name = (m.get("name") or "").lower()
        if "winner" in name and len(m.get("outcomes") or []) == 2:
            return m
    return None


def find_atleast1map_markets(markets: list[dict]) -> list[dict]:
    """Return the "Match Handicap" markets that price the "wins ≥1 map"
    market for BO3 matches.

    Coolbet's structure (verified 2026-06-12 on LCK 2026 / Hanwha vs T1):
      market_type_id=12735  name='Match Handicap'  line='0 - 1.5'
        outcome[0] (HOME): -1.5 maps (must win 2-0)
        outcome[1] (AWAY): +1.5 maps (= wins ≥1 map)

    For BO3 the +1.5 line is equivalent to "this team wins at least 1
    map" because losing by 1.5+ in BO3 means 0-2.

    Coolbet often offers only ONE direction (favorite at -1.5). For the
    other side's ≥1 map odds we'd need line '1.5 - 0' — included when
    available. Returns a list (possibly with one or two entries)."""
    out: list[dict] = []
    for m in markets:
        name = (m.get("name") or "").lower()
        line = str(m.get("line") or "")
        if "match handicap" not in name:
            continue
        if len(m.get("outcomes") or []) != 2:
            continue
        # Line must reference 1.5 maps (the BO3 ≥1 map equivalent)
        if "1.5" not in line:
            continue
        out.append(m)
    return out


def fetch_odds_batch(session: CoolbetSession, market_ids: list[int]) -> dict[int, dict[int, float]]:
    result: dict[int, dict[int, float]] = {}
    for i in range(0, len(market_ids), ODDS_BATCH_SIZE):
        batch = market_ids[i:i + ODDS_BATCH_SIZE]
        resp = session.post(_ODDS_URL, json={"where": {"market_id": {"in": batch}}})
        if not resp.ok:
            continue
        data = resp.json()
        for _, row in data.items():
            if not isinstance(row, dict):
                continue
            mid = row.get("market_id"); oid = row.get("outcome_id")
            raw = row.get("value") or row.get("odds") or 0
            price = float(raw) if raw else 0.0
            if mid and oid and price > 1.0:
                result.setdefault(mid, {})[oid] = price
        time.sleep(0.3)
    return result


def _normalise(name: str) -> str:
    return (name or "").lower().replace(".", "").replace("-", " ").strip()


def _load_upcoming() -> list[dict]:
    """Load matches from cs2_upcoming_matches in the next 36h."""
    now = datetime.now(timezone.utc)
    horizon = (now + timedelta(hours=36)).isoformat()
    return execute_query("""
        SELECT id, bo3gg_id, team1, team2, kickoff_time
        FROM cs2_upcoming_matches
        WHERE kickoff_time >= %s AND kickoff_time <= %s
    """, (now.isoformat(), horizon))


def _match_to_row(coolbet_start: str, cb_home: str, cb_away: str, rows: list[dict]) -> tuple[dict, bool] | None:
    """Returns (row, swap) where swap=True if coolbet home==row.team2 (swap odds)."""
    try:
        cb_dt = datetime.fromisoformat(coolbet_start.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None

    cb_h, cb_a = _normalise(cb_home), _normalise(cb_away)
    window = timedelta(minutes=KICKOFF_MATCH_WINDOW_MIN)

    best = None
    for row in rows:
        try:
            kt = row["kickoff_time"]
            kt_dt = kt if isinstance(kt, datetime) else datetime.fromisoformat(str(kt).replace("Z", "+00:00"))
        except Exception:
            continue
        if abs(kt_dt - cb_dt) > window:
            continue
        t1, t2 = _normalise(row["team1"]), _normalise(row["team2"])
        # Token overlap heuristic — handles e.g. "G2" vs "G2 Esports", "FaZe" vs "FaZe Clan"
        def overlap(a: str, b: str) -> int:
            sa, sb = set(a.split()), set(b.split())
            return len(sa & sb)
        forward  = overlap(cb_h, t1) + overlap(cb_a, t2)
        backward = overlap(cb_h, t2) + overlap(cb_a, t1)
        score = max(forward, backward)
        if score < 1:
            continue
        swap = backward > forward
        candidate = (row, swap, score, abs(kt_dt - cb_dt))
        if best is None or candidate[2] > best[2] or (candidate[2] == best[2] and candidate[3] < best[3]):
            best = candidate

    if best is None:
        return None
    return best[0], best[1]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true", help="Write odds to cs2_upcoming_matches")
    args = p.parse_args()

    print(f"\n=== CS2 COOLBET SCANNER  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    upcoming = _load_upcoming()
    print(f"  {len(upcoming)} CS2 matches in cs2_upcoming_matches (next 36h)")
    if not upcoming:
        return

    session = CoolbetSession(require_auth=False)
    leagues = fetch_cs2_leagues(session)
    print(f"  {len(leagues)} Counter-Strike-2 leagues on Coolbet")
    if not leagues:
        return

    all_matches = []
    for lg in leagues:
        ms = fetch_league_matches(session, lg["id"], lg["fullSlug"])
        for m in ms:
            m["league"] = lg["name"]
        all_matches.extend(ms)
        time.sleep(0.3)
    print(f"  {len(all_matches)} matches across Coolbet CS2 leagues")

    # Collect market_winner + atleast-1-map market IDs (both go in one
    # odds-fetch batch so we save round-trips). Each match contributes
    # one match-winner market and 0-2 map-handicap markets.
    market_to_match = {}
    atleast1map_market_to_match: dict[int, tuple] = {}
    for m in all_matches:
        mw = find_match_winner_market(m["markets"])
        if mw and mw.get("id"):
            market_to_match[int(mw["id"])] = (m, mw)
        for ah in find_atleast1map_markets(m["markets"]):
            if ah.get("id"):
                atleast1map_market_to_match[int(ah["id"])] = (m, ah)
    print(f"  {len(market_to_match)} match-winner + {len(atleast1map_market_to_match)} ≥1-map markets to price")
    if not market_to_match:
        return

    all_market_ids = list(market_to_match.keys()) + list(atleast1map_market_to_match.keys())
    odds_by_market = fetch_odds_batch(session, all_market_ids)
    matched, unmatched, written = 0, 0, 0

    # Index atleast1map odds by the match (m) for easy lookup once we've
    # resolved the match to a DB row in the main loop.
    atleast1map_by_match_id: dict[int, list[dict]] = {}
    for ah_mid, (m, ah_mkt) in atleast1map_market_to_match.items():
        prices = odds_by_market.get(ah_mid, {})
        outcomes = ah_mkt.get("outcomes") or []
        if len(outcomes) != 2 or len(prices) < 2:
            continue
        o_home = outcomes[0].get("id"); o_away = outcomes[1].get("id")
        odds_home = prices.get(o_home); odds_away = prices.get(o_away)
        if not (odds_home and odds_away):
            continue
        # Parse the line to know which side is +1.5 (= wins ≥1 map).
        # Coolbet's "Match Handicap" line is a string like '0 - 1.5':
        # split on dash, the larger number is the +1.5 side.
        line = str(ah_mkt.get("line") or "")
        parts = [p.strip() for p in line.split("-")]
        # Convert to floats; failures fall back to assuming home=-1.5/away=+1.5
        try:
            home_handicap = float(parts[0]) if len(parts) >= 1 else 0.0
            away_handicap = float(parts[1]) if len(parts) >= 2 else 1.5
        except (ValueError, IndexError):
            home_handicap, away_handicap = 0.0, 1.5
        # The same Match Handicap market prices TWO things at once:
        #   +1.5 side's odds → that team's "wins ≥1 map" (atleast1map).
        #   -1.5 side's odds → that team's "wins 2-0 in BO3 / 3-0 in BO5"
        #                      (clean_sweep). CS2-CLEAN-SWEEP 2026-06-25.
        # Coolbet usually only offers ONE direction per match (favourite at
        # -1.5, underdog at +1.5), so each market entry contributes one or
        # the other side here; both directions are merged below.
        if away_handicap > home_handicap:
            # away is +1.5 → away = atleast1map ; home is -1.5 → home = clean_sweep
            home_atleast1, away_atleast1 = None, odds_away
            home_clean_sweep, away_clean_sweep = odds_home, None
        else:
            # home is +1.5 → home = atleast1map ; away is -1.5 → away = clean_sweep
            home_atleast1, away_atleast1 = odds_home, None
            home_clean_sweep, away_clean_sweep = None, odds_away
        atleast1map_by_match_id.setdefault(int(m["id"] or 0), []).append({
            "home_atleast1": home_atleast1,
            "away_atleast1": away_atleast1,
            "home_clean_sweep": home_clean_sweep,
            "away_clean_sweep": away_clean_sweep,
            "home": m["home"], "away": m["away"], "start": m["start"],
        })

    for mid, (m, mw) in market_to_match.items():
        prices = odds_by_market.get(mid, {})
        outcomes = mw.get("outcomes") or []
        if len(outcomes) != 2 or len(prices) < 2:
            continue
        # Outcome 0 = home, outcome 1 = away (Coolbet convention)
        o_home = outcomes[0].get("id"); o_away = outcomes[1].get("id")
        odds_home = prices.get(o_home); odds_away = prices.get(o_away)
        if not (odds_home and odds_away):
            continue

        match = _match_to_row(m["start"], m["home"], m["away"], upcoming)
        if not match:
            unmatched += 1
            continue
        row, swap = match
        matched += 1

        odds1 = odds_away if swap else odds_home
        odds2 = odds_home if swap else odds_away

        # Combine all atleast-1-map markets we found for THIS coolbet
        # match (there may be 0, 1, or 2 — one for each handicap direction).
        # Each entry contributes EITHER atleast1map OR clean_sweep for each
        # side (mirror outcomes of the same handicap market).
        ah_entries = atleast1map_by_match_id.get(int(m.get("id") or 0), [])
        atleast1_home = atleast1_away = None
        clean_sweep_home = clean_sweep_away = None
        for ah in ah_entries:
            if ah["home_atleast1"] and not atleast1_home:
                atleast1_home = ah["home_atleast1"]
            if ah["away_atleast1"] and not atleast1_away:
                atleast1_away = ah["away_atleast1"]
            if ah["home_clean_sweep"] and not clean_sweep_home:
                clean_sweep_home = ah["home_clean_sweep"]
            if ah["away_clean_sweep"] and not clean_sweep_away:
                clean_sweep_away = ah["away_clean_sweep"]
        # Map to row's team1/team2 ordering (applying the same swap).
        atleast1_team1 = atleast1_away if swap else atleast1_home
        atleast1_team2 = atleast1_home if swap else atleast1_away
        clean_sweep_team1 = clean_sweep_away if swap else clean_sweep_home
        clean_sweep_team2 = clean_sweep_home if swap else clean_sweep_away

        tag = "✓ would write" if args.record else "  dry"
        ah_part = ""
        if atleast1_team1 or atleast1_team2:
            ah_part = f"  ≥1map:{atleast1_team1 or '—'}/{atleast1_team2 or '—'}"
        cs_part = ""
        if clean_sweep_team1 or clean_sweep_team2:
            cs_part = f"  2-0:{clean_sweep_team1 or '—'}/{clean_sweep_team2 or '—'}"
        print(f"    {tag}  {row['team1']:25} vs {row['team2']:25}  {odds1:.2f}/{odds2:.2f}{ah_part}{cs_part}")

        if args.record:
            execute_write("""
                UPDATE cs2_upcoming_matches
                   SET coolbet_odds1 = %s, coolbet_odds2 = %s,
                       coolbet_odds_map1 = %s, coolbet_odds_map2 = %s,
                       coolbet_odds_cs1 = %s, coolbet_odds_cs2 = %s
                 WHERE id = %s
            """, (round(odds1, 3), round(odds2, 3),
                  round(atleast1_team1, 3) if atleast1_team1 else None,
                  round(atleast1_team2, 3) if atleast1_team2 else None,
                  round(clean_sweep_team1, 3) if clean_sweep_team1 else None,
                  round(clean_sweep_team2, 3) if clean_sweep_team2 else None,
                  row["id"]))
            written += 1

    print(f"\n  matched: {matched}  unmatched: {unmatched}  written: {written}\n")


if __name__ == "__main__":
    main()
