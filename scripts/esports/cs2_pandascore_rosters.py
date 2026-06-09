#!/usr/bin/env python3
"""
PandaScore CS2 roster fetcher.

For each team in cs2_upcoming_matches, query PandaScore /csgo/teams to get
current active 5-man lineup. Cached to data/esports/cs2/pandascore_rosters.json.

PandaScore free tier: 1000 req/hour — plenty for ~50 teams.

Usage:
    python3 scripts/esports/cs2_pandascore_rosters.py            # all DB teams
    python3 scripts/esports/cs2_pandascore_rosters.py --teams "Vitality,G2"
    python3 scripts/esports/cs2_pandascore_rosters.py --refresh  # ignore cache

API key in PANDASCORE_API_KEY env var.
"""
import argparse
import json
import os
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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

CACHE_FILE = Path("data/esports/cs2/pandascore_rosters.json")
API_BASE   = "https://api.pandascore.co"
RATE_DELAY = 0.25   # 1000/hr => 3.6s, but spread by 0.25s is safe


def _load_cache() -> dict:
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
    """Pull team names from ALL match sources, not just upcoming. This expands
    roster coverage from ~55 (top tournaments) to 500+ (incl. tier-3/4 teams
    that play in CCT, qualifiers, ESEA leagues, etc.). Required for v6+ models
    that need K/D per roster — Oxuji-style tier-3 teams were the original gap.
    """
    from workers.api_clients.db import execute_query
    sql = """
        SELECT t FROM (
          SELECT DISTINCT team1 AS t FROM cs2_upcoming_matches WHERE team1 IS NOT NULL
          UNION SELECT DISTINCT team2 FROM cs2_upcoming_matches WHERE team2 IS NOT NULL
          UNION SELECT DISTINCT team1 FROM cs2_results          WHERE team1 IS NOT NULL
          UNION SELECT DISTINCT team2 FROM cs2_results          WHERE team2 IS NOT NULL
          UNION SELECT DISTINCT team1_name FROM cs2_pandascore_matches WHERE team1_name IS NOT NULL
          UNION SELECT DISTINCT team2_name FROM cs2_pandascore_matches WHERE team2_name IS NOT NULL
          UNION SELECT DISTINCT team_name  FROM cs2_hltv_rankings WHERE team_name IS NOT NULL
        ) u
        WHERE LENGTH(t) > 0
    """
    try:
        rows = execute_query(sql, ())
        return sorted({r["t"] for r in rows if r.get("t")})
    except Exception as e:
        print(f"[!] DB query failed: {e}", file=sys.stderr)
        return []


# Manual aliases for teams whose names don't search cleanly.
_ALIASES: dict[str, list[str]] = {
    "navi":          ["Natus Vincere"],
    "natus vincere": ["Natus Vincere"],
    "vp":            ["Virtus.pro"],
    "spirit":        ["Team Spirit"],
    "liquid":        ["Team Liquid"],
    "team liquid":   ["Liquid"],
    "9z team":       ["9z"],
    "9z":            ["9z"],
    "imperial":      ["Imperial Esports"],
    "mongolz":       ["The MongolZ"],
    "the mongolz":   ["The MongolZ"],
    "oldboys pl":    ["Oldboys"],
    "xi":            ["XI Esports"],
    "aaa":           ["aAa", "Against All Authority"],
    "masked regime": ["Masked"],
}


def _fetch_one(key: str, params: dict) -> list:
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    r = requests.get(f"{API_BASE}/csgo/teams", headers=headers, params=params, timeout=15)
    if r.status_code == 429:
        print("  [!] rate-limited; sleeping 60s", file=sys.stderr)
        time.sleep(60)
        return _fetch_one(key, params)
    return r.json() if r.ok else []


