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
from workers.api_clients.supabase_client import (
    get_client, ensure_bots, store_match, store_odds,
    store_prediction, store_bet, settle_bet,
    get_pending_bets, update_bot_bankroll, update_match_result,
    get_bot_performance, get_todays_matches,
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
        "description": "Only Greek + Turkish leagues — consistently profitable in backtests",
        "tier_label": "syndicate",
        "markets": ["1x2"],
        "tier_filter": [1],  # These are tier 1 in their countries
        "league_filter": ["Turkey", "Greece"],
        "edge_thresholds": {
            1: {"1x2_fav": 0.04, "1x2_long": 0.06},
        },
        "odds_range": (1.40, 4.00),
        "min_prob": 0.30,
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


def _merge_odds_sources(kambi_matches: list[dict], sofascore_matches: list[dict]) -> list[dict]:
    """
    Merge odds from multiple sources by match key (home_team + away_team + date).
    - Kambi is authoritative when available (we trust its team names for our mapping).
    - SofaScore fills in leagues Kambi doesn't cover.
    - If both cover the same match, keep both and use best odds.
    Returns unified list of match dicts with bookmaker field set.
    """
    merged: dict[str, dict] = {}

    for m in kambi_matches:
        key = f"{m['home_team'].lower()}_{m['away_team'].lower()}_{m['start_time'][:10]}"
        merged[key] = {**m, "bookmaker": "kambi"}

    for m in sofascore_matches:
        key = f"{m['home_team'].lower()}_{m['away_team'].lower()}_{m['start_time'][:10]}"
        if key in merged:
            # Update best odds but keep kambi as primary source
            existing = merged[key]
            if m["odds_home"] > existing.get("odds_home", 0):
                existing["odds_home"] = m["odds_home"]
            if m["odds_draw"] > existing.get("odds_draw", 0):
                existing["odds_draw"] = m["odds_draw"]
            if m["odds_away"] > existing.get("odds_away", 0):
                existing["odds_away"] = m["odds_away"]
            if m["odds_over_25"] > existing.get("odds_over_25", 0):
                existing["odds_over_25"] = m["odds_over_25"]
            if m["odds_under_25"] > existing.get("odds_under_25", 0):
                existing["odds_under_25"] = m["odds_under_25"]
            existing["bookmaker"] = "kambi+sofascore"
        else:
            merged[key] = {**m, "bookmaker": "sofascore"}

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

    # 6. Merge odds sources
    odds_matches = _merge_odds_sources(kambi_matches, sofascore_matches)
    console.print(f"\n  [bold]{len(odds_matches)} matches with odds (Kambi + SofaScore merged)[/bold]")

    # Coverage by source
    kambi_only = sum(1 for m in odds_matches if m.get("bookmaker") == "kambi")
    sofascore_only = sum(1 for m in odds_matches if m.get("bookmaker") == "sofascore")
    both = sum(1 for m in odds_matches if m.get("bookmaker") == "kambi+sofascore")
    console.print(f"  Kambi only: {kambi_only} | SofaScore only: {sofascore_only} | Both: {both}")

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

        # Compute prediction
        pred = compute_prediction(
            match, hist_targets,
            hist_targets_global=hist_targets_global,
            sofascore_cache=sofascore_cache,
        )
        if not pred:
            continue

        data_tier = pred.get("data_tier", "A")

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
        DATA_TIER_STAKE_MULT = {"A": 1.00, "B": 0.50, "C": 0.25}
        edge_bump = DATA_TIER_EDGE_BUMP.get(data_tier, 0.00)
        stake_mult = DATA_TIER_STAKE_MULT.get(data_tier, 1.00)
        tier_tag = f"[Tier {data_tier}] " if data_tier != "A" else ""

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

            # 1X2 Home
            if "1x2" in config["markets"] and match["odds_home"] > 0:
                odds = match["odds_home"]
                mp = pred["home_prob"]
                ip = 1 / odds
                edge = mp - ip
                me = (thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08)) + edge_bump
                if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                    bet_candidates.append(("1X2", "Home", odds, mp, ip, edge))

            # 1X2 Away
            if "1x2" in config["markets"] and match["odds_away"] > 0:
                odds = match["odds_away"]
                mp = pred["away_prob"]
                ip = 1 / odds
                edge = mp - ip
                me = thresholds.get("1x2_long", 0.08) + edge_bump
                if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                    bet_candidates.append(("1X2", "Away", odds, mp, ip, edge))

            # O/U Over
            if "ou" in config.get("markets", []) and match.get("odds_over_25", 0) > 0:
                odds = match["odds_over_25"]
                mp = pred["over_25_prob"]
                ip = 1 / odds
                edge = mp - ip
                me = thresholds.get("ou", 0.05) + edge_bump
                if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                    bet_candidates.append(("O/U", "Over 2.5", odds, mp, ip, edge))

            # O/U Under
            if "ou" in config.get("markets", []) and match.get("odds_under_25", 0) > 0:
                odds = match["odds_under_25"]
                mp = pred["under_25_prob"]
                ip = 1 / odds
                edge = mp - ip
                me = thresholds.get("ou", 0.05) + edge_bump
                if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                    bet_candidates.append(("O/U", "Under 2.5", odds, mp, ip, edge))

            for market, selection, odds, mp, ip, edge in bet_candidates:
                try:
                    store_bet(bot_ids[bot_name], match_id, {
                        "market": market,
                        "selection": selection,
                        "odds": odds,
                        "model_prob": mp,
                        "implied_prob": ip,
                        "edge": edge,
                        "stake": round(STAKE * stake_mult, 2),
                        "placed_at": datetime.now().isoformat(),
                        "reasoning": f"{tier_tag}{match['home_team']} vs {match['away_team']} | edge={edge:.3f}",
                    })
                    total_bets += 1
                except Exception as e:
                    console.print(f"  [red]Error storing bet: {e}[/red]")

        # Brief status
        console.print(f"  {match['home_team']} vs {match['away_team']} — predicted [Tier {data_tier}]")

    console.print(f"\n[bold green]Done! {total_bets} bets placed across {len(BOTS_CONFIG)} bots[/bold green]")
    console.print(f"[green]All data stored in Supabase — frontend can display it now[/green]")


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
