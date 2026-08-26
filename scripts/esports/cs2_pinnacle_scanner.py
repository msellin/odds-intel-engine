"""
CS2 Pinnacle Scanner — fetch CS2 match odds from Pinnacle's public guest API.

Pinnacle's closing line is the gold-standard truth label in sports betting —
their book is sharp, well-capitalized, and moves on real information. Adding
pinnacle_implied_prob as a model feature is documented to add 2-5pp AUC in
similar studies — bigger than every other signal we've added today combined.

API: guest.api.arcadia.pinnacle.com — public, no auth key required, but
geo-blocked in several countries including Estonia. the VPS US IP works.

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

# ── fuzzy backend ───────────────────────────────────────────────────
# Mirrors cs2_match_id_bridge_populate.py — rapidfuzz preferred, difflib
# fallback so the script keeps working in any minimal env.
try:
    from rapidfuzz import fuzz as _rf_fuzz  # type: ignore

    def _fuzz_score(a: str, b: str) -> float:
        return float(_rf_fuzz.token_set_ratio(a, b))
    _FUZZ_BACKEND = "rapidfuzz"
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def _fuzz_score(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0
    _FUZZ_BACKEND = "difflib"

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
def discover_cs2_leagues(c: PinnacleClient) -> tuple[list[dict], str]:
    """Hit /sports/12/leagues, filter for CS2/Counter-Strike, cache to disk.

    Returns (filtered_list, diagnostic_str) so the scraper state row can record
    what we found — important since the script runs remotely on the VPS and we
    can't see stdout directly.
    """
    leagues = c.get(f"/0.1/sports/{PINNACLE_ESPORTS_SPORT_ID}/leagues")
    if leagues is None:
        return [], "sports/12/leagues returned None (HTTP error)"
    if not isinstance(leagues, list):
        return [], f"unexpected response type: {type(leagues).__name__}"
    if not leagues:
        return [], "sports/12/leagues returned empty list"

    cs2_leagues = []
    sample_names = []
    for L in leagues:
        name = (L.get("name") or "")
        nl = name.lower()
        if len(sample_names) < 5:
            sample_names.append(name)
        # Pinnacle uses "Counter-Strike" for CS2 leagues. Also match broader
        # patterns in case the naming is different than expected.
        if ("counter-strike" in nl or "counter strike" in nl or " cs2" in nl
            or "cs:go" in nl or "cs go" in nl):
            cs2_leagues.append({"id": L["id"], "name": name,
                                "matchupCount": L.get("matchupCount", 0)})
    LEAGUE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    LEAGUE_CACHE.write_text(json.dumps({
        "filtered": cs2_leagues,
        "all_count": len(leagues),
        "sample_names": sample_names,
    }, indent=2))
    diag = f"sport_12: {len(leagues)} leagues total, {len(cs2_leagues)} CS-filtered. Sample: {sample_names[:3]}"
    return cs2_leagues, diag


def load_cs2_leagues(c: PinnacleClient, refresh: bool = False) -> tuple[list[dict], str]:
    if not refresh and LEAGUE_CACHE.exists():
        try:
            cached = json.loads(LEAGUE_CACHE.read_text())
            if isinstance(cached, dict) and cached.get("filtered"):
                return cached["filtered"], "loaded from cache"
            if isinstance(cached, list) and cached:
                return cached, "loaded from legacy cache"
        except json.JSONDecodeError:
            pass
    return discover_cs2_leagues(c)


# ── Team-name normalisation ─────────────────────────────────────────
# Mirrors cs2_match_id_bridge_populate.py (which hit 84.5% coverage on
# 9,266 bo3.gg results) and adds Pinnacle-specific aliases noticed in
# Pinnacle's CS2 markets vs bo3.gg's listings.
#
# Trailing org-suffix words that don't change team identity.
# Order matters — multi-word phrases first.
_SUFFIX_WORDS = [
    "esports.net",
    "e-sports",
    "esports",
    "esport",
    "gaming",
    "academy",
    "clan",
    "club",
    "team",
    "pro",
    "fe",
]

# Known alias pairs (lowercased pre-collapse). We unify both sides to the
# same canonical token so exact + normalised lookups collide cleanly.
_ALIAS_MAP = {
    # bo3gg/HLTV-side aliases (carried over from the bridge populator)
    "faze clan": "faze",
    "team spirit": "spirit",
    "spirit academy": "spirit_academy",
    "team vitality": "vitality",
    "team liquid": "liquid",
    "team falcons": "falcons",
    "team heretics": "heretics",
    "team aurora": "aurora",
    "natus vincere": "navi",
    "natus vincere junior": "navi_junior",
    "navi junior": "navi_junior",
    "ninjas in pyjamas": "nip",
    "g2 esports": "g2",
    "mouz nxt": "mouz_nxt",
    "mouz": "mouz",
    "mousesports": "mouz",
    "fnatic rising": "fnatic_rising",
    "1win team": "1win",
    "9 pandas": "9pandas",
    "ninepandas": "9pandas",
    "saw esports": "saw",
    "the mongolz": "mongolz",
    "mongolz": "mongolz",
    "ence academy": "ence_academy",
    "ence": "ence",
    "heroic academy": "heroic_academy",
    "complexity gaming": "complexity",
    "ex-betera": "betera",
    "ex-anonymo": "anonymo",
    # Pinnacle-specific variants (Pinnacle tends to drop "Team"/"Clan"
    # and uses shorter org-name forms).
    "navi": "navi",
    "faze": "faze",
    "spirit": "spirit",
    "vitality": "vitality",
    "liquid": "liquid",
    "falcons": "falcons",
    "heretics": "heretics",
    "aurora": "aurora",
    "1win": "1win",
    "nip": "nip",
    "g2": "g2",
    "fnatic": "fnatic",
    "navi jr": "navi_junior",
    "navi jr.": "navi_junior",
    "navi junior team": "navi_junior",
    "the mongoiz": "mongolz",  # occasional typo in feeds
    "9z": "9z",
    "9z team": "9z",
    "team 9z": "9z",
    "imperial fe": "imperial",
    "imperial esports": "imperial",
    "imperial": "imperial",
    "fluxo demolition": "fluxo_demolition",
    "betclic apogee": "apogee",
    "apogee": "apogee",
    "passion ua": "passion_ua",
    "passion": "passion_ua",
    "betera esports": "betera",
    "betera": "betera",
    "monte gen": "monte_gen",
    "monte": "monte",
    "los kogutos": "los_kogutos",
    "kogutos": "los_kogutos",
    "virtus.pro": "virtuspro",
    "virtus pro": "virtuspro",
    "vp": "virtuspro",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _strip_suffixes(s: str) -> str:
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIX_WORDS:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
                break
    return s


def _strip_prefix_team(s: str) -> str:
    if s.startswith("team ") and len(s) > 6:
        return s[5:]
    return s


def normalize_team(name: str) -> str:
    """Lower, deaccent, drop org suffixes/prefixes, apply alias map, collapse non-alnum."""
    if not name:
        return ""
    s = unicodedata.normalize("NFD", name)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower().strip()
    # First pass — raw lowercased alias hit (e.g. "FaZe Clan" → "faze")
    if s in _ALIAS_MAP:
        s = _ALIAS_MAP[s]
    s = _strip_prefix_team(s)
    s = _strip_suffixes(s)
    # Second pass — stripped form alias hit (e.g. "spirit academy" after
    # suffix strip is "spirit academy" still — re-check)
    if s in _ALIAS_MAP:
        s = _ALIAS_MAP[s]
    # Collapse non-alnum for resilience to dots/hyphens/spaces.
    s = _NON_ALNUM.sub("", s)
    return s


# ── 3-stage matcher ─────────────────────────────────────────────────
FUZZ_MIN_BOTH = 70.0  # min token_set_ratio on BOTH team comparisons


def _exact_pair(a1: str, a2: str, b1: str, b2: str) -> bool:
    """Exact match on raw lowercased names, either ordering."""
    a1l, a2l, b1l, b2l = a1.lower(), a2.lower(), b1.lower(), b2.lower()
    return (a1l == b1l and a2l == b2l) or (a1l == b2l and a2l == b1l)


def _norm_pair(a1n: str, a2n: str, b1n: str, b2n: str) -> bool:
    """Match on normalised + alias-collapsed forms, either ordering."""
    if not (a1n and a2n and b1n and b2n):
        return False
    return (a1n == b1n and a2n == b2n) or (a1n == b2n and a2n == b1n)


def _fuzzy_pair_score(a1: str, a2: str, b1: str, b2: str) -> tuple[float, float, bool]:
    """Return (best_avg, best_min, swapped) across the two pairings.
    `swapped` is True if (a1↔b2, a2↔b1) beat (a1↔b1, a2↔b2)."""
    s_a1 = _fuzz_score(a1, b1)
    s_a2 = _fuzz_score(a2, b2)
    s_b1 = _fuzz_score(a1, b2)
    s_b2 = _fuzz_score(a2, b1)
    avg_a = (s_a1 + s_a2) / 2.0
    min_a = min(s_a1, s_a2)
    avg_b = (s_b1 + s_b2) / 2.0
    min_b = min(s_b1, s_b2)
    if avg_a >= avg_b:
        return avg_a, min_a, False
    return avg_b, min_b, True


def match_pinnacle_to_db(
    pin_t1: str, pin_t2: str, pin_kickoff: datetime | None,
    db_matches: list[dict],
) -> tuple[dict, bool, str] | None:
    """3-stage match — returns (db_row, swap, joined_by) or None.

    Stage 1: exact lowercased team-name match (either ordering)
    Stage 2: normalised + alias-map match (either ordering)
    Stage 3: rapidfuzz token_set_ratio on both team comparisons, min ≥70

    Kickoff filter: only DB rows within ±4h of pin_kickoff are considered
    (when both timestamps are present). Pinnacle and bo3.gg occasionally
    drift by an hour or so on rescheduled matches.
    """
    pin_n1 = normalize_team(pin_t1)
    pin_n2 = normalize_team(pin_t2)

    # Time-filter candidates first (cheap)
    candidates: list[dict] = []
    for row in db_matches:
        ko = row.get("kickoff_time")
        if pin_kickoff and ko:
            if abs((ko - pin_kickoff).total_seconds()) > 4 * 3600:
                continue
        candidates.append(row)

    # Stage 1 — exact
    for row in candidates:
        if _exact_pair(pin_t1, pin_t2, row["team1"], row["team2"]):
            swap = pin_t1.lower() != row["team1"].lower()
            return row, swap, "exact"

    # Stage 2 — normalised + alias-mapped
    for row in candidates:
        db_n1 = normalize_team(row["team1"])
        db_n2 = normalize_team(row["team2"])
        if _norm_pair(pin_n1, pin_n2, db_n1, db_n2):
            swap = (pin_n1 != db_n1)
            return row, swap, "norm_team"

    # Stage 3 — fuzzy. Take the best across all candidates above threshold.
    best: tuple[float, dict, bool] | None = None
    for row in candidates:
        avg, mn, swapped = _fuzzy_pair_score(
            pin_t1, pin_t2, row["team1"], row["team2"]
        )
        if mn < FUZZ_MIN_BOTH:
            continue
        if best is None or avg > best[0]:
            best = (avg, row, swapped)
    if best is not None:
        _avg, row, swapped = best
        return row, swapped, "fuzzy"

    return None


# ── Markets parsing ─────────────────────────────────────────────────
def parse_moneyline(markets: list[dict], matchup_id: int) -> tuple[float, float] | None:
    """From the /markets/straight response, find the SERIES moneyline (decimal
    odds) for the given matchupId. Returns (price_home, price_away) or None.

    Pinnacle exposes several market types per matchup:
      - type="moneyline" + period=0   → full series (BO3/BO5) winner — what we want
      - type="moneyline" + period=1+  → individual map winners
      - type="spread"/"total"         → handicap / totals (rounds/maps)

    We only consume the series-level moneyline (period=0). Map-level and
    handicap markets are filtered out here so the parent matchup row gets
    written with the right closing line for the bet we're tracking
    (series winner — bo3.gg matches our `team1`/`team2` are series rows).
    """
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


def _is_series_matchup(mu: dict) -> bool:
    """True if this matchup is the parent series (not a map-handicap /
    map-totals child). Pinnacle marks children with `parentId` set or a
    `type` of "special". We want only top-level series with 2 participants.
    """
    if mu.get("parentId"):
        return False
    if mu.get("type") and mu.get("type") not in ("matchup",):
        return False
    parts = mu.get("participants") or []
    if len(parts) != 2:
        return False
    return True


def _parse_pinnacle_kickoff(mu: dict) -> datetime | None:
    """Pinnacle returns `startTime` as ISO-8601 UTC (e.g. 2026-06-10T15:00:00Z).
    Returns timezone-aware UTC datetime or None."""
    raw = mu.get("startTime")
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


# ── Main scan ───────────────────────────────────────────────────────
def scan(dry: bool = False, limit: int | None = None, refresh_leagues: bool = False,
         dump_unmatched: bool = False) -> dict:
    c = PinnacleClient()
    leagues, league_diag = load_cs2_leagues(c, refresh=refresh_leagues)
    print(f"  cs2 leagues: {len(leagues)}  ({league_diag})  fuzzy-backend={_FUZZ_BACKEND}")
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

    matched = updated = unmatched = 0
    match_breakdown = {"exact": 0, "norm_team": 0, "fuzzy": 0}
    unmatched_samples: list[dict] = []

    for L in leagues:
        if c.req_count >= MAX_REQUESTS_PER_RUN - 2:
            break
        matchups = c.get(f"/0.1/leagues/{L['id']}/matchups")
        if not matchups:
            continue
        # Restrict to series-level matchups before slicing by --limit so
        # `--limit 5` doesn't accidentally consume 5 map-handicap children.
        matchups = [mu for mu in matchups if _is_series_matchup(mu)]
        if limit:
            matchups = matchups[:limit]
        markets = c.get(f"/0.1/leagues/{L['id']}/markets/straight")
        if markets is None:
            markets = []
        for mu in matchups:
            participants = mu.get("participants") or []
            mu_id = mu.get("id")
            t1_name = participants[0].get("name") or ""
            t2_name = participants[1].get("name") or ""
            pin_ko = _parse_pinnacle_kickoff(mu)

            result = match_pinnacle_to_db(t1_name, t2_name, pin_ko, db_matches)
            if result is None:
                unmatched += 1
                if len(unmatched_samples) < 15:
                    unmatched_samples.append({
                        "league": L["name"],
                        "team1": t1_name,
                        "team2": t2_name,
                        "kickoff": pin_ko.isoformat() if pin_ko else None,
                    })
                continue
            row, swap, joined_by = result
            matched += 1
            match_breakdown[joined_by] += 1

            ml = parse_moneyline(markets, mu_id)
            if not ml:
                continue
            pin1, pin2 = ml
            if swap:
                pin1, pin2 = pin2, pin1
            tag = {"exact": "=", "norm_team": "~", "fuzzy": "?"}[joined_by]
            print(f"  {tag} {row['team1'][:18]:18} vs {row['team2'][:18]:18}  "
                  f"pinnacle({t1_name[:15]}/{t2_name[:15]}): {pin1:.3f} / {pin2:.3f}")
            if not dry:
                execute_write("""
                    UPDATE cs2_upcoming_matches
                       SET pinnacle_odds1 = %s, pinnacle_odds2 = %s
                     WHERE id = %s
                """, (pin1, pin2, row["id"]))
                updated += 1

    if dump_unmatched or dry:
        print()
        print("── Unmatched Pinnacle matchups (up to 15) ─────────────────────")
        for u in unmatched_samples:
            print(f"  [{u['league'][:25]:25}] {u['team1']!r} vs {u['team2']!r}"
                  f"  ko={u['kickoff']}")

    return {
        "leagues": len(leagues),
        "league_diag": league_diag,
        "our_matches": len(db_matches),
        "matched": matched, "updated": updated, "unmatched": unmatched,
        "match_breakdown": match_breakdown,
        "requests": c.req_count,
        "fuzz_backend": _FUZZ_BACKEND,
        "unmatched_samples": unmatched_samples,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", "--dry-run", action="store_true", dest="dry",
                    help="Print only, no DB write; also dumps unmatched Pinnacle matchups")
    ap.add_argument("--limit", type=int, help="Cap matchups per league (debug)")
    ap.add_argument("--refresh-leagues", action="store_true", help="Re-discover CS2 league IDs")
    ap.add_argument("--dump-unmatched", action="store_true",
                    help="Always print up to 15 unmatched Pinnacle matchups (even in live mode)")
    args = ap.parse_args()

    print(f"\n=== CS2 Pinnacle Scanner  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    ctx = scraper_run("pinnacle_scanner", "Pinnacle CS2 moneyline scrape (VPS-side IP only)") if (scraper_run and not args.dry) else None
    st = ctx.__enter__() if ctx else None
    try:
        try:
            result = scan(dry=args.dry, limit=args.limit,
                          refresh_leagues=args.refresh_leagues,
                          dump_unmatched=args.dump_unmatched)
        except SystemExit as e:
            print(f"  ! aborted: {e}", file=sys.stderr)
            if st: st.tick_failed(str(e))
            return
        # Pretty-print the result (drop the verbose sample list)
        result_summary = {k: v for k, v in result.items() if k != "unmatched_samples"}
        print(f"\n  result: {result_summary}")
        if st:
            st.set_total(result["our_matches"])
            for _ in range(result["updated"]):
                st.tick_done()
            # Surface league discovery diagnostic in notes — critical for debugging
            # remote-only failures (VPS-side IP can't be probed locally).
            mb = result.get("match_breakdown", {})
            st.note(
                f"leagues={result['leagues']} "
                f"diag=\"{result.get('league_diag','')}\" "
                f"matched={result['matched']} (exact={mb.get('exact',0)} "
                f"norm={mb.get('norm_team',0)} fuzzy={mb.get('fuzzy',0)}) "
                f"updated={result['updated']} unmatched={result['unmatched']} "
                f"reqs={result['requests']} backend={result.get('fuzz_backend','?')}"
            )
    finally:
        if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
