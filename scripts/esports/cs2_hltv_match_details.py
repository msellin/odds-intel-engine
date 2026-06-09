#!/usr/bin/env python3
"""
HLTV match-detail scraper.

Pulls /matches/{id}/{slug} pages and parses:
  - Maps played + per-map scores
  - Veto sequence (pick / ban / left over)
  - Per-player match stats (K/D, ADR, KAST, Rating)
  - Event + stage + best-of + date

Writes to cs2_hltv_matches / cs2_hltv_match_maps / cs2_hltv_match_veto /
cs2_hltv_player_match_stats.

Polite — 8s between requests. Each page is ~400 KB.

Usage:
    python3 scripts/esports/cs2_hltv_match_details.py --queue          # pull /results, queue new IDs
    python3 scripts/esports/cs2_hltv_match_details.py --process N      # fetch + parse N queued matches
    python3 scripts/esports/cs2_hltv_match_details.py --match-id 2395247 --slug club-333-vs-chicken-coop-... # one-off
"""
import argparse
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
from workers.api_clients.db import execute_write, execute_query

RATE_DELAY = 8.0   # be polite — keeps us at ~7 req/min
RESULTS_URL = "https://www.hltv.org/results"
MATCH_URL_FMT = "https://www.hltv.org/matches/{mid}/{slug}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none",
}

# /matches/{id}/{slug}  e.g. /matches/2395247/club-333-vs-chicken-coop-...
_RESULT_LINK_RE = re.compile(r'href="/matches/(\d+)/([^"\'/#?\s]+)"')
_TEAM_NAME_RE   = re.compile(r'class="teamName team">([^<]+)</a>')
_EVENT_RE       = re.compile(r'class="event[^"]*"[^>]*>.*?<span>([^<]+)</span>', re.DOTALL)
# "Best of 3 (Online)" or "Best of 5 (LAN)..."
_BO_LINE_RE     = re.compile(r'Best of\s+(\d+)\s*\(([^)]+)\)')
_DATE_UNIX_RE   = re.compile(r'data-unix="(\d+)"')

# Map section: mapholder block per map
_MAPHOLDER_RE = re.compile(r'<div class="mapholder">(.*?)</div>\s*</div>\s*</div>', re.DOTALL)
_MAPNAME_RE   = re.compile(r'<div class="mapname">([^<]+)</div>')
_MAP_SCORE_RE = re.compile(r'<div class="results-team-score[^"]*">\s*(\d+)\s*</div>')

# Veto lines: "1. Team X removed Map" / picked / left over.
# We scan the whole HTML for these — there's nothing else that follows the
# "N. <team> <verb> <map>" pattern, so no need to box the section first.
_VETO_LINE_RE = re.compile(
    r'<div>\s*(\d+)\.\s+([^<]+?)\s+(removed|picked|was left over)\s*([^<]*)</div>'
)

# Player stats table — "totalstats" is the wrapped stats block
_TOTALSTATS_RE = re.compile(r'<table[^>]*class="totalstats"[^>]*>(.*?)</table>', re.DOTALL)
_PLAYER_ROW_RE = re.compile(
    r'<td class="players"[^>]*>.*?'
    r'<a href="/player/(\d+)/[^"]+"[^>]*>([^<]+)</a>.*?'
    r'<td class="kd[^"]*"[^>]*>(\d+-\d+)</td>',
    re.DOTALL,
)


def _fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"  [!] {url[-60:]} status={r.status_code}", file=sys.stderr)
            return None
        return r.text
    except Exception as e:
        print(f"  [!] {url[-60:]} {e}", file=sys.stderr)
        return None


def fetch_results_listing(offset: int = 0) -> list[tuple[int, str]]:
    """Return [(match_id, slug)] from /results page. offset advances pagination."""
    url = RESULTS_URL if offset == 0 else f"{RESULTS_URL}?offset={offset}"
    html = _fetch(url)
    if not html:
        return []
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for mid, slug in _RESULT_LINK_RE.findall(html):
        mid = int(mid)
        if mid in seen:
            continue
        seen.add(mid)
        out.append((mid, slug))
    return out


def queue_new_matches(limit_pages: int = 1) -> int:
    """Walk /results pages, INSERT new (match_id, slug) into the queue."""
    total = 0
    for page in range(limit_pages):
        if page > 0:
            time.sleep(RATE_DELAY)
        offset = page * 100
        rows = fetch_results_listing(offset)
        if not rows:
            break
        for mid, slug in rows:
            execute_write("""
                INSERT INTO cs2_hltv_match_queue (hltv_match_id, slug)
                VALUES (%s, %s)
                ON CONFLICT (hltv_match_id) DO NOTHING
            """, (mid, slug))
            total += 1
        print(f"  page {page} (offset={offset}): {len(rows)} candidates")
    return total


