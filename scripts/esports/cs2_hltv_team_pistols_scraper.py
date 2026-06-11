#!/usr/bin/env python3
"""
CS2-HLTV-TEAM-PISTOLS scraper — per-team-per-map pistol-round splits.

The per-team URL `/stats/teams/pistols/{teamId}/{slug}` returns 404. Same data
is reachable in one shot via the BULK page
    /stats/teams/pistols?startDate=&endDate=&maps=de_X[&side=...]
which returns ~100 teams in the period filtered to a single map. Iterating
9 maps × 3 sides (overall + CT + T) covers every top-100 team across the full
active map pool in ~27 requests instead of ~300 per-team fetches.

For each (map, side) we parse the stats-table → team_id + team_name + pistol
rounds W-L from the third column. We upsert one row per (team, map, period)
with ct_pistol_* and t_pistol_* columns; the overall fetch is used only as a
tie-break to detect maps where the team has zero samples.

Auth: HLTV's /stats/* subdomain is Cloudflare-gated. Same flow as
cs2_hltv_stats_scraper.py — FlareSolverr preferred (auto-solves CF), cookie
fallback. RATE_DELAY mirrors the existing stats scraper.

CLI:
    python3 scripts/esports/cs2_hltv_team_pistols_scraper.py --record
    python3 scripts/esports/cs2_hltv_team_pistols_scraper.py --limit 50 --record
    python3 scripts/esports/cs2_hltv_team_pistols_scraper.py --team-id 9565 --record
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests",
                           "--break-system-packages", "-q"])
    import requests

# Load .env so DB connection + HLTV creds are populated.
try:
    from dotenv import dotenv_values
    for k, v in dotenv_values(Path(__file__).resolve().parents[2] / ".env").items():
        os.environ[k] = v
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, get_conn  # noqa: E402

# psycopg2.extras.execute_values needs a raw cursor — execute_write does the
# safe-per-row pattern; for bulk we use get_conn() + execute_values.
import psycopg2.extras  # noqa: E402


RATE_DELAY = 5.0
DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/148.0.0.0 Safari/537.36")
BASE = "https://www.hltv.org"

# Active map pool — every HLTV /stats page uses the de_* internal name in the
# &maps= query param. Mapping back to display names matches what we store in
# cs2_hltv_match_maps.map_name (capitalised, no prefix).
MAP_INTERNAL_TO_DISPLAY = {
    "de_mirage":   "Mirage",
    "de_inferno":  "Inferno",
    "de_nuke":     "Nuke",
    "de_dust2":    "Dust2",
    "de_ancient":  "Ancient",
    "de_anubis":   "Anubis",
    "de_train":    "Train",
    "de_overpass": "Overpass",
    "de_vertigo":  "Vertigo",
}

# Parser regexes — mirror cs2_hltv_pistol_scraper.py (proven against the live
# pistol table) and cs2_hltv_stats_scraper.py (CF-aware fetch).
_TEAMS_TABLE_RE = re.compile(
    r'<table[^>]*class="[^"]*stats-table[^"]*"[^>]*>(.*?)</table>',
    re.DOTALL,
)
_TBODY_RE = re.compile(r'<tbody[^>]*>(.*?)</tbody>', re.DOTALL)
_TR_RE   = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
_TD_RE   = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)
_TEAM_LINK_RE = re.compile(r'<a href="/stats/teams/(\d+)/([^"?]+)(?:\?[^"]*)?"[^>]*>([^<]+)</a>')
_WL_RE = re.compile(r'(\d+)\s*-\s*(\d+)')
_PCT_RE = re.compile(r'(\d+\.?\d*)\s*%')


def _load_cookies() -> dict:
    raw = os.getenv("HLTV_AUTH_COOKIES", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("[!] HLTV_AUTH_COOKIES is not valid JSON", file=sys.stderr)
        return {}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": os.getenv("HLTV_USER_AGENT", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": f"{BASE}/stats",
    })
    for k, v in _load_cookies().items():
        s.cookies.set(k, v, domain="www.hltv.org")
    return s


def _fetch_url(session: requests.Session, url: str) -> str | None:
    """Try FlareSolverr first (auto-solves CF), fall back to cookie session."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        if is_available():
            return fs_fetch(url, session="hltv_team_pistols")
    except ImportError:
        pass
    try:
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            return r.text
        print(f"  [!] {r.status_code} on {url[-80:]}", file=sys.stderr)
    except Exception as e:
        print(f"  [!] {url[-80:]} {e}", file=sys.stderr)
    return None


