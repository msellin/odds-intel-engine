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

    # Collect market_winner market IDs
    market_to_match = {}
    for m in all_matches:
        mw = find_match_winner_market(m["markets"])
        if not mw or not mw.get("id"):
            continue
        market_to_match[int(mw["id"])] = (m, mw)
    print(f"  {len(market_to_match)} match-winner markets to price")
    if not market_to_match:
        return

    odds_by_market = fetch_odds_batch(session, list(market_to_match.keys()))
    matched, unmatched, written = 0, 0, 0

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

        tag = "✓ would write" if args.record else "  dry"
        print(f"    {tag}  {row['team1']:25} vs {row['team2']:25}  {odds1:.2f}/{odds2:.2f}")

        if args.record:
            execute_write("""
                UPDATE cs2_upcoming_matches
                   SET coolbet_odds1 = %s, coolbet_odds2 = %s
                 WHERE id = %s
            """, (round(odds1, 3), round(odds2, 3), row["id"]))
            written += 1

    print(f"\n  matched: {matched}  unmatched: {unmatched}  written: {written}\n")


if __name__ == "__main__":
    main()
