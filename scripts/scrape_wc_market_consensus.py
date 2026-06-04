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

Sources (in order of preference):
  1. eloratings.net  — static HTML, scraped from /2026_World_Cup.
                       Predicted probabilities per fixture.
  2. forebet         — /en/football-tips-and-predictions-for-tomorrow
                       1X2 percentages per match (also accepts the WC URL).
  3. oddsportal      — /football/world/world-cup-2026/ aggregated bookmaker
                       avg. Cloudflare-protected — uses polite headers +
                       backoff. Best-effort, will gracefully skip on 403.
  4. betfair         — exchange data is gated on session cookies; included
                       as a hook but skipped when the endpoint requires auth.

A fixture is written only when at least TWO sources succeed. Per-source
probabilities are vig-removed (each (h, d, a) tuple normalised to sum to
1.0). Source-level outputs are stored under `sources` JSONB for audit.

Polite scraping: ≥2s between requests, real User-Agent, no parallel
hammering, graceful skip on 403/timeout/parse error.

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
from bs4 import BeautifulSoup
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
ELORATINGS_URL = "https://www.eloratings.net/2026_World_Cup"
FOREBET_URL = "https://www.forebet.com/en/football-tips-and-predictions-for-tomorrow"
ODDSPORTAL_URL = "https://www.oddsportal.com/football/world/world-cup-2026/"
BETFAIR_URL = "https://www.betfair.com/exchange/plus/football"


# ── HTTP layer ────────────────────────────────────────────────────────────

