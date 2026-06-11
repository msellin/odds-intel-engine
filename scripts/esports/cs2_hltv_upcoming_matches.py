#!/usr/bin/env python3
"""
HLTV upcoming-matches scraper — the missing CS2 fixture source.

Background
----------
The original CS2 fixture scanner (cs2_elo_scanner.py) pulls from bo3.gg, which
in practice gives us 2-3 matches/day across the entire global CS2 schedule.
HLTV's /matches page exposes the canonical schedule (~150-200 upcoming matches
spanning every tier from Tier 1 LANs to open qualifiers).

This scraper fills the gap by parsing https://www.hltv.org/matches and writing
the fixtures into cs2_upcoming_matches. Downstream predictors
(cs2_hltv_predict, cs2_v7_predict, cs2_v8_predict) read that table and pick
up our rows automatically once HLTV's stats scraper (cs2_hltv_stats_scraper)
populates hltv_rank1/2 + hltv_points1/2 for the teams involved.

bo3gg_id sentinel
-----------------
The existing predictors hard-require `bo3gg_id IS NOT NULL` (the column is
both the bo3.gg primary key in their source schema AND the join key the
predictors use for cs2_predictions writes). We encode HLTV-sourced rows as
`bo3gg_id = -hltv_match_id` — negative integers guarantee zero collision with
bo3.gg's positive numeric IDs, and the predictors don't care about the sign
since they read the column as opaque identifier.

ON CONFLICT semantics
---------------------
Upsert key is (team1, team2, kickoff_time) per cs2_upcoming_matches_uniq.
We DO NOTHING on conflict so HLTV writes don't disturb existing bo3.gg rows
(which carry enriched odds + ELO data this scraper can't produce). HLTV-only
matchups still land cleanly because bo3.gg never wrote them.

Usage
-----
    python3 scripts/esports/cs2_hltv_upcoming_matches.py            # dry-run
    python3 scripts/esports/cs2_hltv_upcoming_matches.py --record   # write DB

Polite: 1 GET to hltv.org/matches per run. Plain `requests` with a real-browser
User-Agent works fine — HLTV doesn't gate /matches behind Cloudflare for normal
GETs (verified 2026-06-11). FlareSolverr is used only if the plain path 4xx/5xxs.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests",
                            "--break-system-packages", "-q"])
    import requests

from dotenv import load_dotenv

load_dotenv()

from workers.api_clients.db import execute_query, execute_write_returning  # noqa: E402


URL = "https://www.hltv.org/matches"
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

# Match-wrapper regex: anchor on the attribute-rich opening div. HLTV uses
# Angular-style data-* attributes which are stable and far less likely to
# rotate than CSS classes. Captures match_id, team IDs, event_id, LAN flag,
# stars (event tier), event-type (ranked/lan/other), and region.
_MATCH_WRAPPER_RE = re.compile(
    r'<div class="match-wrapper[^"]*"\s+'
    r'data-match-wrapper=""\s+'
    r'data-match-id="(?P<match_id>\d+)"\s+'
    r'data-stars="(?P<stars>\d+)"\s+'
    r'data-event-id="(?P<event_id>\d+)"\s+'
    r'data-eventtype="(?P<event_type>[^"]*)"\s+'
    r'data-region="(?P<region>[^"]*)"\s+'
    r'lan="(?P<lan>true|false)"\s+'
    r'live="(?P<live>true|false)"\s+'
    r'team1="(?P<team1_id>\d+)"\s+'
    r'team2="(?P<team2_id>\d+)"\s+'
    r'data-pinned="(?P<pinned>true|false)">',
    re.DOTALL,
)

# Inside each wrapper: kickoff (data-unix in ms), event name, bo, team names.
_EVENT_HEADLINE_RE = re.compile(r'data-event-headline="([^"]+)"')
_UNIX_MS_RE = re.compile(r'data-unix="(\d+)"')
_BEST_OF_RE = re.compile(r'<div class="match-meta">\s*([^<]+?)\s*</div>')
_TEAMNAME_RE = re.compile(r'<div class="match-teamname[^"]*">\s*([^<]+?)\s*</div>')

# How far ahead to keep matches (predictors only act on near-term ones, but
# scraping the full page is free so we capture everything). The 14-day window
# matches cs2_elo_scanner's _parse_upcoming cutoff intent.
_KEEP_WINDOW_DAYS = 14


def fetch_matches_page() -> str:
    """GET hltv.org/matches with a real-browser UA. Returns HTML string."""
    # Plain requests first — fast path. FlareSolverr fallback only if blocked.
    try:
        r = requests.get(URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except requests.HTTPError as e:
        print(f"  [info] plain GET failed ({e.response.status_code}), trying FlareSolverr…")
    except Exception as e:
        print(f"  [info] plain GET failed ({e}), trying FlareSolverr…")

    # FlareSolverr fallback (rarely needed for /matches in practice).
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from flaresolverr_client import fetch as fs_fetch, is_available
        if is_available():
            text = fs_fetch(URL, session="hltv_upcoming")
            if text:
                return text
    except Exception as e:
        print(f"  [info] FlareSolverr fallback also failed: {e}")
    raise RuntimeError("Could not fetch HLTV /matches via plain GET or FlareSolverr")


def parse_best_of(meta: str) -> int:
    """Map HLTV match-meta strings (bo1 / bo3 / bo5 / Best of 3 / etc.) to int."""
    s = meta.strip().lower()
    if s.startswith("bo"):
        try:
            return int(s[2:].strip())
        except ValueError:
            pass
    m = re.search(r'(\d+)', s)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 7:
                return n
        except ValueError:
            pass
    return 3  # sensible default — bo3 is the most common CS2 format


def parse_matches(html: str) -> list[dict]:
    """Parse the HLTV /matches page into row dicts.

    Skips matches where either team is TBD (the kickoff exists but the
    bracket hasn't seeded the entrants yet — useless to bots until then).
    """
    now = datetime.now(timezone.utc)
    cutoff_ms = int((now.timestamp() + _KEEP_WINDOW_DAYS * 86400) * 1000)
    matches: list[dict] = []

    for opener in _MATCH_WRAPPER_RE.finditer(html):
        # The match content lives between this opener and either the next
        # match-wrapper or a wide enough chunk after. Take a generous slice.
        start = opener.end()
        # Find next match-wrapper opener to bound the block; if none, take
        # 4KB ahead which is more than any real entry.
        next_match = _MATCH_WRAPPER_RE.search(html, opener.end())
        end = next_match.start() if next_match else min(opener.end() + 4096, len(html))
        block = html[start:end]

        unix_m = _UNIX_MS_RE.search(block)
        if not unix_m:
            continue
        try:
            unix_ms = int(unix_m.group(1))
        except ValueError:
            continue
        if unix_ms > cutoff_ms:
            continue
        kickoff = datetime.fromtimestamp(unix_ms / 1000, tz=timezone.utc)

        event_m = _EVENT_HEADLINE_RE.search(block)
        league = event_m.group(1).strip() if event_m else ""

        bo_m = _BEST_OF_RE.search(block)
        best_of = parse_best_of(bo_m.group(1)) if bo_m else 3

        teamnames = _TEAMNAME_RE.findall(block)
        if len(teamnames) < 2:
            continue
        team1, team2 = teamnames[0].strip(), teamnames[1].strip()
        if not team1 or not team2:
            continue
        # TBD vs TBD or TBD-side rows are useless until the bracket resolves.
        if team1.upper() == "TBD" or team2.upper() == "TBD":
            continue

        attrs = opener.groupdict()
        try:
            hltv_match_id = int(attrs["match_id"])
        except (KeyError, ValueError):
            continue

        matches.append({
            "hltv_match_id": hltv_match_id,
            "bo3gg_id": -hltv_match_id,  # negative sentinel — see module docstring
            "kickoff_time": kickoff,
            "league": league or "Unknown",
            "best_of": best_of,
            "team1": team1,
            "team2": team2,
            "is_lan": attrs.get("lan") == "true",
            "live": attrs.get("live") == "true",
            "stars": int(attrs.get("stars") or 0),
        })

    return matches


def write_matches(matches: list[dict]) -> tuple[int, int, int]:
    """Upsert into cs2_upcoming_matches. Returns (inserted, skipped, rank_hits).

    ON CONFLICT (team1, team2, kickoff_time) DO NOTHING — when a row already
    exists from bo3.gg (or a previous HLTV run), we leave it alone. bo3.gg
    rows carry richer odds + ELO data that this scraper can't produce.

    Populates hltv_rank1/2 + hltv_points1/2 by looking up the latest snapshot
    in cs2_hltv_rankings per team. Without these the downstream cs2_hltv_predict
    job filters the row out (its WHERE hltv_points1 IS NOT NULL AND
    hltv_points2 IS NOT NULL gate). Matches where one or both teams aren't
    ranked (lower-tier, ungated qualifier sides) still INSERT cleanly with
    NULL points — they're just invisible to the HLTV-only predictor; v7/v8
    can still score them once their stats land.
    """
    if not matches:
        return (0, 0, 0)

    inserted = 0
    skipped = 0
    rank_hits = 0
    for m in matches:
        state = "live" if m["live"] else "unstarted"
        rows = execute_write_returning(
            """
            INSERT INTO cs2_upcoming_matches
                (bo3gg_id, league, kickoff_time, state, best_of,
                 team1, team2, is_lan, has_elo_history,
                 hltv_rank1, hltv_points1, hltv_rank2, hltv_points2,
                 scanned_at)
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, FALSE,
                (SELECT hltv_rank   FROM cs2_hltv_rankings WHERE team_name = %s
                   ORDER BY snapshot_date DESC LIMIT 1),
                (SELECT hltv_points FROM cs2_hltv_rankings WHERE team_name = %s
                   ORDER BY snapshot_date DESC LIMIT 1),
                (SELECT hltv_rank   FROM cs2_hltv_rankings WHERE team_name = %s
                   ORDER BY snapshot_date DESC LIMIT 1),
                (SELECT hltv_points FROM cs2_hltv_rankings WHERE team_name = %s
                   ORDER BY snapshot_date DESC LIMIT 1),
                NOW()
            )
            ON CONFLICT (team1, team2, kickoff_time) DO NOTHING
            RETURNING (hltv_points1 IS NOT NULL AND hltv_points2 IS NOT NULL) AS predictor_ready
            """,
            (
                m["bo3gg_id"], m["league"], m["kickoff_time"], state,
                m["best_of"], m["team1"], m["team2"], m["is_lan"],
                m["team1"], m["team1"],  # rank1, points1 lookups
                m["team2"], m["team2"],  # rank2, points2 lookups
            ),
        )
        if rows:
            inserted += 1
            if rows[0].get("predictor_ready"):
                rank_hits += 1
        else:
            skipped += 1
    return (inserted, skipped, rank_hits)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--record", action="store_true",
                   help="Write parsed matches to cs2_upcoming_matches (default: dry-run)")
    args = p.parse_args()

    print(f"\n=== HLTV upcoming-matches scrape  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC ===")
    print(f"  GET {URL}")
    html = fetch_matches_page()
    print(f"  ✓ {len(html):,} bytes")

    matches = parse_matches(html)
    print(f"  parsed {len(matches)} upcoming matches (≤{_KEEP_WINDOW_DAYS} days out, TBD-vs-TBD skipped)")
    if matches:
        print(f"  earliest: {matches[0]['kickoff_time'].strftime('%Y-%m-%d %H:%M')} UTC  "
              f"{matches[0]['team1']} vs {matches[0]['team2']}  ({matches[0]['league']})")
        latest = max(matches, key=lambda x: x["kickoff_time"])
        print(f"  latest:   {latest['kickoff_time'].strftime('%Y-%m-%d %H:%M')} UTC  "
              f"{latest['team1']} vs {latest['team2']}  ({latest['league']})")

    if not args.record:
        print("\n[DRY-RUN] No DB writes. Re-run with --record to persist.")
        for m in matches[:10]:
            print(f"    {m['kickoff_time'].strftime('%m-%d %H:%M')} bo{m['best_of']:1d} "
                  f"{m['team1']!r:>30} vs {m['team2']!r:30}  {m['league']}")
        if len(matches) > 10:
            print(f"    … {len(matches) - 10} more")
        return 0

    ins, skip, rank_hits = write_matches(matches)
    print(f"\n  ✓ inserted {ins}, skipped {skip} (already in cs2_upcoming_matches)")
    print(f"  ✓ {rank_hits}/{ins} new rows have HLTV rank+points for BOTH teams (predictor-ready)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
