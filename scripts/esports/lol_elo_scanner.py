#!/usr/bin/env python3
"""
LoL ELO Scanner — uses Riot Games public esports API (no key required for public key).

Flow:
  1. Fetch historical match results from Riot LoL esports API (with cache)
  2. Build ELO ratings from chronological match history
  3. Fetch upcoming scheduled matches
  4. Print each matchup: ELO ratings, fair win %, fair odds, minimum betting threshold

Usage:
    python3 scripts/esports/lol_elo_scanner.py           # print upcoming matches
    python3 scripts/esports/lol_elo_scanner.py --record   # also write to lol_upcoming_matches DB
    python3 scripts/esports/lol_elo_scanner.py --refresh  # force re-fetch historical data
    python3 scripts/esports/lol_elo_scanner.py --ratings  # print all current team ratings

No .env keys needed — Riot LoL esports API uses a well-known public key.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── Config ───────────────────────────────────────────────────────────────────
RIOT_API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
BASE_URL = "https://esports-api.lolesports.com/persisted/gw"
HEADERS = {"x-api-key": RIOT_API_KEY}

CACHE_PATH = Path("data/esports/riot_lol_matches.json")
CACHE_MAX_AGE_HOURS = 6

INITIAL_ELO = 1500
ELO_K_BASE = 32

# Leagues to track — id: (name, tier_weight, max_pages)
LEAGUES: dict[str, tuple[str, float, int]] = {
    "98767975604431411":  ("Worlds",      2.0, 8),
    "98767991325878492":  ("MSI",         1.8, 8),
    "113464388705111224": ("First Stand", 1.8, 6),
    "98767991302996019":  ("LEC",         1.4, 10),
    "98767991310872058":  ("LCK",         1.4, 10),
    "98767991314006698":  ("LPL",         1.4, 10),
    "98767991299243165":  ("LCS",         1.3, 10),
    "100695891328981122": ("EMEA Masters",1.1, 6),
    "98767991332355509":  ("CBLOL",       1.0, 6),
    "113476371197627891": ("LCP",         1.0, 6),
    "98767991349978712":  ("LJL",         1.0, 5),
    "98767991343597634":  ("TCL",         1.0, 5),
}

EDGE_THRESHOLD = 0.03     # minimum edge to flag as actionable
DISPLAY_DAYS   = 7        # show upcoming matches within N days

# ── ELO helpers ──────────────────────────────────────────────────────────────
def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def fair_odds(prob: float) -> float:
    return round(1.0 / prob, 3) if prob > 0 else 999.0


def threshold_odds(prob: float, edge: float = EDGE_THRESHOLD) -> float:
    """Minimum decimal odds that give >=edge% return over fair probability."""
    return round(fair_odds(prob) * (1 - edge), 2)


# ── Data fetching ────────────────────────────────────────────────────────────
def _schedule_page(league_id: str, page_token: str | None = None) -> dict:
    params: dict = {"hl": "en-US", "leagueId": league_id}
    if page_token:
        params["pageToken"] = page_token
    r = requests.get(f"{BASE_URL}/getSchedule", headers=HEADERS, params=params, timeout=12)
    r.raise_for_status()
    return r.json().get("data", {}).get("schedule", {})


def fetch_historical(max_pages: int = 10) -> list[dict]:
    """Fetch all completed matches, paginating backwards."""
    matches: list[dict] = []
    for league_id, (league_name, tier_w, pages_cap) in LEAGUES.items():
        pages_left = min(max_pages, pages_cap)
        page_token: str | None = None
        while pages_left > 0:
            try:
                sched = _schedule_page(league_id, page_token)
            except Exception as e:
                print(f"  Warning: {league_name} fetch failed — {e}", file=sys.stderr)
                break
            events = sched.get("events", [])
            for ev in events:
                if ev.get("state") != "completed" or ev.get("type") != "match":
                    continue
                match_data = ev.get("match", {})
                teams = match_data.get("teams", [])
                if len(teams) < 2:
                    continue
                t1, t2 = teams[0], teams[1]
                r1 = t1.get("result", {})
                r2 = t2.get("result", {})
                winner = None
                if r1.get("outcome") == "win":
                    winner = t1.get("name")
                elif r2.get("outcome") == "win":
                    winner = t2.get("name")
                if not winner:
                    continue
                gw1 = r1.get("gameWins", 0) or 0
                gw2 = r2.get("gameWins", 0) or 0
                best_of = (gw1 + gw2) * 2 - 1  # 1→BO1, 3→BO3, 5→BO5 approx
                matches.append({
                    "date":       ev.get("startTime", "")[:10],
                    "league":     league_name,
                    "league_id":  league_id,
                    "tier_weight": tier_w,
                    "team1":      t1.get("name", ""),
                    "team2":      t2.get("name", ""),
                    "winner":     winner,
                    "best_of":    max(1, best_of),
                    "gw1":        gw1,
                    "gw2":        gw2,
                })
            older = sched.get("pages", {}).get("older")
            if not older:
                break
            page_token = older
            pages_left -= 1
            time.sleep(0.15)
    return matches


def load_cached_matches(force_refresh: bool = False) -> list[dict]:
    """Return cached matches if fresh, otherwise fetch and cache."""
    if not force_refresh and CACHE_PATH.exists():
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(
            CACHE_PATH.stat().st_mtime, tz=timezone.utc
        )
        if age < timedelta(hours=CACHE_MAX_AGE_HOURS):
            data = json.loads(CACHE_PATH.read_text())
            return data.get("matches", [])

    print(f"  Fetching historical LoL match data from Riot API...", file=sys.stderr)
    matches = fetch_historical()
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "matches": matches,
    }, indent=2))
    print(f"  Cached {len(matches)} matches → {CACHE_PATH}", file=sys.stderr)
    return matches


# ── ELO model ────────────────────────────────────────────────────────────────
def _k_factor(tier_weight: float, best_of: int) -> float:
    bo_scale = {1: 0.60, 3: 0.85, 5: 1.00}.get(best_of, 0.85)
    return ELO_K_BASE * tier_weight * bo_scale


def build_elo(matches: list[dict]) -> dict[str, float]:
    ratings: dict[str, float] = defaultdict(lambda: INITIAL_ELO)
    for m in sorted(matches, key=lambda x: x["date"]):
        t1, t2 = m["team1"], m["team2"]
        k = _k_factor(m.get("tier_weight", 1.0), m.get("best_of", 3))
        r1, r2 = ratings[t1], ratings[t2]
        exp1 = elo_expected(r1, r2)
        t1_won = (m["winner"] == t1)
        score = 1.0 if t1_won else 0.0
        ratings[t1] = r1 + k * (score - exp1)
        ratings[t2] = r2 + k * ((1 - score) - (1 - exp1))
    return dict(ratings)


# ── Upcoming matches ──────────────────────────────────────────────────────────
def fetch_upcoming() -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=DISPLAY_DAYS)
    upcoming: list[dict] = []

    for league_id, (league_name, _, _) in LEAGUES.items():
        try:
            sched = _schedule_page(league_id)
        except Exception:
            continue
        for ev in sched.get("events", []):
            if ev.get("type") != "match":
                continue
            state = ev.get("state", "")
            if state not in ("unstarted", "inProgress"):
                continue
            start_str = ev.get("startTime", "")
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if start_dt > cutoff:
                continue
            teams = ev.get("match", {}).get("teams", [])
            if len(teams) < 2:
                continue
            upcoming.append({
                "date":      start_dt,
                "league":    league_name,
                "state":     state,
                "team1":     teams[0].get("name", ""),
                "team2":     teams[1].get("name", ""),
                "best_of":   ev.get("match", {}).get("strategy", {}).get("count", 3),
            })
        time.sleep(0.1)

    return sorted(upcoming, key=lambda x: x["date"])


# ── Output helpers ────────────────────────────────────────────────────────────
def format_match_row(
    team: str, elo: float, win_pct: float, team_odds: float, threshold: float,
    label_width: int = 26
) -> str:
    label = f"[{win_pct:.0f}%]"
    status = f"min≥{threshold:.2f}" if team_odds >= threshold else "       "
    return (
        f"    {team:<{label_width}}  ELO={elo:.0f}  {label:>5}  "
        f"fair={team_odds:.2f}  {status}"
    )


def print_ratings(ratings: dict[str, float], top_n: int = 40) -> None:
    sorted_teams = sorted(ratings.items(), key=lambda x: -x[1])
    print(f"\n{'Rank':<6} {'Team':<30} {'ELO':>6}")
    print("─" * 50)
    for rank, (team, elo) in enumerate(sorted_teams[:top_n], 1):
        print(f"  {rank:<4} {team:<30} {elo:>6.0f}")


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh",  action="store_true", help="Force re-fetch historical data")
    ap.add_argument("--ratings",  action="store_true", help="Print all current ELO ratings")
    ap.add_argument("--record",   action="store_true", help="Write upcoming matches to DB")
    edge_pct = int(EDGE_THRESHOLD * 100)
    ap.add_argument("--edge", type=float, default=EDGE_THRESHOLD,
                    help=f"Edge threshold to flag bets (default {edge_pct}%%)")
    args = ap.parse_args()

    print("=" * 65)
    print(f"LOL ELO SCANNER  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 65)

    # ── 1. Load data + build ratings ──────────────────────────────────
    print("\n[1] Loading historical match data...")
    matches = load_cached_matches(force_refresh=args.refresh)
    print(f"    {len(matches):,} matches from Riot API")

    # Supplement with Oracle's Elixir CSV data if files are present
    oe_matches = load_oracle_elixir()
    if oe_matches:
        print(f"    {len(oe_matches):,} matches from Oracle's Elixir (merging...)")
        # Deduplicate: Riot API is authoritative for overlapping dates
        # Keep OE matches that predate oldest Riot match (older history)
        riot_oldest = min((m["date"] for m in matches), default="9999")
        oe_older = [m for m in oe_matches if m["date"] < riot_oldest]
        matches = oe_older + matches
        print(f"    {len(matches):,} total matches after merge (OE adds pre-{riot_oldest} data)")
    if not matches:
        print("    No data. Try: python3 scripts/esports/lol_elo_scanner.py --refresh")
        return

    ratings = build_elo(matches)
    print(f"    {len(ratings)} teams rated")

    last_date = max(m["date"] for m in matches)
    print(f"    Most recent data: {last_date}")

    if args.ratings:
        print_ratings(ratings)
        print()
        return

    # ── 2. Fetch upcoming schedule ────────────────────────────────────
    print("\n[2] Fetching upcoming matches...")
    upcoming = fetch_upcoming()
    print(f"    {len(upcoming)} matches in next {DISPLAY_DAYS} days")

    if not upcoming:
        print("    No upcoming matches found.\n")
        return

    # ── 3. Print match analysis ────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"UPCOMING MATCHES — ELO FAIR ODDS + THRESHOLDS (≥{args.edge:.0%} edge)")
    print(f"{'='*65}")
    print(f"  {'min≥ = minimum odds you need at bookmaker to have edge. Higher is better.'}")
    print()

    # Filter out TBD-vs-TBD (bracket slots not yet filled)
    known_upcoming = [m for m in upcoming if m["team1"] != "TBD" or m["team2"] != "TBD"]
    tbd_count = len(upcoming) - len(known_upcoming)

    current_league = None
    for m in known_upcoming:
        if m["league"] != current_league:
            current_league = m["league"]
            print(f"  ── {current_league} ──")

        t1, t2 = m["team1"], m["team2"]
        # Skip if both teams unknown
        if t1 == "TBD" and t2 == "TBD":
            continue
        r1 = ratings.get(t1, INITIAL_ELO)
        r2 = ratings.get(t2, INITIAL_ELO)
        prob1 = elo_expected(r1, r2)
        prob2 = 1.0 - prob1
        f1 = fair_odds(prob1)
        f2 = fair_odds(prob2)
        thr1 = threshold_odds(prob1, args.edge)
        thr2 = threshold_odds(prob2, args.edge)

        # Flag if we haven't seen either team before
        seen1 = t1 in ratings and t1 != "TBD"
        seen2 = t2 in ratings and t2 != "TBD"
        new_flag = " [NEW — no ELO history]" if not seen1 or not seen2 else ""

        dt_str = m["date"].strftime("%m-%d %H:%M")
        bo_str = f"BO{m['best_of']}" if m.get("best_of") else "   "
        state_flag = " ⚡LIVE" if m["state"] == "inProgress" else ""
        print(f"  {dt_str} {bo_str}{state_flag}{new_flag}")
        print(format_match_row(t1, r1, prob1 * 100, f1, thr1))
        print(format_match_row(t2, r2, prob2 * 100, f2, thr2))
        print()

    if tbd_count:
        print(f"  ({tbd_count} bracket matches with TBD teams hidden — check back closer to event)")
        print()

    # ── 4. Write to DB ────────────────────────────────────────────────
    if args.record:
        _write_to_db(known_upcoming, ratings, args.edge)

    # ── 5. Summary ────────────────────────────────────────────────────
    print(f"{'='*65}")
    print(f"  Matches shown:    {len(known_upcoming)} (+ {tbd_count} TBD)")
    print(f"  Teams with data:  {len(ratings)}")
    print(f"  ELO range:        {min(ratings.values()):.0f} – {max(ratings.values()):.0f}")
    print(f"  Data freshness:   last match {last_date}")
    print()
    print("  NOTE: ELO does not account for roster changes. When a key")
    print("  player is subbed or benched, treat the ELO as stale until")
    print("  the team plays its first match with the new roster.")
    print()


# ── DB writer ────────────────────────────────────────────────────────────────
def _write_to_db(matches: list[dict], ratings: dict[str, float], edge: float) -> None:
    """Upsert upcoming matches + ELO data into lol_upcoming_matches."""
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
    from workers.api_clients.db import execute_write

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for m in matches:
        t1, t2 = m["team1"], m["team2"]
        if t1 == "TBD" and t2 == "TBD":
            continue
        r1 = ratings.get(t1, INITIAL_ELO)
        r2 = ratings.get(t2, INITIAL_ELO)
        prob1 = elo_expected(r1, r2)
        prob2 = 1.0 - prob1
        seen1 = t1 in ratings and t1 != "TBD"
        seen2 = t2 in ratings and t2 != "TBD"
        execute_write("""
            INSERT INTO lol_upcoming_matches
                (league, kickoff_time, state, best_of, team1, team2,
                 elo1, elo2, win_prob1, win_prob2,
                 fair_odds1, fair_odds2, threshold_odds1, threshold_odds2,
                 has_elo_history, scanned_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (team1, team2, kickoff_time) DO UPDATE SET
                state           = EXCLUDED.state,
                elo1            = EXCLUDED.elo1,
                elo2            = EXCLUDED.elo2,
                win_prob1       = EXCLUDED.win_prob1,
                win_prob2       = EXCLUDED.win_prob2,
                fair_odds1      = EXCLUDED.fair_odds1,
                fair_odds2      = EXCLUDED.fair_odds2,
                threshold_odds1 = EXCLUDED.threshold_odds1,
                threshold_odds2 = EXCLUDED.threshold_odds2,
                has_elo_history = EXCLUDED.has_elo_history,
                scanned_at      = EXCLUDED.scanned_at
        """, (
            m["league"],
            m["date"].isoformat(),
            m["state"],
            m.get("best_of") or 3,
            t1, t2,
            round(r1, 1), round(r2, 1),
            round(prob1, 4), round(prob2, 4),
            round(fair_odds(prob1), 3), round(fair_odds(prob2), 3),
            round(threshold_odds(prob1, edge), 3), round(threshold_odds(prob2, edge), 3),
            seen1 and seen2,
            now,
        ))
        written += 1

    print(f"  ✓ Written {written} matches to lol_upcoming_matches")


# ── Oracle's Elixir loader (optional enrichment) ──────────────────────────────
def load_oracle_elixir(years: list[int] | None = None) -> list[dict]:
    """
    Load historical LoL match data from Oracle's Elixir CSV files.

    Files expected at: data/esports/lol_YYYY.csv
    Download from: https://oracleselixir.com/tools/downloads (Google Drive mirror)
    Get years 2023-2026 for useful training signal.

    Returns same format as fetch_historical() so it can be merged.
    """
    base = Path("data/esports")
    if years is None:
        years = list(range(2020, 2027))

    # League name → tier weight (same mapping as LEAGUES)
    TIER_MAP = {
        "worlds": 2.0, "msi": 1.8, "first stand": 1.8,
        "lec": 1.4, "lck": 1.4, "lpl": 1.4, "lcs": 1.3,
        "emea masters": 1.1, "lck challengers": 1.0,
        "cblol": 1.0, "lcp": 1.0, "ljl": 1.0, "tcl": 1.0,
    }

    matches: list[dict] = []
    for year in years:
        path = base / f"lol_{year}.csv"
        if not path.exists():
            continue
        try:
            import pandas as pd
            df = pd.read_csv(path, low_memory=False)
        except Exception as e:
            print(f"  Warning: could not load {path}: {e}", file=sys.stderr)
            continue

        # Keep only team-summary rows
        team_rows = df[df["position"] == "team"].copy()
        if team_rows.empty:
            continue

        # Build series-level results: group by (gameid without game suffix)
        # gameid format: YYYY/league/series_NNNN_gameN or similar
        # Use 'game' column to aggregate within a series
        for gid, grp in team_rows.groupby("gameid"):
            if len(grp) != 2:
                continue
            blue = grp[grp["side"] == "Blue"]
            red  = grp[grp["side"] == "Red"]
            if blue.empty or red.empty:
                continue
            b = blue.iloc[0]
            r = red.iloc[0]
            winner = b["teamname"] if b["result"] == 1 else r["teamname"]
            league_raw = str(b.get("league", "") or "").strip()
            tier_w = TIER_MAP.get(league_raw.lower(), 1.0)
            raw_game = b.get("game")
            game_num = 1 if (raw_game is None or (isinstance(raw_game, float) and raw_game != raw_game)) else int(float(raw_game))
            # BO inferred from max game number seen per series is done post-hoc;
            # use game column as BO proxy (game 1 = probably BO1/3/5)
            matches.append({
                "date":        str(b.get("date", ""))[:10],
                "league":      league_raw,
                "league_id":   f"oe_{league_raw.lower().replace(' ', '_')}",
                "tier_weight": tier_w,
                "team1":       str(b["teamname"]).strip(),
                "team2":       str(r["teamname"]).strip(),
                "winner":      winner,
                "best_of":     3,  # Oracle's Elixir doesn't encode BO directly
                "gw1":         int(b.get("result", 0)),
                "gw2":         int(r.get("result", 0)),
                "source":      "oracle_elixir",
            })
    return matches


if __name__ == "__main__":
    main()
