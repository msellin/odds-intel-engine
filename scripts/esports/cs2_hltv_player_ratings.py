#!/usr/bin/env python3
"""
HLTV per-player Rating 3.0 scraper.

Fetches HLTV's current per-player rating from /player/{id}/{nickname} pages.
This solves the PQ-staleness problem — the CSV we shipped with is Oct 2025;
this gives us live current ratings.

Strategy:
  1. Discover player IDs via /players/archive/active (one cheap page).
  2. For each player we care about (default: those on a team in our HLTV
     top-100 ranking), fetch their page and extract Rating 3.0.
  3. UPSERT to cs2_hltv_player_ratings (one row per player, latest wins).

Rate limited: 3.5s between requests. Polite to HLTV.

Usage:
    python3 scripts/esports/cs2_hltv_player_ratings.py --discover     # build player ID list
    python3 scripts/esports/cs2_hltv_player_ratings.py --top 100      # ratings for top-100 teams' rosters
    python3 scripts/esports/cs2_hltv_player_ratings.py --record       # write to DB
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

CACHE_DIR = Path("data/esports/cs2")
PLAYER_ID_CACHE = CACHE_DIR / "hltv_player_ids.json"
PLAYER_RATINGS_CACHE = CACHE_DIR / "hltv_player_ratings.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}
RATE_DELAY = 3.5  # seconds between requests

# /player/{id}/{slug}  e.g. /player/7998/zywoo
# Reject anything with #/?/space inside the slug — those are tab anchors like
# "/player/123/zywoo#tab-faceitBox" we don't want as separate IDs.
_PLAYER_LINK_RE = re.compile(r'href="/player/(\d+)/([^"\'/#?\s]+)"')
# <b>Rating 3.0</b><span class="statsVal"><p>1.03</p>
_RATING_RE = re.compile(
    r'<b>Rating\s+3\.0</b>\s*<span class="statsVal">\s*<p>(\d\.\d{1,3})</p>',
    re.DOTALL,
)
# Fallback for old Rating 2.x pages
_RATING_FALLBACK_RE = re.compile(
    r'<b>Rating\s+2\.\d+</b>\s*<span class="statsVal">\s*<p>(\d\.\d{1,3})</p>',
    re.DOTALL,
)


def discover_player_ids() -> dict[str, int]:
    """27 page hits — symbol + A..Z. Returns {nickname_lower: player_id}.

    HLTV paginates /players/archive/active by first character of nickname.
    The landing page only shows ~50 names; we iterate every filter to capture
    the full active roster (~1,300 players).
    """
    filters = ["symbol"] + [chr(ord("A") + i) for i in range(26)]
    out: dict[str, int] = {}
    for i, f in enumerate(filters):
        if i > 0:
            time.sleep(2.0)  # polite, lighter than the player-page rate
        url = f"https://www.hltv.org/players/archive/active?filter={f}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                print(f"  [!] {f}: {r.status_code}", file=sys.stderr)
                continue
            before = len(out)
            for pid, slug in _PLAYER_LINK_RE.findall(r.text):
                out.setdefault(slug.lower(), int(pid))
            print(f"  filter={f:>6}  +{len(out) - before:>4} new  (total {len(out)})")
        except Exception as e:
            print(f"  [!] {f}: {e}", file=sys.stderr)
    return out


def fetch_player_rating(pid: int, slug: str) -> float | None:
    url = f"https://www.hltv.org/player/{pid}/{slug}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        m = _RATING_RE.search(r.text) or _RATING_FALLBACK_RE.search(r.text)
        return float(m.group(1)) if m else None
    except Exception as e:
        print(f"    [!] {slug}: {e}", file=sys.stderr)
        return None


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _players_to_fetch(top: int) -> list[tuple[int, str]]:
    """Read cs2_hltv_rankings for top-N teams, return their roster players."""
    from workers.api_clients.db import execute_query
    rows = execute_query("""
        SELECT DISTINCT ON (team_name) team_name, players
        FROM cs2_hltv_rankings
        WHERE hltv_rank <= %s
        ORDER BY team_name, snapshot_date DESC
    """, (top,))
    all_players: set[str] = set()
    for r in rows:
        for p in (r.get("players") or []):
            all_players.add(p.lower())

    # Resolve player names to IDs
    ids = _load_json(PLAYER_ID_CACHE)
    if not ids:
        print("  [!] no player ID cache — run with --discover first", file=sys.stderr)
        return []
    out = []
    for name in sorted(all_players):
        if name in ids:
            out.append((ids[name], name))
    print(f"  {len(out)} resolved / {len(all_players)} unique players in top-{top} rosters")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--discover", action="store_true", help="Refresh player-name → ID map from /players/archive/active")
    p.add_argument("--top", type=int, default=100, help="Fetch ratings for top-N teams' rosters")
    p.add_argument("--record", action="store_true", help="Write to cs2_hltv_player_ratings")
    p.add_argument("--limit", type=int, help="Cap players fetched (debug)")
    args = p.parse_args()

    if args.discover:
        print("Discovering player IDs from /players/archive/active...")
        ids = discover_player_ids()
        _save_json(PLAYER_ID_CACHE, ids)
        print(f"  cached {len(ids)} player IDs to {PLAYER_ID_CACHE}")
        return

    print(f"\n=== HLTV Player Ratings  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    targets = _players_to_fetch(args.top)
    if not targets:
        print("  no players to fetch — try --discover first")
        return
    if args.limit:
        targets = targets[:args.limit]
    print(f"  fetching ratings for {len(targets)} players (~{len(targets) * RATE_DELAY / 60:.0f} min)")

    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent))
        from scraper_state import scraper_run  # type: ignore
    except ImportError:
        scraper_run = None  # type: ignore

    cache = _load_json(PLAYER_RATINGS_CACHE)
    ctx = scraper_run("player_ratings", "HLTV Rating 2.1 per player (3-month rolling)") if (scraper_run and args.record) else None
    st = ctx.__enter__() if ctx else None
    if st: st.set_total(len(targets))
    hits, miss = 0, 0
    for i, (pid, slug) in enumerate(targets, 1):
        if i > 1:
            time.sleep(RATE_DELAY)
        rating = fetch_player_rating(pid, slug)
        if rating is not None:
            cache[slug] = {
                "id": pid,
                "rating": rating,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            hits += 1
            print(f"  [{i:>3}/{len(targets)}] ✓ {slug:25} → {rating:.3f}")
            if args.record:
                from workers.api_clients.db import execute_write
                execute_write("""
                    INSERT INTO cs2_hltv_player_ratings (hltv_player_id, nickname, rating, fetched_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (hltv_player_id) DO UPDATE SET
                        nickname   = EXCLUDED.nickname,
                        rating     = EXCLUDED.rating,
                        fetched_at = NOW()
                """, (pid, slug, rating))
                if st: st.tick_done()
        else:
            miss += 1
            print(f"  [{i:>3}/{len(targets)}]   {slug:25}  rating not parsed")
            if st: st.tick_failed(f"no_rating {slug}")
        if i % 25 == 0:
            _save_json(PLAYER_RATINGS_CACHE, cache)  # checkpoint

    _save_json(PLAYER_RATINGS_CACHE, cache)
    print(f"\n  hits: {hits}  miss: {miss}  → {PLAYER_RATINGS_CACHE}\n")
    if ctx: ctx.__exit__(None, None, None)


if __name__ == "__main__":
    main()
