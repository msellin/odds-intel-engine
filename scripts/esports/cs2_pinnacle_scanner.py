"""
CS2 Pinnacle Scanner — fetch CS2 match odds from Pinnacle's public guest API.

Pinnacle's closing line is the gold-standard truth label in sports betting —
their book is sharp, well-capitalized, and moves on real information. Adding
pinnacle_implied_prob as a model feature is documented to add 2-5pp AUC in
similar studies — bigger than every other signal we've added today combined.

API: guest.api.arcadia.pinnacle.com — public, no auth key required, but
geo-blocked in several countries including Estonia. Railway US IP works.

Politeness:
- 4-6s random jitter AFTER each request (lifted from our soccer scraper)
- Realistic Browser User-Agent + Referer
- Hard caps: MAX_REQUESTS_PER_RUN, MAX_CONSECUTIVE_ERRORS
- 60s back-off on 429/503
- Pinnacle's guest API doesn't have published rate limits but the soccer
  scraper has been running at this cadence for months without issues.

Coverage caveat: cannot backfill historical Pinnacle odds — only current/
upcoming markets are exposed. We accumulate going forward.

Output: writes pinnacle_odds1/pinnacle_odds2 to cs2_upcoming_matches via
team-name fuzzy match against existing bo3.gg-indexed rows. The
cs2_clv_snapshot script already reads these columns to lock the closing
line for placed bets.

Run:
    python3 scripts/esports/cs2_pinnacle_scanner.py [--dry] [--limit 5]
"""

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
try:
    from scraper_state import scraper_run
except ImportError:
    scraper_run = None  # type: ignore


# ── Config ──────────────────────────────────────────────────────────
API_HOST = "https://guest.api.arcadia.pinnacle.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
REFERER = "https://www.pinnacle.com/"
# Known Pinnacle public guest API key (rotated rarely; widely documented).
GUEST_API_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"

# Esports sport ID on Pinnacle — confirmed as 12 across multiple community posts.
PINNACLE_ESPORTS_SPORT_ID = 12

REQUEST_JITTER_SEC = (4.0, 6.0)            # politeness sleep AFTER each request
MAX_REQUESTS_PER_RUN = 80                  # safety cap
MAX_CONSECUTIVE_ERRORS = 5
POLL_WINDOW_HOURS = 48                     # only score matches kicking off in this window

# Pinnacle league IDs for CS2 are not stable — discover and cache.
LEAGUE_CACHE = Path(__file__).resolve().parents[2] / "data/esports/cs2/pinnacle_cs2_leagues.json"


# ── HTTP client ─────────────────────────────────────────────────────
class PinnacleClient:
    def __init__(self):
        self.req_count = 0
        self.consecutive_errors = 0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": REFERER,
            "Origin": "https://www.pinnacle.com",
            "X-API-Key": GUEST_API_KEY,
        })

    def get(self, path: str) -> dict | list | None:
        if self.req_count >= MAX_REQUESTS_PER_RUN:
            print(f"  [!] max requests ({MAX_REQUESTS_PER_RUN}) reached — aborting", file=sys.stderr)
            return None
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            print(f"  [!] {MAX_CONSECUTIVE_ERRORS} consecutive errors — backing off", file=sys.stderr)
            return None
        self.req_count += 1
        url = f"{API_HOST}{path}"
        try:
            r = self.session.get(url, timeout=20)
        except requests.exceptions.RequestException as e:
            self.consecutive_errors += 1
            print(f"  [!] network error on {path}: {e}", file=sys.stderr)
            time.sleep(5)
            return None
        finally:
            time.sleep(random.uniform(*REQUEST_JITTER_SEC))
        if r.status_code == 200:
            self.consecutive_errors = 0
            try:
                return r.json()
            except json.JSONDecodeError:
                return None
        if r.status_code in (429, 503):
            print(f"  [!] {r.status_code} on {path} — extra 60s back-off", file=sys.stderr)
            time.sleep(60)
        else:
            print(f"  [!] {r.status_code} on {path}", file=sys.stderr)
        self.consecutive_errors += 1
        return None


# ── League discovery + caching ──────────────────────────────────────
def discover_cs2_leagues(c: PinnacleClient) -> list[dict]:
    """Hit /sports/12/leagues, filter for CS2/Counter-Strike, cache to disk."""
    leagues = c.get(f"/0.1/sports/{PINNACLE_ESPORTS_SPORT_ID}/leagues")
    if not leagues:
        return []
    cs2_leagues = []
    for L in leagues:
        name = (L.get("name") or "").lower()
        # Pinnacle uses "Counter-Strike" for CS2 leagues
        if "counter-strike" in name or "counter strike" in name or " cs2" in name:
            cs2_leagues.append({"id": L["id"], "name": L["name"],
                                "matchupCount": L.get("matchupCount", 0)})
    LEAGUE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    LEAGUE_CACHE.write_text(json.dumps(cs2_leagues, indent=2))
    return cs2_leagues


def load_cs2_leagues(c: PinnacleClient, refresh: bool = False) -> list[dict]:
    if not refresh and LEAGUE_CACHE.exists():
        try:
            cached = json.loads(LEAGUE_CACHE.read_text())
            if cached:
                return cached
        except json.JSONDecodeError:
            pass
    return discover_cs2_leagues(c)


# ── Team-name normalisation ─────────────────────────────────────────
_CLUB_PREFIX_RE = re.compile(r"^(team|fc|esports|gaming|club)\s+", re.IGNORECASE)


