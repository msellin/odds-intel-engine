"""
OddsIntel — Daily Pipeline v2 (Supabase)
Stores everything in Supabase instead of JSON files.
Frontend can read data directly from the same database.

Usage:
  python daily_pipeline_v2.py            # Morning: fetch + predict + bet
  python daily_pipeline_v2.py settle     # Evening: settle bets with results
  python daily_pipeline_v2.py report     # Anytime: show bot performance
"""

import sys
import os
import time
import json
import numpy as np
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime, date
from scipy.stats import poisson
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.scrapers.kambi_odds import fetch_all_operators, get_target_league_matches
from workers.scrapers.flashscore import get_todays_matches_from_flashscore
from workers.scrapers.sofascore_odds import fetch_all_odds as fetch_sofascore_odds
from workers.scrapers.betexplorer_odds import fetch_gap_leagues_odds as fetch_betexplorer_odds
from workers.api_clients.supabase_client import (
    get_client, ensure_bots, store_match, store_odds,
    store_prediction, store_bet, store_prediction_snapshot, settle_bet,
    get_pending_bets, update_bot_bankroll, update_match_result,
    get_bot_performance, get_todays_matches,
)
from workers.model.improvements import (
    calibrate_prob, compute_odds_movement, compute_alignment,
    compute_kelly, compute_stake, compute_rank_score,
)
from workers.model.xgboost_ensemble import (
    get_xgboost_prediction, ensemble_prediction,
)

console = Console()

ENGINE_DIR = Path(__file__).parent.parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"

STAKE = 10.0

