"""
PINNACLE-WEEKEND-EXPERIMENT (2026-06-05) — research-only, time-boxed.

PURPOSE: measure how much Pinnacle's published 1X2 line actually moves
in the 3-4h pre-kickoff window. AF refreshes Pinnacle every 3 hours;
our CLV calc is benchmarked against AF's last snapshot before kickoff.
This experiment quantifies whether a fresher Pinnacle source would
materially change CLV — answering the bookmaker-expansion question
with data instead of speculation.

THIS IS NOT A PRODUCTION SCRAPER. Hard constraints baked into the
script itself, not relying on the operator to enforce them:

  END_TIME       — hard exit at 2026-06-09 06:00 UTC (Monday morning)
  MAX_REQUESTS   — hard cap at 1000 total HTTP requests across run
  REQUEST_JITTER — 4-6 second random sleep between requests (polite)
  USER_AGENT     — honest identification, no spoofing
  NO_PROXY       — single-IP, no rotation, no evasion logic
  STORAGE        — append-only CSV in dev/active/, never odds_snapshots

The script also bails on consecutive 429s/5xx (3 in a row → abort).

The data sits in `dev/active/pinnacle-movement-{date}.csv`. Run
`scripts/analyze_pinnacle_movement.py` Monday to summarise — that's
the script that produces the decision-quality markdown analysis.

USAGE:
  python3 scripts/pinnacle_movement_research.py [--dry-run] [--max-matches N]

Operator controls:
  --dry-run     enumerate matches + Pinnacle IDs but make no Pinnacle calls
  --max-matches limit to N matches (default 30; useful for first-run test)

After it starts: tails to stdout. Safe to Ctrl-C; safe to restart
(CSV appends, run summary tracks last poll time per match).
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# ───── HARD CONSTRAINTS ─────────────────────────────────────────────
END_TIME_UTC = datetime(2026, 6, 9, 6, 0, 0, tzinfo=timezone.utc)
MAX_REQUESTS = 1000
REQUEST_JITTER_SEC = (4.0, 6.0)
USER_AGENT = "OddsIntelResearch/1.0 (line-movement measurement; contact: margus@dolmit.com)"
MAX_CONSECUTIVE_ERRORS = 3
# Pinnacle sport ID for soccer (confirmed via spike doc)
PINNACLE_SOCCER_SPORT_ID = 29
API_HOST = "https://guest.api.arcadia.pinnacle.com"
# Poll a match no more often than this
MIN_POLL_INTERVAL = timedelta(minutes=20)
# Only start polling a match when this close to kickoff (irrelevant earlier)
POLL_WINDOW_BEFORE_KICKOFF = timedelta(hours=4)

# Top European leagues we care about — used to filter our DB matches
PRIORITY_LEAGUES = (
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Eredivisie", "Primeira Liga", "Champions League",
    "Europa League", "Conference League",
)
PRIORITY_COUNTRIES = ("England", "Spain", "Germany", "Italy", "France",
                      "Netherlands", "Portugal", "Europe", "World")


# ───── DB ACCESS (read-only) ────────────────────────────────────────
def fetch_weekend_matches(max_matches: int):
    """Get our matches with kickoff between now and END_TIME_UTC. Returns
    list of dicts with id, kickoff, home_team, away_team for Pinnacle ID
    resolution.

    We deliberately DON'T filter by league here — pulled all senior matches
    in the window. Pinnacle covers most senior soccer worldwide. Filtering
    by "priority European leagues" gave us only 5 matchable matches
    because mid-season-break + international-friendlies window. Letting
    the Pinnacle resolver decide what's covered is more efficient than
    pre-guessing.

    Excludes obvious non-Pinnacle markets: women's (W suffix), youth
    (U18/U19/U20/U21/U23), reserve teams.
    """
    from workers.api_clients.db import execute_query

    now_iso = datetime.now(timezone.utc).isoformat()
    end_iso = END_TIME_UTC.isoformat()
    rows = execute_query(
        """
        SELECT m.id, m.date AS kickoff,
               ht.name AS home_team, at.name AS away_team,
               l.name AS league, l.country AS country
        FROM matches m
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        JOIN leagues l ON l.id = m.league_id
        WHERE m.date > %s::timestamptz
          AND m.date < %s::timestamptz
          AND m.status = 'scheduled'
          -- exclude women / youth / reserve — Pinnacle skips these
          AND ht.name NOT LIKE '%% W'
          AND at.name NOT LIKE '%% W'
          AND ht.name NOT LIKE '%% U1_'
          AND at.name NOT LIKE '%% U1_'
          AND ht.name NOT LIKE '%% U2_'
          AND at.name NOT LIKE '%% U2_'
          AND l.name NOT ILIKE '%%women%%'
          AND l.name NOT ILIKE '%%U18%%'
          AND l.name NOT ILIKE '%%U19%%'
          AND l.name NOT ILIKE '%%U20%%'
          AND l.name NOT ILIKE '%%U21%%'
          AND l.name NOT ILIKE '%%U23%%'
          AND l.name NOT ILIKE '%%reserves%%'
        ORDER BY m.date ASC
        LIMIT %s
        """,
        [now_iso, end_iso, max_matches],
    )
    return rows


# ───── PINNACLE API CLIENT (HTTP, polite, time-boxed) ───────────────
class Throttler:
    def __init__(self):
        self.req_count = 0
        self.consecutive_errors = 0

    def check_limits(self) -> Optional[str]:
        """Return abort-reason string or None if OK to proceed."""
        if datetime.now(timezone.utc) >= END_TIME_UTC:
            return f"END_TIME_UTC ({END_TIME_UTC}) reached"
        if self.req_count >= MAX_REQUESTS:
            return f"MAX_REQUESTS ({MAX_REQUESTS}) exhausted"
        if self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            return f"MAX_CONSECUTIVE_ERRORS ({MAX_CONSECUTIVE_ERRORS}) reached — backing off entirely"
        return None

    def fetch(self, path: str):
        """Single GET with the polite UA + jitter sleep AFTER. Returns
        parsed JSON or raises on persistent failure."""
        import json
        abort = self.check_limits()
        if abort:
            raise SystemExit(f"Aborting: {abort}")

        url = f"{API_HOST}{path}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        self.req_count += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                body = json.loads(data)
                self.consecutive_errors = 0
                return body
        except urllib.error.HTTPError as e:
            self.consecutive_errors += 1
            print(f"  ! HTTP {e.code} on {path}", file=sys.stderr)
            if e.code in (429, 503):
                time.sleep(60.0)  # extra back-off
            return None
        except Exception as e:
            self.consecutive_errors += 1
            print(f"  ! Exception on {path}: {e}", file=sys.stderr)
            return None
        finally:
            time.sleep(random.uniform(*REQUEST_JITTER_SEC))


# ───── TEAM-NAME NORMALISATION ──────────────────────────────────────
import re
import unicodedata

CLUB_PREFIXES = re.compile(
    r"^(FK|FC|AC|AS|SC|RC|CD|CF|BK|SK|NK|GK|SV|TSV|BSC|SG|RB|SpVgg)\s+",
    re.IGNORECASE,
)


def normalize_team(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFD", name)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.lower().strip()
    s = CLUB_PREFIXES.sub("", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


# ───── PINNACLE MATCHUP-ID RESOLUTION ────────────────────────────────
def resolve_pinnacle_ids(our_matches: list[dict], throttler: Throttler) -> dict:
    """Fetch Pinnacle's soccer matchups list once, build a
    (norm_home, norm_away, date_utc) → matchup_id index. Returns a
    dict our_match_id → pinnacle_matchup_id for matches we found.
    """
    print(f"\n[resolve] Fetching Pinnacle soccer matchups list (one call)...")
    data = throttler.fetch(
        f"/0.1/sports/{PINNACLE_SOCCER_SPORT_ID}/matchups?withSpecials=false&brandId=0"
    )
    if not data or not isinstance(data, list):
        print("[resolve] Pinnacle matchups endpoint returned nothing — aborting.")
        return {}

    # Build index: normalized (home, away, YYYY-MM-DD) → matchup_id
    pin_index: dict[tuple[str, str, str], int] = {}
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        start = m.get("startTime")
        parts = m.get("participants") or []
        if not (mid and start and len(parts) >= 2):
            continue
        # find home + away
        home = next((p.get("name") for p in parts if p.get("alignment") == "home"), None)
        away = next((p.get("name") for p in parts if p.get("alignment") == "away"), None)
        if not (home and away):
            continue
        # ISO timestamp like "2026-06-06T07:00:00Z" → YYYY-MM-DD
        date_str = start[:10]
        key = (normalize_team(home), normalize_team(away), date_str)
        pin_index[key] = int(mid)

    print(f"[resolve] Pinnacle has {len(pin_index)} soccer matchups indexed.")

    # Match our DB rows
    mapping: dict = {}
    misses = []
    for row in our_matches:
        h = normalize_team(row["home_team"])
        a = normalize_team(row["away_team"])
        d = row["kickoff"].strftime("%Y-%m-%d")
        # try exact first, then ± 1 day in case of timezone slip
        for date_try in (d,
                          (row["kickoff"] - timedelta(days=1)).strftime("%Y-%m-%d"),
                          (row["kickoff"] + timedelta(days=1)).strftime("%Y-%m-%d")):
            mid = pin_index.get((h, a, date_try))
            if mid:
                mapping[str(row["id"])] = mid
                break
        else:
            misses.append(f"{row['home_team']} vs {row['away_team']} ({d}, {row['league']})")

    print(f"[resolve] Matched {len(mapping)} of {len(our_matches)} matches to Pinnacle IDs.")
    if misses:
        print("[resolve] Unmatched (no Pinnacle equivalent or name mismatch):")
        for m in misses[:10]:
            print(f"  - {m}")
        if len(misses) > 10:
            print(f"  ... +{len(misses) - 10} more")
    return mapping


# ───── POLLING LOOP ─────────────────────────────────────────────────
def fetch_match_markets(pinnacle_id: int, throttler: Throttler):
    """Fetch the straight-bet markets for one matchup. Returns list of
    market dicts or None on failure."""
    data = throttler.fetch(
        f"/0.1/matchups/{pinnacle_id}/markets/related/straight"
    )
    if not isinstance(data, list):
        return None
    return data


def extract_1x2(markets: list[dict]) -> dict:
    """From the markets list, return {home: american_odds, draw: ..., away: ...}
    for the FULL-game moneyline (period=0, type=moneyline). Empty dict if not found."""
    for m in markets:
        if not isinstance(m, dict):
            continue
        if m.get("type") != "moneyline":
            continue
        if m.get("period") != 0:
            continue
        if m.get("status") != "open":
            continue
        prices = m.get("prices") or []
        out: dict = {}
        for p in prices:
            d = p.get("designation")
            price = p.get("price")
            if d in ("home", "draw", "away") and price is not None:
                out[d] = int(price)
        if out:
            out["version"] = m.get("version")
            out["cutoffAt"] = m.get("cutoffAt")
            return out
    return {}


def american_to_decimal(american: int) -> float:
    if american > 0:
        return round(1.0 + american / 100.0, 4)
    return round(1.0 + 100.0 / abs(american), 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-matches", type=int, default=30)
    parser.add_argument("--csv-path", default=None,
                        help="Override CSV path (default: dev/active/pinnacle-movement-YYYY-MM-DD.csv)")
    args = parser.parse_args()

    start_time = datetime.now(timezone.utc)
    csv_path = Path(args.csv_path) if args.csv_path else (
        REPO_ROOT / "dev" / "active" / f"pinnacle-movement-{start_time:%Y-%m-%d}.csv"
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("PINNACLE-WEEKEND-EXPERIMENT  research run")
    print(f"Start:        {start_time:%Y-%m-%d %H:%M UTC}")
    print(f"Hard end:     {END_TIME_UTC:%Y-%m-%d %H:%M UTC}")
    print(f"Max requests: {MAX_REQUESTS}")
    print(f"Max matches:  {args.max_matches}")
    print(f"CSV output:   {csv_path}")
    print(f"Mode:         {'DRY-RUN (no Pinnacle calls)' if args.dry_run else 'LIVE'}")
    print("=" * 70)

    matches = fetch_weekend_matches(args.max_matches)
    print(f"\n[setup] Found {len(matches)} weekend matches in priority leagues.")
    for m in matches[:10]:
        print(f"  - {m['kickoff']:%Y-%m-%d %H:%M} | {m['home_team']:<25} vs {m['away_team']:<25} | {m['league']}")
    if len(matches) > 10:
        print(f"  ... +{len(matches) - 10} more")

    throttler = Throttler()
    mapping = resolve_pinnacle_ids(matches, throttler)
    if not mapping:
        print("\n[abort] No Pinnacle ID matches — nothing to poll.")
        return 2

    if args.dry_run:
        print(f"\n[dry-run] Would poll {len(mapping)} matches. Exiting.")
        return 0

    # CSV header if file is new
    file_exists = csv_path.exists()
    csvf = csv_path.open("a", newline="")
    writer = csv.writer(csvf)
    if not file_exists:
        writer.writerow([
            "fetch_time_utc", "our_match_id", "pinnacle_matchup_id",
            "kickoff_utc", "home_team", "away_team", "league",
            "home_american", "draw_american", "away_american",
            "home_decimal", "draw_decimal", "away_decimal",
            "version", "cutoff_at",
        ])
        csvf.flush()

    # Tracking per-match last-poll time so we can space them
    last_poll: dict[str, datetime] = {}
    matches_by_id = {str(m["id"]): m for m in matches}

    print("\n[poll] Starting polling loop. Ctrl-C to stop.")
    print(f"[poll] Will poll each match every {MIN_POLL_INTERVAL.total_seconds()/60:.0f}min "
          f"when within {POLL_WINDOW_BEFORE_KICKOFF.total_seconds()/3600:.0f}h of kickoff.")

    iteration = 0
    while True:
        iteration += 1
        now = datetime.now(timezone.utc)
        if now >= END_TIME_UTC:
            print(f"\n[abort] End-time reached ({END_TIME_UTC}). Exiting cleanly.")
            break
        if throttler.req_count >= MAX_REQUESTS:
            print(f"\n[abort] Request budget exhausted ({MAX_REQUESTS}). Exiting cleanly.")
            break

        polled_this_round = 0
        for our_id, pin_id in mapping.items():
            m = matches_by_id[our_id]
            kickoff = m["kickoff"]
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            # Within the polling window?
            time_to_kickoff = kickoff - now
            if time_to_kickoff < timedelta(seconds=0):
                continue  # already kicked off, skip
            if time_to_kickoff > POLL_WINDOW_BEFORE_KICKOFF:
                continue  # too early to poll
            # Not too recently polled?
            last = last_poll.get(our_id)
            if last and (now - last) < MIN_POLL_INTERVAL:
                continue
            # Poll
            markets = fetch_match_markets(pin_id, throttler)
            last_poll[our_id] = now
            polled_this_round += 1
            if not markets:
                print(f"  · {now:%H:%M:%S} | {m['home_team'][:15]} vs {m['away_team'][:15]} — no markets")
                continue
            line = extract_1x2(markets)
            if not line:
                print(f"  · {now:%H:%M:%S} | {m['home_team'][:15]} vs {m['away_team'][:15]} — no 1X2 open")
                continue
            h_am = line.get("home")
            d_am = line.get("draw")
            a_am = line.get("away")
            h_dec = american_to_decimal(h_am) if h_am is not None else None
            d_dec = american_to_decimal(d_am) if d_am is not None else None
            a_dec = american_to_decimal(a_am) if a_am is not None else None
            writer.writerow([
                now.isoformat(), our_id, pin_id, kickoff.isoformat(),
                m["home_team"], m["away_team"], m["league"],
                h_am, d_am, a_am, h_dec, d_dec, a_dec,
                line.get("version"), line.get("cutoffAt"),
            ])
            csvf.flush()
            print(f"  · {now:%H:%M:%S} | {m['home_team'][:15]:<15} vs {m['away_team'][:15]:<15} | "
                  f"H={h_dec} D={d_dec} A={a_dec} | T-{time_to_kickoff.total_seconds()/60:.0f}min | "
                  f"req={throttler.req_count}/{MAX_REQUESTS}")

        if polled_this_round == 0:
            # Nothing in window — sleep longer
            print(f"[idle] No matches in polling window (iter {iteration}, "
                  f"req={throttler.req_count}/{MAX_REQUESTS}). Sleeping 5 min.")
            time.sleep(300)
        else:
            # Short pause before next round
            time.sleep(30)

    csvf.close()
    print(f"\n[done] {throttler.req_count} requests made. CSV: {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
