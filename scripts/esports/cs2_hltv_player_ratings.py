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


def discover_player_ids(max_pages_per_letter: int = 30) -> dict[str, int]:
    """Iterate every filter letter AND paginate within each letter.

    HLTV paginates /players/archive/active by first character (filter)
    AND ~50 results per page (?page=N). We walk each letter's pages until
    a page returns no new IDs.

    Uses FlareSolverr when available (CF-protected on EU IPs).
    """
    # Local imports — keep top-level lightweight
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        use_fs = is_available()
    except ImportError:
        use_fs, fs_fetch = False, None

    filters = ["symbol"] + [chr(ord("A") + i) for i in range(26)]
    out: dict[str, int] = {}

    def _get_html(url: str) -> str | None:
        if use_fs:
            return fs_fetch(url, session="hltv_archive")
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return None
            return r.text
        except Exception:
            return None

    for fi, f in enumerate(filters):
        if fi > 0 and not use_fs:
            time.sleep(2.0)  # FlareSolverr enforces its own throttle
        letter_added = 0
        for page in range(1, max_pages_per_letter + 1):
            url = f"https://www.hltv.org/players/archive/active?filter={f}&page={page}"
            html = _get_html(url)
            if not html:
                print(f"  [!] filter={f} page={page}: no html", file=sys.stderr)
                break
            before = len(out)
            for pid, slug in _PLAYER_LINK_RE.findall(html):
                out.setdefault(slug.lower(), int(pid))
            added = len(out) - before
            letter_added += added
            if added == 0:
                # Either the page is empty or every player was already seen
                # from a previous page → end of this letter
                break
            if page == 1:
                print(f"  filter={f:>6} page={page:>2}  +{added:>4} new  (total {len(out)})")
            else:
                print(f"            page={page:>2}  +{added:>4} new  (letter total {letter_added}, grand {len(out)})")
    return out


def fetch_player_rating(pid: int, slug: str) -> float | None:
    url = f"https://www.hltv.org/player/{pid}/{slug}"
    text = None
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        if is_available():
            text = fs_fetch(url, session="hltv_ratings")
    except ImportError:
        pass
    if not text:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return None
            text = r.text
        except Exception as e:
            print(f"    [!] {slug}: {e}", file=sys.stderr)
            return None
    m = _RATING_RE.search(text) or _RATING_FALLBACK_RE.search(text)
    return float(m.group(1)) if m else None


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
    """Collect players to fetch. Two sources, in order:
      1) HLTV /ranking/teams.players (currently NULL — column never populated)
      2) PandaScore rosters cache (~950 teams) — primary source today.
    Intersect against hltv_player_ids cache to filter to scrapable IDs.
    --top caps at the first N players alphabetically (0 = no cap).
    """
    from workers.api_clients.db import execute_query

    # Source 1: HLTV rankings.players (legacy — null for now).
    rows = execute_query("""
        SELECT DISTINCT ON (team_name) team_name, players
        FROM cs2_hltv_rankings
        WHERE hltv_rank <= %s
        ORDER BY team_name, snapshot_date DESC
    """, (top if top > 0 else 9999,))
    all_players: set[str] = set()
    for r in rows:
        for p in (r.get("players") or []):
            all_players.add(p.lower())

    # Source 2: PandaScore rosters cache.
    ps_path = Path(__file__).resolve().parents[2] / "data/esports/cs2/pandascore_rosters.json"
    ps_count = 0
    if ps_path.exists():
        try:
            ps = json.loads(ps_path.read_text())
            for team_name, payload in ps.items():
                players = payload.get("players") if isinstance(payload, dict) else payload
                if not players:
                    continue
                for p in players:
                    nick = (p.get("nickname") if isinstance(p, dict) else None) or ""
                    if nick:
                        all_players.add(nick.lower())
                        ps_count += 1
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [!] rosters cache read error: {e}", file=sys.stderr)
    print(f"  rosters: hltv_rankings={sum(1 for r in rows if r.get('players'))} teams, "
          f"pandascore={ps_count} nicknames")

    # Resolve nicknames → HLTV IDs via the discover cache.
    ids = _load_json(PLAYER_ID_CACHE)
    if not ids:
        print("  [!] no player ID cache — run with --discover first", file=sys.stderr)
        return []
    out = []
    for name in sorted(all_players):
        if name in ids:
            out.append((ids[name], name))
    if top > 0 and len(out) > top:
        out = out[:top]
    print(f"  {len(out)} resolved / {len(all_players)} unique players (cap: {top or 'no'})")
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