# Bot configurations
BOTS_CONFIG = {
    "bot_v10_all": {
        "description": "v10 model, all target leagues, tier-adjusted thresholds",
        "tier_label": "sharp",
        "markets": ["1x2", "ou"],
        "tier_filter": None,
        "edge_thresholds": {
            1: {"1x2_fav": 0.08, "1x2_long": 0.12, "ou": 0.08},
            2: {"1x2_fav": 0.05, "1x2_long": 0.08, "ou": 0.06},
            3: {"1x2_fav": 0.04, "1x2_long": 0.06, "ou": 0.05},
            4: {"1x2_fav": 0.03, "1x2_long": 0.05, "ou": 0.04},
        },
        "odds_range": (1.30, 4.50),
        "min_prob": 0.30,
    },
    "bot_lower_1x2": {
        "description": "Tier 2-4 only, 1X2 only — our best backtest signal",
        "tier_label": "sharp",
        "markets": ["1x2"],
        "tier_filter": [2, 3, 4],
        "edge_thresholds": {
            2: {"1x2_fav": 0.05, "1x2_long": 0.07},
            3: {"1x2_fav": 0.04, "1x2_long": 0.06},
            4: {"1x2_fav": 0.03, "1x2_long": 0.05},
        },
        "odds_range": (1.35, 3.50),
        "min_prob": 0.35,
    },
    "bot_conservative": {
        "description": "Only bet on 10%+ edge, very selective",
        "tier_label": "sharp",
        "markets": ["1x2"],
        "tier_filter": None,
        "edge_thresholds": {
            1: {"1x2_fav": 0.10, "1x2_long": 0.15},
            2: {"1x2_fav": 0.10, "1x2_long": 0.12},
            3: {"1x2_fav": 0.08, "1x2_long": 0.10},
            4: {"1x2_fav": 0.08, "1x2_long": 0.10},
        },
        "odds_range": (1.50, 3.00),
        "min_prob": 0.40,
    },
    "bot_aggressive": {
        "description": "Low threshold (3% edge), high volume",
        "tier_label": "analyst",
        "markets": ["1x2", "ou"],
        "tier_filter": None,
        "edge_thresholds": {
            1: {"1x2_fav": 0.03, "1x2_long": 0.05, "ou": 0.03},
            2: {"1x2_fav": 0.03, "1x2_long": 0.04, "ou": 0.03},
            3: {"1x2_fav": 0.03, "1x2_long": 0.04, "ou": 0.03},
            4: {"1x2_fav": 0.03, "1x2_long": 0.04, "ou": 0.03},
        },
        "odds_range": (1.25, 5.00),
        "min_prob": 0.25,
    },
    "bot_greek_turkish": {
        # NOTE: +ROI in 2022-25 backtest but -ROI in mega backtest (2005-15).
        # Era discrepancy — treat results here as exploratory until more live data.
        "description": "Only Greek + Turkish leagues — profitable in 2022-25 backtest (era-sensitive)",
        "tier_label": "syndicate",
        "markets": ["1x2"],
        "tier_filter": [1],
        "league_filter": ["Turkey", "Greece"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.04, "1x2_long": 0.06},
        },
        "odds_range": (1.40, 4.00),
        "min_prob": 0.30,
    },
    "bot_high_roi_global": {
        # Based on mega backtest findings (354K matches, 275 leagues, 2005-15).
        # Targets leagues where even a basic Poisson model found consistent edge.
        # Scotland all divs: covers League Two (+12.3% ROI, cross-era confirmed).
        # Austria all divs: covers Erste Liga (+5.5% ROI, 5/7 seasons).
        # Ireland: Division 1 showed +2.7% ROI (4/7 seasons).
        # South Korea: K League Challenge +3.2% ROI (3/3 seasons).
        # Singapore: S.League +27.5% ROI (5/5 seasons) — need odds source, tracked for data.
        # Tier B stake cap (50%) applied since these use targets_global history only.
        "description": "Mega backtest confirmed leagues — Scotland/Austria/Ireland/Korea (Tier B)",
        "tier_label": "syndicate",
        "markets": ["1x2"],
        "tier_filter": None,
        "league_filter": ["Scotland", "Austria", "Ireland", "South Korea", "Singapore"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.06, "1x2_long": 0.09},
            2: {"1x2_fav": 0.05, "1x2_long": 0.08},
            3: {"1x2_fav": 0.05, "1x2_long": 0.08},
        },
        "odds_range": (1.50, 5.00),
        "min_prob": 0.28,
    },
    # ── Optimizer-found bots (2026-04-27) ──────────────────────────────────
    # Grid-searched 412K parameter combos across 1.6M potential bets
    # (football-data 2007-2025 + beat_the_bookie 2005-2015).
    # Only cross-era validated strategies included.
    "bot_opt_away_british": {
        # Confirmed in both FD (+30.6% ROI) and BTB (+15-26% ROI) datasets.
        # Away wins in English lower divisions at mid-range longshot odds.
        "description": "Optimizer: Away wins, T2+ British Isles — cross-era +16% ROI, 336 bets",
        "tier_label": "syndicate",
        "markets": ["1x2"],
        "selection_filter": ["Away"],
        "tier_filter": [2, 3, 4],
        "league_filter": ["England", "Scotland", "Ireland", "Wales"],
        "edge_thresholds": {
            2: {"1x2_fav": 0.05, "1x2_long": 0.05},
            3: {"1x2_fav": 0.05, "1x2_long": 0.05},
            4: {"1x2_fav": 0.05, "1x2_long": 0.05},
        },
        "odds_range": (2.50, 3.00),
        "min_prob": 0.25,
    },
    "bot_opt_away_europe": {
        # Confirmed in FD (+18.8% ROI, CI +4.9% to +32.8%) and BTB (+30.5%).
        # Away wins in Europe top 5 second divisions.
        "description": "Optimizer: Away wins, T2+ Europe top 5 — cross-era +19% ROI, 373 bets",
        "tier_label": "syndicate",
        "markets": ["1x2"],
        "selection_filter": ["Away"],
        "tier_filter": [2, 3, 4],
        "league_filter": ["England", "Spain", "Germany", "Italy", "France"],
        "edge_thresholds": {
            2: {"1x2_fav": 0.05, "1x2_long": 0.05},
            3: {"1x2_fav": 0.05, "1x2_long": 0.05},
            4: {"1x2_fav": 0.05, "1x2_long": 0.05},
        },
        "odds_range": (2.50, 3.00),
        "min_prob": 0.40,
    },
    "bot_opt_home_lower": {
        # Confirmed in FD (+24.2% ROI) and BTB (+12.5% ROI, 448 bets).
        # Home underdogs in lower European divisions.
        "description": "Optimizer: Home underdogs, T2+ Europe — cross-era +14% ROI, 244 bets",
        "tier_label": "syndicate",
        "markets": ["1x2"],
        "selection_filter": ["Home"],
        "tier_filter": [2, 3, 4],
        "edge_thresholds": {
            2: {"1x2_fav": 0.20, "1x2_long": 0.20},
            3: {"1x2_fav": 0.20, "1x2_long": 0.20},
            4: {"1x2_fav": 0.20, "1x2_long": 0.20},
        },
        "odds_range": (3.00, 5.00),
        "min_prob": 0.30,
    },
    "bot_opt_ou_british": {
        # FD only (BTB has no O/U data) — +29% ROI, 85 bets, +22% on O/U combined.
        # Over 2.5 goals in English lower divisions at value odds.
        "description": "Optimizer: O/U T2+ British Isles — FD +22-29% ROI, 85-146 bets",
        "tier_label": "syndicate",
        "markets": ["ou"],
        "tier_filter": [2, 3, 4],
        "league_filter": ["England", "Scotland", "Ireland", "Wales"],
        "edge_thresholds": {
            2: {"ou": 0.07},
            3: {"ou": 0.07},
            4: {"ou": 0.07},
        },
        "odds_range": (2.50, 4.00),
        "min_prob": 0.40,
    },
}


