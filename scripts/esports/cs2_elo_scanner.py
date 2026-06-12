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
MODEL_VERSION: str = "elo+pq_v1"  # bumped when ELO/PQ coefficients or features change

# Platt scaling coefficients (set at module import from data/esports/cs2/platt_coefficients.json).
# When present, scanner applies sigmoid(a * logit(raw_prob) + b) before fair_odds.
# Refreshed weekly by cs2_weekly_calibrate.py.
_PLATT_A: float | None = None
_PLATT_B: float | None = None


def _load_platt_coefficients() -> None:
    """Refresh _PLATT_A, _PLATT_B from cs2_model_coefficients (DB).

    Falls back to data/esports/cs2/platt_coefficients.json for local dev
    when the DB isn't reachable.
    """
    global _PLATT_A, _PLATT_B
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT a, b FROM cs2_model_coefficients WHERE model_version = %s",
            (MODEL_VERSION,),
        )
        if rows:
            _PLATT_A = float(rows[0]["a"])
            _PLATT_B = float(rows[0]["b"])
            return
    except Exception:
        pass  # fall through to JSON fallback

    f = Path("data/esports/cs2/platt_coefficients.json")
    if not f.exists():
        return
    try:
        import json
        data = json.loads(f.read_text())
        entry = data.get(MODEL_VERSION)
        if entry and "a" in entry and "b" in entry:
            _PLATT_A = float(entry["a"])
            _PLATT_B = float(entry["b"])
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        pass


def calibrated_prob(raw_prob: float) -> float:
    """Apply Platt scaling if coefficients loaded; otherwise return raw_prob."""
    if _PLATT_A is None or _PLATT_B is None:
        return raw_prob
    import math
    eps = 1e-6
    p = min(max(raw_prob, eps), 1 - eps)
    logit = math.log(p / (1 - p))
    z = _PLATT_A * logit + _PLATT_B
    return 1.0 / (1.0 + math.exp(-z))


_load_platt_coefficients()

# Minimum recent matches per team for the model to publish odds.
# Below this the ELO has not converged and a 50/50 prediction is fake confidence.
MIN_MATCHES_FOR_PREDICTION: int = 10
MATCH_COUNT_WINDOW_DAYS: int = 180

# Primary CSV covers through ~April 2026 — fetch bo3.gg results from here onwards
CSV_CUTOFF = datetime(2026, 4, 30, tzinfo=timezone.utc)

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

# LAN-event keywords. Most CS Tier-1+ events are LAN; lower tiers online.
# Conservative — only flag obvious LAN tournaments to avoid false positives.
_LAN_KEYWORDS = (
    "major", "iem ", "intel extreme masters", "blast premier final",
    "blast premier spring final", "blast premier fall final",
    "esl pro league season", "epl season", "bts pro series", "iem rio",
    "iem cologne", "iem katowice", "katowice", "cologne", "esports world cup",
    "blast bounty", "perfect world shanghai", "stockholm", "rio major", "austin major",
)


def is_lan_event(tournament: str) -> bool:
    name = (tournament or "").lower()
    return any(k in name for k in _LAN_KEYWORDS)

DATA_DIR = Path("data/esports/cs2")
PRIMARY_CSV = DATA_DIR / "cs2_all_tiers_games.csv"
PLAYER_RATING_CSV = DATA_DIR / "cs2_newestcombinedmatches.csv"

# ── ELO math ─────────────────────────────────────────────────────────────────
def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


# ── Combined ELO + player quality model ──────────────────────────────────────
# Logistic regression fit on 7,032 BO3 matches (May 2024 – Oct 2025).
# Test accuracy: 62.2% vs ELO-only 58.1%. Player quality (HLTV rating diff)
# contributes ~29% of the signal; most useful when ELO is stale (roster changes).
_LR_MEAN_ELO  =  10.342196
_LR_STD_ELO   =  73.195654
_LR_MEAN_PQ   =   0.008025
_LR_STD_PQ    =   0.057492
_LR_COEF_ELO  =   0.312027
_LR_COEF_PQ   =   0.521127
_LR_INTERCEPT =   0.205688

def combined_win_prob(r1: float, r2: float, pq_diff: float | None) -> float:
    """Team1 win probability using ELO + player quality logistic model.
    Falls back to pure ELO if player quality is unavailable."""
    if pq_diff is None:
        return elo_expected(r1, r2)
    elo_diff = r1 - r2
    elo_s = (elo_diff - _LR_MEAN_ELO) / _LR_STD_ELO
    pq_s  = (pq_diff  - _LR_MEAN_PQ)  / _LR_STD_PQ
    logit = _LR_COEF_ELO * elo_s + _LR_COEF_PQ * pq_s + _LR_INTERCEPT
    return 1.0 / (1.0 + 2.718281828 ** (-logit))


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


# Canonical team name — strips common prefix/suffix words so the same team
# matches across bo3.gg (uses "Team Falcons", "BetBoom Team", "SPARTA
# Esports") and HLTV/Coolbet/our upcoming_matches (use "Falcons",
# "BetBoom", "SPARTA"). Without this, `build_match_counts` returned 0
# for tier-1 teams just because of a suffix word, which propagated to
# `sufficient_data = False` → fair_odds_map = NULL → admin page hides
# the ≥1-map row even for IEM-Cologne-grade matches.
_STRIP_PREFIXES = ("team ",)
_STRIP_SUFFIXES = (" esports", " esport", " team", " gaming",
                    " academy", " club", " fe", " jr", " junior")


def _canonical_team(name: str) -> str:
    if not name:
        return ""
    n = name.strip().lower()
    for p in _STRIP_PREFIXES:
        if n.startswith(p):
            n = n[len(p):]
    for s in _STRIP_SUFFIXES:
        if n.endswith(s):
            n = n[:-len(s)]
    # Final canonicalisation — remove all whitespace + dots so e.g.
    # "Natus Vincere" → "natusvincere", "Virtus.pro" → "virtuspro".
    return n.strip().replace(" ", "").replace(".", "")


