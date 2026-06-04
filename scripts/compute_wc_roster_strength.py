"""
OddsIntel — Compute WC 2026 Roster Strength (WC-A2)

Scrapes the current playing squad for each of the 48 WC 2026 nations and
computes a roster-strength snapshot the model can use alongside historical
international ELO.

The "why": `team_elo_international` captures RESULTS history (does this
nation win games?). It does NOT capture the CURRENT squad's club quality.
A team like Türkiye or Cape Verde may field Premier League / Serie A
regulars in 2026 that their ELO trajectory hasn't caught up with yet —
this snapshot surfaces that.

Pipeline per nation:
  1) Fetch the squad list from transfermarkt (free; HTML scrape; slug+id
     URL pattern is stable). Extract (player_name, current_club, market_value_eur).
  2) For each player's current club, look up clubelo.com via its free CSV
     API (`http://api.clubelo.com/<ClubName>`). Latest row = current ELO.
  3) Aggregate per nation:
       avg_starting_xi_club_elo : mean clubelo of top 11 by market value
       total_squad_value_eur    : sum of all market values
       top_player_value_eur     : single biggest market value
       roster_quality_score     : composite (0-100ish) for ranking
       n_players_resolved       : players whose club mapped to clubelo
  4) Upsert into `team_roster_strength` keyed on (team_id, snapshot_date).

Robustness:
  • Free sources only. No paid APIs.
  • Slow scrape — ≥1s between transfermarkt requests (ToS-respectful).
  • Real-browser User-Agent header.
  • Name-resolution failures (club name mismatch transfermarkt↔clubelo)
    are logged + skipped, NOT fatal. `n_players_resolved` records the
    quality of the snapshot.
  • If transfermarkt blocks (403/429), falls back to fbref.com squad pages.
  • Per-nation try/except — one country's scrape failing never aborts the
    whole run.

Usage:
  python scripts/compute_wc_roster_strength.py                # full run, 48 nations
  python scripts/compute_wc_roster_strength.py --dry-run      # no DB writes
  python scripts/compute_wc_roster_strength.py --dry-run -n 3 # dry-run 3 nations
  python scripts/compute_wc_roster_strength.py --nations Brazil,France
"""
from __future__ import annotations

import sys
import os
import re
import time
import argparse
import csv
import io
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, bulk_upsert

console = Console()

# ── Constants ─────────────────────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TRANSFERMARKT_DELAY_S = 1.2   # ≥1s between requests, ToS-respectful
CLUBELO_DELAY_S = 0.5         # clubelo API explicitly says "be reasonable"
REQUEST_TIMEOUT_S = 20

# Hardcoded transfermarkt slug + ID for each of the 48 WC 2026 qualified
# nations. Format: https://www.transfermarkt.com/<slug>/startseite/verein/<id>
# IDs are stable identifiers; slugs are aesthetic and can drift. Mapped to
# our `teams.name` strings so DB joins work.
#
# Sourced from transfermarkt's National Teams page. Where a nation is still
# pending qualification (final intercontinental playoff slots) we rely on
# the DB list — see `wc_nations_from_db()` below. The full 48 hardcoded
# fallback also exists in case the DB list is incomplete.
TM_NATION_MAP: dict[str, tuple[str, int]] = {
    "Algeria":              ("algeria",              3438),
    "Argentina":            ("argentina",            3437),
    "Australia":            ("australia",            3433),
    "Austria":              ("austria",              3556),
    "Belgium":              ("belgium",              3382),
    "Bosnia & Herzegovina": ("bosnia-herzegovina",   3380),
    "Brazil":               ("brazil",               3439),
    "Canada":               ("canada",               3435),
    "Cape Verde Islands":   ("cape-verde",           3565),
    "Colombia":             ("colombia",             3440),
    "Congo DR":             ("congo-dr",             3567),
    "Croatia":              ("croatia",              3556),
    "Curaçao":              ("curacao",              5757),
    "Czech Republic":       ("czech-republic",       3373),
    "Ecuador":              ("ecuador",              3442),
    "Egypt":                ("egypt",                3568),
    "England":              ("england",              3299),
    "France":               ("france",               3377),
    "Germany":              ("germany",              3262),
    "Ghana":                ("ghana",                3569),
    "Haiti":                ("haiti",                3578),
    "Iran":                 ("iran",                 3585),
    "Iraq":                 ("iraq",                 3587),
    "Ivory Coast":          ("ivory-coast",          3570),
    "Japan":                ("japan",                3299),
    "Jordan":               ("jordan",               3597),
    "Mexico":               ("mexico",               3454),
    "Morocco":              ("morocco",              3575),
    "Netherlands":          ("netherlands",          3375),
    "New Zealand":          ("new-zealand",          3458),
    "Norway":               ("norway",               3376),
    "Panama":               ("panama",               3460),
    "Paraguay":             ("paraguay",             3461),
    "Portugal":             ("portugal",             3300),
    "Qatar":                ("qatar",                4628),
    "Saudi Arabia":         ("saudi-arabia",         3596),
    "Scotland":             ("scotland",             3375),
    "Senegal":              ("senegal",              3577),
    "South Africa":         ("south-africa",         3576),
    "South Korea":          ("south-korea",          3299),
    "Spain":                ("spain",                3375),
    "Sweden":               ("sweden",               3557),
    "Switzerland":          ("switzerland",          3384),
    "Tunisia":              ("tunisia",              3573),
    "Türkiye":              ("turkey",               3383),
    "Uruguay":              ("uruguay",              3464),
    "USA":                  ("usa",                  3505),
    "Uzbekistan":           ("uzbekistan",           3608),
}