def parse_match(html: str) -> dict | None:
    """Extract everything from a single match page. Returns None on parse failure."""
    teams = _TEAM_NAME_RE.findall(html)
    if len(teams) < 2:
        return None
    team1, team2 = teams[0].strip(), teams[1].strip()

    # Event
    event_m = _EVENT_RE.search(html)
    event = event_m.group(1).strip() if event_m else None

    # Best-of + LAN/Online (from veto-box's preamble or stage tag)
    bo, lan = None, None
    bo_m = _BO_LINE_RE.search(html)
    if bo_m:
        bo = int(bo_m.group(1))
        lan = "lan" in bo_m.group(2).lower()

    # Match date (unix ms or seconds)
    date_iso = None
    date_m = _DATE_UNIX_RE.search(html)
    if date_m:
        ts = int(date_m.group(1))
        if ts > 10**12:
            ts //= 1000
        date_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    # Maps + scores per map
    maps = []
    for holder in _MAPHOLDER_RE.findall(html):
        name_m = _MAPNAME_RE.search(holder)
        scores = _MAP_SCORE_RE.findall(holder)
        if not name_m:
            continue
        s1 = int(scores[0]) if scores else None
        s2 = int(scores[1]) if len(scores) > 1 else None
        if not s1 and not s2:
            # Maps that weren't played still appear as "Not played" or 0-0
            continue
        winner = team1 if (s1 or 0) > (s2 or 0) else team2 if (s2 or 0) > (s1 or 0) else None
        maps.append({"name": name_m.group(1).strip(), "team1_score": s1, "team2_score": s2, "winner": winner})

    # Series score = sum of map wins
    score1 = sum(1 for m in maps if m["winner"] == team1)
    score2 = sum(1 for m in maps if m["winner"] == team2)
    winner_name = team1 if score1 > score2 else team2 if score2 > score1 else None

    # Veto sequence — scan all "N. <team> <verb> <map>" lines in the HTML.
    veto = []
    for step, who, action_phrase, map_part in _VETO_LINE_RE.findall(html):
        action = "left_over" if "left over" in action_phrase else action_phrase.lower()
        if action == "left_over":
            # "Nuke was left over" → who="Nuke", map_part=""
            map_name = who.strip()
            team_name = ""
        else:
            map_name = map_part.strip()
            team_name = who.strip()
        veto.append({"step": int(step), "team": team_name, "action": action, "map": map_name})

    # Per-player stats: parse only Rating (other cols too noisy across versions)
    players = []
    for ts_block in _TOTALSTATS_RE.findall(html):
        # rating-like floats per row
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", ts_block, re.DOTALL):
            pm = re.search(
                r'<a href="/player/(\d+)/[^"]+"[^>]*>([^<]+)</a>'
                r'.*?<td[^>]*class="rating[^"]*"[^>]*>(\d\.\d{1,3})</td>',
                row, re.DOTALL,
            )
            if pm:
                players.append({
                    "id": int(pm.group(1)),
                    "nickname": pm.group(2).strip(),
                    "rating": float(pm.group(3)),
                })

    return {
        "team1": team1, "team2": team2,
        "event": event, "best_of": bo, "is_lan": lan, "date": date_iso,
        "score1": score1, "score2": score2, "winner": winner_name,
        "maps": maps, "veto": veto, "players": players,
    }


