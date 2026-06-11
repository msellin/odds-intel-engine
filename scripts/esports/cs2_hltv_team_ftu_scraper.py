#!/usr/bin/env python3
"""
CS2-HLTV-TEAM-FTU scraper — per-team Firepower / Teamwork / Utility table.

The per-team URL `/stats/teams/ftu/{teamId}/{slug}` does NOT exist on HLTV
(returns 404). The BULK page `/stats/teams/ftu?startDate=&endDate=[&side=&maps=]`
is the only working entry point and returns ~100 teams in one shot. Same
pattern as cs2_hltv_team_pistols_scraper.py.

IMPORTANT — what HLTV /stats/teams/ftu actually contains:
  The visual header groups columns under three composite banners
      Firepower | Teamwork | Utility
  but the underlying data is ten plain columns:
      Team | Maps | RW% | OpK | MultiK | 5v4% | 4v5% | Traded% | ADR | FA
  HLTV does NOT publish per-team flashes/HE/molly/smoke thrown counts. The
  closest utility-usage signals are FA (flash assists per round) and ADR
  (avg damage per round, partly utility damage). See migration 240 comment
  for the schema-vs-spec rationale.

Bulk plan: top-100 teams × 3 sides (overall + CT + T) = 3 requests per
period. Across four periods (2024-06→2025-05, 2024-09→2025-08, 2024-12→2025-11,
2025-01→today) that's 12 fetches. At RATE_DELAY=5s this is ~1 minute.

Auth: HLTV /stats/* is Cloudflare-gated. FlareSolverr preferred, cookie
session fallback — same pattern as cs2_hltv_stats_scraper.py.

CLI:
    python3 scripts/esports/cs2_hltv_team_ftu_scraper.py --record
    python3 scripts/esports/cs2_hltv_team_ftu_scraper.py --limit 50 --record
    python3 scripts/esports/cs2_hltv_team_ftu_scraper.py --team-id 9565 --record
    python3 scripts/esports/cs2_hltv_team_ftu_scraper.py --periods latest --record
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, date
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

import psycopg2.extras  # noqa: E402


RATE_DELAY = 5.0
DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/148.0.0.0 Safari/537.36")
BASE = "https://www.hltv.org"

# Quarterly-rolling periods. Each period is a ~12-month trailing window with a
# different period_end so v19 can pick the most recent eligible (PIT) sample.
DEFAULT_PERIODS = [
    ("2024-06-01", "2025-05-31"),
    ("2024-09-01", "2025-08-31"),
    ("2024-12-01", "2025-11-30"),
    ("2025-01-01", None),   # None → today
]

# Map pool — not used by default (we collect overall+CT+T across all maps),
# but retained for parity with cs2_hltv_team_pistols_scraper.py and exposed
# via --maps for ad-hoc per-map FTU pulls if we want per-map FTU later.
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

# Parser regexes — table is class="stats-table player-ratings-table ftu ..."
_FTU_TABLE_RE = re.compile(
    r'<table[^>]*class="[^"]*\bftu\b[^"]*"[^>]*>(.*?)</table>',
    re.DOTALL,
)
_TBODY_RE = re.compile(r'<tbody[^>]*>(.*?)</tbody>', re.DOTALL)
_TR_RE   = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
_TD_RE   = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)
_TEAM_LINK_RE = re.compile(r'<a href="/stats/teams/(\d+)/([^"?]+)(?:\?[^"]*)?"[^>]*>([^<]+)</a>')
_PCT_RE = re.compile(r'(-?\d+\.?\d*)\s*%')
_NUM_RE = re.compile(r'(-?\d+\.?\d*)')


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
            return fs_fetch(url, session="hltv_team_ftu")
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


def _strip(s: str) -> str:
    return re.sub(r'<[^>]+>', '', s).strip()


def _parse_pct(s: str) -> float | None:
    m = _PCT_RE.search(s)
    return float(m.group(1)) if m else None


def _parse_num(s: str) -> float | None:
    m = _NUM_RE.search(s)
    return float(m.group(1)) if m else None


def _parse_int(s: str) -> int | None:
    try:
        return int(_strip(s))
    except (ValueError, AttributeError):
        return None


def parse_ftu_rows(html: str) -> list[dict]:
    """Parse /stats/teams/ftu table → list of dicts.

    Column order on the live page (verified 2026-06-10):
        0: Team link (with flag img)
        1: Maps played
        2: RW%      (Round Win %)         — Firepower bucket
        3: OpK%     (Opening kill %)      — Firepower
        4: MultiK%  (multi-kill %, ratio so e.g. 0.85 not pct)
        5: 5v4%     (post-plant 5v4 win)  — Firepower
        6: 4v5%     (4v5 retake win)      — Teamwork
        7: Traded%  (% deaths traded)     — Teamwork
        8: ADR      (avg damage / round)  — Utility-ish
        9: FA       (flash assists / rd)  — Utility
    """
    out: list[dict] = []
    table_m = _FTU_TABLE_RE.search(html or "")
    if not table_m:
        return out
    tbody_m = _TBODY_RE.search(table_m.group(1))
    if not tbody_m:
        return out
    for row in _TR_RE.finditer(tbody_m.group(1)):
        cells = _TD_RE.findall(row.group(1))
        if len(cells) < 10:
            continue
        link_m = _TEAM_LINK_RE.search(cells[0])
        if not link_m:
            continue
        tid = int(link_m.group(1))
        name = link_m.group(3).strip()

        maps_played = _parse_int(cells[1])
        rw_pct      = _parse_pct(_strip(cells[2]))
        opk_pct     = _parse_pct(_strip(cells[3]))
        # MultiK is a ratio like "0.85" not a percent on the live page.
        multik      = _parse_num(_strip(cells[4]))
        five_v_four = _parse_pct(_strip(cells[5]))
        four_v_five = _parse_pct(_strip(cells[6]))
        traded_pct  = _parse_pct(_strip(cells[7]))
        adr         = _parse_num(_strip(cells[8]))
        fa          = _parse_num(_strip(cells[9]))

        out.append({
            "hltv_team_id": tid,
            "team_name":    name,
            "maps_played":  maps_played,
            "rw_pct":       rw_pct,
            "opk_pct":      opk_pct,
            "multik_pct":   multik,
            "five_v_four":  five_v_four,
            "four_v_five":  four_v_five,
            "traded_pct":   traded_pct,
            "adr":          adr,
            "fa":           fa,
        })
    return out


def fetch_ftu(session: requests.Session,
              start_date: str, end_date: str,
              side: str | None = None,
              map_internal: str | None = None) -> list[dict]:
    """One bulk fetch for (period, optional side, optional map). ~100 team rows."""
    parts = [f"startDate={start_date}", f"endDate={end_date}"]
    if side:
        if side not in ("TERRORIST", "COUNTER_TERRORIST"):
            raise ValueError(f"side must be TERRORIST or COUNTER_TERRORIST, got {side!r}")
        parts.append(f"side={side}")
    if map_internal:
        parts.append(f"maps={map_internal}")
    url = f"{BASE}/stats/teams/ftu?" + "&".join(parts)
    # Defensive — rankingFilter caps to top-20 silently.
    assert "rankingFilter" not in url, "rankingFilter is forbidden — caps to top-20"
    html = _fetch_url(session, url)
    return parse_ftu_rows(html) if html else []


def bulk_upsert(rows: list[dict], side: str,
                period_start: str, period_end: str) -> int:
    """One execute_values batch for the (side, period) slice."""
    if not rows:
        return 0
    sql = """
        INSERT INTO cs2_hltv_team_ftu
            (hltv_team_id, team_name, side, period_start, period_end,
             maps_played, rw_pct, opk_pct, multik_pct,
             five_v_four, four_v_five, traded_pct, adr, fa, scraped_at)
        VALUES %s
        ON CONFLICT (hltv_team_id, side, period_start, period_end) DO UPDATE SET
            team_name   = EXCLUDED.team_name,
            maps_played = EXCLUDED.maps_played,
            rw_pct      = EXCLUDED.rw_pct,
            opk_pct     = EXCLUDED.opk_pct,
            multik_pct  = EXCLUDED.multik_pct,
            five_v_four = EXCLUDED.five_v_four,
            four_v_five = EXCLUDED.four_v_five,
            traded_pct  = EXCLUDED.traded_pct,
            adr         = EXCLUDED.adr,
            fa          = EXCLUDED.fa,
            scraped_at  = NOW()
    """
    values = [(
        r["hltv_team_id"], r["team_name"], side,
        period_start, period_end,
        r["maps_played"], r["rw_pct"], r["opk_pct"], r["multik_pct"],
        r["five_v_four"], r["four_v_five"], r["traded_pct"],
        r["adr"], r["fa"], datetime.now(timezone.utc),
    ) for r in rows]
    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, values, template=None, page_size=200)
        conn.commit()
    return len(values)


def select_team_ids(limit: int | None) -> set[str] | None:
    """Top-N teams from cs2_hltv_rankings (by normalised team_name). cs2_hltv_rankings
    stores team_name (no hltv_team_id) so we filter the bulk results by name."""
    if limit is None:
        return None
    rows = execute_query("""
        SELECT DISTINCT ON (team_name) team_name, hltv_rank
        FROM cs2_hltv_rankings
        WHERE hltv_rank <= %s
        ORDER BY team_name, snapshot_date DESC
    """, (limit,))
    return {_norm(r["team_name"]) for r in rows}


def _norm(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', (s or "").lower())


def _resolve_periods(arg: str) -> list[tuple[str, str]]:
    today = date.today().isoformat()
    if arg == "latest":
        return [(DEFAULT_PERIODS[-1][0], DEFAULT_PERIODS[-1][1] or today)]
    if arg == "all":
        return [(s, e or today) for s, e in DEFAULT_PERIODS]
    # custom CSV "2024-06-01:2025-05-31,2024-09-01:2025-08-31"
    out = []
    for chunk in arg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        s, e = chunk.split(":")
        out.append((s.strip(), (e.strip() or today)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Restrict to top-N teams from cs2_hltv_rankings (default: all teams returned by HLTV per page, ~100)")
    ap.add_argument("--team-id", type=int, default=None,
                    help="One-off: only persist this hltv_team_id (still does bulk fetches)")
    ap.add_argument("--periods", default="all",
                    help="'all' (4 quarterly windows), 'latest' (most-recent only), or comma-separated YYYY-MM-DD:YYYY-MM-DD pairs")
    ap.add_argument("--maps", default=None,
                    help="Optional comma-separated subset of map internal names for per-map FTU (default: collect overall, no per-map iteration)")
    ap.add_argument("--no-record", action="store_true",
                    help="Parse only, do not write to DB (smoke debug)")
    args = ap.parse_args()

    periods = _resolve_periods(args.periods)

    print(f"\n=== HLTV /stats/teams/ftu scraper "
          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  periods: {len(periods)}  {[f'{s}→{e}' for s,e in periods]}")
    print(f"  record:  {'no (debug)' if args.no_record else 'yes'}")

    name_filter = select_team_ids(args.limit) if args.limit else None
    if name_filter is not None:
        print(f"  --limit {args.limit}: restricting to {len(name_filter)} ranked teams (normalised name match)")

    s = make_session()

    sides = [("all", None),
             ("ct", "COUNTER_TERRORIST"),
             ("t",  "TERRORIST")]
    map_list = args.maps.split(",") if args.maps else [None]

    total_rows = 0
    total_teams: set[int] = set()
    first_fetch = True
    for start_d, end_d in periods:
        print(f"\n  ▶ period {start_d} → {end_d}")
        for side_label, side_param in sides:
            for map_internal in map_list:
                if not first_fetch:
                    time.sleep(RATE_DELAY)
                first_fetch = False
                rows = fetch_ftu(s, start_d, end_d,
                                 side=side_param, map_internal=map_internal)
                tag = f"side={side_label}"
                if map_internal:
                    tag += f" map={map_internal}"
                print(f"      {tag}: {len(rows)} team rows")
                if not rows:
                    continue
                # Filter
                if name_filter is not None:
                    rows = [r for r in rows if _norm(r["team_name"]) in name_filter]
                    print(f"        filtered to {len(rows)} ranked teams")
                if args.team_id is not None:
                    rows = [r for r in rows if r["hltv_team_id"] == args.team_id]
                if not rows:
                    continue
                if not args.no_record:
                    n = bulk_upsert(rows, side_label, start_d, end_d)
                    print(f"        → upserted {n} rows into cs2_hltv_team_ftu")
                    total_rows += n
                    total_teams.update(r["hltv_team_id"] for r in rows)

    print(f"\n  ✓ done — {total_rows} total rows across {len(total_teams)} teams")


if __name__ == "__main__":
    main()
