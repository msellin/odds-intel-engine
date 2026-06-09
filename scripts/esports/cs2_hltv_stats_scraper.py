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


# FlareSolverr helper — try once for /stats/* URLs since they're CF-protected.
# Falls back to the cookie-based session.get() on FlareSolverr unavailability.
def _fetch_url(session: requests.Session, url: str) -> str | None:
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        if is_available():
            return fs_fetch(url, session="hltv_stats")
    except ImportError:
        pass
    try:
        r = session.get(url, timeout=20)
        if r.status_code == 200:
            return r.text
        print(f"  [!] {r.status_code} on {url[-60:]}", file=sys.stderr)
    except Exception as e:
        print(f"  [!] {url[-60:]} {e}", file=sys.stderr)
    return None


def fetch_team_maps_summary(session: requests.Session, team_id: int, slug: str,
                            start_date: str, end_date: str) -> tuple[dict[str, float], dict]:
    """One request per team — extract per-map win rates + team aggregate stats.

    URL: /stats/teams/maps/{id}/{slug}?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD

    Returns:
      (map_winrates, team_aggregate)
      map_winrates = {map_name: win_pct}
      team_aggregate = {win_pct, round_win_pct_after_first_kill,
                        round_win_pct_after_first_death, ...}
    """
    url = (f"https://www.hltv.org/stats/teams/maps/{team_id}/{slug}"
           f"?startDate={start_date}&endDate={end_date}")
    html = _fetch_url(session, url)
    if not html:
        return {}, {}
    out: dict[str, float] = {}
    for mp, pct in _MAP_WINRATE_RE.findall(html):
        if mp not in out:
            out[mp] = float(pct)
    aggregate = parse_team_map_page(html)
    return out, aggregate


# /team/{id}/{slug} links in the /ranking/teams page give us team_id + slug.
_TEAM_LINK_RE = re.compile(r'href="/team/(\d+)/([^"\']+)"')


def discover_team_ids(session: requests.Session) -> dict[str, tuple[int, str]]:
    """Return {team_name_lower: (team_id, slug)} from /ranking/teams.

    The page has 248 ranked teams. Use a flat scan + assume the name and the
    first /team/{id}/{slug} link inside each rank's section pair up.
    """
    html = _fetch_url(session, "https://www.hltv.org/ranking/teams")
    # Wrap result so the rest of the function can keep using `.text`
    class _R: pass
    r = _R(); r.text = html or ""; r.ok = bool(html); r.status_code = 200 if html else 0
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


def _flatten_stats_rows(html: str) -> dict:
    """All <div class='stats-row'><span>label</span><span>value</span></div> pairs
    flattened to {label_slug: value}. Used for player stats + map meta where
    HLTV's schema varies but the row markup is consistent."""
    out: dict = {}
    for label, value in _STATS_ROW_RE.findall(html):
        key = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
        if not key or not value.strip():
            continue
        out[key] = value.strip()
    # Also capture .maps-info-percentage + .maps-info-desc pairs (used on map pages)
    map_info = re.findall(
        r'<div class="maps-info-percentage">([\d\.]+%)</div>\s*<div class="maps-info-desc">([^<]+)</div>',
        html
    )
    for pct, desc in map_info:
        k = re.sub(r"[^a-z0-9]+", "_", desc.strip().lower()).strip("_")
        if k:
            out[k] = pct
    return out


# Look up an active CS2 map's HLTV ID + page slug from the maps overview.
_MAP_LINK_RE = re.compile(r'href="/stats/maps/map/(\d+)/([^"\']+)"')


def discover_map_ids(session: requests.Session) -> list[tuple[int, str]]:
    html = _fetch_url(session, "https://www.hltv.org/stats/maps")
    class _R: pass
    r = _R(); r.text = html or ""; r.ok = bool(html); r.status_code = 200 if html else 0
    if not r.ok:
        return []
    seen = set()
    out = []
    for mid, slug in _MAP_LINK_RE.findall(r.text):
        mid = int(mid)
        if mid in seen:
            continue
        seen.add(mid)
        out.append((mid, slug))
    return out