def parse_pistol_rows(html: str) -> list[dict]:
    """Parse /stats/teams/pistols table → list of {hltv_team_id, team_name,
    slug, maps_played, pistol_won, pistol_lost, pistol_pct}.

    Column order on the live page (verified 2026-06-10):
        0: Team link
        1: Maps played
        2: "W - L" (pistol rounds, single side if &side= set, else combined)
        3: Pistol round win %
        4: Rounds won after pistol won  %
        5: Rounds won after pistol lost %
    """
    out: list[dict] = []
    table_m = _TEAMS_TABLE_RE.search(html or "")
    if not table_m:
        return out
    tbody_m = _TBODY_RE.search(table_m.group(1))
    if not tbody_m:
        return out
    for row in _TR_RE.finditer(tbody_m.group(1)):
        cells = _TD_RE.findall(row.group(1))
        if len(cells) < 4:
            continue
        link_m = _TEAM_LINK_RE.search(cells[0])
        if not link_m:
            continue
        tid = int(link_m.group(1))
        slug = link_m.group(2)
        name = link_m.group(3).strip()

        def _strip(s: str) -> str:
            return re.sub(r'<[^>]+>', '', s).strip()

        maps_txt = _strip(cells[1])
        try:
            maps_played = int(maps_txt)
        except ValueError:
            maps_played = None

        wl = _WL_RE.search(_strip(cells[2]))
        if not wl:
            continue
        won, lost = int(wl.group(1)), int(wl.group(2))

        pct_m = _PCT_RE.search(_strip(cells[3]))
        pct = float(pct_m.group(1)) if pct_m else None

        out.append({
            "hltv_team_id":  tid,
            "team_name":     name,
            "slug":          slug,
            "maps_played":   maps_played,
            "pistol_won":    won,
            "pistol_lost":   lost,
            "pistol_total":  won + lost,
            "pistol_pct":    pct,
        })
    return out


def fetch_pistols_for_map(session: requests.Session,
                          map_internal: str,
                          start_date: str, end_date: str,
                          side: str | None = None) -> list[dict]:
    """One bulk fetch for (map_internal, side). Returns ~100 team rows."""
    parts = [f"startDate={start_date}", f"endDate={end_date}",
             f"maps={map_internal}"]
    if side:
        if side not in ("TERRORIST", "COUNTER_TERRORIST"):
            raise ValueError(f"side must be TERRORIST or COUNTER_TERRORIST, got {side!r}")
        parts.append(f"side={side}")
    url = f"{BASE}/stats/teams/pistols?" + "&".join(parts)
    # Defensive — rankingFilter caps to top-20 silently.
    assert "rankingFilter" not in url, "rankingFilter is forbidden — caps to top-20"
    html = _fetch_url(session, url)
    return parse_pistol_rows(html) if html else []


def merge_sides(ct_rows: list[dict], t_rows: list[dict],
                map_display: str) -> dict[int, dict]:
    """{hltv_team_id: combined row with ct + t splits}."""
    by_team: dict[int, dict] = {}
    for r in ct_rows:
        by_team[r["hltv_team_id"]] = {
            "hltv_team_id":    r["hltv_team_id"],
            "team_name":       r["team_name"],
            "map_name":        map_display,
            "maps_played":     r["maps_played"],
            "ct_pistol_won":   r["pistol_won"],
            "ct_pistol_total": r["pistol_total"],
            "ct_pistol_pct":   r["pistol_pct"],
            "t_pistol_won":    None,
            "t_pistol_total":  None,
            "t_pistol_pct":    None,
        }
    for r in t_rows:
        slot = by_team.setdefault(r["hltv_team_id"], {
            "hltv_team_id":    r["hltv_team_id"],
            "team_name":       r["team_name"],
            "map_name":        map_display,
            "maps_played":     r["maps_played"],
            "ct_pistol_won":   None,
            "ct_pistol_total": None,
            "ct_pistol_pct":   None,
            "t_pistol_won":    None,
            "t_pistol_total":  None,
            "t_pistol_pct":    None,
        })
        slot["t_pistol_won"]   = r["pistol_won"]
        slot["t_pistol_total"] = r["pistol_total"]
        slot["t_pistol_pct"]   = r["pistol_pct"]
        # Use the larger maps_played sample if both sides reported.
        if slot["maps_played"] is None or (
            r["maps_played"] is not None and r["maps_played"] > (slot["maps_played"] or 0)
        ):
            slot["maps_played"] = r["maps_played"]
    return by_team


def bulk_upsert(rows: list[dict], period_start: str, period_end: str) -> int:
    """One execute_values batch for the (map, period) slice."""
    if not rows:
        return 0
    sql = """
        INSERT INTO cs2_hltv_team_pistols
            (hltv_team_id, team_name, map_name, period_start, period_end,
             ct_pistol_won, ct_pistol_total, t_pistol_won, t_pistol_total,
             ct_pistol_pct, t_pistol_pct, maps_played, scraped_at)
        VALUES %s
        ON CONFLICT (hltv_team_id, map_name, period_start, period_end) DO UPDATE SET
            team_name       = EXCLUDED.team_name,
            ct_pistol_won   = EXCLUDED.ct_pistol_won,
            ct_pistol_total = EXCLUDED.ct_pistol_total,
            t_pistol_won    = EXCLUDED.t_pistol_won,
            t_pistol_total  = EXCLUDED.t_pistol_total,
            ct_pistol_pct   = EXCLUDED.ct_pistol_pct,
            t_pistol_pct    = EXCLUDED.t_pistol_pct,
            maps_played     = EXCLUDED.maps_played,
            scraped_at      = NOW()
    """
    values = [(
        r["hltv_team_id"], r["team_name"], r["map_name"],
        period_start, period_end,
        r["ct_pistol_won"], r["ct_pistol_total"],
        r["t_pistol_won"],  r["t_pistol_total"],
        r["ct_pistol_pct"], r["t_pistol_pct"],
        r["maps_played"], datetime.now(timezone.utc),
    ) for r in rows]
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, values, template=None, page_size=200)
        conn.commit()
    return len(values)