_SOFASCORE_CACHE_PATH = PROCESSED_DIR / "sofascore_team_cache.json"
_SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.sofascore.com/",
}


def _load_sofascore_cache() -> dict:
    if _SOFASCORE_CACHE_PATH.exists():
        try:
            return json.loads(_SOFASCORE_CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_sofascore_cache(cache: dict) -> None:
    _SOFASCORE_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _fetch_sofascore_team_history(team_id: int, cache: dict) -> list[dict] | None:
    """
    Fetch last 15 matches for a Sofascore team_id.
    Returns list of {home_team, away_team, FTHG, FTAG} dicts, or None on failure.
    Results are cached in sofascore_team_cache.json (reset daily by the pipeline).
    """
    key = str(team_id)
    if key in cache:
        return cache[key]

    url = f"https://api.sofascore.com/api/v1/team/{team_id}/events/last/0"
    try:
        resp = requests.get(url, headers=_SOFASCORE_HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        events = data.get("events", [])
        matches = []
        for ev in events:
            ht = ev.get("homeTeam", {}).get("name", "")
            at = ev.get("awayTeam", {}).get("name", "")
            score = ev.get("homeScore", {})
            hg = score.get("current")
            ag = ev.get("awayScore", {}).get("current")
            if ht and at and hg is not None and ag is not None:
                matches.append({
                    "home_team": ht,
                    "away_team": at,
                    "FTHG": int(hg),
                    "FTAG": int(ag),
                })
        matches = matches[-15:]  # Cap at 15 most recent
        cache[key] = matches
        _save_sofascore_cache(cache)
        return matches
    except Exception:
        return None


def _poisson_probs(exp_h: float, exp_a: float) -> dict:
    """Compute 1X2 + O/U 2.5 probabilities from expected goals."""
    p_h = p_d = p_a = p_over = 0.0
    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_h) * poisson.pmf(a, exp_a)
            if h > a:
                p_h += p
            elif h == a:
                p_d += p
            else:
                p_a += p
            if h + a > 2:
                p_over += p
    return {
        "home_prob": p_h, "draw_prob": p_d, "away_prob": p_a,
        "over_25_prob": p_over, "under_25_prob": 1 - p_over,
    }


def _goals_from_hist(df: pd.DataFrame, team: str) -> tuple[list[float], list[float]]:
    """Extract goals-for and goals-against lists for a team from a history DataFrame."""
    gf, ga = [], []
    for _, m in df.iterrows():
        if m["home_team"] == team:
            gf.append(float(m["FTHG"]))
            ga.append(float(m["FTAG"]))
        else:
            gf.append(float(m["FTAG"]))
            ga.append(float(m["FTHG"]))
    return gf, ga


def _goals_from_sofascore(matches: list[dict], team: str) -> tuple[list[float], list[float]]:
    """Extract goals-for and goals-against from Sofascore raw match dicts."""
    gf, ga = [], []
    for m in matches:
        if m["home_team"] == team:
            gf.append(float(m["FTHG"]))
            ga.append(float(m["FTAG"]))
        else:
            gf.append(float(m["FTAG"]))
            ga.append(float(m["FTHG"]))
    return gf, ga


def compute_prediction(match, hist_targets, hist_targets_global=None, sofascore_cache=None):
    """
    Compute Poisson prediction for a match using the best available history.

    Data tiers:
      A — team found in targets_v9 (has bookmaker odds calibration)
      B — team found only in targets_global (global results, no odds calibration)
      C — team not in either dataset; Sofascore API fetched on-demand

    Returns prediction dict with a 'data_tier' field, or None if no data.
    """
    from workers.utils.team_names import normalize_team_name, fuzzy_match_team

    home_raw = match["home_team"]
    away_raw = match["away_team"]

    # Normalise Kambi names
    home = normalize_team_name(home_raw, source="kambi")
    away = normalize_team_name(away_raw, source="kambi")

    # --- Tier A: search in targets_v9 ---
    v9_teams = set(hist_targets["home_team"].unique()) | set(hist_targets["away_team"].unique())
    home_v9 = fuzzy_match_team(home, v9_teams) or fuzzy_match_team(home_raw, v9_teams)
    away_v9 = fuzzy_match_team(away, v9_teams) or fuzzy_match_team(away_raw, v9_teams)

    # --- Tier B: search in targets_global ---
    home_global = away_global = None
    if hist_targets_global is not None:
        global_teams = (
            set(hist_targets_global["home_team"].unique()) |
            set(hist_targets_global["away_team"].unique())
        )
        if not home_v9:
            home_global = fuzzy_match_team(home, global_teams) or fuzzy_match_team(home_raw, global_teams)
        if not away_v9:
            away_global = fuzzy_match_team(away, global_teams) or fuzzy_match_team(away_raw, global_teams)

    # Determine effective matched names and tier
    home_matched = home_v9 or home_global
    away_matched = away_v9 or away_global

    if home_v9 and away_v9:
        data_tier = "A"
    elif home_matched and away_matched:
        data_tier = "B"
    else:
        # --- Tier C: try Sofascore on-demand ---
        if sofascore_cache is None:
            return None
        home_sf_id = match.get("home_team_id")
        away_sf_id = match.get("away_team_id")
        if not home_sf_id or not away_sf_id:
            return None

        home_sf = _fetch_sofascore_team_history(home_sf_id, sofascore_cache)
        away_sf = _fetch_sofascore_team_history(away_sf_id, sofascore_cache)

        if not home_sf or not away_sf or len(home_sf) < 3 or len(away_sf) < 3:
            return None

        home_gf, home_ga = _goals_from_sofascore(home_sf, home_raw)
        away_gf, away_ga = _goals_from_sofascore(away_sf, away_raw)

        exp_h = max(0.3, np.mean(home_gf[-10:])) * 1.08
        exp_a = max(0.3, np.mean(away_gf[-10:])) * 0.92
        exp_h = (exp_h + np.mean(away_ga[-10:])) / 2
        exp_a = (exp_a + np.mean(home_ga[-10:])) / 2

        result = _poisson_probs(exp_h, exp_a)
        result.update({"exp_home": exp_h, "exp_away": exp_a, "data_tier": "C"})
        return result

    # --- Fetch history for matched teams ---
    # Home team history: prefer v9 if available, else global
    if home_v9:
        home_hist = hist_targets[
            (hist_targets["home_team"] == home_v9) |
            (hist_targets["away_team"] == home_v9)
        ].tail(20)
    else:
        home_hist = hist_targets_global[
            (hist_targets_global["home_team"] == home_global) |
            (hist_targets_global["away_team"] == home_global)
        ].tail(20)

    # Away team history: prefer v9 if available, else global
    if away_v9:
        away_hist = hist_targets[
            (hist_targets["home_team"] == away_v9) |
            (hist_targets["away_team"] == away_v9)
        ].tail(20)
    else:
        away_hist = hist_targets_global[
            (hist_targets_global["home_team"] == away_global) |
            (hist_targets_global["away_team"] == away_global)
        ].tail(20)

    if len(home_hist) < 3 or len(away_hist) < 3:
        return None

    home_gf, home_ga = _goals_from_hist(home_hist, home_matched if home_v9 else home_global)
    away_gf, away_ga = _goals_from_hist(away_hist, away_matched if away_v9 else away_global)

    exp_h = max(0.3, np.mean(home_gf[-10:])) * 1.08  # Slight home advantage
    exp_a = max(0.3, np.mean(away_gf[-10:])) * 0.92
    exp_h = (exp_h + np.mean(away_ga[-10:])) / 2
    exp_a = (exp_a + np.mean(home_ga[-10:])) / 2

    result = _poisson_probs(exp_h, exp_a)
    result.update({"exp_home": exp_h, "exp_away": exp_a, "data_tier": data_tier})
    return result


def _merge_odds_sources(kambi_matches: list[dict], sofascore_matches: list[dict], betexplorer_matches: list[dict] = None) -> list[dict]:
    """
    Merge odds from multiple sources by match key (home_team + away_team + date).
    - Kambi is authoritative when available (we trust its team names for our mapping).
    - SofaScore fills in leagues Kambi doesn't cover.
    - BetExplorer fills remaining gaps (Singapore, South Korea, Scotland lower divs).
    - If multiple sources cover the same match, keep best odds across all.
    Returns unified list of match dicts with bookmaker field set.
    """
    merged: dict[str, dict] = {}

    def _merge_into(matches: list[dict], source_name: str):
        for m in matches:
            date_part = m.get("start_time", "")[:10] or "nodate"
            key = f"{m['home_team'].lower()}_{m['away_team'].lower()}_{date_part}"
            if key in merged:
                existing = merged[key]
                for field in ["odds_home", "odds_draw", "odds_away",
                              "odds_over_05", "odds_under_05",
                              "odds_over_15", "odds_under_15",
                              "odds_over_25", "odds_under_25",
                              "odds_over_35", "odds_under_35",
                              "odds_over_45", "odds_under_45"]:
                    if m.get(field, 0) > existing.get(field, 0):
                        existing[field] = m[field]
                # Append source name
                sources = existing["bookmaker"].split("+")
                if source_name not in sources:
                    existing["bookmaker"] = "+".join(sources + [source_name])
            else:
                merged[key] = {**m, "bookmaker": source_name}

    _merge_into(kambi_matches, "kambi")
    _merge_into(sofascore_matches, "sofascore")
    if betexplorer_matches:
        _merge_into(betexplorer_matches, "betexplorer")

    return list(merged.values())


def run_morning():
    """Fetch data → predict → store matches/odds/bets in Supabase"""
    today_str = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Pipeline: {today_str} ═══[/bold green]\n")

    # 1. Ensure bots exist in DB
    console.print("[cyan]Creating/checking bots in Supabase...[/cyan]")
    bot_ids = ensure_bots(BOTS_CONFIG)
    console.print(f"  {len(bot_ids)} bots ready")

    # 2. Fetch ALL fixtures from SofaScore (source of truth for match list)
    console.print("\n[cyan]Fetching all fixtures from SofaScore...[/cyan]")
    all_fixtures = get_todays_matches_from_flashscore()
    console.print(f"  {len(all_fixtures)} total fixtures today")

    # 3. Store ALL fixtures in Supabase (even without odds)
    console.print("\n[cyan]Storing all fixtures in Supabase...[/cyan]")
    stored_fixture_count = 0
    fixture_id_map: dict[str, str] = {}  # event_id → match_id

    for fixture in all_fixtures:
        # Convert sofascore fixture to match dict format
        match_dict = {
            "home_team": fixture.get("home_team", ""),
            "away_team": fixture.get("away_team", ""),
            "start_time": fixture.get("date", ""),
            "league_path": f"{fixture.get('country', '')} / {fixture.get('league_name', '')}",
            "league_code": "",  # Unknown until odds are fetched
            "tier": 0,
            "operator": "sofascore_fixture",
            "odds_home": 0,
            "odds_draw": 0,
            "odds_away": 0,
            "odds_over_25": 0,
            "odds_under_25": 0,
        }
        try:
            match_id = store_match(match_dict)
            if match_id and fixture.get("event_id"):
                fixture_id_map[str(fixture["event_id"])] = match_id
            stored_fixture_count += 1
        except Exception as e:
            console.print(f"  [yellow]Could not store fixture {fixture.get('home_team')} vs {fixture.get('away_team')}: {e}[/yellow]")

    console.print(f"  {stored_fixture_count} fixtures stored")

    # 4. Fetch odds from Kambi
    console.print("\n[cyan]Fetching odds from Kambi...[/cyan]")
    kambi_matches = get_target_league_matches()
    console.print(f"  {len(kambi_matches)} matches with Kambi odds")

    # 5. Fetch odds from SofaScore
    console.print("\n[cyan]Fetching odds from SofaScore (target leagues)...[/cyan]")
    sofascore_matches = fetch_sofascore_odds(all_fixtures)

    # 5b. Fetch odds from BetExplorer (fills gaps: Singapore, S. Korea, Scotland lower divs)
    console.print("\n[cyan]Fetching odds from BetExplorer (gap leagues)...[/cyan]")
    try:
        betexplorer_matches = fetch_betexplorer_odds(delay=1.0)
    except Exception as e:
        console.print(f"  [yellow]BetExplorer scrape failed (non-fatal): {e}[/yellow]")
        betexplorer_matches = []

    # 6. Merge odds sources
    odds_matches = _merge_odds_sources(kambi_matches, sofascore_matches, betexplorer_matches)
    console.print(f"\n  [bold]{len(odds_matches)} matches with odds (Kambi + SofaScore + BetExplorer merged)[/bold]")

    # Coverage by source
    source_counts = {}
    for m in odds_matches:
        bk = m.get("bookmaker", "unknown")
        source_counts[bk] = source_counts.get(bk, 0) + 1
    for source, count in sorted(source_counts.items()):
        console.print(f"  {source}: {count}")

    if not odds_matches:
        console.print("[yellow]No matches with odds today — predictions skipped.[/yellow]")
        return

    # 7. Load historical data for predictions
    console.print("\n[cyan]Loading historical data...[/cyan]")
    targets_path = PROCESSED_DIR / "targets_v9.csv"
    if not targets_path.exists():
        targets_path = PROCESSED_DIR / "targets_fast.csv"
    hist_targets = pd.read_csv(targets_path)
    console.print(f"  {len(hist_targets):,} Tier A matches (targets_v9)")

    hist_targets_global = None
    global_path = PROCESSED_DIR / "targets_global.csv"
    if global_path.exists():
        hist_targets_global = pd.read_csv(global_path)
        console.print(f"  {len(hist_targets_global):,} Tier B matches (targets_global)")
    else:
        console.print("  [yellow]targets_global.csv not found — Tier B unavailable[/yellow]")

    # Load Sofascore team cache for Tier C (reset each day)
    sofascore_cache = _load_sofascore_cache()

    # 8. Process each match with odds
    console.print("\n[cyan]Processing matches with odds...[/cyan]")
    total_bets = 0

    for match in odds_matches:
        # Store match in Supabase
        try:
            match_id = store_match(match)
        except Exception as e:
            console.print(f"  [red]Error storing match: {e}[/red]")
            continue

        # Store odds (bookmaker set to source name)
        try:
            store_odds(match_id, {**match, "bookmaker": match.get("bookmaker", match.get("operator", "unknown"))})
        except Exception as e:
            console.print(f"  [yellow]Error storing odds: {e}[/yellow]")

        # Compute Poisson prediction
        poisson_pred = compute_prediction(
            match, hist_targets,
            hist_targets_global=hist_targets_global,
            sofascore_cache=sofascore_cache,
        )
        if not poisson_pred:
            continue

        data_tier = poisson_pred.get("data_tier", "A")

        # Try XGBoost ensemble for Tier A teams
        pred = poisson_pred  # default: Poisson-only
        xgb_pred = None
        if data_tier == "A":
            from workers.utils.team_names import normalize_team_name, fuzzy_match_team
            home_norm = normalize_team_name(match["home_team"], source="kambi")
            away_norm = normalize_team_name(match["away_team"], source="kambi")
            # Try to get XGBoost prediction
            xgb_pred = get_xgboost_prediction(
                home_norm, away_norm,
                tier=match.get("tier", 1),
            )
            if xgb_pred:
                pred = ensemble_prediction(poisson_pred, xgb_pred)
            else:
                # Also try with raw names
                xgb_pred = get_xgboost_prediction(
                    match["home_team"], match["away_team"],
                    tier=match.get("tier", 1),
                )
                if xgb_pred:
                    pred = ensemble_prediction(poisson_pred, xgb_pred)

        # Store predictions
        for market, prob_key in [
            ("1x2_home", "home_prob"),
            ("1x2_away", "away_prob"),
            ("over25", "over_25_prob"),
            ("under25", "under_25_prob"),
        ]:
            odds_key = {
                "1x2_home": "odds_home",
                "1x2_away": "odds_away",
                "over25": "odds_over_25",
                "under25": "odds_under_25",
            }[market]

            odds_val = match.get(odds_key, 0)
            if odds_val > 0:
                try:
                    store_prediction(match_id, market, {
                        "model_prob": pred[prob_key],
                        "implied_prob": 1 / odds_val,
                        "edge": pred[prob_key] - (1 / odds_val),
                        "odds": odds_val,
                    })
                except Exception:
                    pass

        # Place bets for each bot
        tier = match.get("tier", 1)
        country = match.get("league_path", "").split(" / ")[0] if " / " in match.get("league_path", "") else ""

        # Data-tier adjustments:
        #   A — full odds history, no adjustment
        #   B — results only, require +2% edge, cap stake at 50%
        #   C — on-demand Sofascore, require +5% edge, cap stake at 25%
        DATA_TIER_EDGE_BUMP = {"A": 0.00, "B": 0.02, "C": 0.05}
        edge_bump = DATA_TIER_EDGE_BUMP.get(data_tier, 0.00)
        tier_tag = f"[Tier {data_tier}] " if data_tier != "A" else ""

        # P2: Compute odds movement for this match (once per match, cached per market)
        odds_movement_cache = {}

        for bot_name, config in BOTS_CONFIG.items():
            # Check tier filter
            if config.get("tier_filter") and tier not in config["tier_filter"]:
                continue

            # Check league filter
            if config.get("league_filter") and country not in config.get("league_filter", []):
                continue

            thresholds = config["edge_thresholds"].get(tier, {})
            odds_min, odds_max = config["odds_range"]
            min_prob = config["min_prob"]

            bet_candidates = []
            sel_filter = config.get("selection_filter")

            # Build candidates: (market, selection, odds_key, prob_key, threshold_key)
            candidate_specs = []
            if "1x2" in config["markets"] and match["odds_home"] > 0 and (not sel_filter or "Home" in sel_filter):
                odds = match["odds_home"]
                me = (thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08))
                candidate_specs.append(("1X2", "Home", odds, pred["home_prob"], "1x2", "home", me))
            if "1x2" in config["markets"] and match["odds_away"] > 0 and (not sel_filter or "Away" in sel_filter):
                candidate_specs.append(("1X2", "Away", match["odds_away"], pred["away_prob"], "1x2", "away", thresholds.get("1x2_long", 0.08)))
            if "ou" in config.get("markets", []) and match.get("odds_over_25", 0) > 0:
                candidate_specs.append(("O/U", "Over 2.5", match["odds_over_25"], pred["over_25_prob"], "over_under_25", "over", thresholds.get("ou", 0.05)))
            if "ou" in config.get("markets", []) and match.get("odds_under_25", 0) > 0:
                candidate_specs.append(("O/U", "Under 2.5", match["odds_under_25"], pred["under_25_prob"], "over_under_25", "under", thresholds.get("ou", 0.05)))

            for mkt, selection, odds, raw_mp, os_market, os_selection, base_threshold in candidate_specs:
                ip = 1 / odds

                # P1: Calibrate probability (tier-specific shrinkage)
                cal_prob = calibrate_prob(raw_mp, ip, tier=tier)

                # Use calibrated probability for edge calculation
                edge = cal_prob - ip
                me = base_threshold + edge_bump

                if edge < me or odds < odds_min or odds > odds_max or cal_prob < min_prob:
                    continue

                # P2: Odds movement — soft penalty, hard veto only >10%
                mv_key = f"{os_market}_{os_selection}"
                if mv_key not in odds_movement_cache:
                    odds_movement_cache[mv_key] = compute_odds_movement(
                        match_id, os_market, os_selection, odds
                    )
                odds_mv = odds_movement_cache[mv_key]

                if odds_mv["veto"]:
                    continue  # Market moved >10% against pick — hard skip

                # P4: Kelly fraction (using calibrated prob)
                kelly = compute_kelly(cal_prob, odds)
                if kelly <= 0:
                    continue

                # P3: Alignment — LOG-ONLY (stored but does not affect decisions)
                alignment = compute_alignment(
                    match_id, selection, odds_mv, match
                )

                # P4: Kelly-based stake sizing with soft odds penalty
                bot_bankroll = 1000.0
                try:
                    bot_record = get_client().table("bots").select("current_bankroll").eq(
                        "id", bot_ids[bot_name]
                    ).execute().data
                    if bot_record:
                        bot_bankroll = float(bot_record[0]["current_bankroll"])
                except Exception:
                    pass

                stake = compute_stake(
                    kelly, bot_bankroll, data_tier,
                    odds_penalty=odds_mv.get("penalty", 0.0),
                )
                if stake < 1.0:
                    continue

                bet_candidates.append((mkt, selection, odds, raw_mp, cal_prob, ip, edge, kelly, alignment, odds_mv, stake))

            for mkt, selection, odds, raw_mp, cal_prob, ip, edge, kelly, alignment, odds_mv, stake in bet_candidates:
                try:
                    bet_id = store_bet(bot_ids[bot_name], match_id, {
                        "market": mkt,
                        "selection": selection,
                        "odds": odds,
                        "model_prob": raw_mp,
                        "implied_prob": ip,
                        "edge": edge,
                        "stake": stake,
                        "placed_at": datetime.now().isoformat(),
                        "reasoning": f"{tier_tag}{match['home_team']} vs {match['away_team']} | edge={edge:.3f} cal={cal_prob:.3f} kelly={kelly:.4f} align={alignment['alignment_class']}",
                        # P1: Calibration
                        "calibrated_prob": round(cal_prob, 4),
                        # P2: Odds movement
                        "odds_at_open": odds_mv.get("odds_at_open"),
                        "odds_drift": odds_mv.get("odds_drift"),
                        # P3: Alignment
                        "dimension_scores": alignment["dimensions"],
                        "alignment_count": alignment["alignment_count"],
                        "alignment_total": alignment["alignment_total"],
                        "alignment_class": alignment["alignment_class"],
                        # P4: Kelly
                        "kelly_fraction": round(kelly, 6),
                        # Model disagreement (when ensemble is active)
                        "model_disagreement": pred.get("model_disagreement"),
                    })
                    if bet_id:
                        total_bets += 1
                        # Save Stage 1 snapshot: stats-only probability
                        try:
                            store_prediction_snapshot(
                                bet_id=bet_id,
                                stage="stats_only",
                                model_probability=raw_mp,
                                implied_probability=ip,
                                edge_percent=edge,
                                odds_at_snapshot=odds,
                                metadata={
                                    "data_tier": data_tier,
                                    "bot": bot_name,
                                    "calibrated_prob": round(cal_prob, 4),
                                    "kelly": round(kelly, 4),
                                    "alignment_class": alignment["alignment_class"],
                                },
                            )
                        except Exception:
                            pass  # non-critical
                    # else: already placed today, skip silently
                except Exception as e:
                    console.print(f"  [red]Error storing bet: {e}[/red]")

        # Brief status
        ensemble_tag = " [ensemble]" if pred.get("ensemble") else ""
        disagree_tag = f" disagree={pred['model_disagreement']:.1%}" if pred.get("model_disagreement") else ""
        console.print(f"  {match['home_team']} vs {match['away_team']} — predicted [Tier {data_tier}]{ensemble_tag}{disagree_tag}")

    console.print(f"\n[bold green]Done! {total_bets} bets placed across {len(BOTS_CONFIG)} bots[/bold green]")
    console.print(f"[green]All data stored in Supabase — frontend can display it now[/green]")

    # 11.6: Cross-match correlation check — warn about concentrated exposure
    _check_exposure_concentration()