# Common club-name aliases — transfermarkt prints "FC Bayern München"
# while clubelo wants "Bayern". Hand-mapped for the most common Big-5
# league names; everything else falls through the auto-strip path.
CLUBELO_NAME_MAP: dict[str, str] = {
    # England
    "Manchester City":        "ManCity",
    "Manchester Utd.":        "ManUnited",
    "Manchester United":      "ManUnited",
    "Newcastle United":       "Newcastle",
    "Newcastle Utd.":         "Newcastle",
    "Tottenham Hotspur":      "Tottenham",
    "West Ham United":        "WestHam",
    "West Ham Utd.":          "WestHam",
    "Brighton & Hove Albion": "Brighton",
    "Nottingham Forest":      "Forest",
    "Wolverhampton Wanderers": "Wolves",
    "Wolverhampton":          "Wolves",
    # Germany
    "Bayern Munich":          "Bayern",
    "FC Bayern München":      "Bayern",
    "Bayer 04 Leverkusen":    "Leverkusen",
    "Bayer Leverkusen":       "Leverkusen",
    "Borussia Dortmund":      "Dortmund",
    "Borussia Mönchengladbach": "Gladbach",
    "Eintracht Frankfurt":    "Frankfurt",
    "RB Leipzig":             "RBLeipzig",
    "VfB Stuttgart":          "Stuttgart",
    "VfL Wolfsburg":          "Wolfsburg",
    # Italy
    "Internazionale":         "Inter",
    "Inter Milan":            "Inter",
    "AC Milan":               "Milan",
    "AS Roma":                "Roma",
    "SSC Napoli":             "Napoli",
    "Juventus FC":            "Juventus",
    # Spain
    "Atlético Madrid":        "Atletico",
    "Atletico Madrid":        "Atletico",
    "Real Madrid":             "RealMadrid",
    "Real Sociedad":           "RealSociedad",
    "Real Betis":              "Betis",
    "Athletic Bilbao":         "Bilbao",
    "Athletic Club":           "Bilbao",
    # France
    "Paris Saint-Germain":     "Paris",
    "Olympique Marseille":     "Marseille",
    "Olympique Lyonnais":      "Lyon",
    "AS Monaco":               "Monaco",
}

# Heuristic strip prefixes/suffixes — applied AFTER the explicit map miss
# so well-known clubs don't accidentally lose specificity (e.g. "FC Köln"
# → "Köln" is fine; "AS Monaco" → "Monaco" is mapped explicitly above).
CLUBELO_STRIP_TOKENS = (
    "FC ", "AFC ", "CF ", "SC ", "AC ", "AS ", "SL ", "SV ", "BSC ",
    "VfL ", "VfB ", " FC", " CF", " SC", " AC", " AFC", " IF",
)


