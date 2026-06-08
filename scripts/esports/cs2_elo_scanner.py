#!/usr/bin/env python3
"""
CS2 ELO scanner — builds team ratings from historical CSV data (bo3.gg / HLTV)
and fetches upcoming matches from bo3.gg API to compute fair odds + thresholds.

Usage:
    python3 scripts/esports/cs2_elo_scanner.py             # print value sheet
    python3 scripts/esports/cs2_elo_scanner.py --record    # write to DB
    python3 scripts/esports/cs2_elo_scanner.py --ratings   # print ranked teams
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from math import comb
from pathlib import Path
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
INITIAL_ELO: float = 1500.0
K_BASE: float = 32.0
EDGE_THRESHOLD: float = 0.03   # 3% edge required

# Tournament tier multipliers (applied to K-factor)
TIER_WEIGHTS: list[tuple[list[str], float]] = [
    (["major", "cologne major", "katowice major", "rio major", "paris major",
      "austin major", "copenhagen major", "astana major"], 2.0),
    (["pro league", "iem cologne", "iem katowice", "blast premier final",
      "blast premier spring final", "blast premier fall final"], 1.7),
    (["iem ", "blast", "esl pro", "intel extreme masters", "perfect world major",
      "pgl major"], 1.4),
    (["challenger league", "regional series", "open qualifier", "closed qualifier",
      "yalla", "elisa invitational"], 0.85),
]

# BO weighting
BO_WEIGHTS: dict[int, float] = {5: 1.0, 3: 0.85, 1: 0.6}

DATA_DIR = Path("data/esports/cs2")
PRIMARY_CSV = DATA_DIR / "cs2_all_tiers_games.csv"

# ── ELO math ─────────────────────────────────────────────────────────────────
def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def fair_odds(prob: float) -> float:
    return round(1.0 / prob, 3) if prob > 0.001 else 999.0


def threshold_odds(prob: float, edge: float = EDGE_THRESHOLD) -> float:
    return round(fair_odds(prob) * (1 - edge), 2)


def tournament_tier(name: str) -> float:
    name_l = name.lower()
    for keywords, weight in TIER_WEIGHTS:
        if any(kw in name_l for kw in keywords):
            return weight
    return 1.0


def bo_weight(best_of: int) -> float:
    return BO_WEIGHTS.get(best_of, 0.85)


# ── ≥1 map market (same bisection as LoL scanner) ────────────────────────────
def p_map_from_series(p_series: float, best_of: int) -> float:
    if best_of <= 1:
        return p_series
    maps_to_win = (best_of + 1) // 2

    def series_prob(p_map: float) -> float:
        total = 0.0
        for losses in range(0, best_of - maps_to_win + 1):
            total += comb(maps_to_win - 1 + losses, losses) * (p_map ** maps_to_win) * ((1 - p_map) ** losses)
        return total

    lo, hi = 0.0001, 0.9999
    for _ in range(60):
        mid = (lo + hi) / 2
        if series_prob(mid) < p_series:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def atleast1_map_probs(p_series: float, best_of: int) -> tuple[float, float]:
    if best_of <= 1:
        return p_series, 1.0 - p_series
    maps_to_win = (best_of + 1) // 2
    p_map = p_map_from_series(p_series, best_of)
    p1_swept = (1 - p_map) ** maps_to_win
    p2_swept = p_map ** maps_to_win
    return (1 - p1_swept), (1 - p2_swept)


# ── Data loading ──────────────────────────────────────────────────────────────
def _normalize(name: str) -> str:
    return name.strip().lower()


def load_historical() -> list[dict]:
    """Load series-level matches from primary CSV, sorted by date."""
    if not PRIMARY_CSV.exists():
        print(f"[!] Primary CSV not found: {PRIMARY_CSV}", file=sys.stderr)
        return []

    rows = []
    with open(PRIMARY_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("is_total") not in ("1", "1.0", "True", "true"):
                continue
            try:
                bo = int(r.get("bestOf") or 3)
            except (ValueError, TypeError):
                bo = 3
            if bo not in (1, 3, 5):
                continue
            try:
                dt = datetime.fromisoformat(r["datetime"].replace(" ", "T"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
            team1_win = r.get("team1_win", "")
            if team1_win in ("1", "1.0", "True", "true"):
                result = 1
            elif team1_win in ("0", "0.0", "False", "false"):
                result = 0
            else:
                continue
            rows.append({
                "date": dt,
                "team1": r["team1"].strip(),
                "team2": r["team2"].strip(),
                "result": result,   # 1 if team1 won
                "best_of": bo,
                "tournament": r.get("tournament", ""),
            })

    rows.sort(key=lambda x: x["date"])
    return rows


# ── ELO builder ───────────────────────────────────────────────────────────────
def build_elo(matches: list[dict]) -> dict[str, float]:
    ratings: dict[str, float] = {}

    for m in matches:
        t1, t2 = m["team1"], m["team2"]
        r1 = ratings.get(t1, INITIAL_ELO)
        r2 = ratings.get(t2, INITIAL_ELO)

        tier = tournament_tier(m["tournament"])
        bo = bo_weight(m["best_of"])
        k = K_BASE * tier * bo

        e1 = elo_expected(r1, r2)
        result = float(m["result"])
        ratings[t1] = r1 + k * (result - e1)
        ratings[t2] = r2 + k * ((1 - result) - (1 - e1))

    return ratings


# ── bo3.gg upcoming matches ───────────────────────────────────────────────────
async def _fetch_bo3gg_upcoming() -> list[dict]:
    """Fetch upcoming + live CS2 matches from bo3.gg (next ~7 days)."""
    try:
        from cs2api import CS2APIClient
    except ImportError:
        print("[!] cs2api not installed: pip3 install cs2api", file=sys.stderr)
        return []

    endpoint = "/matches"
    params = {
        "scope": "widget-matches",
        "page[offset]": 0,
        "page[limit]": 100,
        "sort": "start_date",
        "filter[matches.status][in]": "upcoming,current",
        "filter[matches.discipline_id][eq]": 1,
        "with": "teams,tournament,ai_predictions,games,streams",
    }

    api = CS2APIClient()
    try:
        data = await api._make_request(endpoint, params)
    finally:
        await api.close()

    results = data.get("results", [])
    cutoff = datetime.now(timezone.utc) + timedelta(days=7)
    matches = []
    for r in results:
        try:
            start = datetime.fromisoformat(r["start_date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if start > cutoff:
            continue

        t1 = (r.get("team1") or {}).get("name") or ""
        t2 = (r.get("team2") or {}).get("name") or ""
        if not t1 or not t2:
            # fall back to bet_updates names
            bu = r.get("bet_updates") or {}
            t1 = (bu.get("team_1") or {}).get("name") or t1
            t2 = (bu.get("team_2") or {}).get("name") or t2
        if not t1 or not t2:
            continue

        tournament = (r.get("tournament") or {}).get("name") or ""
        stars = r.get("stars") or 0
        matches.append({
            "id": r["id"],
            "date": start,
            "team1": t1,
            "team2": t2,
            "best_of": r.get("bo_type") or 3,
            "state": "inProgress" if r.get("status") == "current" else "unstarted",
            "tournament": tournament,
            "stars": stars,
        })

    return matches


def fetch_upcoming() -> list[dict]:
    return asyncio.run(_fetch_bo3gg_upcoming())


# ── Name matching ─────────────────────────────────────────────────────────────
def _build_alias_map(ratings: dict[str, float]) -> dict[str, str]:
    """Build lowercase → canonical name map for fuzzy team lookup."""
    alias: dict[str, str] = {}
    for name in ratings:
        alias[_normalize(name)] = name
        # common abbreviations: "Natus Vincere" → "navi", "G2 Esports" → "g2"
        parts = name.lower().split()
        if len(parts) >= 2:
            abbrev = "".join(p[0] for p in parts if p not in ("the", "team", "esports", "gaming"))
            if len(abbrev) >= 2:
                alias.setdefault(abbrev, name)
    return alias


def lookup_team(name: str, ratings: dict[str, float], alias_map: dict[str, str]) -> tuple[float, bool]:
    """Return (elo, found_in_history). Falls back to INITIAL_ELO if unknown."""
    key = _normalize(name)
    canonical = alias_map.get(key)
    if canonical and canonical in ratings:
        return ratings[canonical], True
    # partial match
    for alias, canon in alias_map.items():
        if key in alias or alias in key:
            if canon in ratings:
                return ratings[canon], True
    return INITIAL_ELO, False


# ── Output helpers ────────────────────────────────────────────────────────────
def format_match_row(
    team: str, elo: float, win_pct: float, fair: float, thr: float,
    map1_odds: float | None = None, map1_thr: float | None = None,
    label_width: int = 28,
) -> str:
    status = f"min≥{thr:.2f}" if thr > 0 else "      "
    row = (
        f"    {team:<{label_width}}  ELO={elo:.0f}  [{win_pct:.0f}%]"
        f"  fair={fair:.2f}  {status}"
    )
    if map1_odds is not None and map1_thr is not None:
        row += f"  (≥1map fair={map1_odds:.2f} min≥{map1_thr:.2f})"
    return row


def print_ratings(ratings: dict[str, float], top_n: int = 40) -> None:
    print(f"\n{'='*60}")
    print(f"  CS2 ELO RANKINGS  (top {top_n})")
    print(f"{'='*60}")
    for i, (team, elo) in enumerate(sorted(ratings.items(), key=lambda x: -x[1])[:top_n], 1):
        print(f"  {i:3d}.  {team:<30}  {elo:.0f}")


# ── DB write ──────────────────────────────────────────────────────────────────
def _write_to_db(matches: list[dict], ratings: dict[str, float], edge: float) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from pipeline.supabase_client import execute_write
    except ImportError:
        print("[!] Could not import execute_write — DB write skipped", file=sys.stderr)
        return

    alias_map = _build_alias_map(ratings)
    now = datetime.now(timezone.utc).isoformat()
    written = 0

    for m in matches:
        t1, t2 = m["team1"], m["team2"]
        r1, seen1 = lookup_team(t1, ratings, alias_map)
        r2, seen2 = lookup_team(t2, ratings, alias_map)
        prob1 = elo_expected(r1, r2)
        prob2 = 1.0 - prob1
        best_of = m.get("best_of") or 3

        pm1 = pm2 = fm1 = fm2 = tm1 = tm2 = None
        if best_of >= 3:
            pm1, pm2 = atleast1_map_probs(prob1, best_of)
            fm1 = round(fair_odds(pm1), 3)
            fm2 = round(fair_odds(pm2), 3)
            tm1 = round(threshold_odds(pm1, edge), 3)
            tm2 = round(threshold_odds(pm2, edge), 3)

        execute_write("""
            INSERT INTO cs2_upcoming_matches
                (bo3gg_id, league, kickoff_time, state, best_of, team1, team2,
                 elo1, elo2, win_prob1, win_prob2,
                 fair_odds1, fair_odds2, threshold_odds1, threshold_odds2,
                 has_elo_history, fair_odds_map1, fair_odds_map2,
                 threshold_map1, threshold_map2, scanned_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                fair_odds_map1  = EXCLUDED.fair_odds_map1,
                fair_odds_map2  = EXCLUDED.fair_odds_map2,
                threshold_map1  = EXCLUDED.threshold_map1,
                threshold_map2  = EXCLUDED.threshold_map2,
                scanned_at      = EXCLUDED.scanned_at
        """, (
            m.get("id"),
            m["tournament"], m["date"].isoformat(), m["state"], best_of,
            t1, t2,
            round(r1, 1), round(r2, 1),
            round(prob1, 4), round(prob2, 4),
            round(fair_odds(prob1), 3), round(fair_odds(prob2), 3),
            round(threshold_odds(prob1, edge), 3), round(threshold_odds(prob2, edge), 3),
            seen1 and seen2,
            fm1, fm2, tm1, tm2,
            now,
        ))
        written += 1

    print(f"  {written} matches written to cs2_upcoming_matches")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="CS2 ELO value scanner")
    parser.add_argument("--record", action="store_true", help="Write results to DB")
    parser.add_argument("--ratings", action="store_true", help="Print team ELO rankings")
    parser.add_argument("--top", type=int, default=40, help="Top N teams for --ratings")
    parser.add_argument("--edge", type=float, default=EDGE_THRESHOLD, help="Edge threshold (default 0.03)")
    args = parser.parse_args()

    edge_pct = int(args.edge * 100)
    print(f"\n{'='*65}")
    print(f"  CS2 ELO SCANNER  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'='*65}\n")

    print("[1] Loading historical match data...")
    matches_hist = load_historical()
    print(f"    {len(matches_hist):,} series from {PRIMARY_CSV.name}")
    if matches_hist:
        print(f"    Range: {matches_hist[0]['date'].date()} → {matches_hist[-1]['date'].date()}")

    print("\n[2] Building ELO ratings...")
    ratings = build_elo(matches_hist)
    print(f"    {len(ratings)} teams rated")
    if ratings:
        best = max(ratings, key=ratings.get)
        print(f"    Highest ELO: {best} ({ratings[best]:.0f})")

    if args.ratings:
        print_ratings(ratings, args.top)
        return

    print("\n[3] Fetching upcoming matches from bo3.gg...")
    upcoming = fetch_upcoming()
    print(f"    {len(upcoming)} matches in next 7 days")

    if not upcoming:
        print("\n  No upcoming matches found.")
        return

    alias_map = _build_alias_map(ratings)

    print(f"\n{'='*65}")
    print(f"  UPCOMING MATCHES — ELO FAIR ODDS + THRESHOLDS ({edge_pct}% edge)")
    print(f"{'='*65}")
    print(f"  min≥ = minimum bookmaker odds needed for edge\n")

    tbd_count = 0
    by_tournament: dict[str, list] = defaultdict(list)
    for m in upcoming:
        by_tournament[m["tournament"]].append(m)

    for tournament, t_matches in by_tournament.items():
        print(f"  ── {tournament} ──")
        for m in t_matches:
            t1, t2 = m["team1"], m["team2"]
            r1, found1 = lookup_team(t1, ratings, alias_map)
            r2, found2 = lookup_team(t2, ratings, alias_map)

            if t1 in ("TBD", "") or t2 in ("TBD", ""):
                tbd_count += 1
                continue

            prob1 = elo_expected(r1, r2)
            prob2 = 1.0 - prob1
            f1, f2 = fair_odds(prob1), fair_odds(prob2)
            thr1, thr2 = threshold_odds(prob1, args.edge), threshold_odds(prob2, args.edge)

            best_of = m.get("best_of") or 3
            map1_f1 = map1_thr1 = map1_f2 = map1_thr2 = None
            if best_of >= 3:
                pm1, pm2 = atleast1_map_probs(prob1, best_of)
                map1_f1, map1_thr1 = fair_odds(pm1), threshold_odds(pm1, args.edge)
                map1_f2, map1_thr2 = fair_odds(pm2), threshold_odds(pm2, args.edge)

            new_flag = " [NEW]" if not found1 or not found2 else ""
            dt_str = m["date"].strftime("%m-%d %H:%M")
            bo_str = f"BO{best_of}"
            state_flag = " ⚡LIVE" if m["state"] == "inProgress" else ""
            stars_str = f"  {'★' * m.get('stars', 0)}" if m.get("stars") else ""
            print(f"  {dt_str} {bo_str}{state_flag}{stars_str}{new_flag}")
            print(format_match_row(t1, r1, prob1 * 100, f1, thr1, map1_f1, map1_thr1))
            print(format_match_row(t2, r2, prob2 * 100, f2, thr2, map1_f2, map1_thr2))
            print()

    if tbd_count:
        print(f"  ({tbd_count} matches with TBD teams hidden)\n")

    if args.record:
        print("[4] Writing to database...")
        _write_to_db(upcoming, ratings, args.edge)


if __name__ == "__main__":
    main()