def write_match(mid: int, slug: str, parsed: dict) -> None:
    url = MATCH_URL_FMT.format(mid=mid, slug=slug)
    execute_write("""
        INSERT INTO cs2_hltv_matches (hltv_match_id, event_name, stage, match_date,
            team1_name, team2_name, score1, score2, winner_name, best_of, raw_url, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (hltv_match_id) DO UPDATE SET
            event_name = EXCLUDED.event_name, stage = EXCLUDED.stage,
            match_date = EXCLUDED.match_date,
            team1_name = EXCLUDED.team1_name, team2_name = EXCLUDED.team2_name,
            score1 = EXCLUDED.score1, score2 = EXCLUDED.score2,
            winner_name = EXCLUDED.winner_name, best_of = EXCLUDED.best_of,
            raw_url = EXCLUDED.raw_url, fetched_at = NOW()
    """, (
        mid, parsed["event"], "LAN" if parsed.get("is_lan") else "Online",
        parsed["date"], parsed["team1"], parsed["team2"],
        parsed["score1"], parsed["score2"], parsed["winner"],
        parsed["best_of"], url,
    ))

    # Wipe + insert child rows (idempotent)
    execute_write("DELETE FROM cs2_hltv_match_maps          WHERE hltv_match_id = %s", (mid,))
    execute_write("DELETE FROM cs2_hltv_match_veto          WHERE hltv_match_id = %s", (mid,))
    execute_write("DELETE FROM cs2_hltv_player_match_stats  WHERE hltv_match_id = %s", (mid,))

    for i, m in enumerate(parsed["maps"], 1):
        execute_write("""
            INSERT INTO cs2_hltv_match_maps (hltv_match_id, map_order, map_name,
                team1_score, team2_score, winner_name)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (mid, i, m["name"], m["team1_score"], m["team2_score"], m["winner"]))

    for v in parsed["veto"]:
        execute_write("""
            INSERT INTO cs2_hltv_match_veto (hltv_match_id, step, team_name, action, map_name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (mid, v["step"], v["team"], v["action"], v["map"]))

    for p in parsed["players"]:
        execute_write("""
            INSERT INTO cs2_hltv_player_match_stats (hltv_match_id, hltv_player_id, nickname, rating)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (mid, p["id"], p["nickname"], p["rating"]))


def process_queue(limit: int) -> tuple[int, int]:
    rows = execute_query("""
        SELECT hltv_match_id, slug FROM cs2_hltv_match_queue
        WHERE fetched_at IS NULL AND error IS NULL
        ORDER BY discovered_at
        LIMIT %s
    """, (limit,))
    hits = miss = 0
    for i, r in enumerate(rows):
        mid, slug = r["hltv_match_id"], r["slug"]
        if i > 0:
            time.sleep(RATE_DELAY)
        url = MATCH_URL_FMT.format(mid=mid, slug=slug)
        html = _fetch(url)
        if not html:
            execute_write("UPDATE cs2_hltv_match_queue SET error = %s WHERE hltv_match_id = %s",
                          ("fetch_failed", mid))
            miss += 1
            continue
        parsed = parse_match(html)
        if not parsed:
            execute_write("UPDATE cs2_hltv_match_queue SET error = %s WHERE hltv_match_id = %s",
                          ("parse_failed", mid))
            miss += 1
            print(f"  [!] {mid} parse failed")
            continue
        write_match(mid, slug, parsed)
        execute_write("UPDATE cs2_hltv_match_queue SET fetched_at = NOW() WHERE hltv_match_id = %s",
                      (mid,))
        hits += 1
        veto_n = len(parsed["veto"])
        print(f"  [{i+1:>3}/{len(rows)}] ✓ {parsed['team1']:18} vs {parsed['team2']:18}  "
              f"{parsed['score1']}-{parsed['score2']}  veto={veto_n}  maps={len(parsed['maps'])}  players={len(parsed['players'])}")
    return hits, miss


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--queue", action="store_true", help="Pull /results, queue new match IDs")
    p.add_argument("--pages", type=int, default=1, help="How many /results pages to walk (for --queue)")
    p.add_argument("--process", type=int, default=0, help="Process N queued matches")
    p.add_argument("--match-id", type=int, help="One-off: fetch + parse this match ID")
    p.add_argument("--slug", default="", help="Slug for --match-id")
    args = p.parse_args()

    print(f"\n=== HLTV match details  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    if args.queue:
        n = queue_new_matches(limit_pages=args.pages)
        print(f"  queued {n} candidates from {args.pages} page(s)")

    if args.process:
        hits, miss = process_queue(args.process)
        print(f"\n  fetched: {hits}  failed: {miss}")

    if args.match_id and args.slug:
        url = MATCH_URL_FMT.format(mid=args.match_id, slug=args.slug)
        html = _fetch(url)
        parsed = parse_match(html) if html else None
        if parsed:
            print(f"  parsed: {parsed['team1']} vs {parsed['team2']}  score={parsed['score1']}-{parsed['score2']}")
            print(f"  veto: {len(parsed['veto'])} steps  maps: {len(parsed['maps'])}  players: {len(parsed['players'])}")
            write_match(args.match_id, args.slug, parsed)
            print(f"  ✓ wrote match {args.match_id}")
        else:
            print(f"  ✗ failed to parse match {args.match_id}")


if __name__ == "__main__":
    main()