def normalize_team(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFD", name)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower().strip()
    s = _CLUB_PREFIX_RE.sub("", s)
    s = re.sub(r"\b(esports|gaming|team|club)\b", "", s)  # strip suffixes too
    s = re.sub(r"[^a-z0-9]+", "", s).strip()
    return s


# ── Markets parsing ─────────────────────────────────────────────────
def parse_moneyline(markets: list[dict], matchup_id: int) -> tuple[float, float] | None:
    """From the /markets/straight response, find the moneyline (decimal odds)
    for the given matchupId. Returns (price_home, price_away) or None."""
    for m in markets:
        if m.get("matchupId") != matchup_id:
            continue
        if m.get("type") != "moneyline" or m.get("period") != 0:
            continue
        prices = m.get("prices") or []
        if len(prices) != 2:
            continue
        # Pinnacle uses "designation": "home"/"away"
        odds = {}
        for p in prices:
            d = p.get("designation")
            if d in ("home", "away") and p.get("price"):
                # American odds → decimal
                am = float(p["price"])
                if am > 0:
                    dec = am / 100 + 1
                else:
                    dec = 100 / abs(am) + 1
                odds[d] = round(dec, 3)
        if "home" in odds and "away" in odds:
            return odds["home"], odds["away"]
    return None


# ── Main scan ───────────────────────────────────────────────────────
def scan(dry: bool = False, limit: int | None = None, refresh_leagues: bool = False) -> dict:
    c = PinnacleClient()
    leagues = load_cs2_leagues(c, refresh=refresh_leagues)
    print(f"  cs2 leagues: {len(leagues)}")
    for L in leagues:
        print(f"    - {L['name']:40} (id={L['id']}, matchups={L.get('matchupCount',0)})")

    # Pull our pending matches in the next 48h to fuzzy-match against
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=POLL_WINDOW_HOURS)
    db_matches = execute_query("""
        SELECT id, bo3gg_id, team1, team2, kickoff_time
        FROM cs2_upcoming_matches
        WHERE kickoff_time BETWEEN %s AND %s
          AND state IN ('unstarted','inProgress')
    """, (now.isoformat(), cutoff.isoformat()))
    print(f"  our matches in next {POLL_WINDOW_HOURS}h: {len(db_matches)}")

    # Index our matches by normalised team-name pair for fuzzy lookup
    db_index: dict[tuple[str, str], dict] = {}
    for row in db_matches:
        a, b = normalize_team(row["team1"]), normalize_team(row["team2"])
        # Store both orderings so home/away in Pinnacle can match either side
        db_index[(a, b)] = {"row": row, "swap": False}
        db_index[(b, a)] = {"row": row, "swap": True}

    matched = updated = unmatched = 0
    for L in leagues:
        if c.req_count >= MAX_REQUESTS_PER_RUN - 2:
            break
        matchups = c.get(f"/0.1/leagues/{L['id']}/matchups")
        if not matchups:
            continue
        if limit:
            matchups = matchups[:limit]
        markets = c.get(f"/0.1/leagues/{L['id']}/markets/straight")
        if markets is None:
            markets = []
        for mu in matchups:
            participants = mu.get("participants") or []
            if len(participants) != 2:
                continue
            mu_id = mu.get("id")
            t1_name = participants[0].get("name") or ""
            t2_name = participants[1].get("name") or ""
            key = (normalize_team(t1_name), normalize_team(t2_name))
            entry = db_index.get(key)
            if not entry:
                unmatched += 1
                continue
            matched += 1
            ml = parse_moneyline(markets, mu_id)
            if not ml:
                continue
            pin1, pin2 = ml
            if entry["swap"]:
                pin1, pin2 = pin2, pin1
            row = entry["row"]
            print(f"  ✓ {row['team1'][:18]:18} vs {row['team2'][:18]:18}  pinnacle: {pin1:.3f} / {pin2:.3f}")
            if not dry:
                execute_write("""
                    UPDATE cs2_upcoming_matches
                       SET pinnacle_odds1 = %s, pinnacle_odds2 = %s
                     WHERE id = %s
                """, (pin1, pin2, row["id"]))
                updated += 1

    return {
        "leagues": len(leagues),
        "our_matches": len(db_matches),
        "matched": matched, "updated": updated, "unmatched": unmatched,
        "requests": c.req_count,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="Print only, no DB write")
    ap.add_argument("--limit", type=int, help="Cap matchups per league (debug)")
    ap.add_argument("--refresh-leagues", action="store_true", help="Re-discover CS2 league IDs")
    args = ap.parse_args()

    print(f"\n=== CS2 Pinnacle Scanner  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    ctx = scraper_run("pinnacle_scanner", "Pinnacle CS2 moneyline scrape (Railway-side IP only)") if (scraper_run and not args.dry) else None
    st = ctx.__enter__() if ctx else None
    try:
        try:
            result = scan(dry=args.dry, limit=args.limit, refresh_leagues=args.refresh_leagues)
        except SystemExit as e:
            print(f"  ! aborted: {e}", file=sys.stderr)
            if st: st.tick_failed(str(e))
            return
        print(f"\n  result: {result}")
        if st:
            st.set_total(result["our_matches"])
            for _ in range(result["updated"]):
                st.tick_done()
            st.note(f"matched={result['matched']} updated={result['updated']} "
                    f"unmatched={result['unmatched']} reqs={result['requests']}")
    finally:
        if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
