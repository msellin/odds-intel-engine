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

from workers.api_clients.db import execute_query, execute_write, execute_write_returning  # noqa: E402

# CS2-HLTV-ELO-ENRICH (2026-06-11): the HLTV-sourced fixtures we INSERT have
# NULL elo1/elo2/win_prob1 because we don't run the bo3.gg-keyed ELO walk.
# Without those columns the production v8 scorer (cs2_v8_predict.py) and v7
# fallback both early-return — the admin/cs2 page shows "no model coverage"
# even though HLTV-sourced rows landed cleanly. Bridge: after every scrape,
# walk cs2_results to build a team-name → ELO map and back-fill the rows
# the scrape just wrote. Reuses the scanner's exact math (build_elo +
# combined_win_prob) so HLTV-sourced and bo3.gg-sourced rows are scored on
# the same scale.
sys.path.insert(0, str(Path(__file__).parent))  # for sibling import below
from cs2_elo_scanner import (  # noqa: E402
    build_elo, build_match_counts, combined_win_prob,
    INITIAL_ELO, MIN_MATCHES_FOR_PREDICTION,
)


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


def _load_cs2_results() -> list[dict]:
    """Shape cs2_results rows into the form build_elo() expects.

    Source columns: team1, team2, kickoff_time, best_of, winner.
    Target shape: team1, team2, tournament, best_of, result (1.0/0.0), date.

    cs2_results doesn't store the tournament name (only bo3gg_id), so we feed
    tournament='' which makes tournament_tier() return the 1.0 default. Team
    ELOs will be slightly less differentiated than the scanner's (which has
    bo3.gg tournament names available) but in the same ballpark — close enough
    for the win_prob gate.
    """
    rows = execute_query(
        """SELECT team1, team2, best_of, winner,
                  COALESCE(finished_at, kickoff_time) AS date
           FROM cs2_results
           WHERE team1 IS NOT NULL AND team2 IS NOT NULL AND winner IS NOT NULL
           ORDER BY COALESCE(finished_at, kickoff_time) ASC"""
    )
    shaped: list[dict] = []
    for r in rows:
        # cs2_results.winner stores the literal strings 'team1' / 'team2'
        # (matching the order in the same row), NOT the actual team name.
        w = r["winner"]
        if w == "team1":
            result = 1.0
        elif w == "team2":
            result = 0.0
        else:
            continue  # unexpected winner value, skip
        shaped.append({
            "team1": r["team1"], "team2": r["team2"],
            "tournament": "", "best_of": int(r["best_of"] or 3),
            "result": result, "date": r["date"],
        })
    return shaped


# HLTV /matches uses short team names ("Vitality", "FURIA", "9z", "NAVI") while
# cs2_results carries bo3.gg's longer forms ("Team Vitality", "FURIA Esports",
# "9z Team", "Natus Vincere"). Without resolving these, ~90% of Tier-1 matches
# get filtered out of ELO enrichment because the lookup misses on naming alone.
# Generated from a 2026-06-11 audit of cs2_results vs HLTV /matches.
_HLTV_TO_RESULTS_ALIASES: dict[str, list[str]] = {
    "NAVI": ["Natus Vincere"],
    "Natus Vincere": ["NAVI"],
    "The MongolZ": ["MongolZ", "Mongolz"],
    "MongolZ": ["The MongolZ"],
}


