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
from workers.api_clients.db import execute_write, execute_query

RATE_DELAY = 0.5   # FlareSolverr's browser navigation already paces us
                   # (~3-5s per page). 0.5s + FS response ≈ 4-5s/match ≈
                   # 13-15 matches/min. Previous 2s was overkill — FS itself
                   # is the natural rate-limiter against HLTV's CF.
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
# Team names appear many times on a match page — restrict to the header pair.
# Pattern: standard-box teamsBox contains both teams' main names.
_TEAMSBOX_RE    = re.compile(
    r'<div class="standard-box teamsBox">(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL,
)
_TEAM_NAME_RE   = re.compile(r'class="teamName team">([^<]+)</a>')
_EVENT_RE       = re.compile(r'href="/events/(\d+)/[^"]+"[^>]*>([^<]+)</a>')
_BO_LINE_RE     = re.compile(r'Best of\s+(\d+)\s*\(([^)]+)\)')
_DATE_UNIX_RE   = re.compile(r'data-unix="(\d+)"')

# Per-map section: each map starts with <div class="mapname">X</div> and runs until
# the next mapname or the end of the maps section.
_MAP_BLOCK_RE = re.compile(
    r'<div class="mapname">([^<]+)</div>(.*?)(?=<div class="mapname">|<div class="lineups|<div class="standard-box stats-container"|\Z)',
    re.DOTALL,
)
_MAP_SCORE_RE = re.compile(r'<div class="results-team-score[^"]*">\s*(\d+)\s*</div>')
# Halftime per-side scores: <span class="ct">N</span><span>:</span><span class="t">M</span>
_HALF_SCORE_RE = re.compile(
    r'<span class="(ct|t)">\s*(\d+)\s*</span>\s*<span[^>]*>:</span>\s*<span class="(ct|t)">\s*(\d+)\s*</span>'
)

# Veto lines: "1. Team X removed Map" / picked / left over.
# We scan the whole HTML for these — there's nothing else that follows the
# "N. <team> <verb> <map>" pattern, so no need to box the section first.
_VETO_LINE_RE = re.compile(
    r'<div>\s*(\d+)\.\s+([^<]+?)\s+(removed|picked|was left over)\s*([^<]*)</div>'
)

# Player stats table — class is "table totalstats" (multi-class).
_TOTALSTATS_RE = re.compile(
    r'<table[^>]*class="[^"]*\btotalstats\b[^"]*"[^>]*>(.*?)</table>',
    re.DOTALL,
)
# Per-row pieces. Nickname is inside <span class="player-nick">N</span> OR
# directly inside an <a href="/player/...">N</a> on table rows that have no
# wrapping span (rare).
_PLAYER_LINK_RE = re.compile(r'href="/player/(\d+)/[^"]+"')
_PLAYER_NICK_RE = re.compile(r'class="player-nick">([^<]+)</span>')
_PLAYER_NICK_FALLBACK_RE = re.compile(
    r'class="smartphone-only statsPlayerName[^"]*">([^<]+)</div>'
)
_KD_CELL_RE     = re.compile(r'<td class="kd[^"]*\btraditional-data\b[^"]*"[^>]*>\s*(\d+)-(\d+)')
_ADR_CELL_RE    = re.compile(r'<td class="adr[^"]*\btraditional-data\b[^"]*"[^>]*>\s*([\d.]+)')
_KAST_CELL_RE   = re.compile(r'<td class="kast[^"]*\btraditional-data\b[^"]*"[^>]*>\s*([\d.]+)%?')
_RATING_CELL_RE = re.compile(r'<td class="rating[^"]*"[^>]*>\s*(\d\.\d{1,3})')


