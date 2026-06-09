"""
CS2 HLTV team rosters scraper.

Reads each team's /team/{id}/{slug} page (NO Cloudflare auth needed) and
extracts the current 5-player roster + days_in_team + rating from the
roster-timeline tooltip blocks.

Use case: detect roster freshness. A team with avg days_in_team < 30 has had
a recent lineup change and its team_map_stats from before the change are
unreliable.

Writes to cs2_hltv_team_rosters (one row per (team, player, snapshot_date)).

Run:
    python3 scripts/esports/cs2_hltv_rosters.py --top-n 100 --record
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
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


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
}
RATE_DELAY = 5  # seconds between team pages

# The team page has a roster-timeline-tooltip block per current player.
# Each block contains: player link, name, then stat-rows with
# "Days in team", "Maps played", "Rating 2.0".
_TOOLTIP_RE = re.compile(
    r'class="roster-timeline-tooltip">(.*?)<div class="stats">(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)
_PLAYER_LINK_RE = re.compile(r'href="/player/(\d+)/([^"]+)"')
_STAT_ROW_RE = re.compile(
    r'<b class="stat-value">([^<]+)</b><br><span class="stat-name">([^<]+)</span>'
)


def parse_team_roster(html: str) -> list[dict]:
    """Return [{player_id, nickname, days_in_team, maps_played, rating}, ...]."""
    rows: list[dict] = []
    for m in _TOOLTIP_RE.finditer(html):
        header, stats_block = m.group(1), m.group(2)
        link_m = _PLAYER_LINK_RE.search(header)
        if not link_m:
            continue
        pid = int(link_m.group(1))
        slug = link_m.group(2)
        out: dict = {"player_id": pid, "nickname": slug}
        for value, label in _STAT_ROW_RE.findall(stats_block):
            L = label.strip().lower()
            v = value.strip()
            if "days in team" == L:
                try: out["days_in_team"] = int(v.replace(",", ""))
                except ValueError: pass
            elif "maps played" == L:
                try: out["maps_played"] = int(v.replace(",", ""))
                except ValueError: pass
            elif "rating 2.0" == L:
                try: out["rating_2_0"] = float(v)
                except ValueError: pass
        rows.append(out)
    # First 5 are the current roster (later tooltips are sometimes stat panels).
    return rows[:5]


def fetch_team_roster(team_id: int, slug: str) -> list[dict]:
    url = f"https://www.hltv.org/team/{team_id}/{slug}"
    # Try FlareSolverr first (defeats CF), fall back to plain requests
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        if is_available():
            html = fs_fetch(url, session="hltv_rosters")
            return parse_team_roster(html) if html else []
    except ImportError:
        pass
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        print(f"  [!] {team_id} {slug}: {e}", file=sys.stderr)
        return []
    if not r.ok:
        print(f"  [!] {team_id} {slug}: status {r.status_code}", file=sys.stderr)
        return []
    return parse_team_roster(r.text)


def load_targets(top_n: int) -> list[tuple[int, str, str]]:
    """Read top-N teams from cs2_hltv_rankings + their slug from team-map-stats.
    Returns [(team_id, team_name, slug), ...]."""
    # Join rankings to team_map_stats which has the slug we need (via team_id)
    rows = execute_query("""
        SELECT DISTINCT ms.hltv_team_id, ms.team_name
        FROM cs2_hltv_team_map_stats ms
        WHERE ms.hltv_team_id IS NOT NULL
        ORDER BY ms.team_name
        LIMIT %s
    """, (top_n,))
    # Slug: lowercase team name with non-alphanumeric replaced by -, stripped
    out = []
    for r in rows:
        slug = re.sub(r"[^a-z0-9]+", "-", r["team_name"].lower()).strip("-")
        out.append((r["hltv_team_id"], r["team_name"], slug))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top-n", type=int, default=100, help="Number of teams to scrape")
    p.add_argument("--team", type=int, help="One-off: just this team_id")
    p.add_argument("--slug", default="", help="Slug for --team")
    p.add_argument("--record", action="store_true", help="Write to DB")
    args = p.parse_args()

    print(f"\n=== HLTV team rosters  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    if args.team and args.slug:
        targets = [(args.team, args.slug, args.slug)]
    else:
        targets = load_targets(args.top_n)
    print(f"  {len(targets)} teams to scrape")

    ctx = scraper_run("team_rosters", "Current rosters + days_in_team per player") if (scraper_run and args.record) else None
    st = ctx.__enter__() if ctx else None
    if st: st.set_total(len(targets))

    snapshot = datetime.now(timezone.utc).date().isoformat()
    hits = miss = 0
    for i, (team_id, name, slug) in enumerate(targets):
        if i > 0:
            time.sleep(RATE_DELAY)
        roster = fetch_team_roster(team_id, slug)
        if not roster:
            print(f"  [{i+1:>3}/{len(targets)}] ! {name}  no roster parsed")
            if st: st.tick_failed(f"no_roster {name}")
            miss += 1
            continue
        avg_days = sum(p.get("days_in_team", 0) for p in roster) / max(len(roster), 1)
        print(f"  [{i+1:>3}/{len(targets)}] ✓ {name:25} {len(roster)} players  avg_days_in_team={avg_days:.0f}")
        if args.record:
            for p in roster:
                execute_write("""
                    INSERT INTO cs2_hltv_team_rosters
                        (hltv_team_id, team_name, hltv_player_id, nickname,
                         days_in_team, maps_played, rating_2_0, snapshot_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (hltv_team_id, hltv_player_id, snapshot_date)
                    DO UPDATE SET days_in_team = EXCLUDED.days_in_team,
                                  maps_played = EXCLUDED.maps_played,
                                  rating_2_0 = EXCLUDED.rating_2_0,
                                  fetched_at = NOW()
                """, (team_id, name, p["player_id"], p["nickname"],
                      p.get("days_in_team"), p.get("maps_played"),
                      p.get("rating_2_0"), snapshot))
            if st: st.tick_done()
        hits += 1

    print(f"\n  hits: {hits}  miss: {miss}")
    if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
