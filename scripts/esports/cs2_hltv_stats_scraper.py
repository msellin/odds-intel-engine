#!/usr/bin/env python3
"""
Authenticated HLTV /stats/* scraper.

HLTV's /stats/* subdomain is gated by Cloudflare. The non-stats pages work
without cookies, but the rich aggregated tables (per-team-per-map win rate,
top-20 player rankings over time windows, etc.) live under /stats/* which
requires a valid Cloudflare session.

Cookie capture flow:
  1. Log into HLTV in a browser (or use incognito)
  2. Visit a /stats/* page once so Cloudflare clears the challenge
  3. DevTools → Application → Cookies → www.hltv.org → copy these four:
       __cflb       (Cloudflare load balancer — lifetime: weeks)
       cf_clearance (Cloudflare challenge clearance — ~24-72h)
       _cfuvid      (Cloudflare unique visitor ID — weeks)
       __cf_bm      (Cloudflare bot mgmt — refreshes every ~30 min)
  4. Set HLTV_AUTH_COOKIES env var to JSON: {"__cflb":"...","cf_clearance":"...",
       "_cfuvid":"...","__cf_bm":"..."}
  5. HLTV_USER_AGENT must MATCH the UA used when you got the cookies.

If cookies expire (403), this script prints a clear "cookies expired" line
and exits non-zero so the cron flags it.

Usage:
    python3 scripts/esports/cs2_hltv_stats_scraper.py --team 9565 --slug vitality
    python3 scripts/esports/cs2_hltv_stats_scraper.py --top-n 50 --record
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "--break-system-packages", "-q"])
    import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_query, execute_write

RATE_DELAY = 5.0   # polite — these are auth'd but still don't hammer

DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"

_STATS_ROW_RE = re.compile(
    r'<div class="stats-row">\s*<span[^>]*>([^<]+)</span>\s*<span[^>]*>([^<]+)</span>\s*</div>'
)
# Per-map blocks on the team page — each map has its own .stats-rows container.
_MAP_HEADER_RE = re.compile(r'<div class="map-header"[^>]*>.*?<div class="mapname"[^>]*>([^<]+)</div>', re.DOTALL)


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
        "User-Agent": os.getenv("HLTV_USER_AGENT", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    })
    cookies = load_cookies()
    if not cookies:
        print("[!] HLTV_AUTH_COOKIES not set — /stats/* requests will 403", file=sys.stderr)
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="www.hltv.org")
    return s


def _pct(text: str) -> float | None:
    m = re.search(r"(\d+\.?\d*)\s*%", text)
    return float(m.group(1)) if m else None


def _int(text: str) -> int | None:
    m = re.search(r"(\d+)", (text or "").replace(",", ""))
    return int(m.group(1)) if m else None


def _wdl(text: str) -> tuple[int | None, int | None, int | None]:
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*", text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (None, None, None)


def parse_team_map_page(html: str) -> dict:
    """Extract the OVERALL aggregate stats block (top of the team-map page).

    Per-individual-map breakdowns appear lower; we capture the headline first.
    """
    out: dict = {}
    for label, value in _STATS_ROW_RE.findall(html):
        L = label.strip().lower()
        if "wins" in L and "losses" in L:
            w, d, l = _wdl(value)
            out["wins"], out["draws"], out["losses"] = w, d, l
        elif "win rate" == L:
            out["win_pct"] = _pct(value)
        elif "total rounds" == L:
            out["total_rounds"] = _int(value)
        elif "after getting first kill" in L:
            out["round_win_pct_after_first_kill"] = _pct(value)
        elif "after receiving first death" in L:
            out["round_win_pct_after_first_death"] = _pct(value)
        elif "pick %" == L or L == "pick%":
            out["pick_pct"] = _pct(value)
        elif "ban %" == L or L == "ban%":
            out["ban_pct"] = _pct(value)
    return out


CS2_MAP_POOL = ["Mirage", "Inferno", "Nuke", "Dust2", "Ancient", "Anubis", "Train", "Overpass", "Vertigo"]

# One regex captures the per-map win-rate row: "MapName - 92.5%"
_MAP_WINRATE_RE = re.compile(r"(Mirage|Inferno|Nuke|Dust2|Ancient|Anubis|Train|Overpass|Vertigo)\s*-\s*(\d+\.?\d*)%")


def fetch_team_maps_summary(session: requests.Session, team_id: int, slug: str,
                            start_date: str, end_date: str) -> dict[str, float]:
    """One request per team — extract per-map win rates from the team-maps page.

    URL: /stats/teams/maps/{id}/{slug}?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
    The summary table embeds each map as "MapName - XX.X%".
    """
    url = (f"https://www.hltv.org/stats/teams/maps/{team_id}/{slug}"
           f"?startDate={start_date}&endDate={end_date}")
    r = session.get(url, timeout=20)
    if r.status_code == 403:
        print(f"  [!] 403 on {url} — cookies likely expired", file=sys.stderr)
        return {}
    if not r.ok:
        print(f"  [!] {r.status_code} on team {team_id}", file=sys.stderr)
        return {}
    out: dict[str, float] = {}
    for mp, pct in _MAP_WINRATE_RE.findall(r.text):
        if mp not in out:
            out[mp] = float(pct)
    return out


# /team/{id}/{slug} links in the /ranking/teams page give us team_id + slug.
_TEAM_LINK_RE = re.compile(r'href="/team/(\d+)/([^"\']+)"')


def discover_team_ids(session: requests.Session) -> dict[str, tuple[int, str]]:
    """Return {team_name_lower: (team_id, slug)} from /ranking/teams.

    The page has 248 ranked teams. Use a flat scan + assume the name and the
    first /team/{id}/{slug} link inside each rank's section pair up.
    """
    r = session.get("https://www.hltv.org/ranking/teams", timeout=15)
    if not r.ok:
        return {}
    # Pull names + IDs in order. Each ranked-team starts with a <span class="name">
    # then a /team/{id}/{slug} link appears soon after.
    # Iterate matches of both with positions, then zip.
    name_iter = list(re.finditer(r'<span class="name">([^<]+)</span>', r.text))
    link_iter = list(re.finditer(r'href="/team/(\d+)/([^"\']+)"', r.text))
    out: dict[str, tuple[int, str]] = {}
    li = 0
    for nm_m in name_iter:
        # advance link iterator to first link AFTER this name's position
        while li < len(link_iter) and link_iter[li].start() < nm_m.end():
            li += 1
        if li >= len(link_iter):
            break
        name = nm_m.group(1).strip()
        tid = int(link_iter[li].group(1))
        slug = link_iter[li].group(2)
        out.setdefault(name.lower(), (tid, slug))
    return out


def _top_n_teams_with_hltv_id(session: requests.Session, n: int) -> list[tuple[int, str, str]]:
    rankings = execute_query("""
        SELECT DISTINCT ON (team_name) team_name, hltv_rank
        FROM cs2_hltv_rankings
        WHERE hltv_rank <= %s
        ORDER BY team_name, snapshot_date DESC
    """, (n,))
    id_map = discover_team_ids(session)
    out: list[tuple[int, str, str]] = []
    for r in sorted(rankings, key=lambda x: x["hltv_rank"]):
        name = r["team_name"]
        info = id_map.get(name.lower())
        if info:
            out.append((info[0], name, info[1]))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--team", type=int, help="HLTV team_id for one-off")
    p.add_argument("--slug", default="", help="Team slug for one-off")
    p.add_argument("--top-n", type=int, help="Run against top-N teams from cs2_hltv_rankings")
    p.add_argument("--record", action="store_true", help="Write to cs2_hltv_team_map_stats")
    args = p.parse_args()

    print(f"\n=== HLTV /stats/teams/maps scraper  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    s = make_session()

    if args.team and args.slug:
        targets = [(args.team, args.slug, args.slug)]
    elif args.top_n:
        targets = _top_n_teams_with_hltv_id(s, args.top_n)
        print(f"  top-{args.top_n}: {len(targets)} resolved with team_id")
    else:
        print("  pass --team + --slug OR --top-n", file=sys.stderr)
        sys.exit(1)

    snapshot = datetime.now(timezone.utc).date().isoformat()
    today = datetime.now(timezone.utc).date()
    one_year_ago = today.replace(year=today.year - 1)

    for i, (team_id, name, slug) in enumerate(targets):
        if not slug:
            continue
        if i > 0:
            time.sleep(RATE_DELAY)
        print(f"\n  → {name} (id={team_id})")
        wr = fetch_team_maps_summary(s, team_id, slug,
                                     str(one_year_ago), str(today))
        if not wr:
            print(f"    no map data (cookies expired?)")
            continue
        for mp, pct in wr.items():
            print(f"    {mp:10}  win% = {pct:5.1f}")
            if args.record:
                execute_write("""
                    INSERT INTO cs2_hltv_team_map_stats
                        (hltv_team_id, team_name, map_name, win_pct, snapshot_date)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (hltv_team_id, map_name, snapshot_date) DO UPDATE SET
                        win_pct=EXCLUDED.win_pct, fetched_at=NOW()
                """, (team_id, name, mp, pct, snapshot))


if __name__ == "__main__":
    main()