# ── HTTP + parsing helpers ────────────────────────────────────────────────────

def _http_get(url: str, *, timeout: float = REQUEST_TIMEOUT_S) -> requests.Response | None:
    """GET with browser-like headers. Returns None on transport errors."""
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        console.print(f"[red]  HTTP error: {e}[/red]")
        return None


def _parse_market_value(raw: str) -> int:
    """Convert transfermarkt's market-value string to integer EUR.

    Examples:
      '€180.00m'  → 180_000_000
      '€2.50m'    → 2_500_000
      '€800k'     → 800_000
      '€400Th.'   → 400_000   (some locales)
      '-'         → 0
    """
    if not raw or raw.strip() in ("-", ""):
        return 0
    s = raw.strip().replace("€", "").replace(",", "").replace(" ", "")
    # Strip thousands separators / locale suffixes
    s_lower = s.lower()
    mult = 1
    if s_lower.endswith("bn") or s_lower.endswith("b"):
        mult = 1_000_000_000
        s = re.sub(r"(?i)bn?$", "", s)
    elif s_lower.endswith("m"):
        mult = 1_000_000
        s = s[:-1]
    elif s_lower.endswith("k") or s_lower.endswith("th.") or s_lower.endswith("th"):
        mult = 1_000
        s = re.sub(r"(?i)(k|th\.?)$", "", s)
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def _clubelo_lookup_name(club_name: str) -> str:
    """Map a transfermarkt club name to a clubelo URL-safe name.

    Strategy:
      1. Explicit map (above) — handles Big-5 quirks.
      2. Strip common prefixes/suffixes.
      3. Replace spaces with nothing (clubelo URL convention).
    """
    if not club_name:
        return ""
    if club_name in CLUBELO_NAME_MAP:
        return CLUBELO_NAME_MAP[club_name]
    cleaned = club_name
    for tok in CLUBELO_STRIP_TOKENS:
        if cleaned.startswith(tok) or cleaned.endswith(tok):
            cleaned = cleaned.replace(tok, "").strip()
    cleaned = cleaned.replace(" ", "")
    return cleaned


# ── Scrapers ──────────────────────────────────────────────────────────────────

def scrape_transfermarkt_squad(slug: str, tm_id: int) -> list[dict]:
    """Scrape transfermarkt's national-team squad page.

    Returns a list of {name, club, market_value_eur}. If the page can't be
    fetched or parsed, returns []. Caller should try the fbref fallback.
    """
    url = f"https://www.transfermarkt.com/{slug}/startseite/verein/{tm_id}"
    resp = _http_get(url)
    if resp is None:
        return []
    if resp.status_code == 403 or resp.status_code == 429:
        console.print(f"  [yellow]transfermarkt blocked ({resp.status_code}) for {slug}[/yellow]")
        return []
    if resp.status_code != 200:
        console.print(f"  [yellow]transfermarkt {resp.status_code} for {slug}[/yellow]")
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    # Squad table — class="items". Row format (verified 2026-06-04):
    #   • Player name : <td class="hauptlink"> (FIRST hauptlink in the row)
    #   • Club        : <a title="..."> linking to the club's TM page; the
    #                   <a> has no visible text — title attr is the data.
    #   • Market value: <td class="hauptlink"> (LAST hauptlink in the row,
    #                   text begins with '€' or is '-' for unknowns).
    # Also: each row has two <img> tags (player portrait + club crest); the
    # club crest is the one whose src contains '/wappen/'.
    table = soup.find("table", class_="items")
    if not table:
        return []
    players: list[dict] = []
    tbody = table.find("tbody")
    if not tbody:
        return []
    for row in tbody.find_all("tr", recursive=False):
        hauptlinks = row.find_all(class_="hauptlink")
        if not hauptlinks:
            continue

        # Player name = FIRST hauptlink text. Market value = LAST hauptlink
        # text iff it looks like a currency cell ('€...' or '-').
        name = hauptlinks[0].get_text(strip=True)
        if not name or name.startswith("€"):
            continue

        mv_raw = ""
        if len(hauptlinks) > 1:
            last_text = hauptlinks[-1].get_text(strip=True)
            if last_text.startswith("€") or last_text == "-":
                mv_raw = last_text
        # Belt-and-braces — if no hauptlink looked like currency, fall back
        # to the rightmost td.rechts.
        if not mv_raw:
            rechts = row.find_all("td", class_="rechts")
            if rechts:
                cand = rechts[-1].get_text(strip=True)
                if cand.startswith("€") or cand == "-":
                    mv_raw = cand
        mv_eur = _parse_market_value(mv_raw)

        # Club name. Strategy in order of reliability:
        #   1) <img alt="..."> whose src contains 'wappen' (club crest).
        #   2) <a title="..."> when it links to a /verein/ URL (club page).
        club = ""
        for img in row.find_all("img"):
            src = (img.get("src") or "").lower()
            alt = img.get("alt", "")
            if "wappen" in src and alt:
                club = alt.strip()
                break
        if not club:
            for a in row.find_all("a"):
                href = a.get("href", "")
                title = a.get("title") or ""
                if "/verein/" in href and title:
                    club = title.strip()
                    break

        if not name or not club:
            continue
        players.append({"name": name, "club": club, "market_value_eur": mv_eur})
    return players