def _check_exposure_concentration():
    """
    11.6: Cross-match correlation / exposure management.
    After all bets are placed, checks if any single league has
    too many bets on the same day (correlated outcomes).

    If a bot has 3+ bets in the same league on the same day,
    outcomes are correlated — warns and logs. Future: auto-reduce stakes.

    See MODEL_ANALYSIS.md Section 11.6.
    """
    from collections import defaultdict

    try:
        client = get_client()
        today_str = date.today().isoformat()

        # Get today's pending bets with league info
        bets = client.table("simulated_bets").select(
            "id, bot_id, stake, market, selection, "
            "matches(league_id, leagues(name, country))"
        ).eq("result", "pending").gte(
            "pick_time", f"{today_str}T00:00:00"
        ).execute().data

        if not bets:
            return

        # Group by bot × league
        exposure: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

        for b in bets:
            match = b.get("matches", {})
            league = match.get("leagues", [{}])
            league_name = league[0]["name"] if isinstance(league, list) and league else league.get("name", "?")
            bot_id = b["bot_id"]
            exposure[bot_id][league_name].append(b)

        # Check for concentrated exposure
        warnings_found = False
        for bot_id, leagues in exposure.items():
            for league_name, league_bets in leagues.items():
                if len(league_bets) >= 3:
                    total_stake = sum(float(b["stake"]) for b in league_bets)
                    if not warnings_found:
                        console.print("\n[yellow]═══ Exposure Concentration Warnings ═══[/yellow]")
                        warnings_found = True
                    console.print(
                        f"  [yellow]⚠ {len(league_bets)} bets in {league_name} "
                        f"(total stake €{total_stake:.2f}) — outcomes are correlated[/yellow]"
                    )

        if not warnings_found:
            console.print("\n[dim]Exposure check: OK — no concentrated league exposure[/dim]")

    except Exception as e:
        console.print(f"  [yellow]Exposure check error (non-critical): {e}[/yellow]")