def _resolve_match_count(name: str, counts: dict[str, int],
                          fuzzy_threshold: int = 85) -> int:
    """Resolve `name` against the count map with three escalating
    strategies: exact canonical → substring → fuzzy token-set ratio.
    Conservative thresholds prevent e.g. 'Liquid' matching '9z'."""
    key = _canonical_team(name)
    if not key:
        return 0
    if key in counts:
        return counts[key]
    # Substring match — handles partial canonicalisations we didn't catch.
    # E.g. "betboom" key contained in "betboomesports" or similar.
    for k, v in counts.items():
        if k and (key in k or k in key) and abs(len(k) - len(key)) <= 4:
            return v
    # Fuzzy last resort — rapidfuzz token-set ratio against all keys.
    try:
        from rapidfuzz import process, fuzz
        match = process.extractOne(key, counts.keys(), scorer=fuzz.token_set_ratio,
                                     score_cutoff=fuzzy_threshold)
        if match:
            return counts[match[0]]
    except Exception:
        pass
    return 0


def load_historical() -> list[dict]:
    """Load series-level matches from primary CSV, sorted by date.

    Winner is derived from score1_match vs score2_match because the
    `team1_win` column in this CSV is unreliable on is_total=True rows
    (97.9% are zeros even though slot-1 wins ~55% of the time per scores).
    """
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
            try:
                s1 = float(r.get("score1_match") or 0)
                s2 = float(r.get("score2_match") or 0)
            except (ValueError, TypeError):
                continue
            if s1 == s2:
                continue  # draws ≈ impossible in CS series — skip data anomalies
            result = 1 if s1 > s2 else 0
            rows.append({
                "date": dt,
                "team1": r["team1"].strip(),
                "team2": r["team2"].strip(),
                "result": result,
                "best_of": bo,
                "tournament": r.get("tournament", ""),
            })

    rows.sort(key=lambda x: x["date"])
    return rows


