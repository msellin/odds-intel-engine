"""
OddsIntel — WC2026 Market Consensus Scraper (WC-A3)

Scrapes 1X2 implied probabilities for every upcoming World Cup 2026 fixture
from 2-3 FREE public sources, vig-removes them, aggregates by simple mean
across sources, and writes the result to `wc_market_consensus`.

Why this exists: our ELO+Poisson national-team model produced
"Brazil 22% / Morocco 50%" for the WC opener while the market (Bet365,
Dimers, Opta) had Brazil at 55-69%. We want a per-fixture market-consensus
signal so we can (a) blend it with our own model in the next wave, and
(b) surface model-vs-market disagreement on the match-detail page.

Sources (Wave-2 working set — replaced the Wave-1 stubs):
  1. eloratings.net  — fixtures.tsv has every international fixture with
                       team1_win%, draw%, team2_win% columns. Single TSV
                       request, very efficient. Pure model (Elo-based).
  2. Pinnacle public — guest.api.arcadia.pinnacle.com exposes league 2686
       (Arcadia)      (FIFA World Cup) /matchups + /markets/straight. Two
                       JSON requests give us every WC moneyline (American
                       odds). The sharpest book on the planet — high-signal
                       market data.
  3. Smarkets API    — api.smarkets.com/v3/events with parent_id of the WC
                       container event returns 64-72 fixtures; per-event
                       /markets/ + /contracts/ + /last_executed_prices/
                       gives exchange-implied probabilities. Real money,
                       essentially vig-free.

Wave-1 sources that turned out to be dead ends and were removed:
  - forebet — WC predictions page has no WC fixtures listed yet (only
    club games). Will revisit if forebet adds WC coverage closer to KO.
  - oddsportal — Cloudflare-protected, 404s the slug, JS-only HTML.
  - betfair.com /exchange/plus/ — JS shell, no static markup; the actual
    exchange API requires app key + session.

A fixture is written only when at least TWO sources succeed. Per-source
probabilities are vig-removed (each (h, d, a) tuple normalised to sum to
1.0). Source-level outputs are stored under `sources` JSONB for audit.

Polite scraping: ≥2s between requests, real User-Agent, no parallel
hammering, graceful skip on 403/timeout/parse error. Smarkets requires
4 requests per fixture (events list + per-event markets/contracts/prices),
so on a 72-fixture run that's ~5min — comfortably within the daily window.

Usage:
    python3 scripts/scrape_wc_market_consensus.py
    python3 scripts/scrape_wc_market_consensus.py --dry-run --max-fixtures 3
    python3 scripts/scrape_wc_market_consensus.py --max-fixtures 10
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

from workers.api_clients.db import execute_query, execute_write  # noqa: E402

console = Console()

# ── Constants ─────────────────────────────────────────────────────────────

WC_LEAGUE_AF_ID = 1  # matches workers.jobs.wc_match_previews.WC_LEAGUE_AF_ID

# Polite scraping config — ≥2s between requests, real UA, sensible timeout.
REQUEST_INTERVAL_S = 2.0
REQUEST_TIMEOUT_S = 20
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

# A consensus row needs at least this many successful sources to be stored.
MIN_SOURCES = 2

# Vig-removal tolerance — assert sum(h, d, a) ≈ 1.0 ±VIG_TOL post-normalise.
VIG_TOL = 0.01

# Source endpoints — kept module-level so the smoke test can pin them.
# Eloratings publishes a single TSV with every international fixture's
# predicted W/D/L (model-based, Elo-derived).
ELORATINGS_FIXTURES_TSV = "https://www.eloratings.net/fixtures.tsv"
ELORATINGS_TEAMS_TSV    = "https://www.eloratings.net/en.teams.tsv"

# Pinnacle's guest Arcadia API — no auth required for read.
PINNACLE_WC_LEAGUE_ID   = 2686  # FIFA - World Cup (verified 2026-06-04)
PINNACLE_MATCHUPS_URL   = (
    f"https://guest.api.arcadia.pinnacle.com/0.1/leagues/{PINNACLE_WC_LEAGUE_ID}/matchups"
)
PINNACLE_MARKETS_URL    = (
    f"https://guest.api.arcadia.pinnacle.com/0.1/leagues/{PINNACLE_WC_LEAGUE_ID}/markets/straight"
)

# Smarkets — exchange odds, last executed price per contract.
# parent_id 42791414 is the WC2026 container event (verified 2026-06-04).
SMARKETS_WC_PARENT_ID   = 42791414
SMARKETS_EVENTS_URL     = (
    "https://api.smarkets.com/v3/events/"
    "?states=upcoming&type_scope=single_event&types=football_match"
    f"&with_new_in=true&parent_id={SMARKETS_WC_PARENT_ID}&limit=200"
)


# ── HTTP layer ────────────────────────────────────────────────────────────

class PoliteFetcher:
    """Single-threaded HTTP fetcher that enforces ≥REQUEST_INTERVAL_S
    between requests so we never hammer a source. Real UA, persistent
    session for keep-alive.

    Provides both `get()` (raw text) and `get_json()` (auto-decoded) —
    every source we use now returns JSON or TSV, no HTML parsing left.
    """

    def __init__(self, *, interval_s: float = REQUEST_INTERVAL_S) -> None:
        self._last_request_ts = 0.0
        self._interval_s = interval_s
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, text/tab-separated-values, */*",
            "Accept-Language": "en-US,en;q=0.5",
        })

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._interval_s:
            time.sleep(self._interval_s - elapsed)
        self._last_request_ts = time.monotonic()

    def get(self, url: str, *, extra_headers: Optional[dict] = None) -> Optional[str]:
        self._wait()
        headers = dict(extra_headers or {})
        try:
            resp = self._session.get(url, headers=headers, timeout=REQUEST_TIMEOUT_S)
        except requests.RequestException as e:
            console.print(f"  [yellow]HTTP error for {url}: {type(e).__name__}: {e}[/yellow]")
            return None
        if resp.status_code != 200:
            console.print(f"  [yellow]HTTP {resp.status_code} for {url}[/yellow]")
            return None
        return resp.text

    def get_json(self, url: str, *, extra_headers: Optional[dict] = None) -> Optional[object]:
        txt = self.get(url, extra_headers=extra_headers)
        if txt is None:
            return None
        try:
            return json.loads(txt)
        except json.JSONDecodeError as e:
            console.print(f"  [yellow]JSON decode error for {url}: {e}[/yellow]")
            return None


# ── Normalisation helpers ─────────────────────────────────────────────────

def _slug(name: str) -> str:
    """Normalise a team name for fuzzy matching across sources.

    Strip diacritics (Côte d'Ivoire → Cote dIvoire), lowercase, drop non-
    alphanumerics. Source pages use a mix of conventions ('USA' vs 'United
    States', 'Korea Republic' vs 'South Korea') so we also expand a few
    common aliases below.
    """
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", ascii_name.lower())


# Hand-curated aliases. Keys are slugs of the canonical AF name, values are
# slugs of the variant likely to appear in the wild. Bidirectional matching
# via _slug-equivalence below — we add the reverse mapping at module load.
# Updated 2026-06-04 to cover the WC opener canary (Brazil v Morocco — no
# alias needed, both spellings agree) plus every country in our 06-11 → 06-15
# fixture window.
_ALIASES_RAW = {
    "usa":                "unitedstates",
    "southkorea":         "korearepublic",
    "iran":               "iranislamicrepublic",
    "ivorycoast":         "cotedivoire",
    "capeverdeislands":   "capeverde",
    "czechrepublic":      "czechia",
    "northmacedonia":     "macedoniafyr",
    "turkiye":            "turkey",
    "bosniaherzegovina":  "bosniaandherzegovina",
    "curacao":            "curaao",  # diacritic-stripped 'Curaçao' → 'curaao'
}


def _names_match(a: str, b: str) -> bool:
    sa, sb = _slug(a), _slug(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    if _ALIASES_RAW.get(sa) == sb or _ALIASES_RAW.get(sb) == sa:
        return True
    # Loose containment ("Brazil" in "Brazil U23" — rare for senior WC but safe).
    return sa in sb or sb in sa


def vig_remove(h: float, d: float, a: float) -> Optional[tuple[float, float, float]]:
    """Take raw implied probs (already in [0, 1] or [0, 100]) and return
    a vig-removed triple summing to exactly 1.0. Returns None if the input
    is malformed (zero/negative or wildly out of range)."""
    if h is None or d is None or a is None:
        return None
    # Tolerate percentages.
    if max(h, d, a) > 1.5:
        h, d, a = h / 100.0, d / 100.0, a / 100.0
    if h <= 0 or d <= 0 or a <= 0:
        return None
    s = h + d + a
    if s <= 0:
        return None
    return (h / s, d / s, a / s)


def american_to_decimal(odds: float) -> Optional[float]:
    """Pinnacle returns American moneylines. Convert to decimal so we can
    take 1/decimal as implied probability. Returns None on degenerate input."""
    if odds is None:
        return None
    try:
        odds = float(odds)
    except (TypeError, ValueError):
        return None
    if odds == 0:
        return None
    if odds > 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)


# ── Source: eloratings.net (model — fixtures.tsv) ─────────────────────────

def _winexp_to_wdl(we: float) -> tuple[float, float, float]:
    """Convert a single Elo win-expectancy into a discrete (win, draw, loss)
    triple for the home team.

    Eloratings' fixtures.tsv only publishes win-expectancy (team1's expected
    share of points, where win=1, draw=0.5, loss=0). Their own page derives
    team2 win-expectancy as `100 - we1` and never breaks the draw out
    separately — they label col 12 'draw' but it's actually the rating
    exchange on a draw outcome via `formatChange(fields[12])` in their JS,
    not a probability. So to get a 1X2 from their TSV we need to split
    win-expectancy ourselves.

    We use a draw-probability parabola peaking at we=0.5: this is the
    classic "Maher-style" closed form often used to back out a 1X2 from
    a single rating-derived expectancy. It produces:
        we = 0.50 → draw ≈ 0.30 (balanced match)
        we = 0.72 → draw ≈ 0.24 (Brazil-Morocco — Pinnacle agrees: 0.252)
        we = 0.93 → draw ≈ 0.08 (Mexico-South Africa — favourites get low draw rate)
        we = 0.06 → draw ≈ 0.07 (heavy underdog at home)
    then win = we - 0.5*draw, loss = (1 - we) - 0.5*draw. This guarantees
    win + draw + loss = 1.0 (i.e. winexp is preserved as team1's expected
    points share).

    This is a model-based approximation — Eloratings remains our noisiest
    source. The point isn't to match it to the market exactly; it's to
    have an independent third source so MIN_SOURCES≥2 still passes when
    Pinnacle or Smarkets has a transient outage.
    """
    if we is None or we < 0 or we > 1:
        return (0.0, 0.0, 0.0)
    draw = max(0.0, 0.30 * (1.0 - 4.0 * (we - 0.5) ** 2))
    win = max(0.0, we - 0.5 * draw)
    loss = max(0.0, (1.0 - we) - 0.5 * draw)
    return (win, draw, loss)


def scrape_eloratings(fetcher: PoliteFetcher) -> dict[tuple[str, str], tuple[float, float, float]]:
    """Return {(home_slug, away_slug): (h, d, a)} from eloratings.net.

    eloratings.net is a JS SPA, but the data layer is plain TSV at known
    URLs (the page just renders these client-side). We fetch:
      - en.teams.tsv     → ISO-ish country code → display name
      - fixtures.tsv     → one row per upcoming international fixture

    fixtures.tsv columns (verified 2026-06-04 against `scripts/ratings.js`
    in the JS bundle — see lines 38770-38800 which assign row.winexp /
    row.draw from fields[11..]):
        0..2   year, month, day
        3,4    team1_code, team2_code   (e.g. BR, MA)
        5      tournament_code          (WC = World Cup)
        6      venue_code
        7,8    team1_rank, team2_rank
        9,10   team1_elo,  team2_elo
        11     team1 win-expectancy %   (team1's expected share of points,
                                          where win=1, draw=0.5, loss=0)
        12     rating exchange on draw  (signed integer, NOT a probability;
                                          the eloratings page labels this
                                          'draw' but it's the rating delta
                                          via formatChange(), not P(draw))
        13+    change1..change5         (rating changes in different scenarios)

    We pull col 11 as winexp and convert to a discrete (h, d, a) via
    `_winexp_to_wdl()`.
    """
    teams_tsv = fetcher.get(ELORATINGS_TEAMS_TSV)
    if not teams_tsv:
        return {}
    code_to_name: dict[str, str] = {}
    for line in teams_tsv.split("\n"):
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            code_to_name[parts[0]] = parts[1]

    fixtures_tsv = fetcher.get(ELORATINGS_FIXTURES_TSV)
    if not fixtures_tsv:
        return {}

    out: dict[tuple[str, str], tuple[float, float, float]] = {}
    for line in fixtures_tsv.split("\n"):
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        # Restrict to World Cup rows only — fixtures.tsv contains every
        # international (friendlies, qualifiers, AFCON, etc.).
        if parts[5] != "WC":
            continue
        t1_code, t2_code = parts[3], parts[4]
        t1_name = code_to_name.get(t1_code)
        t2_name = code_to_name.get(t2_code)
        if not (t1_name and t2_name):
            continue
        try:
            winexp_pct = float(parts[11])
        except ValueError:
            continue
        # Eloratings publishes a single win-expectancy (team1's share of
        # points, where win=1, draw=0.5, loss=0). Split into discrete W/D/L
        # via the draw-parabola model — see _winexp_to_wdl docstring.
        h, d, a = _winexp_to_wdl(winexp_pct / 100.0)
        triple = vig_remove(h, d, a)
        if not triple:
            continue
        out[(_slug(t1_name), _slug(t2_name))] = triple
    return out


# ── Source: Pinnacle public Arcadia API ───────────────────────────────────

def scrape_pinnacle(fetcher: PoliteFetcher) -> dict[tuple[str, str], tuple[float, float, float]]:
    """Return {(home_slug, away_slug): (h, d, a)} from Pinnacle.

    Pinnacle exposes a guest API at guest.api.arcadia.pinnacle.com — no
    auth required, JSON, public. League 2686 is FIFA - World Cup. We fetch:

      1. /leagues/2686/matchups            → matchup metadata, participants
                                              (home/away alignment)
      2. /leagues/2686/markets/straight    → all markets across every
                                              matchup in this league

    Filter markets to (period=0, type=moneyline, key='s;0;m') — that's
    the full-match 1X2. Prices are American (positive/negative integers);
    convert to decimal → implied → vig-remove. Three-way moneylines have
    'home', 'away', 'draw' designations.
    """
    matchups = fetcher.get_json(PINNACLE_MATCHUPS_URL)
    if not isinstance(matchups, list):
        return {}

    # Build matchup_id → (home_name, away_name) map. Skip 'TBD' matchups
    # (where the away participant name is None because the bracket round
    # isn't resolved yet) — they have no 1X2 we can use.
    mid_to_teams: dict[int, tuple[str, str]] = {}
    for m in matchups:
        parts = m.get("participants") or []
        if len(parts) != 2:
            continue
        home_p = next((p for p in parts if p.get("alignment") == "home"), None)
        away_p = next((p for p in parts if p.get("alignment") == "away"), None)
        if not (home_p and away_p):
            continue
        h_name = home_p.get("name")
        a_name = away_p.get("name")
        if not (h_name and a_name):
            continue
        mid_to_teams[m["id"]] = (h_name, a_name)

    markets = fetcher.get_json(PINNACLE_MARKETS_URL)
    if not isinstance(markets, list):
        return {}

    out: dict[tuple[str, str], tuple[float, float, float]] = {}
    for mkt in markets:
        if mkt.get("period") != 0:
            continue
        if mkt.get("type") != "moneyline":
            continue
        if mkt.get("key") != "s;0;m":
            continue
        mid = mkt.get("matchupId")
        teams = mid_to_teams.get(mid)
        if not teams:
            continue
        home_name, away_name = teams
        # prices: list of {designation, price} for 'home', 'away', 'draw'
        price_by_des: dict[str, float] = {}
        for p in (mkt.get("prices") or []):
            d = p.get("designation")
            if d in ("home", "away", "draw"):
                price_by_des[d] = p.get("price")
        if not all(k in price_by_des for k in ("home", "away", "draw")):
            continue
        dh = american_to_decimal(price_by_des["home"])
        dd = american_to_decimal(price_by_des["draw"])
        da = american_to_decimal(price_by_des["away"])
        if not (dh and dd and da):
            continue
        triple = vig_remove(1.0 / dh, 1.0 / dd, 1.0 / da)
        if not triple:
            continue
        out[(_slug(home_name), _slug(away_name))] = triple
    return out


# ── Source: Smarkets exchange API ─────────────────────────────────────────

def _smarkets_first_winner_market(fetcher: PoliteFetcher, event_id: str) -> Optional[str]:
    """Return the market_id of the WINNER_3_WAY (1X2) market for an event,
    or None if the event has no such market open."""
    data = fetcher.get_json(f"https://api.smarkets.com/v3/events/{event_id}/markets/")
    if not isinstance(data, dict):
        return None
    for mkt in data.get("markets", []):
        # Prefer the WINNER_3_WAY market explicitly. Smarkets sometimes
        # offers 'Match Odds (90 mins)' which is also winner-cat — we just
        # take the first winner-category market with 3 contracts.
        if mkt.get("category") == "winner" and mkt.get("market_type", {}).get("name") == "WINNER_3_WAY":
            return mkt.get("id")
    # Fallback: first 'winner' market.
    for mkt in data.get("markets", []):
        if mkt.get("category") == "winner":
            return mkt.get("id")
    return None


def scrape_smarkets(fetcher: PoliteFetcher,
                    fixture_match_keys: Optional[set[tuple[str, str]]] = None
                    ) -> dict[tuple[str, str], tuple[float, float, float]]:
    """Return {(home_slug, away_slug): (h, d, a)} from Smarkets.

    Smarkets is an exchange — last_executed_price is in 1/100ths of a
    percent (e.g. 5988 cents == 59.88% implied). 3-way (1X2) markets have
    HOME / DRAW / AWAY contract types. Last-executed sums ≈ 100% (exchange
    is essentially vig-free; the small drift we still pass through
    vig_remove() defensively).

    `fixture_match_keys` is an optional set of (home_slug, away_slug)
    tuples — when provided, we only call the per-event markets endpoint
    for events that match one of our fixtures. Saves ~3 requests per
    non-matching event on a typical run.
    """
    events_data = fetcher.get_json(SMARKETS_EVENTS_URL)
    if not isinstance(events_data, dict):
        return {}
    events = events_data.get("events") or []
    out: dict[tuple[str, str], tuple[float, float, float]] = {}

    for ev in events:
        # Event names look like "Brazil vs Morocco" — split on ' vs '.
        name = ev.get("name") or ""
        if " vs " not in name:
            continue
        home_name, away_name = name.split(" vs ", 1)
        key = (_slug(home_name), _slug(away_name))

        # Cost control: smarkets requires 3 extra HTTP requests per event
        # (markets, contracts, last_executed_prices) — at 2s/request that's
        # ~6s/event. On a --max-fixtures=5 dry-run we still want all 5
        # canary fixtures covered, but we should skip events that don't
        # match any caller fixture. The fixture_match_keys check below
        # tolerates the alias map (Bosnia & Herzegovina ↔ Bosnia and
        # Herzegovina, Türkiye ↔ Turkey, etc.) — a strict set lookup
        # would miss those.
        if fixture_match_keys is not None:
            matched = key in fixture_match_keys or any(
                _names_match(home_name, h) and _names_match(away_name, a)
                for (h, a) in fixture_match_keys
            )
            if not matched:
                continue

        ev_id = ev.get("id")
        if not ev_id:
            continue
        market_id = _smarkets_first_winner_market(fetcher, ev_id)
        if not market_id:
            continue
        # Contracts → name → contract_type (HOME/DRAW/AWAY)
        contracts_data = fetcher.get_json(
            f"https://api.smarkets.com/v3/markets/{market_id}/contracts/"
        )
        if not isinstance(contracts_data, dict):
            continue
        ctype_by_cid: dict[str, str] = {}
        for c in contracts_data.get("contracts", []):
            ctype = (c.get("contract_type") or {}).get("name")
            cid = c.get("id")
            if cid and ctype in ("HOME", "DRAW", "AWAY"):
                ctype_by_cid[cid] = ctype
        if len(ctype_by_cid) < 3:
            continue
        # Last executed prices
        lep_data = fetcher.get_json(
            f"https://api.smarkets.com/v3/markets/{market_id}/last_executed_prices/"
        )
        if not isinstance(lep_data, dict):
            continue
        leps = (lep_data.get("last_executed_prices") or {}).get(market_id, [])
        price_by_type: dict[str, float] = {}
        for p in leps:
            ctype = ctype_by_cid.get(p.get("contract_id"))
            lep = p.get("last_executed_price")
            if ctype and lep is not None:
                try:
                    price_by_type[ctype] = float(lep)
                except (TypeError, ValueError):
                    continue
        if not all(k in price_by_type for k in ("HOME", "DRAW", "AWAY")):
            # Fall back to mid-price from /quotes/ if an outcome never
            # traded yet (rare for headline WC matches but safe to handle).
            quotes_data = fetcher.get_json(
                f"https://api.smarkets.com/v3/markets/{market_id}/quotes/"
            )
            if isinstance(quotes_data, dict):
                for cid, q in quotes_data.items():
                    ctype = ctype_by_cid.get(cid)
                    if not ctype or ctype in price_by_type:
                        continue
                    bids = q.get("bids") or []
                    offers = q.get("offers") or []
                    if bids and offers:
                        mid = (bids[0]["price"] + offers[0]["price"]) / 2.0
                        # Quote prices are in cents (1/100 of %).
                        price_by_type[ctype] = mid / 100.0
        if not all(k in price_by_type for k in ("HOME", "DRAW", "AWAY")):
            continue
        triple = vig_remove(price_by_type["HOME"], price_by_type["DRAW"], price_by_type["AWAY"])
        if not triple:
            continue
        out[key] = triple

    return out


# ── Fixture loader ────────────────────────────────────────────────────────

def load_wc_fixtures(max_fixtures: Optional[int] = None) -> list[dict]:
    """Load every upcoming WC2026 fixture (status='scheduled') ordered by
    kickoff. `max_fixtures` caps the result for --dry-run / smoke runs."""
    today = date.today().isoformat()
    rows = execute_query(
        """
        SELECT
            m.id,
            m.date AS kickoff,
            ht.name AS home_team,
            at.name AS away_team
        FROM matches m
        JOIN teams   ht ON ht.id = m.home_team_id
        JOIN teams   at ON at.id = m.away_team_id
        JOIN leagues l  ON l.id  = m.league_id
        WHERE l.api_football_id = %s
          AND m.status = 'scheduled'
          AND m.date::date >= %s
        ORDER BY m.date ASC
        """,
        [WC_LEAGUE_AF_ID, today],
    ) or []
    if max_fixtures is not None:
        rows = rows[:max_fixtures]
    return rows


# ── Aggregation ───────────────────────────────────────────────────────────

def _match_fixture(home: str, away: str,
                   source: dict[tuple[str, str], tuple[float, float, float]]
                   ) -> Optional[tuple[float, float, float]]:
    """Look up a fixture in a source dict using fuzzy name matching."""
    if not source:
        return None
    # Exact slug pair first.
    direct = source.get((_slug(home), _slug(away)))
    if direct:
        return direct
    for (h_slug, a_slug), triple in source.items():
        if _names_match(home, h_slug) and _names_match(away, a_slug):
            return triple
    return None


def aggregate(per_source: dict[str, tuple[float, float, float]]
              ) -> Optional[tuple[float, float, float]]:
    """Mean-aggregate across sources, then re-normalise to sum to 1.0
    (defensive — the means already sum to ≈1, but float drift can take
    the sum to 0.99998…)."""
    if len(per_source) < MIN_SOURCES:
        return None
    n = len(per_source)
    h = sum(v[0] for v in per_source.values()) / n
    d = sum(v[1] for v in per_source.values()) / n
    a = sum(v[2] for v in per_source.values()) / n
    s = h + d + a
    if s <= 0:
        return None
    h, d, a = h / s, d / s, a / s
    # Sanity check post-aggregation — the smoke test pins this invariant.
    if abs((h + d + a) - 1.0) > VIG_TOL:
        return None
    return (h, d, a)


# ── Storage ───────────────────────────────────────────────────────────────

def upsert_consensus(match_id: str, h: float, d: float, a: float,
                     per_source: dict[str, tuple[float, float, float]]) -> bool:
    """Upsert the consensus row. Idempotent — re-run any time."""
    sources_json = json.dumps({k: [round(v[0], 6), round(v[1], 6), round(v[2], 6)]
                               for k, v in per_source.items()})
    try:
        execute_write(
            """
            INSERT INTO wc_market_consensus
                (match_id, snapshot_at, home_prob, draw_prob, away_prob,
                 n_sources, sources, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (match_id) DO UPDATE SET
                snapshot_at = EXCLUDED.snapshot_at,
                home_prob   = EXCLUDED.home_prob,
                draw_prob   = EXCLUDED.draw_prob,
                away_prob   = EXCLUDED.away_prob,
                n_sources   = EXCLUDED.n_sources,
                sources     = EXCLUDED.sources,
                updated_at  = NOW()
            """,
            [
                match_id,
                datetime.now(timezone.utc).isoformat(),
                float(h), float(d), float(a),
                len(per_source),
                sources_json,
                datetime.now(timezone.utc).isoformat(),
            ],
        )
        return True
    except Exception as e:
        console.print(f"  [red]DB error storing consensus: {type(e).__name__}: {e}[/red]")
        return False


# ── Main ──────────────────────────────────────────────────────────────────

def run_wc_market_consensus(
    max_fixtures: Optional[int] = None,
    dry_run: bool = False,
) -> dict:
    """Callable entry point — scheduler imports this directly. Returns a
    summary dict {fixtures_seen, written, skipped, per_source_counts}."""
    console.print("[bold cyan]═══ WC Market Consensus Scraper ═══[/bold cyan]\n")

    fixtures = load_wc_fixtures(max_fixtures=max_fixtures)
    if not fixtures:
        console.print("[yellow]No upcoming WC fixtures.[/yellow]")
        return {"fixtures_seen": 0, "written": 0, "skipped": 0, "per_source_counts": {}}

    console.print(f"Loaded {len(fixtures)} upcoming WC fixture(s)\n")

    fetcher = PoliteFetcher()

    # Precompute the set of (home_slug, away_slug) we care about — lets
    # smarkets short-circuit per-event probes for events that don't match
    # anything in our DB (e.g. knockout-round TBDs that share a parent_id).
    fixture_keys: set[tuple[str, str]] = {
        (_slug(fx["home_team"]), _slug(fx["away_team"])) for fx in fixtures
    }

    # Fetch each source once. eloratings and pinnacle return data for all
    # WC fixtures in 1-2 requests; smarkets needs ~4 requests per event so
    # it's the slowest, but still bounded by total event count.
    console.print("[bold]Scraping sources (≥2s between requests)...[/bold]")
    sources_data: dict[str, dict[tuple[str, str], tuple[float, float, float]]] = {}
    for name, fn in [
        ("eloratings", lambda f: scrape_eloratings(f)),
        ("pinnacle",   lambda f: scrape_pinnacle(f)),
        ("smarkets",   lambda f: scrape_smarkets(f, fixture_match_keys=fixture_keys)),
    ]:
        try:
            data = fn(fetcher)
        except Exception as e:
            console.print(f"  [yellow]{name} crashed: {type(e).__name__}: {e}[/yellow]")
            data = {}
        sources_data[name] = data
        console.print(f"  {name:<12} {len(data)} fixture(s) extracted")

    per_source_counts = {k: len(v) for k, v in sources_data.items()}

    written = 0
    skipped = 0
    for fx in fixtures:
        home = fx["home_team"]
        away = fx["away_team"]
        match_id = str(fx["id"])
        ko = fx.get("kickoff")
        ko_str = ko if isinstance(ko, str) else (ko.isoformat() if ko else "?")
        console.print(f"\n[{home}] vs [{away}] — {ko_str}")

        per_source: dict[str, tuple[float, float, float]] = {}
        for name, src_map in sources_data.items():
            triple = _match_fixture(home, away, src_map)
            if triple is None:
                continue
            # Double-check vig removal at the per-source layer (defence-in-
            # depth — the scrapers already vig-remove, but if a future scraper
            # forgets we catch it here before aggregation).
            re_triple = vig_remove(*triple)
            if re_triple is None:
                continue
            per_source[name] = re_triple
            console.print(f"  {name:<12} H={re_triple[0]:.3f} D={re_triple[1]:.3f} A={re_triple[2]:.3f}")

        if len(per_source) < MIN_SOURCES:
            console.print(f"  [yellow]Only {len(per_source)} source(s) found — skipping (need ≥{MIN_SOURCES}).[/yellow]")
            skipped += 1
            continue

        agg = aggregate(per_source)
        if agg is None:
            console.print(f"  [yellow]Aggregation failed — skipping.[/yellow]")
            skipped += 1
            continue
        h, d, a = agg
        console.print(f"  [bold green]CONSENSUS H={h:.3f} D={d:.3f} A={a:.3f} (sum={h+d+a:.4f}, n={len(per_source)})[/bold green]")

        if dry_run:
            continue
        if upsert_consensus(match_id, h, d, a, per_source):
            written += 1
        else:
            skipped += 1

    console.print(
        f"\n[bold]Done:[/bold] {written} written | {skipped} skipped "
        f"| {len(fixtures)} fixtures seen"
    )
    if dry_run:
        console.print("[yellow](dry-run — nothing written to DB)[/yellow]")

    return {
        "fixtures_seen": len(fixtures),
        "written": written,
        "skipped": skipped,
        "per_source_counts": per_source_counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape WC2026 market consensus from free public sources")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print outputs without writing to DB")
    parser.add_argument("--max-fixtures", type=int, default=None,
                        help="Cap fixtures processed (useful for smoke runs)")
    args = parser.parse_args()
    run_wc_market_consensus(max_fixtures=args.max_fixtures, dry_run=args.dry_run)