def fetch_player_stats(session: requests.Session, player_id: int, slug: str) -> dict:
    url = f"https://www.hltv.org/stats/players/{player_id}/{slug}"
    html = _fetch_url(session, url)
    return _flatten_stats_rows(html) if html else {}


def fetch_map_meta(session: requests.Session, map_id: int, slug: str) -> dict:
    url = f"https://www.hltv.org/stats/maps/map/{map_id}/{slug}"
    html = _fetch_url(session, url)
    return _flatten_stats_rows(html) if html else {}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--team", type=int, help="HLTV team_id for one-off")
    p.add_argument("--slug", default="", help="Team slug for one-off")
    p.add_argument("--top-n", type=int, help="Run team-map backfill against top-N teams")
    p.add_argument("--players-top-n", type=int, help="Run per-player stats against top-N teams' rosters")
    p.add_argument("--maps", action="store_true", help="Scrape /stats/maps + each map page")
    p.add_argument("--record", action="store_true", help="Write to DB")
    args = p.parse_args()

    print(f"\n=== HLTV /stats/teams/maps scraper  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    s = make_session()

    if args.team and args.slug:
        targets = [(args.team, args.slug, args.slug)]
    elif args.top_n:
        targets = _top_n_teams_with_hltv_id(s, args.top_n)
        print(f"  top-{args.top_n}: {len(targets)} resolved with team_id")
    elif args.maps or args.players_top_n:
        targets = []   # only running the new modes
    else:
        print("  pass --team + --slug OR --top-n OR --maps OR --players-top-n", file=sys.stderr)
        sys.exit(1)

    # Import state helper (only needed when args.record so it can write back)
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from scraper_state import scraper_run  # type: ignore
    except ImportError:
        scraper_run = None  # type: ignore

    # --maps backfill: per-map meta from /stats/maps/map/{id}/{slug}
    if args.maps:
        print("\n=== Maps backfill ===")
        maps = discover_map_ids(s)
        print(f"  discovered {len(maps)} map IDs")
        ctx = scraper_run("map_meta", "HLTV /stats/maps overview per map") if (scraper_run and args.record) else None
        st = ctx.__enter__() if ctx else None
        try:
            if st: st.set_total(len(maps))
            for i, (mid, slug) in enumerate(maps):
                if i > 0:
                    time.sleep(RATE_DELAY)
                stats = fetch_map_meta(s, mid, slug)
                print(f"  map id={mid:>3} {slug:15}  fields={len(stats)}")
                if args.record and stats:
                    execute_write("""
                        INSERT INTO cs2_hltv_map_meta (hltv_map_id, map_name, stats, fetched_at)
                        VALUES (%s, %s, %s::jsonb, NOW())
                        ON CONFLICT (hltv_map_id) DO UPDATE SET
                            map_name=EXCLUDED.map_name, stats=EXCLUDED.stats, fetched_at=NOW()
                    """, (mid, slug, json.dumps(stats)))
                    if st: st.tick_done()
                elif st:
                    st.tick_failed("no stats")
        finally:
            if ctx: ctx.__exit__(None, None, None)

    # --players-top-n: scrape first N players from the player_ids cache.
    # cs2_hltv_rankings.players is unpopulated (parser fallback didn't store it)
    # so we take the discovered player ID cache as the universe.
    if args.players_top_n:
        print("\n=== Player-stats backfill ===")
        try:
            id_cache = json.loads(Path("data/esports/cs2/hltv_player_ids.json").read_text())
        except (json.JSONDecodeError, OSError):
            id_cache = {}
        # Skip already-fetched players (resumable). Only those not in cs2_hltv_player_stats.
        existing = {r["hltv_player_id"] for r in execute_query(
            "SELECT hltv_player_id FROM cs2_hltv_player_stats")} if args.record else set()
        # First N alphabetically by nickname among missing — gives a decent sample
        all_targets: list[tuple[int, str]] = sorted(
            ((pid, nick) for nick, pid in id_cache.items()),
            key=lambda x: x[1]
        )
        targets_pl = [(pid, slug) for pid, slug in all_targets
                      if pid not in existing][:args.players_top_n]
        print(f"  {len(targets_pl)} players to fetch ({len(existing)} already done, "
              f"alphabetical first {args.players_top_n} of remaining)")

        ctx = scraper_run("player_stats", "Per-player career stats from /stats/players/{id}") if (scraper_run and args.record) else None
        st = ctx.__enter__() if ctx else None
        try:
            if st:
                st.set_total(len(existing) + len(targets_pl))
                st.set_pending(len(targets_pl))
                # Already-done count carries over from prior runs
                pass  # items_done will tick from 0; UI can read items_total - pending if needed
            for i, (pid, slug) in enumerate(targets_pl):
                if i > 0:
                    time.sleep(RATE_DELAY)
                stats = fetch_player_stats(s, pid, slug)
                print(f"  [{i+1:>3}/{len(targets_pl)}] id={pid:>5} slug={slug:20}  fields={len(stats)}")
                if args.record and stats:
                    execute_write("""
                        INSERT INTO cs2_hltv_player_stats (hltv_player_id, nickname, stats, fetched_at)
                        VALUES (%s, %s, %s::jsonb, NOW())
                        ON CONFLICT (hltv_player_id) DO UPDATE SET
                            nickname=EXCLUDED.nickname, stats=EXCLUDED.stats, fetched_at=NOW()
                    """, (pid, slug, json.dumps(stats)))
                    if st: st.tick_done()
                elif st:
                    st.tick_failed(f"no stats for {slug}")
        finally:
            if ctx: ctx.__exit__(None, None, None)

    snapshot = datetime.now(timezone.utc).date().isoformat()
    today = datetime.now(timezone.utc).date()
    one_year_ago = today.replace(year=today.year - 1)

    ctx = scraper_run("team_map_stats", "Per-team-per-map career win%") if (scraper_run and args.record and targets) else None
    st = ctx.__enter__() if ctx else None
    try:
        if st: st.set_total(len(targets))
        for i, (team_id, name, slug) in enumerate(targets):
            if not slug:
                if st: st.tick_failed(f"no slug for team {team_id}")
                continue
            if i > 0:
                time.sleep(RATE_DELAY)
            print(f"\n  → {name} (id={team_id})")
            wr, agg = fetch_team_maps_summary(s, team_id, slug,
                                              str(one_year_ago), str(today))
            if not wr:
                print(f"    no map data (cookies expired?)")
                if st: st.tick_failed(f"no_map_data {team_id}")
                continue
            clutch = agg.get("round_win_pct_after_first_kill")
            comeback = agg.get("round_win_pct_after_first_death")
            if clutch is not None or comeback is not None:
                print(f"    [aggregate] clutch={clutch}  comeback={comeback}")
            for mp, pct in wr.items():
                print(f"    {mp:10}  win% = {pct:5.1f}")
                if args.record:
                    # Denormalize team-aggregate clutch/comeback into each row,
                    # so cs2_hltv_team_map_stats has them populated for backtests.
                    execute_write("""
                        INSERT INTO cs2_hltv_team_map_stats
                            (hltv_team_id, team_name, map_name, win_pct,
                             round_win_pct_after_first_kill,
                             round_win_pct_after_first_death,
                             snapshot_date)
                        VALUES (%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (hltv_team_id, map_name, snapshot_date) DO UPDATE SET
                            win_pct=EXCLUDED.win_pct,
                            round_win_pct_after_first_kill=EXCLUDED.round_win_pct_after_first_kill,
                            round_win_pct_after_first_death=EXCLUDED.round_win_pct_after_first_death,
                            fetched_at=NOW()
                    """, (team_id, name, mp, pct, clutch, comeback, snapshot))
            if st: st.tick_done()
    finally:
        if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