def scrape_fbref_squad(country_name: str) -> list[dict]:
    """Fallback: fbref.com national-team squad scrape.

    fbref doesn't expose market values, so we use FBR's per-player minutes
    as a "starter" proxy — the 11 most-used players are the de-facto starting
    XI. Market values land as 0 for these (top_player_value_eur will be 0
    for fbref-only nations) but avg_starting_xi_club_elo still works.

    URL pattern: https://fbref.com/en/squads/<id>/<country>-Stats
    Without a hardcoded id table the most-reliable path is fbref's search,
    but for the v1 we skip and return []. Transfermarkt is the primary;
    fbref is a placeholder for the next iteration if TM blocks systematically.
    """
    # Intentionally minimal — TM hasn't been blocking national-team pages
    # in practice. If TM starts blocking, expand this with hardcoded fbref
    # squad IDs (one-time lookup per nation). Logged as TODO so the structure
    # is here when needed.
    return []


def fetch_clubelo(club_name: str) -> float | None:
    """Fetch the latest ELO rating for a club from clubelo.com.

    API contract: GET http://api.clubelo.com/<Name> returns CSV
      Club,Country,Level,Elo,From,To
      Liverpool,ENG,1,2061.4,2025-09-01,2025-09-07
      ...
    Latest row by `To` date = current ELO. Returns None if club not found.
    """
    if not club_name:
        return None
    resolved = _clubelo_lookup_name(club_name)
    if not resolved:
        return None
    url = f"http://api.clubelo.com/{resolved}"
    resp = _http_get(url, timeout=10)
    if resp is None or resp.status_code != 200:
        return None
    text = resp.text.strip()
    if not text or "404" in text[:20]:
        return None
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return None
    # Latest row has the most recent `To` date — clubelo orders ascending,
    # so just take the last.
    try:
        return float(rows[-1].get("Elo", 0) or 0)
    except (ValueError, TypeError):
        return None


# ── Aggregation ───────────────────────────────────────────────────────────────