def _fetch(url: str) -> str | None:
    """Fetch via FlareSolverr if FLARESOLVERR_URL is set. Else fall back to
    plain requests (will hit CF blocks ~50% of time for non-/stats/* URLs).

    Behavior change 2026-06-09: when FLARESOLVERR_URL IS set but FlareSolverr
    is unreachable (e.g. cold start, container restarting), we now retry the
    is_available probe twice with backoff before falling back. Previously a
    single 5s timeout would silently fall back to plain requests and every
    match would 403. Loud failure beats silent.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from flaresolverr_client import fetch as fs_fetch, is_available
    except ImportError:
        fs_fetch = None
        is_available = lambda: False

    fs_url = os.getenv("FLARESOLVERR_URL", "").strip()
    if fs_url and fs_fetch:
        # Treat FLARESOLVERR_URL as authoritative: try FlareSolverr first,
        # don't pre-check availability (waste of a roundtrip per call).
        result = fs_fetch(url, session="hltv_matches")
        if result is not None:
            return result
        # Don't fall back to plain requests when FlareSolverr is configured;
        # the queue row will be marked fetch_failed and auto-retry on next run.
        print(f"  [!] FlareSolverr returned None for {url[-60:]} — won't fall back to plain requests", file=sys.stderr)
        return None

    # Fallback path: only used when FLARESOLVERR_URL is not configured.
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


def queue_new_matches(limit_pages: int = 1, from_page: int = 0) -> int:
    """Walk /results pages, INSERT new (match_id, slug) into the queue.
    Walks pages [from_page, from_page+limit_pages). Use from_page to resume
    from a specific offset or split work across parallel walkers."""
    try:
        from scraper_state import scraper_run  # type: ignore
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parent))
        from scraper_state import scraper_run

    total = 0
    # Queue walking just hits public /results pages — no auth needed, FlareSolverr's
    # ~3-5s navigation already paces us. Use a tiny sleep instead of full RATE_DELAY
    # (which is set for the heavier match-detail page parses).
    QUEUE_SLEEP_S = 0.3
    with scraper_run("match_details_queue", "Walks HLTV /results pages and queues match IDs") as st:
        st.set_total(limit_pages * 100)  # nominal capacity
        st.note(f"walking {limit_pages} pages")
        for i in range(limit_pages):
            page = from_page + i
            if i > 0:
                time.sleep(QUEUE_SLEEP_S)
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
                st.tick_done(persist_every=50)
            print(f"  page {page} (offset={offset}): {len(rows)} candidates")
        # Final state: reflect actual queue size as items_total.
        q = execute_query("SELECT COUNT(*) AS c FROM cs2_hltv_match_queue")[0]["c"]
        p = execute_query("SELECT COUNT(*) AS c FROM cs2_hltv_match_queue WHERE fetched_at IS NULL AND error IS NULL")[0]["c"]
        st.set_total(q)
        st.set_pending(p)
        st.note(f"queue now {q} total / {p} pending")
    return total


def parse_match(html: str) -> dict | None:
    """Extract everything from a single match page. Returns None on parse failure."""
    # Team names appear all over the page; restrict to the teamsBox header.
    box_m = _TEAMSBOX_RE.search(html)
    teams_src = box_m.group(1) if box_m else html
    teams = _TEAM_NAME_RE.findall(teams_src)
    if len(teams) < 2:
        # Fallback: dedupe order-preserving from full html.
        all_teams = _TEAM_NAME_RE.findall(html)
        teams = list(dict.fromkeys(all_teams))
    if len(teams) < 2:
        return None
    team1, team2 = teams[0].strip(), teams[1].strip()
    if team1 == team2:
        return None  # parser couldn't distinguish two teams

    # Event — first /events/{id}/slug link in the page is the tournament header
    event_m = _EVENT_RE.search(html)
    event_id = int(event_m.group(1)) if event_m else None
    event = event_m.group(2).strip() if event_m else None

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

    # Maps + scores per map. Walk the HTML by mapname blocks.
    maps = []
    for m_ in _MAP_BLOCK_RE.finditer(html):
        name = m_.group(1).strip()
        block = m_.group(2)
        scores = _MAP_SCORE_RE.findall(block)
        if len(scores) < 2:
            continue
        s1 = int(scores[0])
        s2 = int(scores[1])
        if s1 == 0 and s2 == 0:
            continue
        winner = team1 if s1 > s2 else team2 if s2 > s1 else None
        # Per-side halftime scores — two pairs (1st half, 2nd half), each pair is
        # team1_side:team1_score - team2_side:team2_score.
        half_scores = _HALF_SCORE_RE.findall(block)
        team1_ct = team1_t = team2_ct = team2_t = None
        team1_first_half_side = None  # 'ct' or 't' — which side team1 started on
        if len(half_scores) >= 2:
            # In CS2: team1 plays CT then T (or vice-versa); halftime split helps
            # us extract their CT-side and T-side round counts.
            # Each match: (team1_side1, team1_score1, team2_side1, team2_score1),
            #             (team1_side2, team1_score2, team2_side2, team2_score2)
            try:
                s1_first_side, s1_first_score, s2_first_side, s2_first_score = half_scores[0]
                s1_second_side, s1_second_score, s2_second_side, s2_second_score = half_scores[1]
                team1_first_half_side = s1_first_side  # 'ct' or 't' — store for model feature
                # Sum each team's CT and T halves
                if s1_first_side == "ct":
                    team1_ct = int(s1_first_score); team1_t = int(s1_second_score)
                else:
                    team1_t = int(s1_first_score); team1_ct = int(s1_second_score)
                if s2_first_side == "ct":
                    team2_ct = int(s2_first_score); team2_t = int(s2_second_score)
                else:
                    team2_t = int(s2_first_score); team2_ct = int(s2_second_score)
            except (ValueError, IndexError):
                pass
        maps.append({
            "name": name,
            "team1_first_half_side": team1_first_half_side,
            "team1_score": s1, "team2_score": s2,
            "winner": winner,
            "team1_ct": team1_ct, "team1_t": team1_t,
            "team2_ct": team2_ct, "team2_t": team2_t,
        })

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

    # Per-player stats — one totalstats table per team per map.
    # Tables alternate: [map1_team1, map1_team2, map2_team1, map2_team2, ...]
    # so floor(table_index / 2) gives the map_idx and table_index % 2 gives the
    # team side (0 = team1 of the match, 1 = team2).
    players = []
    for tbl_idx, ts_block in enumerate(_TOTALSTATS_RE.findall(html)):
        map_idx = tbl_idx // 2
        team_side = tbl_idx % 2  # 0 = team1, 1 = team2
        team_name = team1 if team_side == 0 else team2
        if map_idx >= len(maps):
            continue
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", ts_block, re.DOTALL):
            link_m = _PLAYER_LINK_RE.search(row)
            if not link_m:
                continue
            pid = int(link_m.group(1))
            nick_m = _PLAYER_NICK_RE.search(row) or _PLAYER_NICK_FALLBACK_RE.search(row)
            nick = nick_m.group(1).strip() if nick_m else None
            kd_m = _KD_CELL_RE.search(row)
            adr_m = _ADR_CELL_RE.search(row)
            kast_m = _KAST_CELL_RE.search(row)
            rating_m = _RATING_CELL_RE.search(row)
            if not (kd_m and rating_m):
                continue  # header row or empty row
            players.append({
                "id": pid,
                "nickname": nick,
                "team_name": team_name,
                "map_idx": map_idx,
                "kills": int(kd_m.group(1)),
                "deaths": int(kd_m.group(2)),
                "adr": float(adr_m.group(1)) if adr_m else None,
                "kast": float(kast_m.group(1)) if kast_m else None,
                "rating": float(rating_m.group(1)),
            })

    return {
        "team1": team1, "team2": team2,
        "event": event, "event_id": event_id,
        "best_of": bo, "is_lan": lan, "date": date_iso,
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
                team1_score, team2_score, winner_name,
                team1_ct_rounds, team1_t_rounds, team2_ct_rounds, team2_t_rounds,
                team1_first_half_side)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (mid, i, m["name"], m["team1_score"], m["team2_score"], m["winner"],
              m.get("team1_ct"), m.get("team1_t"), m.get("team2_ct"), m.get("team2_t"),
              m.get("team1_first_half_side")))

    for v in parsed["veto"]:
        execute_write("""
            INSERT INTO cs2_hltv_match_veto (hltv_match_id, step, team_name, action, map_name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (mid, v["step"], v["team"], v["action"], v["map"]))

    # One row per (player, map). map_order is 1-indexed to match cs2_hltv_match_maps.
    for p in parsed["players"]:
        map_order = p["map_idx"] + 1
        map_name = parsed["maps"][p["map_idx"]]["name"] if p["map_idx"] < len(parsed["maps"]) else None
        execute_write("""
            INSERT INTO cs2_hltv_player_match_stats
                (hltv_match_id, hltv_player_id, nickname, team_name,
                 map_order, map_name, kills, deaths, adr, kast, rating)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (mid, p["id"], p["nickname"], p.get("team_name"),
              map_order, map_name, p["kills"], p["deaths"], p["adr"], p["kast"], p["rating"]))


def process_queue(limit: int) -> tuple[int, int]:
    # Self-healing: re-claim rows stuck "in flight" >2h (e.g. killed mid-run).
    # Also retry transient fetch_failed errors after 6h.
    execute_write("""
        UPDATE cs2_hltv_match_queue
        SET error = NULL
        WHERE error = 'fetch_failed'
          AND discovered_at < NOW() - INTERVAL '6 hours'
    """)

    rows = execute_query("""
        SELECT hltv_match_id, slug FROM cs2_hltv_match_queue
        WHERE fetched_at IS NULL AND error IS NULL
        ORDER BY discovered_at
        LIMIT %s
    """, (limit,))

    # Count overall pending for the state row (UI progress bar).
    pending = execute_query(
        "SELECT COUNT(*) AS c FROM cs2_hltv_match_queue WHERE fetched_at IS NULL AND error IS NULL"
    )[0]["c"]
    total = execute_query("SELECT COUNT(*) AS c FROM cs2_hltv_match_queue")[0]["c"]

    try:
        from scraper_state import scraper_run  # type: ignore
    except ImportError:  # pragma: no cover
        sys.path.insert(0, str(Path(__file__).parent))
        from scraper_state import scraper_run

    hits = miss = 0
    with scraper_run("match_details_process", "HLTV match-page fetch+parse (queue-driven)") as st:
        st.set_total(total)
        st.set_pending(pending)
        st.note(f"this batch: {len(rows)} rows")

        for i, r in enumerate(rows):
            mid, slug = r["hltv_match_id"], r["slug"]
            if i > 0:
                time.sleep(RATE_DELAY)
            url = MATCH_URL_FMT.format(mid=mid, slug=slug)
            html = _fetch(url)
            if not html:
                execute_write("UPDATE cs2_hltv_match_queue SET error = %s WHERE hltv_match_id = %s",
                              ("fetch_failed", mid))
                st.tick_failed(f"fetch_failed {mid}")
                miss += 1
                continue
            parsed = parse_match(html)
            if not parsed:
                execute_write("UPDATE cs2_hltv_match_queue SET error = %s WHERE hltv_match_id = %s",
                              ("parse_failed", mid))
                st.tick_failed(f"parse_failed {mid}")
                miss += 1
                print(f"  [!] {mid} parse failed")
                continue
            write_match(mid, slug, parsed)
            execute_write("UPDATE cs2_hltv_match_queue SET fetched_at = NOW() WHERE hltv_match_id = %s",
                          (mid,))
            st.tick_done()
            hits += 1
            veto_n = len(parsed["veto"])
            print(f"  [{i+1:>3}/{len(rows)}] ✓ {parsed['team1']:18} vs {parsed['team2']:18}  "
                  f"{parsed['score1']}-{parsed['score2']}  veto={veto_n}  maps={len(parsed['maps'])}  players={len(parsed['players'])}")
    return hits, miss


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--queue", action="store_true", help="Pull /results, queue new match IDs")
    p.add_argument("--pages", type=int, default=1, help="How many /results pages to walk (for --queue)")
    p.add_argument("--from-page", type=int, default=0,
                   help="Start page offset (for resuming or parallel walkers)")
    p.add_argument("--process", type=int, default=0, help="Process N queued matches")
    p.add_argument("--match-id", type=int, help="One-off: fetch + parse this match ID")
    p.add_argument("--slug", default="", help="Slug for --match-id")
    args = p.parse_args()

    print(f"\n=== HLTV match details  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")

    if args.queue:
        n = queue_new_matches(limit_pages=args.pages, from_page=args.from_page)
        print(f"  queued {n} candidates from {args.pages} page(s) starting at page {args.from_page}")

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