def _search_team(key: str, name: str) -> dict | None:
    """Search PandaScore for the closest team match.

    Returns {name, players[]} or None on miss. Players are the active roster.
    Strategy:
      1. filter[acronym] (works for short uppercase names like BIG, NAVI)
      2. search[name] (works for the bulk of names)
      3. Manual aliases for known stragglers
    """
    target_lower = name.lower().strip()
    candidates: list[dict] = []

    # 1) acronym filter — best for short uppercase team names
    if len(name) <= 5 and name.upper() == name:
        candidates.extend(_fetch_one(key, {"filter[acronym]": name.upper(), "per_page": 5}))

    # 1b) Also try acronym for mixed-case short names (NaVi → NAVI)
    if not candidates and len(name) <= 5:
        candidates.extend(_fetch_one(key, {"filter[acronym]": name.upper(), "per_page": 5}))

    # 2) name search
    if not candidates:
        candidates.extend(_fetch_one(key, {"search[name]": name, "per_page": 10}))

    # 3) alias fallback — always try if we have a known alias, even if other
    # searches returned (junk) results
    if target_lower in _ALIASES:
        for alias in _ALIASES[target_lower]:
            extra = _fetch_one(key, {"search[name]": alias, "per_page": 5})
            candidates.extend(extra)
            if extra:
                break

    if not candidates:
        return None

    # Score: exact name match > acronym match > startswith > contains
    scored = []
    for t in candidates:
        t_name = (t.get("name") or "").lower().strip()
        acro = (t.get("acronym") or "").lower().strip()
        slug = (t.get("slug") or "").lower().strip()
        score = 0
        if t_name == target_lower:
            score = 100
        elif acro and acro == target_lower:
            score = 95
        elif t_name.startswith(target_lower):
            score = 80
        elif target_lower in t_name:
            score = 60
        elif target_lower in slug:
            score = 50
        scored.append((score, t))

    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < 50:
        return None

    best = scored[0][1]
    players = [
        {
            "id":          p.get("id"),
            "nickname":    p.get("name"),
            "role":        p.get("role"),
            "nationality": p.get("nationality"),
            "active":      p.get("active", True),
        }
        for p in (best.get("players") or [])
        if p.get("active") is not False
    ]
    return {
        "team":         best.get("name"),
        "id":           best.get("id"),
        "slug":         best.get("slug"),
        "acronym":      best.get("acronym"),
        "players":      players,
        "fetched_at":   datetime.now(timezone.utc).isoformat(),
        "match_query":  name,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--teams", help="Comma-separated list (default: all DB teams)")
    p.add_argument("--refresh", action="store_true",
                   help="Re-fetch teams already in cache (default: skip cached)")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after fetching N teams (0 = no limit). Useful for "
                        "incremental cron runs that respect PandaScore quota.")
    args = p.parse_args()

    key = os.getenv("PANDASCORE_API_KEY", "").strip()
    if not key:
        print("[!] PANDASCORE_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    teams = (
        [t.strip() for t in args.teams.split(",") if t.strip()]
        if args.teams else _teams_from_db()
    )
    if not teams:
        print("[!] no teams to fetch", file=sys.stderr); sys.exit(1)

    cache = {} if args.refresh else _load_cache()
    print(f"\n=== PandaScore Roster Fetch  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  {len(teams)} teams  | cache hits: {sum(1 for t in teams if t in cache)}\n")

    hits = miss = skipped = 0
    fetched_this_run = 0
    for i, name in enumerate(teams, 1):
        if not args.refresh and name in cache:
            skipped += 1
            continue
        if args.limit and fetched_this_run >= args.limit:
            print(f"  [limit] hit {args.limit}, stopping (re-run to continue)")
            break
        if i > 1:
            time.sleep(RATE_DELAY)
        fetched_this_run += 1
        roster = _search_team(key, name)
        if roster and roster["players"]:
            cache[name] = roster
            hits += 1
            ids = ", ".join(p["nickname"] for p in roster["players"][:5])
            tag = "✓" if roster["team"].lower() == name.lower() else "≈"
            print(f"  [{i:>3}/{len(teams)}] {tag} {name:25} → {roster['team']:25} {ids}")
        else:
            miss += 1
            print(f"  [{i:>3}/{len(teams)}]   {name:25}  no match")
        if i % 25 == 0:
            _save_cache(cache)  # checkpoint

    _save_cache(cache)
    print(f"\n  hits: {hits}  miss: {miss}  cache-hit (skipped): {skipped}")
    print(f"  → {CACHE_FILE}\n")


if __name__ == "__main__":
    main()