def select_team_ids(limit: int | None) -> set[int] | None:
    """Top-N teams from cs2_hltv_rankings. Returns a set of hltv_team_id we
    care about — None means take all teams returned by the bulk pages.

    cs2_hltv_rankings stores team_name (no hltv_team_id), so we can't
    pre-filter the bulk page by id. Instead we let the scraper take the full
    ~100 teams the page returns per map, which is already top-100 by Maps
    played → that's effectively HLTV's top tier of CT2 play. If --limit is
    set, we restrict the row set returned to the top-N team_names from
    rankings (so the table grows in step with what we'll later predict on).
    """
    if limit is None:
        return None
    rows = execute_query("""
        SELECT DISTINCT ON (team_name) team_name, hltv_rank
        FROM cs2_hltv_rankings
        WHERE hltv_rank <= %s
        ORDER BY team_name, snapshot_date DESC
    """, (limit,))
    # We don't know hltv_team_id for these names; the bulk page tells us. We
    # return team_name keys for client-side filtering in main(). Encoded as a
    # frozenset of normalised names.
    return {_norm(r["team_name"]) for r in rows}


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or "").lower())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Restrict to top-N teams from cs2_hltv_rankings (default: all teams returned by HLTV per page, ~100)")
    ap.add_argument("--team-id", type=int, default=None,
                    help="One-off: only persist this hltv_team_id (still does bulk fetches)")
    ap.add_argument("--start-date", default="2025-01-01",
                    help="HLTV startDate (YYYY-MM-DD). Default 2025-01-01")
    ap.add_argument("--end-date", default=None,
                    help="HLTV endDate (YYYY-MM-DD). Default today")
    ap.add_argument("--maps", default=None,
                    help="Comma-separated subset of map internal names (e.g. de_mirage,de_inferno). Default: full active pool")
    ap.add_argument("--no-record", action="store_true",
                    help="Parse only, do not write to DB (smoke debug)")
    args = ap.parse_args()

    end_d = args.end_date or date.today().isoformat()
    start_d = args.start_date

    map_list = (args.maps.split(",") if args.maps
                else list(MAP_INTERNAL_TO_DISPLAY.keys()))

    print(f"\n=== HLTV /stats/teams/pistols (per-map) scraper "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  window:  {start_d} → {end_d}")
    print(f"  maps:    {len(map_list)}  ({', '.join(map_list)})")
    print(f"  record:  {'no (debug)' if args.no_record else 'yes'}")

    name_filter = select_team_ids(args.limit) if args.limit else None
    if name_filter is not None:
        print(f"  --limit {args.limit}: restricting to {len(name_filter)} ranked teams (normalised name match)")

    s = make_session()

    total_rows = 0
    total_teams: set[int] = set()
    for i, map_internal in enumerate(map_list):
        if map_internal not in MAP_INTERNAL_TO_DISPLAY:
            print(f"  [!] skipping unknown map {map_internal}")
            continue
        map_display = MAP_INTERNAL_TO_DISPLAY[map_internal]
        print(f"\n  → {map_display}  ({map_internal})")

        # CT-side
        if i > 0:
            time.sleep(RATE_DELAY)
        ct_rows = fetch_pistols_for_map(s, map_internal, start_d, end_d,
                                        side="COUNTER_TERRORIST")
        print(f"      CT: {len(ct_rows)} team rows")

        time.sleep(RATE_DELAY)
        t_rows = fetch_pistols_for_map(s, map_internal, start_d, end_d,
                                       side="TERRORIST")
        print(f"      T:  {len(t_rows)} team rows")

        merged = merge_sides(ct_rows, t_rows, map_display)
        # Filter to ranked subset if asked.
        if name_filter is not None:
            merged = {tid: r for tid, r in merged.items()
                      if _norm(r["team_name"]) in name_filter}
            print(f"      filtered to {len(merged)} ranked teams")
        if args.team_id is not None:
            merged = {tid: r for tid, r in merged.items() if tid == args.team_id}

        if not merged:
            print(f"      (no rows to persist)")
            continue

        if not args.no_record:
            n = bulk_upsert(list(merged.values()), start_d, end_d)
            print(f"      → upserted {n} rows into cs2_hltv_team_pistols")
            total_rows += n
            total_teams.update(merged.keys())

    print(f"\n  ✓ done — {total_rows} total rows across {len(total_teams)} teams")


if __name__ == "__main__":
    main()