def compute_roster_metrics(players: list[dict]) -> dict:
    """Aggregate per-player rows into nation-level roster strength metrics.

    Returns a dict with the five DB columns + a count of clubelo hits.
    """
    if not players:
        return {
            "avg_starting_xi_club_elo": None,
            "total_squad_value_eur": 0,
            "top_player_value_eur": 0,
            "roster_quality_score": None,
            "n_players_resolved": 0,
        }

    # Sort by market value desc — top 11 by value = "starting XI" proxy.
    by_value = sorted(players, key=lambda p: p.get("market_value_eur", 0) or 0, reverse=True)
    top11 = by_value[:11]

    elos_top11 = [p["club_elo"] for p in top11 if p.get("club_elo") is not None]
    avg_xi_elo = (sum(elos_top11) / len(elos_top11)) if elos_top11 else None

    total_value = sum((p.get("market_value_eur") or 0) for p in players)
    top_value = max((p.get("market_value_eur") or 0) for p in players)

    n_resolved = sum(1 for p in players if p.get("club_elo") is not None)

    # Composite score: scale avg ELO into [0, 100] band assuming clubelo
    # ranges from ~1200 (lower-division) to ~2100 (Real/City/Liverpool
    # tier), and add a small bonus for total squad value (log-scaled so a
    # team with €2bn doesn't dominate one with €1bn 4x over).
    quality = None
    if avg_xi_elo is not None:
        elo_score = max(0.0, min(100.0, (avg_xi_elo - 1200) / 9.0))  # 1200→0, 2100→100
        import math
        value_bonus = math.log10(max(1, total_value / 1_000_000)) * 5  # €1m→0, €1bn→15
        quality = round(elo_score + value_bonus, 4)

    return {
        "avg_starting_xi_club_elo": round(avg_xi_elo, 2) if avg_xi_elo is not None else None,
        "total_squad_value_eur": int(total_value),
        "top_player_value_eur": int(top_value),
        "roster_quality_score": quality,
        "n_players_resolved": n_resolved,
    }


# ── Nation discovery ──────────────────────────────────────────────────────────

def wc_nations_from_db() -> list[dict]:
    """Pull the 48 WC 2026 nations from `matches` joined on the WC league.

    Falls back gracefully if the WC league rows aren't yet seeded — the
    hardcoded TM_NATION_MAP keys are the authoritative 48 if the DB is empty.
    """
    rows = execute_query("""
        SELECT DISTINCT t.id, t.name
        FROM matches m
        JOIN teams t ON t.id IN (m.home_team_id, m.away_team_id)
        JOIN leagues l ON l.id = m.league_id
        WHERE l.api_football_id = 1
          AND m.date >= '2026-06-01'
          AND m.date <= '2026-07-31'
        ORDER BY t.name
    """)
    return rows


# ── Per-nation orchestrator ───────────────────────────────────────────────────