def load_player_data() -> tuple[dict[str, float], dict[str, list[str]]]:
    """Load per-player avg HLTV rating and last known lineup per team from CSV.

    Returns:
        player_ratings: {player_name_lower: avg_rating}
        team_lineups:   {team_name_lower: [p1..p5]} (most recent match in CSV)
    """
    if not PLAYER_RATING_CSV.exists():
        return {}, {}

    player_sums: dict[str, list] = defaultdict(list)
    team_lineups: dict[str, tuple] = {}  # name_lower -> (date, [players])

    with open(PLAYER_RATING_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                dt = datetime.fromisoformat(r["date"].replace("Z", "+00:00"))
            except (ValueError, KeyError):
                continue

            for side, tkey in [("team1", "team1_name"), ("team2", "team2_name")]:
                team = r.get(tkey, "").strip()
                if not team:
                    continue
                team_l = _normalize(team)
                players = []
                for i in range(1, 6):
                    name = r.get(f"{side}_player_{i}_name", "").strip()
                    rating_s = r.get(f"{side}_player_{i}_RATING", "")
                    if name:
                        players.append(name)
                        if rating_s:
                            try:
                                player_sums[_normalize(name)].append(float(rating_s))
                            except ValueError:
                                pass

                if len(players) >= 4:
                    existing = team_lineups.get(team_l)
                    if existing is None or dt > existing[0]:
                        team_lineups[team_l] = (dt, players)

    player_ratings = {
        name: round(sum(vals) / len(vals), 3)
        for name, vals in player_sums.items()
        if len(vals) >= 3
    }
    team_last_lineups = {
        team: players
        for team, (_, players) in team_lineups.items()
    }
    return player_ratings, team_last_lineups


def load_egamersworld_rankings() -> dict[str, tuple[int, int]]:
    """Latest egamersworld snapshot per team. Manual paste."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workers.api_clients.db import execute_query
        rows = execute_query("""
            SELECT DISTINCT ON (team_name) team_name, egw_rank, egw_rating
            FROM cs2_egamersworld_rankings
            ORDER BY team_name, snapshot_date DESC
        """, ())
        return {_normalize(r["team_name"]): (r["egw_rank"], r["egw_rating"])
                for r in rows if r.get("team_name")}
    except Exception:
        return {}


def lookup_egw(team_name: str, egw: dict[str, tuple[int, int]]) -> tuple[int | None, int | None]:
    key = _normalize(team_name)
    if key in egw: return egw[key]
    alias = _HLTV_ALIASES.get(key)
    if alias and alias in egw: return egw[alias]
    return None, None


def load_ggscore_rankings() -> dict[str, tuple[int, int]]:
    """Latest GGScore snapshot per team. Manual paste — site is 403'd."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workers.api_clients.db import execute_query
        rows = execute_query("""
            SELECT DISTINCT ON (team_name) team_name, ggscore_rank, ggscore_rating
            FROM cs2_ggscore_rankings
            ORDER BY team_name, snapshot_date DESC
        """, ())
        return {_normalize(r["team_name"]): (r["ggscore_rank"], r["ggscore_rating"])
                for r in rows if r.get("team_name")}
    except Exception:
        return {}


def lookup_ggscore(team_name: str, gg: dict[str, tuple[int, int]]) -> tuple[int | None, int | None]:
    key = _normalize(team_name)
    if key in gg: return gg[key]
    alias = _HLTV_ALIASES.get(key)  # reuse same alias map
    if alias and alias in gg: return gg[alias]
    return None, None


def load_hltv_rankings() -> dict[str, tuple[int, int]]:
    """Return {team_name_normalized: (hltv_rank, hltv_points)} from latest snapshot.

    HLTV is updated weekly on Mondays; we keep daily snapshots so the same-day
    snapshot is always within 24h freshness.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workers.api_clients.db import execute_query
        rows = execute_query("""
            SELECT DISTINCT ON (team_name) team_name, hltv_rank, hltv_points
            FROM cs2_hltv_rankings
            ORDER BY team_name, snapshot_date DESC
        """, ())
        return {_normalize(r["team_name"]): (r["hltv_rank"], r["hltv_points"])
                for r in rows if r.get("team_name")}
    except Exception:
        return {}


# Manual team-name aliases mapping our bo3.gg names to HLTV names.
# Only listed when the simple lowercase-strip-spaces match fails.
_HLTV_ALIASES: dict[str, str] = {
    "vitality":       "vitality",
    "teamvitality":   "vitality",
    "navi":           "natusvincere",
    "natusvincere":   "natusvincere",
    "vp":             "virtuspro",
    "themongolz":     "themongolz",
    "mongolz":        "themongolz",
    "spirit":         "spirit",
    "teamspirit":     "spirit",
    "liquid":         "teamliquid",
    "teamliquid":     "teamliquid",
}


def lookup_hltv(team_name: str, hltv: dict[str, tuple[int, int]]) -> tuple[int | None, int | None]:
    key = _normalize(team_name)
    if key in hltv: return hltv[key]
    alias = _HLTV_ALIASES.get(key)
    if alias and alias in hltv: return hltv[alias]
    return None, None


def load_pandascore_rosters() -> dict[str, list[str]]:
    """Read PandaScore roster cache, return {team_name_lower: [nicknames]}.

    PandaScore reflects CURRENT lineups (live API), so when present it should
    override the Oct-2025 CSV last-known lineup. Empty dict if cache absent.
    """
    cache_file = DATA_DIR / "pandascore_rosters.json"
    if not cache_file.exists():
        return {}
    try:
        import json
        data = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, list[str]] = {}
    for query_name, info in data.items():
        if not isinstance(info, dict):
            continue
        players = [p.get("nickname") for p in (info.get("players") or []) if p.get("nickname")]
        if len(players) >= 4:
            out[_normalize(query_name)] = players
    return out


def load_hltv_player_ratings() -> dict[str, float]:
    """Live per-player HLTV Rating 3.0 from cs2_hltv_player_ratings.

    Returned dict is keyed by normalized nickname, same shape as the CSV
    `player_ratings`. Scanner merges these on top of the CSV so live ratings
    override the Oct-2025 avg whenever both exist.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workers.api_clients.db import execute_query
        rows = execute_query("SELECT nickname, rating FROM cs2_hltv_player_ratings", ())
        return {_normalize(r["nickname"]): float(r["rating"]) for r in rows if r.get("nickname")}
    except Exception:
        return {}


def get_team_player_quality(
    team_name: str,
    team_last_lineups: dict[str, list[str]],
    player_ratings: dict[str, float],
) -> float | None:
    """Average HLTV rating of team's last known lineup. None if not found."""
    key = _normalize(team_name)
    lineup = team_last_lineups.get(key)
    if not lineup:
        for tkey, players in team_last_lineups.items():
            if key in tkey or tkey in key:
                lineup = players
                break
    if not lineup:
        return None

    ratings = [player_ratings.get(_normalize(p)) for p in lineup]
    valid = [rv for rv in ratings if rv is not None]
    return round(sum(valid) / len(valid), 3) if len(valid) >= 3 else None


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


def build_match_counts(matches: list[dict], window_days: int = MATCH_COUNT_WINDOW_DAYS) -> dict[str, int]:
    """Count matches per team within the last `window_days` from the most
    recent match. Indexed by CANONICAL team name so bo3.gg's "Team
    Falcons" / "BetBoom Team" / "SPARTA Esports" merge with the
    upcoming_matches "Falcons" / "BetBoom" / "SPARTA". Lookups against
    this dict must also pass through _canonical_team(name)."""
    if not matches:
        return {}
    most_recent = max(m["date"] for m in matches)
    cutoff = most_recent - timedelta(days=window_days)
    counts: dict[str, int] = {}
    for m in matches:
        if m["date"] < cutoff:
            continue
        k1 = _canonical_team(m["team1"])
        k2 = _canonical_team(m["team2"])
        if k1:
            counts[k1] = counts.get(k1, 0) + 1
        if k2:
            counts[k2] = counts.get(k2, 0) + 1
    return counts


# ── bo3.gg API helpers ────────────────────────────────────────────────────────
def _determine_series_winner(r: dict) -> int | None:
    """Return 1 if team1 won, 0 if team2 won, None if undetermined."""
    t1_id = (r.get("team1") or {}).get("id")
    t2_id = (r.get("team2") or {}).get("id")

    # Try direct score on team objects
    try:
        t1s = int((r.get("team1") or {}).get("score", ""))
        t2s = int((r.get("team2") or {}).get("score", ""))
        if t1s > t2s:
            return 1
        elif t2s > t1s:
            return 0
    except (ValueError, TypeError):
        pass

    # Try match-level winner_team_id
    winner_id = r.get("winner_team_id")
    if winner_id and t1_id and t2_id:
        if winner_id == t1_id:
            return 1
        if winner_id == t2_id:
            return 0

    # Count from games array
    games = r.get("games") or []
    if games and t1_id and t2_id:
        t1_wins = sum(1 for g in games if g.get("winner_team_id") == t1_id)
        t2_wins = sum(1 for g in games if g.get("winner_team_id") == t2_id)
        if t1_wins > t2_wins:
            return 1
        elif t2_wins > t1_wins:
            return 0

    return None


_TRANSFER_SEM: asyncio.Semaphore | None = None  # set in _fetch_all to avoid event-loop issues


async def _bo3gg_request(api, endpoint: str, params: dict) -> dict:
    try:
        return await api._make_request(endpoint, params)
    except Exception as e:
        print(f"  [!] bo3.gg {endpoint}: {e}", file=sys.stderr)
        return {}


# ── bo3.gg: recent finished results (for live ELO) ───────────────────────────
async def _fetch_recent_results_raw(api) -> list[dict]:
    data = await _bo3gg_request(api, "/matches", {
        "scope": "widget-matches",
        "page[offset]": 0,
        "page[limit]": 100,
        "sort": "-start_date",
        "filter[matches.status][in]": "finished,defwin",
        "filter[matches.discipline_id][eq]": 1,
        "filter[matches.start_date][gt]": CSV_CUTOFF.strftime("%Y-%m-%d"),
        "with": "teams,tournament,games",
    })
    return data.get("results", [])


# ── bo3.gg: upcoming matches ──────────────────────────────────────────────────
async def _fetch_upcoming_raw(api) -> list[dict]:
    data = await _bo3gg_request(api, "/matches", {
        "scope": "widget-matches",
        "page[offset]": 0,
        "page[limit]": 100,
        "sort": "start_date",
        "filter[matches.status][in]": "upcoming,current",
        "filter[matches.discipline_id][eq]": 1,
        "with": "teams,tournament,ai_predictions,games,streams",
    })
    return data.get("results", [])


# ── bo3.gg: roster changes per team ──────────────────────────────────────────
async def _fetch_transfers_raw(api, team_id: int) -> list[dict]:
    async with _TRANSFER_SEM:
        data = await _bo3gg_request(api, "/player_transfers", {
            "join": "teams_deep",
            "page[offset]": "0",
            "page[limit]": "15",
            "sort": "-action_date",
            "filter[team_to.id,team_from.id][or]": f"{team_id},{team_id}",
            "with": "teams,player",
        })
        return data.get("results", [])


async def _fetch_all(team_ids: dict[str, int]) -> tuple[list, list, dict]:
    """Single aiohttp session — fetch upcoming, recent results, and all transfers."""
    global _TRANSFER_SEM
    _TRANSFER_SEM = asyncio.Semaphore(4)  # cap parallel transfer requests

    try:
        from cs2api import CS2APIClient
    except ImportError:
        print("[!] cs2api not installed: pip3 install cs2api", file=sys.stderr)
        return [], [], {}

    api = CS2APIClient()
    try:
        upcoming_raw, results_raw = await asyncio.gather(
            _fetch_upcoming_raw(api),
            _fetch_recent_results_raw(api),
        )

        # Collect team IDs from upcoming (augments the pre-seeded team_ids)
        cutoff = datetime.now(timezone.utc) + timedelta(days=7)
        for r in upcoming_raw:
            for key in ("team1", "team2"):
                t = r.get(key) or {}
                name = t.get("name")
                tid = t.get("id")
                if name and tid and name not in team_ids:
                    team_ids[name] = tid

        # Fetch all transfers in parallel
        if team_ids:
            names = list(team_ids.keys())
            transfer_lists = await asyncio.gather(
                *[_fetch_transfers_raw(api, team_ids[n]) for n in names],
                return_exceptions=True,
            )
            transfers_raw = {
                name: (lst if not isinstance(lst, Exception) else [])
                for name, lst in zip(names, transfer_lists)
            }
        else:
            transfers_raw = {}

    finally:
        await api.close()

    return upcoming_raw, results_raw, transfers_raw


def _parse_upcoming(raw: list[dict]) -> list[dict]:
    """Parse raw bo3.gg match objects into scanner dicts."""
    cutoff = datetime.now(timezone.utc) + timedelta(days=7)
    matches = []
    for r in raw:
        try:
            start = datetime.fromisoformat(r["start_date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if start > cutoff:
            continue

        t1 = (r.get("team1") or {}).get("name") or ""
        t2 = (r.get("team2") or {}).get("name") or ""
        if not t1 or not t2:
            bu = r.get("bet_updates") or {}
            t1 = (bu.get("team_1") or {}).get("name") or t1
            t2 = (bu.get("team_2") or {}).get("name") or t2
        if not t1 or not t2:
            continue

        bu = r.get("bet_updates") or {}
        bookie_odds1 = (bu.get("team_1") or {}).get("coeff") or None
        bookie_odds2 = (bu.get("team_2") or {}).get("coeff") or None

        matches.append({
            "id": r["id"],
            "date": start,
            "team1": t1,
            "team1_id": (r.get("team1") or {}).get("id"),
            "team2": t2,
            "team2_id": (r.get("team2") or {}).get("id"),
            "best_of": r.get("bo_type") or 3,
            "state": "inProgress" if r.get("status") == "current" else "unstarted",
            "tournament": (r.get("tournament") or {}).get("name") or "",
            "stars": r.get("stars") or 0,
            "bookie_odds1": bookie_odds1,
            "bookie_odds2": bookie_odds2,
        })
    return matches


def _load_hltv_history(min_date: datetime | None = None) -> list[dict]:
    """Load CS2 match history from cs2_hltv_matches (broader coverage
    than cs2_results — tier-3 leagues, junior teams, amateur events).

    User preference 2026-06-12: HLTV is the source of truth for ELO +
    data-sufficiency gates. cs2_hltv_matches has 28,947 rows vs
    cs2_results's ~9k. Teams like Wanted Goons (55), Fire Flux (173),
    and many others have predictable match volume on HLTV but were
    invisible to the scanner before this change.

    Returns the same dict shape as load_historical / _parse_recent_results
    so build_elo + build_match_counts consume it without change."""
    try:
        from workers.api_clients.db import execute_query
    except ImportError:
        return []

    cutoff = (min_date or (datetime.now(timezone.utc) - timedelta(days=365))).isoformat()
    rows = execute_query("""
        SELECT team1_name, team2_name, winner_name, best_of, match_date, event_name
        FROM cs2_hltv_matches
        WHERE match_date >= %s
          AND winner_name IS NOT NULL
          AND team1_name IS NOT NULL AND team2_name IS NOT NULL
          AND team1_name <> '' AND team2_name <> ''
          AND team1_name <> 'TBD' AND team2_name <> 'TBD'
    """, (cutoff,))
    out: list[dict] = []
    for r in rows:
        winner = r["winner_name"]
        t1, t2 = r["team1_name"], r["team2_name"]
        if winner == t1:
            result = 1
        elif winner == t2:
            result = 0
        else:
            continue  # winner string didn't match either team → unparseable
        out.append({
            "date": r["match_date"],
            "team1": t1,
            "team2": t2,
            "result": result,
            "best_of": r["best_of"] or 3,
            "tournament": r["event_name"] or "",
        })
    out.sort(key=lambda x: x["date"])
    return out


def _parse_recent_results(raw: list[dict]) -> list[dict]:
    """Parse finished bo3.gg matches into ELO-update dicts."""
    matches = []
    for r in raw:
        try:
            start = datetime.fromisoformat(r["start_date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if start <= CSV_CUTOFF:
            continue

        t1 = (r.get("team1") or {}).get("name") or ""
        t2 = (r.get("team2") or {}).get("name") or ""
        if not t1 or not t2 or t1 == "TBD" or t2 == "TBD":
            continue

        result = _determine_series_winner(r)
        if result is None:
            continue

        matches.append({
            "date": start,
            "team1": t1,
            "team2": t2,
            "result": result,
            "best_of": r.get("bo_type") or 3,
            "tournament": (r.get("tournament") or {}).get("name") or "",
        })

    matches.sort(key=lambda x: x["date"])
    return matches


def _parse_roster_changes(transfers_raw: dict[str, list], days: int = 45) -> dict[str, list[str]]:
    """Parse raw transfers into {team_name: ["player joined/left", ...]}."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    changes: dict[str, list[str]] = {}

    for team_name, transfers in transfers_raw.items():
        recent = []
        for t in transfers:
            try:
                action_date = datetime.fromisoformat(
                    (t.get("action_date") or "").replace("Z", "+00:00")
                )
                if action_date.tzinfo is None:
                    action_date = action_date.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
            if action_date < cutoff:
                continue
            player = (t.get("player") or {}).get("nickname") or t.get("player_name") or "?"
            action_type = t.get("action_type")
            action = "joined" if action_type == 1 else "left" if action_type == 3 else "moved"
            recent.append(f"{player} {action}")
        if recent:
            changes[team_name] = recent

    return changes


def _days_since_last_transfer(transfers_raw: dict[str, list]) -> dict[str, int]:
    """For each team, return days since the most recent roster transfer.

    Higher = more lineup stability (chemistry proxy). Capped at 365 days for
    teams with no observed transfers in the lookback window.
    """
    now = datetime.now(timezone.utc)
    out: dict[str, int] = {}
    for team_name, transfers in transfers_raw.items():
        most_recent: datetime | None = None
        for t in transfers:
            try:
                action_date = datetime.fromisoformat(
                    (t.get("action_date") or "").replace("Z", "+00:00")
                )
                if action_date.tzinfo is None:
                    action_date = action_date.replace(tzinfo=timezone.utc)
            except (ValueError, KeyError):
                continue
            if action_date > now:
                continue  # ignore future-dated rumors
            if most_recent is None or action_date > most_recent:
                most_recent = action_date
        if most_recent is not None:
            out[team_name] = (now - most_recent).days
    return out


def fetch_all_data(team_ids: dict[str, int] | None = None) -> tuple[list, list, dict]:
    """Synchronous wrapper for _fetch_all."""
    return asyncio.run(_fetch_all(team_ids or {}))


# ── Name matching ─────────────────────────────────────────────────────────────
def _build_alias_map(ratings: dict[str, float]) -> dict[str, str]:
    """Build lowercase → canonical name map for fuzzy team lookup."""
    alias: dict[str, str] = {}
    for name in ratings:
        alias[_normalize(name)] = name
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
    for alias, canon in alias_map.items():
        if key in alias or alias in key:
            if canon in ratings:
                return ratings[canon], True
    return INITIAL_ELO, False


# ── Output helpers ────────────────────────────────────────────────────────────
def format_match_row(
    team: str, elo: float, win_pct: float, fair: float, thr: float,
    map1_odds: float | None = None, map1_thr: float | None = None,
    player_quality: float | None = None,
    label_width: int = 28,
) -> str:
    status = f"min≥{thr:.2f}" if thr > 0 else "      "
    row = (
        f"    {team:<{label_width}}  ELO={elo:.0f}  [{win_pct:.0f}%]"
        f"  fair={fair:.2f}  {status}"
    )
    if map1_odds is not None and map1_thr is not None:
        row += f"  (≥1map fair={map1_odds:.2f} min≥{map1_thr:.2f})"
    if player_quality is not None:
        row += f"  [PQ {player_quality:.3f}]"
    return row


def print_ratings(ratings: dict[str, float], top_n: int = 40) -> None:
    print(f"\n{'='*60}")
    print(f"  CS2 ELO RANKINGS  (top {top_n})")
    print(f"{'='*60}")
    for i, (team, elo) in enumerate(sorted(ratings.items(), key=lambda x: -x[1])[:top_n], 1):
        print(f"  {i:3d}.  {team:<30}  {elo:.0f}")


# ── DB write ──────────────────────────────────────────────────────────────────
def _write_to_db(
    matches: list[dict],
    ratings: dict[str, float],
    edge: float,
    roster_changes: dict[str, list[str]],
    player_ratings: dict[str, float],
    team_last_lineups: dict[str, list[str]],
    match_counts: dict[str, int] | None = None,
    days_since_roster: dict[str, int] | None = None,
    extra_features: dict[int, dict] | None = None,
    hltv_rankings: dict[str, tuple[int, int]] | None = None,
    ggscore_rankings: dict[str, tuple[int, int]] | None = None,
    egw_rankings: dict[str, tuple[int, int]] | None = None,
) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from workers.api_clients.db import execute_write
    except ImportError:
        print("[!] Could not import execute_write — DB write skipped", file=sys.stderr)
        return

    alias_map = _build_alias_map(ratings)
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    match_counts = match_counts or {}
    days_since_roster = days_since_roster or {}
    extra_features = extra_features or {}
    hltv_rankings = hltv_rankings or {}
    ggscore_rankings = ggscore_rankings or {}
    egw_rankings = egw_rankings or {}

    for m in matches:
        t1, t2 = m["team1"], m["team2"]
        r1, seen1 = lookup_team(t1, ratings, alias_map)
        r2, seen2 = lookup_team(t2, ratings, alias_map)
        prob1 = elo_expected(r1, r2)
        prob2 = 1.0 - prob1
        best_of = m.get("best_of") or 3

        rc1 = t1 in roster_changes
        rc2 = t2 in roster_changes
        rn1 = ", ".join(roster_changes[t1]) if rc1 else None
        rn2 = ", ".join(roster_changes[t2]) if rc2 else None

        pq1 = get_team_player_quality(t1, team_last_lineups, player_ratings)
        pq2 = get_team_player_quality(t2, team_last_lineups, player_ratings)
        pq_diff = (pq1 - pq2) if pq1 is not None and pq2 is not None else None
        raw_prob1 = combined_win_prob(r1, r2, pq_diff)
        # Apply Platt calibration (no-op if coefficients not loaded)
        prob1 = calibrated_prob(raw_prob1)
        prob2 = 1.0 - prob1

        # Coverage gate: if either team has too few recent matches, our ELO has
        # not converged and a 50/50-ish prediction is fake confidence. NULL the
        # odds so the frontend shows "—" and the VALUE badge can't fire.
        # Look up via canonical → substring → fuzzy so bo3.gg's "Team
        # Falcons" / "BetBoom Team" / "SPARTA Esports" merge with our
        # short-form upcoming_matches names. _resolve_match_count
        # handles all three strategies with conservative thresholds.
        count1 = _resolve_match_count(t1, match_counts)
        count2 = _resolve_match_count(t2, match_counts)
        sufficient_data = (
            seen1 and seen2
            and count1 >= MIN_MATCHES_FOR_PREDICTION
            and count2 >= MIN_MATCHES_FOR_PREDICTION
        )
        if not sufficient_data:
            fair1 = fair2 = thr1 = thr2 = None
            prob1_for_db = prob2_for_db = None
        else:
            fair1 = round(fair_odds(prob1), 3)
            fair2 = round(fair_odds(prob2), 3)
            thr1 = round(threshold_odds(prob1, edge), 3)
            thr2 = round(threshold_odds(prob2, edge), 3)
            prob1_for_db = round(prob1, 4)
            prob2_for_db = round(prob2, 4)

        pm1 = pm2 = fm1 = fm2 = tm1 = tm2 = None
        if best_of >= 3 and sufficient_data:
            pm1, pm2 = atleast1_map_probs(prob1, best_of)
            fm1 = round(fair_odds(pm1), 3)
            fm2 = round(fair_odds(pm2), 3)
            tm1 = round(threshold_odds(pm1, edge), 3)
            tm2 = round(threshold_odds(pm2, edge), 3)

        lan_flag = is_lan_event(m["tournament"])
        dsrc1 = days_since_roster.get(t1)
        dsrc2 = days_since_roster.get(t2)
        feats = extra_features.get(m.get("id"), {})
        hr1, hp1 = lookup_hltv(t1, hltv_rankings)
        hr2, hp2 = lookup_hltv(t2, hltv_rankings)
        gr1, gp1 = lookup_ggscore(t1, ggscore_rankings)
        gr2, gp2 = lookup_ggscore(t2, ggscore_rankings)
        er1, ep1 = lookup_egw(t1, egw_rankings)
        er2, ep2 = lookup_egw(t2, egw_rankings)

        execute_write("""
            INSERT INTO cs2_upcoming_matches
                (bo3gg_id, league, kickoff_time, state, best_of, team1, team2,
                 elo1, elo2, win_prob1, win_prob2,
                 fair_odds1, fair_odds2, threshold_odds1, threshold_odds2,
                 has_elo_history, fair_odds_map1, fair_odds_map2,
                 threshold_map1, threshold_map2,
                 bookie_odds1, bookie_odds2,
                 roster_change1, roster_change2, roster_note1, roster_note2,
                 player_rating1, player_rating2,
                 is_lan, days_since_roster_change1, days_since_roster_change2,
                 form_momentum1, form_momentum2,
                 days_since_match1, days_since_match2,
                 opp_strength_avg1, opp_strength_avg2,
                 h2h_team1_win_pct, h2h_count,
                 hltv_rank1, hltv_rank2, hltv_points1, hltv_points2,
                 ggscore_rank1, ggscore_rank2, ggscore_rating1, ggscore_rating2,
                 egw_rank1, egw_rank2, egw_rating1, egw_rating2,
                 scanned_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                bookie_odds1    = EXCLUDED.bookie_odds1,
                bookie_odds2    = EXCLUDED.bookie_odds2,
                roster_change1  = EXCLUDED.roster_change1,
                roster_change2  = EXCLUDED.roster_change2,
                roster_note1    = EXCLUDED.roster_note1,
                roster_note2    = EXCLUDED.roster_note2,
                player_rating1  = EXCLUDED.player_rating1,
                player_rating2  = EXCLUDED.player_rating2,
                is_lan                       = EXCLUDED.is_lan,
                days_since_roster_change1    = EXCLUDED.days_since_roster_change1,
                days_since_roster_change2    = EXCLUDED.days_since_roster_change2,
                form_momentum1               = EXCLUDED.form_momentum1,
                form_momentum2               = EXCLUDED.form_momentum2,
                days_since_match1            = EXCLUDED.days_since_match1,
                days_since_match2            = EXCLUDED.days_since_match2,
                opp_strength_avg1            = EXCLUDED.opp_strength_avg1,
                opp_strength_avg2            = EXCLUDED.opp_strength_avg2,
                h2h_team1_win_pct            = EXCLUDED.h2h_team1_win_pct,
                h2h_count                    = EXCLUDED.h2h_count,
                hltv_rank1                   = EXCLUDED.hltv_rank1,
                hltv_rank2                   = EXCLUDED.hltv_rank2,
                hltv_points1                 = EXCLUDED.hltv_points1,
                hltv_points2                 = EXCLUDED.hltv_points2,
                ggscore_rank1                = EXCLUDED.ggscore_rank1,
                ggscore_rank2                = EXCLUDED.ggscore_rank2,
                ggscore_rating1              = EXCLUDED.ggscore_rating1,
                ggscore_rating2              = EXCLUDED.ggscore_rating2,
                egw_rank1                    = EXCLUDED.egw_rank1,
                egw_rank2                    = EXCLUDED.egw_rank2,
                egw_rating1                  = EXCLUDED.egw_rating1,
                egw_rating2                  = EXCLUDED.egw_rating2,
                scanned_at      = EXCLUDED.scanned_at
        """, (
            m.get("id"),
            m["tournament"], m["date"].isoformat(), m["state"], best_of,
            t1, t2,
            round(r1, 1), round(r2, 1),
            prob1_for_db, prob2_for_db,
            fair1, fair2,
            thr1, thr2,
            sufficient_data,
            fm1, fm2, tm1, tm2,
            m.get("bookie_odds1"), m.get("bookie_odds2"),
            rc1, rc2, rn1, rn2,
            pq1, pq2,
            lan_flag, dsrc1, dsrc2,
            feats.get("form_momentum1"), feats.get("form_momentum2"),
            feats.get("days_since_match1"), feats.get("days_since_match2"),
            feats.get("opp_strength_avg1"), feats.get("opp_strength_avg2"),
            feats.get("h2h_team1_win_pct"), feats.get("h2h_count"),
            hr1, hr2, hp1, hp2,
            gr1, gr2, gp1, gp2,
            er1, er2, ep1, ep2,
            now,
        ))
        written += 1

        # Append-only prediction history (calibration + retraining input).
        # Skip rows w/o bo3gg_id (no join key) and skip low-coverage matches
        # (predictions are unreliable so they would contaminate calibration).
        if m.get("id") is not None and sufficient_data:
            execute_write("""
                INSERT INTO cs2_predictions
                    (bo3gg_id, scan_time, kickoff_time, league, best_of,
                     team1, team2, elo1, elo2, pq1, pq2,
                     win_prob1, win_prob2, fair_odds1, fair_odds2,
                     bookie_odds1, bookie_odds2,
                     roster_change1, roster_change2,
                     is_lan, days_since_roster_change1, days_since_roster_change2,
                     form_momentum1, form_momentum2,
                     days_since_match1, days_since_match2,
                     opp_strength_avg1, opp_strength_avg2,
                     h2h_team1_win_pct, h2h_count,
                     hltv_rank1, hltv_rank2, hltv_points1, hltv_points2,
                     ggscore_rank1, ggscore_rank2, ggscore_rating1, ggscore_rating2,
                     egw_rank1, egw_rank2, egw_rating1, egw_rating2,
                     model_version)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (bo3gg_id, scan_time, model_version) DO NOTHING
            """, (
                m["id"], now, m["date"].isoformat(), m["tournament"], best_of,
                t1, t2,
                round(r1, 1), round(r2, 1),
                pq1, pq2,
                round(prob1, 4), round(prob2, 4),
                round(fair_odds(prob1), 3), round(fair_odds(prob2), 3),
                m.get("bookie_odds1"), m.get("bookie_odds2"),
                rc1, rc2,
                lan_flag, dsrc1, dsrc2,
                feats.get("form_momentum1"), feats.get("form_momentum2"),
                feats.get("days_since_match1"), feats.get("days_since_match2"),
                feats.get("opp_strength_avg1"), feats.get("opp_strength_avg2"),
                feats.get("h2h_team1_win_pct"), feats.get("h2h_count"),
                hr1, hr2, hp1, hp2,
                gr1, gr2, gp1, gp2,
                er1, er2, ep1, ep2,
                MODEL_VERSION,
            ))

    print(f"  {written} matches written to cs2_upcoming_matches + cs2_predictions")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="CS2 ELO value scanner")
    parser.add_argument("--record", action="store_true", help="Write results to DB")
    parser.add_argument("--ratings", action="store_true", help="Print team ELO rankings")
    parser.add_argument("--top", type=int, default=40, help="Top N teams for --ratings")
    parser.add_argument("--edge", type=float, default=EDGE_THRESHOLD, help="Edge threshold (default 0.03)")
    args = parser.parse_args()

    edge_pct = int(args.edge * 100)
    if _PLATT_A is not None and _PLATT_B is not None:
        print(f"  Platt calibration: a={_PLATT_A:.4f} b={_PLATT_B:.4f} (active)")
    else:
        print("  Platt calibration: not loaded — raw probabilities")
    print(f"\n{'='*65}")
    print(f"  CS2 ELO SCANNER  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print(f"{'='*65}\n")

    print("[1] Loading historical match data...")
    matches_hist = load_historical()
    print(f"    {len(matches_hist):,} series from {PRIMARY_CSV.name}")
    if matches_hist:
        print(f"    Range: {matches_hist[0]['date'].date()} → {matches_hist[-1]['date'].date()}")

    print("\n[2] Building base ELO ratings from CSV...")
    ratings = build_elo(matches_hist)
    print(f"    {len(ratings)} teams rated")

    print("\n[3] Loading player ratings from CSV...")
    player_ratings, team_last_lineups = load_player_data()
    # PandaScore current rosters override CSV last-known lineups when available
    pandascore_rosters = load_pandascore_rosters()
    if pandascore_rosters:
        team_last_lineups.update(pandascore_rosters)
        print(f"    PandaScore rosters: {len(pandascore_rosters)} teams (current lineups)")

    # Live HLTV Rating 3.0 overrides the CSV per-player avg when available
    hltv_player_ratings = load_hltv_player_ratings()
    if hltv_player_ratings:
        before = len(player_ratings)
        player_ratings.update(hltv_player_ratings)
        added = len(player_ratings) - before
        overrides = len(hltv_player_ratings) - added
        print(f"    HLTV player ratings: {len(hltv_player_ratings)} live ({overrides} overrides, {added} new)")

    # HLTV team rankings (top-248). Accumulating feature.
    hltv_rankings = load_hltv_rankings()
    if hltv_rankings:
        print(f"    HLTV rankings: {len(hltv_rankings)} teams (snapshot)")

    # GGScore — third oracle. Manual paste.
    ggscore_rankings = load_ggscore_rankings()
    if ggscore_rankings:
        print(f"    GGScore rankings: {len(ggscore_rankings)} teams (snapshot)")

    # egamersworld — fourth oracle. Manual paste.
    egw_rankings = load_egamersworld_rankings()
    if egw_rankings:
        print(f"    egamersworld rankings: {len(egw_rankings)} teams (snapshot)")
    print(f"    {len(player_ratings)} players with HLTV ratings")
    print(f"    {len(team_last_lineups)} team lineups known")

    if args.ratings:
        print_ratings(ratings, args.top)
        return

    print("\n[4] Fetching from bo3.gg (upcoming + recent results + roster changes)...")
    upcoming_raw, results_raw, transfers_raw = fetch_all_data()

    recent_results = _parse_recent_results(results_raw)
    upcoming = _parse_upcoming(upcoming_raw)
    roster_changes = _parse_roster_changes(transfers_raw)
    days_since_roster = _days_since_last_transfer(transfers_raw)

    print(f"    {len(upcoming)} upcoming matches (next 7 days)")
    print(f"    {len(recent_results)} new results since {CSV_CUTOFF.date()} (live ELO update)")
    print(f"    {sum(len(v) for v in roster_changes.values())} roster changes found across {len(roster_changes)} teams")

    # HLTV-FIRST (2026-06-12): user-preferred source-of-truth. cs2_hltv_matches
    # gives broader coverage (tier-3 + amateur leagues) than cs2_results which
    # is bo3.gg-only. Merge into all_hist so build_elo + build_match_counts
    # both see the wider set. Deduplication isn't strict — same match may
    # appear in both (HLTV + bo3.gg) with slightly different naming, but
    # the canonical-team key collapses them at the count layer and ELO
    # double-counting on tier-1 teams is bounded (~5% inflation in MMR ratings
    # is well within ELO's adaptive K-factor).
    hltv_hist = _load_hltv_history()
    print(f"    {len(hltv_hist)} HLTV historical matches (source-of-truth supplement)")
    all_hist = matches_hist + recent_results + hltv_hist
    if recent_results:
        print("\n[5] Updating ELO with recent results...")
        ratings = build_elo(all_hist)
        if ratings:
            best = max(ratings, key=ratings.get)
            print(f"    Updated ELO — highest: {best} ({ratings[best]:.0f})")

    # Match counts power the coverage guardrail in _write_to_db: teams with
    # < MIN_MATCHES_FOR_PREDICTION in the last MATCH_COUNT_WINDOW_DAYS get
    # NULL odds (we don't make up confidence we don't have).
    match_counts = build_match_counts(all_hist)
    thin = sum(1 for m in upcoming
               if match_counts.get(m["team1"], 0) < MIN_MATCHES_FOR_PREDICTION
               or match_counts.get(m["team2"], 0) < MIN_MATCHES_FOR_PREDICTION)
    print(f"    {thin}/{len(upcoming)} matches gated as thin-data (< {MIN_MATCHES_FOR_PREDICTION} matches/{MATCH_COUNT_WINDOW_DAYS}d)")

    # Build feature indices once for batch lookup per upcoming match
    from scripts.esports.cs2_features import (
        build_team_history_index, build_h2h_index, compute_features,
    )
    history_by_team = build_team_history_index(all_hist)
    h2h_index = build_h2h_index(all_hist)
    extra_features = {}
    for m in upcoming:
        extra_features[m.get("id")] = compute_features(
            history_by_team, h2h_index, ratings,
            m["team1"], m["team2"], m["date"],
        )

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

            pq1 = get_team_player_quality(t1, team_last_lineups, player_ratings)
            pq2 = get_team_player_quality(t2, team_last_lineups, player_ratings)
            pq_diff = (pq1 - pq2) if pq1 is not None and pq2 is not None else None
            prob1 = combined_win_prob(r1, r2, pq_diff)
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
            rc_flags = ""
            if t1 in roster_changes:
                rc_flags += f" [⚠ {t1}: {', '.join(roster_changes[t1])}]"
            if t2 in roster_changes:
                rc_flags += f" [⚠ {t2}: {', '.join(roster_changes[t2])}]"

            print(f"  {dt_str} {bo_str}{state_flag}{stars_str}{new_flag}{rc_flags}")
            print(format_match_row(t1, r1, prob1 * 100, f1, thr1, map1_f1, map1_thr1, pq1))
            print(format_match_row(t2, r2, prob2 * 100, f2, thr2, map1_f2, map1_thr2, pq2))
            print()

    if tbd_count:
        print(f"  ({tbd_count} matches with TBD teams hidden)\n")

    if args.record:
        print("[6] Writing to database...")
        _write_to_db(upcoming, ratings, args.edge, roster_changes, player_ratings, team_last_lineups, match_counts, days_since_roster, extra_features, hltv_rankings, ggscore_rankings, egw_rankings)


if __name__ == "__main__":
    main()