def run_settle():
    """Settle pending bets using match results"""
    console.print(f"[bold green]═══ OddsIntel Settlement ═══[/bold green]\n")

    # Fetch results from Sofascore
    console.print("[cyan]Fetching results...[/cyan]")
    results = get_todays_matches_from_flashscore()
    finished = [r for r in results if r.get("status") in ["FT", "finished"]
                and r.get("home_goals") is not None]
    console.print(f"  {len(finished)} finished matches")

    # Get pending bets
    pending = get_pending_bets()
    console.print(f"  {len(pending)} pending bets")

    if not pending:
        console.print("[yellow]No pending bets to settle.[/yellow]")
        return

    # TODO: Match pending bets to results and settle
    # This requires matching team names between our DB and Sofascore
    # For now, log that settlement needs to happen
    console.print("[yellow]Settlement matching needs team name resolution — TODO[/yellow]")


def run_report():
    """Show cumulative bot performance"""
    console.print(f"[bold green]═══ OddsIntel Bot Report ═══[/bold green]\n")

    client = get_client()

    # Get all bots
    bots = client.table("bots").select("*").execute().data

    t = Table(title="Bot Performance (All Time)")
    t.add_column("Bot", style="cyan")
    t.add_column("Strategy")
    t.add_column("Bankroll", justify="right")
    t.add_column("Bets", justify="right")
    t.add_column("Won", justify="right")
    t.add_column("Lost", justify="right")
    t.add_column("Pending", justify="right")

    for bot in bots:
        bets = client.table("simulated_bets").select("result, pnl").eq("bot_id", bot["id"]).execute().data

        won = sum(1 for b in bets if b["result"] == "won")
        lost = sum(1 for b in bets if b["result"] == "lost")
        pending = sum(1 for b in bets if b["result"] == "pending")

        t.add_row(
            bot["name"],
            bot.get("strategy", "")[:40],
            f"EUR {bot.get('current_bankroll', 1000):.2f}",
            str(len(bets)),
            str(won),
            str(lost),
            str(pending),
        )

    console.print(t)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "settle":
            run_settle()
        elif sys.argv[1] == "report":
            run_report()
        else:
            console.print(f"Unknown command: {sys.argv[1]}")
            console.print("Usage: python daily_pipeline_v2.py [settle|report]")
    else:
        run_morning()
