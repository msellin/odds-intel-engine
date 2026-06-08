#!/usr/bin/env python3
"""
Fetch current CS2 rosters from Liquipedia (free, no auth).

Loads team names from cs2_upcoming_matches, queries Liquipedia for each team's
page, parses the active roster from wikitext, writes to a JSON cache.

The scanner picks up this cache to compute Player Quality (PQ) from current
lineups instead of the stale Oct 2025 CSV lineup.

Usage:
    python3 scripts/esports/cs2_liquipedia_rosters.py            # fetch all teams in upcoming matches
    python3 scripts/esports/cs2_liquipedia_rosters.py --teams "G2 Esports,FaZe"
    python3 scripts/esports/cs2_liquipedia_rosters.py --refresh  # ignore cache, re-fetch all

Liquipedia rate-limit policy: 1 req per 2s (https://liquipedia.net/api-terms-of-use).
"""
import argparse
import json
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

CACHE_FILE = Path("data/esports/cs2/liquipedia_rosters.json")
API = "https://liquipedia.net/counterstrike/api.php"
USER_AGENT = "OddsIntel CS2 Bot (sellinmargus@gmail.com) - research, polite rate"
RATE_LIMIT_SECONDS = 2.5  # > Liquipedia's 2s minimum

# Player template forms we recognise:
#   {{Person|id=huNter-|name=Nemanja Kovac|flag=ba|...}}
#   {{ActivePlayer|id=...}}
#   {{Player|id=...}}
# Inactive markers: inactive=true, leavedate=YYYY-MM-DD before today
_PERSON_RE = re.compile(r"\{\{(Person|ActivePlayer|Player)\b([^}]*)\}\}", re.IGNORECASE)
_ID_RE     = re.compile(r"\bid\s*=\s*([^|}]+)")
_INACTIVE_RE = re.compile(r"\binactive\s*=\s*(true|1|yes)", re.IGNORECASE)
_LEAVE_RE  = re.compile(r"\bleavedate\s*=\s*(\d{4}-\d{2}-\d{2})")
_ROLE_RE   = re.compile(r"\brole\s*=\s*([^|}]+)")
_FLAG_RE   = re.compile(r"\bflag\s*=\s*([^|}]+)")


def _slugify_team(name: str) -> str:
    """Liquipedia uses underscores for spaces."""
    return name.strip().replace(" ", "_")


def _fetch_wikitext(team: str) -> str | None:
    slug = _slugify_team(team)
    try:
        r = requests.get(
            API,
            params={"action": "parse", "format": "json", "page": slug, "prop": "wikitext"},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        if d.get("error"):
            return None
        return ((d.get("parse") or {}).get("wikitext") or {}).get("*")
    except Exception as e:
        print(f"  [!] {team}: fetch error {e}", file=sys.stderr)
        return None


def _find_active_section(wt: str) -> str:
    """Return the wikitext snippet that contains the active roster.

    Liquipedia CS pages typically have a "==Player Roster==" header with an
    "Active" sub-section. Fallback: full text (parser will still filter inactive).
    """
    # Look for "Active" sub-section header (any markup level)
    m = re.search(r"=+\s*Active(?:\s+Roster)?\s*=+", wt, re.IGNORECASE)
    if not m:
        # Fall back to the player roster section if present
        m = re.search(r"=+\s*Player Roster\s*=+", wt, re.IGNORECASE)
    if not m:
        return wt

    start = m.end()
    # Find the next same-or-higher level section
    end_m = re.search(r"\n=+\s*[A-Z]", wt[start:])
    end = start + end_m.start() if end_m else len(wt)
    return wt[start:end]


def _parse_active_players(wt: str) -> list[dict]:
    """Extract active player templates from a wikitext snippet."""
    today = datetime.now(timezone.utc).date()
    players: list[dict] = []
    seen_ids: set[str] = set()

    for m in _PERSON_RE.finditer(wt):
        block = m.group(2)
        id_m = _ID_RE.search(block)
        if not id_m:
            continue
        pid = id_m.group(1).strip()

        if _INACTIVE_RE.search(block):
            continue
        leave_m = _LEAVE_RE.search(block)
        if leave_m:
            try:
                leave_date = datetime.fromisoformat(leave_m.group(1)).date()
                if leave_date <= today:
                    continue
            except ValueError:
                pass

        # Skip role=coach/manager/analyst when present
        role_m = _ROLE_RE.search(block)
        role = (role_m.group(1).strip().lower() if role_m else "")
        if role in {"coach", "manager", "analyst", "owner", "director"}:
            continue

        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        flag_m = _FLAG_RE.search(block)
        players.append({
            "id": pid,
            "role": role or None,
            "flag": flag_m.group(1).strip() if flag_m else None,
        })

    return players[:6]  # cap defensively — active CS rosters are 5 + sometimes a 6th


def fetch_team_roster(team: str) -> dict | None:
    """Return {team, players: [...], fetched_at} or None on miss."""
    wt = _fetch_wikitext(team)
    if not wt:
        return None
    section = _find_active_section(wt)
    players = _parse_active_players(section)
    if not players:
        return None
    return {
        "team": team,
        "players": players,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _load_cache() -> dict[str, dict]:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _teams_from_db() -> list[str]:
    """Get team names from current cs2_upcoming_matches."""
    from workers.api_clients.db import execute_query
    try:
        rows = execute_query(
            "SELECT DISTINCT team1 AS t FROM cs2_upcoming_matches "
            "UNION SELECT DISTINCT team2 FROM cs2_upcoming_matches",
            (),
        )
        return sorted({r["t"] for r in rows if r.get("t")})
    except Exception as e:
        print(f"[!] DB query failed: {e}", file=sys.stderr)
        return []


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--teams", help="Comma-separated list of teams (default: pull from DB)")
    p.add_argument("--refresh", action="store_true", help="Ignore cache and re-fetch all teams")
    args = p.parse_args()

    if args.teams:
        teams = [t.strip() for t in args.teams.split(",") if t.strip()]
    else:
        teams = _teams_from_db()

    if not teams:
        print("[!] No teams to fetch", file=sys.stderr)
        sys.exit(1)

    cache = {} if args.refresh else _load_cache()
    print(f"\n=== Liquipedia Roster Fetch  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  {len(teams)} teams | cache hits: {sum(1 for t in teams if t in cache)}")
    print(f"  Rate: 1 req per {RATE_LIMIT_SECONDS}s\n")

    new_hits, miss, skipped = 0, 0, 0
    for i, team in enumerate(teams, 1):
        if not args.refresh and team in cache:
            skipped += 1
            continue
        time.sleep(RATE_LIMIT_SECONDS) if i > 1 else None
        roster = fetch_team_roster(team)
        if roster:
            cache[team] = roster
            new_hits += 1
            names = ", ".join(p["id"] for p in roster["players"][:5])
            print(f"  [{i:>3}/{len(teams)}] ✓ {team:30}  {names}")
        else:
            miss += 1
            print(f"  [{i:>3}/{len(teams)}]   {team:30}  no roster found")

        if i % 25 == 0:
            _save_cache(cache)  # checkpoint

    _save_cache(cache)
    print(f"\n  cached: {new_hits}  miss: {miss}  cache-hit (skipped): {skipped}")
    print(f"  → {CACHE_FILE}\n")


if __name__ == "__main__":
    main()
