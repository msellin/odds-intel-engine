"""
CS2 HLTV pistol round stats scraper.

Top-1 research signal (+0.010-0.015 AUC est.). Mechanism: pistol round
correlation with match win is ~70-80% in pro Bo1 play because winning a
pistol → $3,250 anti-eco round → bonus round → 3-0 start = ~$15k economy
lead. Captures rolling team pistol-win-rate + per-side splits.

Source: /stats/teams/pistols (Cloudflare auth required — same cookies as
team-map-stats scraper).

Three URLs per scrape:
  ?side= (omitted)        → total pistol stats
  ?side=COUNTER_TERRORIST → CT-side only
  ?side=TERRORIST         → T-side only

Date range defaults to last 365 days for rolling current-roster perf.

Run:
    python3 scripts/esports/cs2_hltv_pistol_scraper.py [--top-n 50] [--record]
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_write  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
try:
    from scraper_state import scraper_run
except ImportError:
    scraper_run = None  # type: ignore


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
RATE_DELAY = 5.0
BASE = "https://www.hltv.org"


def load_cookies() -> dict:
    raw = os.getenv("HLTV_AUTH_COOKIES", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print("[!] HLTV_AUTH_COOKIES env var is not valid JSON", file=sys.stderr)
        return {}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.hltv.org/stats",
    })
    cookies = load_cookies()
    if not cookies:
        print("[!] HLTV_AUTH_COOKIES not set — /stats/* requests will 403", file=sys.stderr)
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="www.hltv.org")
    return s


# Pistol table actual structure (verified via FlareSolverr 2026-06-09):
# col 0: team link <a href="/stats/teams/{id}/{slug}">name</a>
# col 1: maps played
# col 2: "W - L" (eg "195 - 175")
# col 3: pistol win %
# col 4: ??? (rounds won after pistol win — high values, 75-90%)
# col 5: ??? (rounds won after pistol loss — low values, 15-35%)
_TEAMS_TABLE_RE = re.compile(
    r'<table[^>]*class="[^"]*stats-table[^"]*"[^>]*>(.*?)</table>',
    re.DOTALL,
)
_TBODY_RE = re.compile(r'<tbody[^>]*>(.*?)</tbody>', re.DOTALL)
_TR_RE   = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
_TD_RE   = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)
_TEAM_LINK_RE = re.compile(r'<a href="/stats/teams/(\d+)/([^"]+)"[^>]*>([^<]+)</a>')


def _parse_pistol_rows(html: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    table_m = _TEAMS_TABLE_RE.search(html)
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
        def _num(s):
            t = re.sub(r'<[^>]+>', '', s).strip().rstrip('%')
            try: return float(t)
            except ValueError: return None
        maps_played = _num(cells[1])
        # "W - L" cell — split on "-"
        wl_text = re.sub(r'<[^>]+>', '', cells[2]).strip()
        wl_m = re.match(r'(\d+)\s*-\s*(\d+)', wl_text)
        pistols_won = int(wl_m.group(1)) if wl_m else None
        pistols_lost = int(wl_m.group(2)) if wl_m else None
        pistols_played = (pistols_won + pistols_lost) if (pistols_won is not None and pistols_lost is not None) else None
        pistol_win_pct = _num(cells[3])
        out[tid] = {
            "team_name": name,
            "slug": slug,
            "maps_played": int(maps_played) if maps_played is not None else None,
            "pistols_played": pistols_played,
            "pistols_won": pistols_won,
            "pistol_win_pct": pistol_win_pct,
            "rounds_played": None,  # not in this table; left for schema compat
        }
    return out


def fetch_pistol_page(start_date: str, end_date: str,
                     side: str | None = None, ranking_filter: str = "Top50") -> dict:
    """Use FlareSolverr to fetch + parse the pistol stats page."""
    from flaresolverr_client import fetch as fs_fetch
    params = [
        f"startDate={start_date}",
        f"endDate={end_date}",
        f"rankingFilter={ranking_filter}",
    ]
    if side:
        params.append(f"side={side}")
    url = f"{BASE}/stats/teams/pistols?" + "&".join(params)
    html = fs_fetch(url, session="hltv_pistol")
    if not html:
        return {}
    return _parse_pistol_rows(html)


def merge_three_views(total, ct, t):
    merged: dict[int, dict] = {}
    for tid, v in total.items():
        merged[tid] = {
            "team_name": v["team_name"], "slug": v["slug"],
            "pistols_played": v["pistols_played"],
            "pistols_won":    v["pistols_won"],
            "pistol_win_pct": v["pistol_win_pct"],
            "rounds_played":  v["rounds_played"],
            "ct_pistols_played": None, "ct_pistols_won": None, "ct_pistol_win_pct": None,
            "t_pistols_played":  None, "t_pistols_won":  None, "t_pistol_win_pct":  None,
        }
    for tid, v in ct.items():
        if tid in merged:
            merged[tid]["ct_pistols_played"] = v["pistols_played"]
            merged[tid]["ct_pistols_won"] = v["pistols_won"]
            merged[tid]["ct_pistol_win_pct"] = v["pistol_win_pct"]
    for tid, v in t.items():
        if tid in merged:
            merged[tid]["t_pistols_played"] = v["pistols_played"]
            merged[tid]["t_pistols_won"] = v["pistols_won"]
            merged[tid]["t_pistol_win_pct"] = v["pistol_win_pct"]
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--record", action="store_true")
    args = ap.parse_args()

    print(f"\n=== HLTV pistol stats  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    # FlareSolverr handles CF challenges (Cloudflare blocks requests/curl_cffi
    # even with valid cookies; FlareSolverr runs real Chrome that auto-solves).
    sys.path.insert(0, str(Path(__file__).parent))
    from flaresolverr_client import is_available as fs_available
    if not fs_available():
        print(f"  [!] FlareSolverr not reachable at FLARESOLVERR_URL — exit", file=sys.stderr)
        print(f"  [!] Start with: docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest", file=sys.stderr)
        return
    print(f"  [✓] FlareSolverr reachable")

    end = date.today()
    start = end - timedelta(days=args.days)
    ranking_filter = f"Top{args.top_n}" if args.top_n in (20, 30, 50) else "Top50"
    print(f"  window: {start} → {end}  ranking: {ranking_filter}")

    ctx = scraper_run("team_pistol_stats", "Team pistol round stats (overall + CT-side + T-side)") if (scraper_run and args.record) else None
    st = ctx.__enter__() if ctx else None

    try:
        print(f"  fetching overall (via FlareSolverr)...")
        total = fetch_pistol_page(str(start), str(end), side=None, ranking_filter=ranking_filter)
        print(f"    {len(total)} teams")
        if not total:
            print(f"  [!] no data — page structure may have changed", file=sys.stderr)
            return
        time.sleep(RATE_DELAY)

        print(f"  fetching CT-side...")
        ct = fetch_pistol_page(str(start), str(end), side="COUNTER_TERRORIST", ranking_filter=ranking_filter)
        print(f"    {len(ct)} teams")
        time.sleep(RATE_DELAY)

        print(f"  fetching T-side...")
        t = fetch_pistol_page(str(start), str(end), side="TERRORIST", ranking_filter=ranking_filter)
        print(f"    {len(t)} teams")

        merged = merge_three_views(total, ct, t)
        if st: st.set_total(len(merged))

        print(f"\n  merged: {len(merged)} teams")
        snapshot = end.isoformat()
        for tid, v in merged.items():
            ct_str = f"CT {v['ct_pistol_win_pct']:.1f}%" if v["ct_pistol_win_pct"] is not None else "CT —"
            t_str  = f"T {v['t_pistol_win_pct']:.1f}%"  if v["t_pistol_win_pct"]  is not None else "T —"
            print(f"  {v['team_name']:25}  pistol {v['pistol_win_pct']:5.1f}%  ({ct_str} / {t_str})  "
                  f"played={v['pistols_played']}")
            if args.record:
                execute_write("""
                    INSERT INTO cs2_team_pistol_stats
                        (hltv_team_id, team_name, pistols_played, pistols_won, pistol_win_pct,
                         ct_pistols_played, ct_pistols_won, ct_pistol_win_pct,
                         t_pistols_played, t_pistols_won, t_pistol_win_pct,
                         rounds_played, snapshot_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (hltv_team_id, snapshot_date) DO UPDATE SET
                        pistol_win_pct = EXCLUDED.pistol_win_pct,
                        ct_pistol_win_pct = EXCLUDED.ct_pistol_win_pct,
                        t_pistol_win_pct = EXCLUDED.t_pistol_win_pct,
                        fetched_at = NOW()
                """, (
                    tid, v["team_name"],
                    v["pistols_played"], v["pistols_won"], v["pistol_win_pct"],
                    v["ct_pistols_played"], v["ct_pistols_won"], v["ct_pistol_win_pct"],
                    v["t_pistols_played"], v["t_pistols_won"], v["t_pistol_win_pct"],
                    v["rounds_played"], snapshot,
                ))
                if st: st.tick_done()

    finally:
        if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