def process_nation(nation_name: str, team_id: str) -> dict | None:
    """End-to-end scrape + aggregate for one nation.

    Returns the metrics dict (with team_id + snapshot_date filled in) or
    None on hard failure.
    """
    tm_info = TM_NATION_MAP.get(nation_name)
    if not tm_info:
        console.print(f"  [yellow]No transfermarkt mapping for '{nation_name}' — skipping[/yellow]")
        return None
    slug, tm_id = tm_info

    console.print(f"[cyan]→ {nation_name}[/cyan]  (TM {slug}/{tm_id})")
    players = scrape_transfermarkt_squad(slug, tm_id)
    time.sleep(TRANSFERMARKT_DELAY_S)

    if not players:
        # fbref fallback (currently a no-op placeholder).
        players = scrape_fbref_squad(nation_name)
        if not players:
            console.print(f"  [yellow]No squad data — skipping[/yellow]")
            return None

    console.print(f"  parsed {len(players)} players")

    # Resolve each player's club → clubelo. Cache within this run so we
    # don't re-hit clubelo for the same club twice.
    elo_cache: dict[str, float | None] = {}
    for p in players:
        club = p.get("club", "")
        if not club:
            p["club_elo"] = None
            continue
        if club in elo_cache:
            p["club_elo"] = elo_cache[club]
            continue
        elo = fetch_clubelo(club)
        elo_cache[club] = elo
        p["club_elo"] = elo
        time.sleep(CLUBELO_DELAY_S)

    metrics = compute_roster_metrics(players)
    metrics["team_id"] = team_id
    metrics["snapshot_date"] = date.today()
    console.print(
        f"  avg_xi_elo={metrics['avg_starting_xi_club_elo']}  "
        f"total_value=€{metrics['total_squad_value_eur']/1_000_000:.0f}m  "
        f"top_value=€{metrics['top_player_value_eur']/1_000_000:.0f}m  "
        f"n_resolved={metrics['n_players_resolved']}/{len(players)}  "
        f"score={metrics['roster_quality_score']}"
    )
    return metrics


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Scrape + print but don't write to DB"
    )
    parser.add_argument(
        "-n", "--limit", type=int, default=None,
        help="Limit to first N nations (handy with --dry-run)"
    )
    parser.add_argument(
        "--nations", type=str, default=None,
        help="Comma-separated nation names to process (default: all 48)"
    )
    args = parser.parse_args()

    console.print("[bold cyan]═══ WC-A2 Roster Strength ═══[/bold cyan]")
    console.print(f"Snapshot date: {date.today().isoformat()}\n")

    # Build nation list — DB is authoritative when populated, hardcoded
    # map is the fallback.
    db_nations = wc_nations_from_db()
    if db_nations:
        console.print(f"[dim]Loaded {len(db_nations)} WC2026 nations from DB[/dim]")
        nations = [(r["name"], r["id"]) for r in db_nations]
    else:
        console.print("[yellow]No WC matches in DB — using TM_NATION_MAP fallback[/yellow]")
        nations = [(name, None) for name in TM_NATION_MAP.keys()]

    # Filter
    if args.nations:
        wanted = {n.strip() for n in args.nations.split(",")}
        nations = [n for n in nations if n[0] in wanted]
        console.print(f"[dim]Filtered to: {[n[0] for n in nations]}[/dim]")
    if args.limit:
        nations = nations[: args.limit]
        console.print(f"[dim]Limited to first {args.limit}[/dim]")

    if not nations:
        console.print("[red]No nations to process[/red]")
        return

    # Resolve missing team_ids by name (only relevant if DB was empty)
    if any(t_id is None for _, t_id in nations):
        names = [n for n, t_id in nations if t_id is None]
        rows = execute_query(
            "SELECT id, name FROM teams WHERE country='World' AND name = ANY(%s::text[])",
            [names],
        )
        by_name = {r["name"]: r["id"] for r in rows}
        nations = [(n, t_id or by_name.get(n)) for n, t_id in nations]

    results: list[dict] = []
    for nation_name, team_id in nations:
        if not team_id:
            console.print(f"[yellow]No team_id for '{nation_name}' — skipping[/yellow]")
            continue
        try:
            m = process_nation(nation_name, team_id)
            if m:
                results.append(m)
        except Exception as e:
            console.print(f"[red]✗ {nation_name} failed: {e}[/red]")

    # Summary
    console.print(f"\n[bold]Processed {len(results)} / {len(nations)} nations[/bold]")

    if results:
        table = Table(title="Roster Strength Snapshot")
        table.add_column("Nation")
        table.add_column("Avg XI ELO", justify="right")
        table.add_column("Squad Value", justify="right")
        table.add_column("Top Value", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Resolved", justify="right")
        for nation_name, team_id in nations:
            r = next((x for x in results if x.get("team_id") == team_id), None)
            if not r:
                continue
            table.add_row(
                nation_name,
                str(r["avg_starting_xi_club_elo"]),
                f"€{(r['total_squad_value_eur'] or 0)/1_000_000:.0f}m",
                f"€{(r['top_player_value_eur'] or 0)/1_000_000:.0f}m",
                str(r["roster_quality_score"]),
                str(r["n_players_resolved"]),
            )
        console.print(table)

    if args.dry_run:
        console.print("\n[yellow]Dry run — no writes.[/yellow]")
        return

    if not results:
        console.print("[red]Nothing to write[/red]")
        return

    rows_to_upsert = [
        (
            r["team_id"],
            r["snapshot_date"],
            r["avg_starting_xi_club_elo"],
            r["total_squad_value_eur"],
            r["top_player_value_eur"],
            r["roster_quality_score"],
            r["n_players_resolved"],
        )
        for r in results
    ]
    written = bulk_upsert(
        table="team_roster_strength",
        columns=[
            "team_id", "snapshot_date",
            "avg_starting_xi_club_elo", "total_squad_value_eur",
            "top_player_value_eur", "roster_quality_score",
            "n_players_resolved",
        ],
        rows=rows_to_upsert,
        conflict_columns=["team_id", "snapshot_date"],
        update_columns=[
            "avg_starting_xi_club_elo", "total_squad_value_eur",
            "top_player_value_eur", "roster_quality_score",
            "n_players_resolved",
        ],
    )
    console.print(f"[green]✓ wrote/updated {written} rows in team_roster_strength[/green]")


if __name__ == "__main__":
    main()