class PoliteFetcher:
    """Single-threaded HTTP fetcher that enforces ≥REQUEST_INTERVAL_S
    between requests so we never hammer a source. Real UA, persistent
    session for keep-alive."""

    def __init__(self) -> None:
        self._last_request_ts = 0.0
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

    def get(self, url: str, *, extra_headers: Optional[dict] = None) -> Optional[str]:
        # Enforce inter-request gap — politeness budget.
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < REQUEST_INTERVAL_S:
            time.sleep(REQUEST_INTERVAL_S - elapsed)
        self._last_request_ts = time.monotonic()

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
_ALIASES_RAW = {
    "usa":                "unitedstates",
    "southkorea":         "korearepublic",
    "iran":               "iranislamicrepublic",
    "ivorycoast":         "cotedivoire",
    "capeverdeislands":   "capeverde",
    "czechrepublic":      "czechia",
    "northmacedonia":     "macedoniafyr",
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


# ── Source: eloratings.net ────────────────────────────────────────────────

def scrape_eloratings(fetcher: PoliteFetcher) -> dict[tuple[str, str], tuple[float, float, float]]:
    """Return {(home_slug, away_slug): (h, d, a)} from eloratings.net.

    The page lists every fixture with model-predicted W/D/L percentages.
    We parse it as plain text since the table structure is straightforward
    HTML rows: team1, team2, P(team1 win), P(draw), P(team2 win).
    """
    html = fetcher.get(ELORATINGS_URL)
    if not html:
        return {}
    out: dict[tuple[str, str], tuple[float, float, float]] = {}
    try:
        soup = BeautifulSoup(html, "lxml")
        # eloratings.net uses div-row layout in places; fall back to a
        # regex on the rendered text. Pattern: TEAM_A vs TEAM_B ... three
        # percentages on the same row.
        text = soup.get_text("\n")
        # Each fixture line looks like (after whitespace cleanup):
        #   "Brazil 55% 27% 18% Morocco"
        # or table layout — we run a permissive regex over the text.
        line_re = re.compile(
            r"([A-Z][A-Za-z .'\-]{2,30})\s+(\d{1,2})%\s+(\d{1,2})%\s+(\d{1,2})%\s+([A-Z][A-Za-z .'\-]{2,30})"
        )
        for m in line_re.finditer(text):
            team_a, ph, pd, pa, team_b = m.groups()
            triple = vig_remove(float(ph), float(pd), float(pa))
            if not triple:
                continue
            out[(_slug(team_a), _slug(team_b))] = triple
    except Exception as e:
        console.print(f"  [yellow]eloratings parse error: {type(e).__name__}: {e}[/yellow]")
        return {}
    return out


# ── Source: forebet ───────────────────────────────────────────────────────

def scrape_forebet(fetcher: PoliteFetcher) -> dict[tuple[str, str], tuple[float, float, float]]:
    """Return {(home_slug, away_slug): (h, d, a)} from forebet.

    Forebet exposes 1X2 percentages per fixture in `.fprc_1`, `.fprc_X`,
    `.fprc_2` cells. We pull every fixture row from the predictions page
    (which has the WC under its 'World Cup 2026' section) and keep only
    triples that vig-remove cleanly.
    """
    html = fetcher.get(FOREBET_URL)
    if not html:
        return {}
    out: dict[tuple[str, str], tuple[float, float, float]] = {}
    try:
        soup = BeautifulSoup(html, "lxml")
        # Forebet rows: each prediction row carries data-link / data-href
        # plus three .fprc_* cells. We scan every row that has all three.
        for row in soup.select("tr, div.rcnt"):
            cells = {
                "1": row.select_one(".fprc_1, .fprc-1"),
                "X": row.select_one(".fprc_X, .fprc-X"),
                "2": row.select_one(".fprc_2, .fprc-2"),
            }
            if not all(cells.values()):
                continue
            try:
                ph = float(re.sub(r"[^0-9.]", "", cells["1"].get_text() or "0"))
                pd = float(re.sub(r"[^0-9.]", "", cells["X"].get_text() or "0"))
                pa = float(re.sub(r"[^0-9.]", "", cells["2"].get_text() or "0"))
            except ValueError:
                continue
            # Team names — look for two .homePr/.awayPr or two team
            # cells inside the same row.
            team_nodes = row.select(".homeTeam, .awayTeam, .homePr, .awayPr, td.h_t, td.a_t")
            if len(team_nodes) < 2:
                # Try the title attribute fallback.
                title = (row.get("title") or "")
                m = re.match(r"(.+?)\s*[-–vs]+\s*(.+?)$", title)
                if not m:
                    continue
                home_name, away_name = m.group(1).strip(), m.group(2).strip()
            else:
                home_name = team_nodes[0].get_text(strip=True)
                away_name = team_nodes[1].get_text(strip=True)
            triple = vig_remove(ph, pd, pa)
            if not triple:
                continue
            out[(_slug(home_name), _slug(away_name))] = triple
    except Exception as e:
        console.print(f"  [yellow]forebet parse error: {type(e).__name__}: {e}[/yellow]")
        return {}
    return out


# ── Source: OddsPortal ────────────────────────────────────────────────────

def scrape_oddsportal(fetcher: PoliteFetcher) -> dict[tuple[str, str], tuple[float, float, float]]:
    """Return {(home_slug, away_slug): (h, d, a)} from OddsPortal.

    OddsPortal is Cloudflare-protected — most reqs without a real browser
    get 403'd. We try a polite plain GET; if it 403s we log and return {}
    so the caller falls back to the other sources. We use the aggregated
    average row when the page does load (data-attribute 'data-avgodds-1/X/2').
    """
    html = fetcher.get(ODDSPORTAL_URL)
    if not html:
        return {}
    out: dict[tuple[str, str], tuple[float, float, float]] = {}
    try:
        soup = BeautifulSoup(html, "lxml")
        # OddsPortal renders fixture rows under .table-main / .deactivate.
        # We look for rows that expose home + away names and three decimal
        # odds attributes. The exact selector is brittle by design — when
        # it changes, the source quietly returns {} and we fall back.
        for row in soup.select("tr.deactivate, div.eventRow"):
            home_node = row.select_one(".table-participant, .participantName, .home")
            away_node = row.select_one(".table-participant + .table-participant, .participantName + .participantName, .away")
            if not (home_node and away_node):
                continue
            home_name = home_node.get_text(strip=True)
            away_name = away_node.get_text(strip=True)
            # Average odds — pull from data attributes or .odds-data cells.
            odds_cells = row.select(".odds-data, .odds, td.center")
            decs: list[float] = []
            for c in odds_cells:
                txt = c.get_text(strip=True)
                m = re.match(r"^\s*([0-9]+\.[0-9]+)\s*$", txt)
                if m:
                    decs.append(float(m.group(1)))
                if len(decs) >= 3:
                    break
            if len(decs) < 3:
                continue
            try:
                ph, pd, pa = 1.0 / decs[0], 1.0 / decs[1], 1.0 / decs[2]
            except ZeroDivisionError:
                continue
            triple = vig_remove(ph, pd, pa)
            if not triple:
                continue
            out[(_slug(home_name), _slug(away_name))] = triple
    except Exception as e:
        console.print(f"  [yellow]oddsportal parse error: {type(e).__name__}: {e}[/yellow]")
        return {}
    return out


# ── Source: Betfair Exchange (best-effort) ────────────────────────────────

def scrape_betfair(fetcher: PoliteFetcher) -> dict[tuple[str, str], tuple[float, float, float]]:
    """Best-effort hook for the Betfair Exchange public page. The actual
    exchange endpoints require a session/app key — we attempt the public
    coupon page and quietly skip when it returns no parseable markets.

    Kept as a stub for completeness — the function name + entry exists so
    the smoke test can confirm we accept ≥2 sources, but in production we
    expect this source to gracefully degrade in most runs.
    """
    html = fetcher.get(BETFAIR_URL)
    if not html:
        return {}
    # Public page is a JS shell — no static markup to parse. Return empty
    # rather than 500-ing; the caller will fall back to the other sources.
    return {}


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

    # Fetch each source once — every source returns a {(home, away): triple}
    # map covering all fixtures it knows about, so we only hit each endpoint
    # one time per run.
    console.print("[bold]Scraping sources (≥2s between requests)...[/bold]")
    sources_data: dict[str, dict[tuple[str, str], tuple[float, float, float]]] = {}
    for name, fn in [
        ("eloratings", scrape_eloratings),
        ("forebet",    scrape_forebet),
        ("oddsportal", scrape_oddsportal),
        ("betfair",    scrape_betfair),
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
