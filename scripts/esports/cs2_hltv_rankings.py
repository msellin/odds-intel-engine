#!/usr/bin/env python3
"""
HLTV team rankings scraper.

Pulls the current HLTV world ranking (top-248 teams) with their points.
HLTV updates rankings weekly on Mondays. Used as a second strength signal
alongside our ELO — if ELO and HLTV diverge for a given team, both can be
inputs to a richer model.

Data shape: { team_name -> { rank, points, players } }
Stored in DB table cs2_hltv_rankings (one row per (team_name, snapshot_date)).

Usage:
    python3 scripts/esports/cs2_hltv_rankings.py            # dry run
    python3 scripts/esports/cs2_hltv_rankings.py --record   # write DB

Polite: 1 request per run, public URL, no auth.
"""
import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "--break-system-packages", "-q"])
    import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

URL = "https://www.hltv.org/ranking/teams"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

_BLOCK_RE = re.compile(
    r'<div class="ranked-team standard-box">(.*?)</div>\s*</div>\s*</div>\s*</div>\s*</div>',
    re.DOTALL,
)
_RANK_NAME_POINTS_RE = re.compile(
    r'<span class="position wide-position">#(\d+)</span>.*?'
    r'<span class="name">([^<]+)</span>'
    r'<span class="points">\((\d+)',
    re.DOTALL,
)
_PLAYER_RE = re.compile(r'<div class="rankingNicknames"><span>([^<]+)</span></div>')


def fetch_rankings() -> list[dict]:
    """Fetch and parse the current HLTV team ranking. Uses FlareSolverr when
    available (defeats CF challenges)."""
    text = None
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        if is_available():
            text = fs_fetch(URL, session="hltv_rankings")
    except ImportError:
        pass
    if not text:
        r = requests.get(URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
        text = r.text

    teams: list[dict] = []
    # Walk the page; for each ranked-team block extract rank/name/points/players
    for block in _BLOCK_RE.findall(text):
        rnp = _RANK_NAME_POINTS_RE.search(block)
        if not rnp:
            # Fallback to scanning the surrounding context if the block boundary is off
            continue
        rank = int(rnp.group(1))
        name = rnp.group(2).strip()
        points = int(rnp.group(3))
        players = [m.group(1).strip() for m in _PLAYER_RE.finditer(block)][:5]
        teams.append({"rank": rank, "name": name, "points": points, "players": players})

    # The block regex may miss some entries; fall back to a flat scan
    if len(teams) < 50:
        teams = []
        for m in _RANK_NAME_POINTS_RE.finditer(text):
            rank = int(m.group(1))
            name = m.group(2).strip()
            points = int(m.group(3))
            teams.append({"rank": rank, "name": name, "points": points, "players": []})

    return teams


def write_to_db(teams: list[dict]) -> int:
    from workers.api_clients.db import execute_write
    snapshot = datetime.now(timezone.utc).date().isoformat()
    n = 0
    for t in teams:
        execute_write("""
            INSERT INTO cs2_hltv_rankings
                (team_name, hltv_rank, hltv_points, players, snapshot_date)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (team_name, snapshot_date) DO UPDATE SET
                hltv_rank   = EXCLUDED.hltv_rank,
                hltv_points = EXCLUDED.hltv_points,
                players     = EXCLUDED.players
        """, (t["name"], t["rank"], t["points"], t.get("players") or None, snapshot))
        n += 1
    return n


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--record", action="store_true", help="Write to cs2_hltv_rankings")
    args = p.parse_args()

    print(f"\n=== HLTV Rankings  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    teams = fetch_rankings()
    print(f"  {len(teams)} teams parsed (top {min(15, len(teams))}):")
    for t in teams[:15]:
        line = f"  #{t['rank']:>3}  {t['name']:25}  {t['points']:>4} pts"
        if t.get("players"):
            line += f"  · {', '.join(t['players'])}"
        print(line)

    if args.record:
        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent))
            from scraper_state import scraper_run  # type: ignore
            with scraper_run("hltv_rankings", "HLTV top-30/248 weekly rank") as st:
                st.set_total(len(teams))
                n = write_to_db(teams)
                for _ in range(n):
                    st.tick_done()
                st.note(f"latest snapshot {len(teams)} teams")
                print(f"\n  wrote {n} rows to cs2_hltv_rankings")
        except ImportError:
            n = write_to_db(teams)
            print(f"\n  wrote {n} rows to cs2_hltv_rankings")


if __name__ == "__main__":
    main()