def _resolve_elo(team_name: str, elo_map: dict[str, float],
                  counts: dict[str, int]) -> tuple[float | None, int, str | None]:
    """Return (elo, match_count, matched_name) for a team, trying common
    cs2_results name variants when the exact HLTV name doesn't hit.

    Picks the variant with the HIGHEST match count, not just the first hit.
    Without this, exact-match teams that happen to have a low-history stub
    in cs2_results (e.g., "B8" has 3 rows while "B8 Esports" has 247) get
    locked to the stub and fail the MIN_MATCHES gate — even though the
    high-history variant exists. Picking by max count covers both the
    "exact name with thin data" trap and the "long name with thick data"
    happy path.
    """
    candidates = [
        team_name,
        f"Team {team_name}",
        f"{team_name} Team",
        f"{team_name} Esports",
        f"{team_name} Gaming",
    ]
    candidates.extend(_HLTV_TO_RESULTS_ALIASES.get(team_name, []))
    best: tuple[float | None, int, str | None] = (None, 0, None)
    for cand in candidates:
        elo = elo_map.get(cand)
        if elo is None:
            continue
        c = counts.get(cand, 0)
        if c > best[1]:
            best = (elo, c, cand)
    return best


def enrich_elo(verbose: bool = True) -> tuple[int, int, int]:
    """Back-fill elo1/elo2/win_prob1/win_prob2/has_elo_history on rows where
    those columns are NULL. Returns (rows_examined, rows_updated, rows_skipped_no_history).

    Skips teams below MIN_MATCHES_FOR_PREDICTION (10 in last 180d) per the
    scanner's policy — under that threshold the ELO hasn't converged and a
    win_prob would be fake confidence.
    """
    history = _load_cs2_results()
    if verbose:
        print(f"  walked {len(history)} historical matches from cs2_results")
    if not history:
        return (0, 0, 0)

    elo_map = build_elo(history)
    counts = build_match_counts(history)
    if verbose:
        print(f"  built ELO for {len(elo_map)} teams, "
              f"{sum(1 for c in counts.values() if c >= MIN_MATCHES_FOR_PREDICTION)} "
              f"meet MIN_MATCHES gate")

    targets = execute_query(
        """SELECT id, team1, team2
           FROM cs2_upcoming_matches
           WHERE kickoff_time >= NOW()
             AND (elo1 IS NULL OR elo2 IS NULL OR win_prob1 IS NULL)"""
    )
    if verbose:
        print(f"  {len(targets)} upcoming rows need ELO enrichment")

    updated = 0
    skipped_no_history = 0
    for t in targets:
        r1, c1, _ = _resolve_elo(t["team1"], elo_map, counts)
        r2, c2, _ = _resolve_elo(t["team2"], elo_map, counts)
        if r1 is None or r2 is None or c1 < MIN_MATCHES_FOR_PREDICTION or c2 < MIN_MATCHES_FOR_PREDICTION:
            skipped_no_history += 1
            continue

        # pq_diff=None → pure ELO probability. We could add HLTV-rating-based PQ
        # later by joining cs2_hltv_player_ratings, but pure ELO is the same
        # baseline the scanner uses when player quality is missing.
        win_prob1 = combined_win_prob(r1, r2, None)
        win_prob2 = 1.0 - win_prob1

        execute_write(
            """UPDATE cs2_upcoming_matches
               SET elo1 = %s, elo2 = %s,
                   win_prob1 = %s, win_prob2 = %s,
                   has_elo_history = TRUE,
                   scanned_at = NOW()
               WHERE id = %s""",
            (round(r1, 2), round(r2, 2),
             round(win_prob1, 5), round(win_prob2, 5), t["id"]),
        )
        updated += 1

    return (len(targets), updated, skipped_no_history)


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
    print(f"  ✓ {rank_hits}/{ins} new rows have HLTV rank+points for BOTH teams (hltv_v1-ready)")

    # CS2-HLTV-ELO-ENRICH — back-fill ELO so v7/v8 (which both early-return on
    # NULL win_prob1) can score the HLTV-sourced rows. Without this step the
    # bot has no v8 picks on HLTV-only fixtures.
    print("\n  enriching ELO from cs2_results history…")
    examined, updated, skipped_no_history = enrich_elo()
    print(f"  ✓ ELO-enriched {updated}/{examined} rows "
          f"({skipped_no_history} skipped — teams below MIN_MATCHES_FOR_PREDICTION)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
