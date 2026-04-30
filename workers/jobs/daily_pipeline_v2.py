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
import threading
import numpy as np
import pandas as pd
import requests
from pathlib import Path
from datetime import datetime, date, timezone
from concurrent.futures import ThreadPoolExecutor
from scipy.stats import poisson
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.scrapers.kambi_odds import fetch_all_operators, get_target_league_matches
from workers.api_clients.api_football import (
    get_fixtures_by_date, fixture_to_match_dict,
    get_fixture_odds, parse_fixture_odds, get_odds_by_date,
    get_prediction, parse_prediction,
    get_team_statistics, parse_team_statistics,
    get_injuries_batched, parse_injuries,
    get_standings, parse_standings,
    get_h2h, parse_h2h,
)
from workers.api_clients.supabase_client import (
    get_client, ensure_bots, store_match, store_odds,
    store_prediction, store_bet, store_prediction_snapshot, settle_bet,
    get_pending_bets, update_bot_bankroll, update_match_result,
    get_bot_performance, get_todays_matches,
    store_team_season_stats, store_match_injuries,
    store_league_standings, store_match_h2h,
    write_morning_signals,
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
        "tier_label": "elite",
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
        "tier_label": "elite",
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
        "tier_label": "elite",
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
        "tier_label": "pro",
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
        "tier_label": "elite",
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
        "tier_label": "elite",
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
        "tier_label": "elite",
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
        "tier_label": "elite",
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
        "tier_label": "elite",
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
        "tier_label": "elite",
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

    # ─── New bots (2026-04-30): BTTS, O/U 1.5/3.5, draw, O/U 2.5 global ────

    "bot_btts_all": {
        "description": "BTTS all leagues — new market, zero overlap with 1X2 bets",
        "tier_label": "pro",
        "markets": ["btts"],
        "edge_thresholds": {
            1: {"btts": 0.06},
            2: {"btts": 0.06},
            3: {"btts": 0.05},
            4: {"btts": 0.05},
        },
        "odds_range": (1.50, 2.80),
        "min_prob": 0.30,
    },
    "bot_btts_conservative": {
        "description": "BTTS top leagues only — selective, 10%+ edge",
        "tier_label": "elite",
        "markets": ["btts"],
        "tier_filter": [1, 2],
        "edge_thresholds": {
            1: {"btts": 0.10},
            2: {"btts": 0.10},
        },
        "odds_range": (1.60, 2.50),
        "min_prob": 0.35,
    },
    "bot_ou15_defensive": {
        "description": "O/U 1.5 — under 1.5 in defensive leagues, over 1.5 at value odds",
        "tier_label": "pro",
        "markets": ["ou15"],
        "edge_thresholds": {
            1: {"ou": 0.06},
            2: {"ou": 0.06},
            3: {"ou": 0.05},
            4: {"ou": 0.05},
        },
        "odds_range": (1.80, 3.50),
        "min_prob": 0.30,
    },
    "bot_ou35_attacking": {
        "description": "O/U 3.5 — over 3.5 in high-scoring leagues, under 3.5 at value",
        "tier_label": "pro",
        "markets": ["ou35"],
        "edge_thresholds": {
            1: {"ou": 0.06},
            2: {"ou": 0.06},
            3: {"ou": 0.05},
            4: {"ou": 0.05},
        },
        "odds_range": (1.80, 3.50),
        "min_prob": 0.30,
    },
    "bot_ou25_global": {
        "description": "O/U 2.5 all leagues — extends bot_opt_ou_british globally",
        "tier_label": "pro",
        "markets": ["ou"],
        "edge_thresholds": {
            1: {"ou": 0.06},
            2: {"ou": 0.05},
            3: {"ou": 0.05},
            4: {"ou": 0.04},
        },
        "odds_range": (1.60, 3.00),
        "min_prob": 0.30,
    },
    "bot_draw_specialist": {
        "description": "Draw specialist T2-4 — draws underbet in lower tiers",
        "tier_label": "pro",
        "markets": ["1x2"],
        "tier_filter": [2, 3, 4],
        "selection_filter": ["Draw"],
        "edge_thresholds": {
            2: {"1x2_long": 0.05},
            3: {"1x2_long": 0.05},
            4: {"1x2_long": 0.04},
        },
        "odds_range": (2.80, 4.50),
        "min_prob": 0.22,
    },
}


# Dixon-Coles correlation parameter — corrects independent Poisson's draw underestimation.
# Literature standard value. Negative sign: low-scoring draws are more common than Poisson predicts.
DIXON_COLES_RHO = -0.13


def _dc_tau(h: int, a: int, exp_h: float, exp_a: float, rho: float) -> float:
    """Dixon-Coles correction factor τ for the four low-scoring outcomes."""
    if h == 0 and a == 0:
        return 1.0 - exp_h * exp_a * rho
    if h == 1 and a == 0:
        return 1.0 + exp_a * rho
    if h == 0 and a == 1:
        return 1.0 + exp_h * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _poisson_probs(exp_h: float, exp_a: float) -> dict:
    """Compute 1X2 + O/U (1.5, 2.5, 3.5) + BTTS probabilities from expected goals.

    Applies Dixon-Coles bivariate correction to the four low-scoring outcomes
    (0-0, 1-0, 0-1, 1-1) to fix the ~8% draw underestimation of independent Poisson.
    1X2 probabilities are renormalised after correction.
    """
    p_h = p_d = p_a = 0.0
    p_over_15 = p_over_25 = p_over_35 = 0.0
    p_btts_yes = 0.0

    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_h) * poisson.pmf(a, exp_a)
            p *= _dc_tau(h, a, exp_h, exp_a, DIXON_COLES_RHO)
            if h > a:
                p_h += p
            elif h == a:
                p_d += p
            else:
                p_a += p
            if h + a > 1:
                p_over_15 += p
            if h + a > 2:
                p_over_25 += p
            if h + a > 3:
                p_over_35 += p
            if h >= 1 and a >= 1:
                p_btts_yes += p

    # Renormalise 1x2 after DC correction (τ shifts probability mass slightly)
    total_1x2 = p_h + p_d + p_a
    if total_1x2 > 0:
        p_h /= total_1x2
        p_d /= total_1x2
        p_a /= total_1x2
    return {
        "home_prob": p_h, "draw_prob": p_d, "away_prob": p_a,
        "over_15_prob": p_over_15, "under_15_prob": 1 - p_over_15,
        "over_25_prob": p_over_25, "under_25_prob": 1 - p_over_25,
        "over_35_prob": p_over_35, "under_35_prob": 1 - p_over_35,
        "btts_yes_prob": p_btts_yes, "btts_no_prob": 1 - p_btts_yes,
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


def compute_prediction(match, hist_targets, hist_targets_global=None,
                       _team_sets=None):
    """
    Compute Poisson prediction for a match using the best available history.

    Data tiers:
      A — team found in targets_v9 (has bookmaker odds calibration)
      B — team found only in targets_global (global results, no odds calibration)
      D — no historical data (AF prediction fallback only)

    _team_sets: optional pre-computed (v9_teams, global_teams) to avoid rebuilding per call.

    Returns prediction dict with a 'data_tier' field, or None if no data.
    """
    from workers.utils.team_names import normalize_team_name, fuzzy_match_team

    home_raw = match["home_team"]
    away_raw = match["away_team"]

    # Normalise Kambi names
    home = normalize_team_name(home_raw, source="kambi")
    away = normalize_team_name(away_raw, source="kambi")

    # --- Tier A: search in targets_v9 ---
    if _team_sets:
        v9_teams, global_teams_set = _team_sets
    else:
        v9_teams = set(hist_targets["home_team"].unique()) | set(hist_targets["away_team"].unique())
        global_teams_set = None
    home_v9 = fuzzy_match_team(home, v9_teams) or fuzzy_match_team(home_raw, v9_teams)
    away_v9 = fuzzy_match_team(away, v9_teams) or fuzzy_match_team(away_raw, v9_teams)

    # --- Tier B: search in targets_global ---
    home_global = away_global = None
    if hist_targets_global is not None:
        if global_teams_set is not None:
            global_teams = global_teams_set
        else:
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
        # No historical data — skip (Tier D handled by AF prediction only)
        return None

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


def _store_parsed_odds(match_id: str, parsed_odds: list[dict]):
    """Store pre-parsed API-Football odds rows directly into odds_snapshots."""
    from workers.api_clients.supabase_client import get_client
    client = get_client()
    now = datetime.now().astimezone().isoformat()

    rows = []
    for row in parsed_odds:
        rows.append({
            "match_id": match_id,
            "bookmaker": row["bookmaker"],
            "market": row["market"],
            "selection": row["selection"],
            "odds": row["odds"],
            "timestamp": now,
            "is_closing": False,
            "minutes_to_kickoff": None,
        })

    if rows:
        try:
            client.table("odds_snapshots").insert(rows).execute()
        except Exception:
            pass  # Dedup errors are fine


def _fetch_af_predictions(af_id_to_match_id: dict[int, str]) -> dict[str, dict]:
    """
    Fetch API-Football predictions for all today's fixtures.
    Returns {match_id: parsed_prediction_dict}
    """
    from workers.api_clients.supabase_client import get_client
    client = get_client()

    af_preds: dict[str, dict] = {}
    fetched = 0
    failed = 0

    console.print(f"\n[cyan]Fetching API-Football predictions ({len(af_id_to_match_id)} fixtures)...[/cyan]")

    for af_id, match_id in af_id_to_match_id.items():
        try:
            raw = get_prediction(af_id)
            if not raw:
                failed += 1
                continue

            parsed = parse_prediction(raw)
            if not parsed.get("af_home_prob"):
                failed += 1
                continue

            af_preds[match_id] = parsed

            # Store full JSONB on the match row
            try:
                client.table("matches").update({
                    "af_prediction": parsed["raw"]
                }).eq("id", match_id).execute()
            except Exception:
                pass  # non-critical

            # S1-AF: also store as separate prediction rows (source='af')
            for market, prob_key in [
                ("1x2_home", "af_home_prob"),
                ("1x2_draw", "af_draw_prob"),
                ("1x2_away", "af_away_prob"),
            ]:
                prob = parsed.get(prob_key)
                if prob is not None:
                    try:
                        store_prediction(match_id, market, {
                            "model_prob": prob,
                            "reasoning": "af_prediction",
                        }, source="af")
                    except Exception:
                        pass

            fetched += 1

        except Exception as e:
            failed += 1
            continue

    console.print(f"  {fetched} predictions stored, {failed} unavailable (league not supported by AF predictions)")
    return af_preds


def _af_agrees_with_bet(selection: str, parsed_pred: dict | None) -> bool | None:
    """
    Determine if API-Football's prediction agrees with our bet selection.

    For 1X2 bets: AF agrees if their highest probability matches the selection.
    For O/U bets: AF agrees if their under_over sign matches ("+2.5" = over, "-2.5" = under).

    Returns True/False/None (None = no AF prediction available).
    """
    if not parsed_pred:
        return None

    home_p = parsed_pred.get("af_home_prob") or 0
    draw_p = parsed_pred.get("af_draw_prob") or 0
    away_p = parsed_pred.get("af_away_prob") or 0

    sel_l = selection.lower()
    if sel_l == "home":
        return home_p >= draw_p and home_p >= away_p
    elif sel_l == "away":
        return away_p >= home_p and away_p >= draw_p
    elif sel_l == "draw":
        return draw_p >= home_p and draw_p >= away_p
    elif "over" in sel_l:
        uo = parsed_pred.get("af_under_over") or ""
        return str(uo).startswith("+")
    elif "under" in sel_l:
        uo = parsed_pred.get("af_under_over") or ""
        return str(uo).startswith("-")

    return None


def _league_path_to_tier(league_path: str) -> int:
    """
    Look up the tier for a league path (e.g. "England / Championship" → 2).
    Falls back to tier 1 for known top-flight countries, tier 2 otherwise.
    Reuses the same LEAGUE_MAP as the Kambi scraper.
    """
    from workers.scrapers.kambi_odds import LEAGUE_MAP
    info = LEAGUE_MAP.get(league_path)
    if info:
        return info["tier"]
    # Reasonable fallback: if we recognise the country, assign tier 1
    country = league_path.split(" / ")[0] if " / " in league_path else ""
    top_flight_countries = {
        "England", "Spain", "Germany", "Italy", "France",
        "Netherlands", "Portugal", "Turkey", "Greece", "Scotland",
        "Belgium", "Sweden", "Denmark", "Norway", "Poland",
        "Croatia", "Romania", "Serbia", "Ukraine", "Hungary",
        "Iceland", "Latvia", "Cyprus", "Georgia", "Estonia",
        "Austria", "Switzerland", "Russia", "Czech Republic",
        "Slovakia", "Bulgaria", "Belarus", "Finland",
    }
    return 1 if country in top_flight_countries else 2


def _merge_odds_sources(
    af_odds_fixtures: list[dict],
    kambi_matches: list[dict],
) -> list[dict]:
    """
    Build the prediction pool by merging all odds sources.

    Priority / role of each source:
      API-Football (primary) — paid, 1236 leagues, gives us tier + league_path
      Kambi        (additive) — different bookmaker, better odds on some markets

    For each match, we keep the best odds across all sources.
    """
    merged: dict[str, dict] = {}
    ODDS_FIELDS = [
        "odds_home", "odds_draw", "odds_away",
        "odds_over_05", "odds_under_05",
        "odds_over_15", "odds_under_15",
        "odds_over_25", "odds_under_25",
        "odds_over_35", "odds_under_35",
        "odds_over_45", "odds_under_45",
    ]

    def _key(m: dict) -> str:
        date_part = m.get("start_time", "")[:10] or "nodate"
        return f"{m.get('home_team', '').lower()}_{m.get('away_team', '').lower()}_{date_part}"

    def _merge_odds_into(existing: dict, incoming: dict, source_name: str):
        """Update existing entry with better odds from incoming, track source."""
        for field in ODDS_FIELDS:
            if incoming.get(field, 0) > existing.get(field, 0):
                existing[field] = incoming[field]
        sources = existing.get("bookmaker", "").split("+")
        if source_name not in sources:
            existing["bookmaker"] = "+".join(filter(None, sources + [source_name]))

    # 1. Seed with AF fixtures (they carry tier, league_path, api_football_id)
    for m in af_odds_fixtures:
        k = _key(m)
        if k and k != "__nodate":  # skip entries with missing team names
            merged[k] = {**m, "bookmaker": "api-football"}

    # 2. Merge scraper sources on top (best odds win, tier/league_path from AF)
    for matches, source_name in [
        (kambi_matches, "kambi"),
    ]:
        for m in matches:
            k = _key(m)
            if not k or k == "__nodate":
                continue
            if k in merged:
                _merge_odds_into(merged[k], m, source_name)
            else:
                # Scraper-only match (league not in AF today) — use as-is
                merged[k] = {**m, "bookmaker": source_name}

    return list(merged.values())


def _fetch_morning_enrichment(af_fixtures_raw: list[dict], af_id_to_match_id: dict[int, str]):
    """
    T2, T3, T9, T10: Enrich today's fixtures with team stats, injuries,
    standings, and H2H data. Called once per morning after fixtures are stored.
    """
    if not af_fixtures_raw or not af_id_to_match_id:
        return

    today = date.today()
    season = today.year if today.month >= 7 else today.year - 1

    # Build per-fixture metadata for enrichment calls
    fixture_meta: dict[int, dict] = {}
    for af_fix in af_fixtures_raw:
        fid = af_fix.get("fixture", {}).get("id")
        if not fid:
            continue
        teams = af_fix.get("teams", {})
        league = af_fix.get("league", {})
        fixture_meta[fid] = {
            "match_id": af_id_to_match_id.get(fid),
            "home_team_api_id": teams.get("home", {}).get("id"),
            "away_team_api_id": teams.get("away", {}).get("id"),
            "league_api_id": league.get("id"),
            "season": league.get("season") or season,
        }

    # ── T3: Injuries (batched, ~7 calls for 130 fixtures) ──────────────────
    console.print("\n[cyan]T3: Fetching injuries (batched)...[/cyan]")
    fixture_ids_with_match = [fid for fid, m in fixture_meta.items() if m.get("match_id")]
    injuries_by_fixture: dict[int, list[dict]] = {}
    try:
        injuries_by_fixture = get_injuries_batched(fixture_ids_with_match)
    except Exception as e:
        console.print(f"  [yellow]Injuries fetch error: {e}[/yellow]")

    inj_stored = 0
    for fid, injuries in injuries_by_fixture.items():
        if not injuries:
            continue
        meta = fixture_meta.get(fid, {})
        match_id = meta.get("match_id")
        if not match_id:
            continue
        parsed = parse_injuries(injuries, home_team_api_id=meta.get("home_team_api_id"))
        inj_stored += store_match_injuries(match_id, fid, parsed)
    console.print(f"  {inj_stored} injury records stored")

    # ── T2: Team Statistics (Tier A only, ~2 calls per Tier A fixture) ────────
    console.print("[cyan]T2: Fetching team statistics (Tier A only)...[/cyan]")

    # Batch-fetch tier for all today's matches (1 query)
    tier_by_match: dict[str, int] = {}
    match_ids_for_tier = [m["match_id"] for m in fixture_meta.values() if m.get("match_id")]
    if match_ids_for_tier:
        try:
            tier_r = get_client().table("matches").select(
                "id, leagues(tier)"
            ).in_("id", match_ids_for_tier).execute()
            for row in (tier_r.data or []):
                lg = row.get("leagues") or {}
                tier_by_match[row["id"]] = lg.get("tier", 3)
        except Exception:
            pass

    t2_stored = 0
    seen_t2: set[tuple] = set()
    for fid, meta in fixture_meta.items():
        match_id_t2 = meta.get("match_id")
        if not match_id_t2:
            continue
        if tier_by_match.get(match_id_t2, 3) != 1:
            continue  # Tier A only

        lg_api_id = meta.get("league_api_id")
        fix_season = meta.get("season")

        for api_id in [meta.get("home_team_api_id"), meta.get("away_team_api_id")]:
            if not api_id or not lg_api_id or not fix_season:
                continue
            key = (api_id, lg_api_id, fix_season)
            if key in seen_t2:
                continue
            seen_t2.add(key)
            try:
                raw_t2 = get_team_statistics(api_id, lg_api_id, fix_season)
                if raw_t2:
                    parsed_t2 = parse_team_statistics(raw_t2)
                    store_team_season_stats(api_id, lg_api_id, fix_season, parsed_t2)
                    t2_stored += 1
            except Exception:
                continue

    console.print(f"  {t2_stored} team stat records stored ({len(seen_t2)} unique Tier A teams)")

    # ── T9: League Standings (~1 call per unique league) ───────────────────
    console.print("[cyan]T9: Fetching league standings...[/cyan]")
    seen_leagues: set[tuple] = set()
    standings_stored = 0

    for fid, meta in fixture_meta.items():
        league_api_id = meta.get("league_api_id")
        fix_season = meta.get("season")
        if not league_api_id or not fix_season:
            continue
        key = (league_api_id, fix_season)
        if key in seen_leagues:
            continue
        seen_leagues.add(key)

        try:
            raw = get_standings(league_api_id, fix_season)
            if not raw:
                continue
            rows = parse_standings(raw)
            stored = store_league_standings(league_api_id, fix_season, rows)
            standings_stored += stored
        except Exception:
            continue

    console.print(f"  {standings_stored} standing rows stored across {len(seen_leagues)} leagues")

    # ── T10: H2H (~1 call per fixture) ─────────────────────────────────────
    console.print("[cyan]T10: Fetching H2H history...[/cyan]")
    h2h_stored = 0

    for fid, meta in fixture_meta.items():
        match_id = meta.get("match_id")
        home_id = meta.get("home_team_api_id")
        away_id = meta.get("away_team_api_id")
        if not match_id or not home_id or not away_id:
            continue

        try:
            raw = get_h2h(home_id, away_id, last=10)
            if not raw:
                continue
            parsed = parse_h2h(raw, home_team_api_id=home_id)
            store_match_h2h(match_id, parsed)
            h2h_stored += 1
        except Exception:
            continue

    console.print(f"  {h2h_stored} H2H records stored")


def _fetch_af_bulk_odds(today_str, af_fixtures_raw, af_id_to_match_id):
    """Fetch odds from API-Football bulk endpoint and parse per fixture."""
    af_odds_fixtures = []
    af_odds_fetched = 0

    console.print("\n[cyan]Fetching odds from API-Football (bulk)...[/cyan]")
    try:
        bulk_odds = get_odds_by_date(today_str)
        console.print(f"  {len(bulk_odds)} fixtures with odds from API-Football")

        for af_fix in af_fixtures_raw:
            af_id = af_fix.get("fixture", {}).get("id")
            if not af_id or af_id not in bulk_odds:
                continue

            parsed = parse_fixture_odds(bulk_odds[af_id])
            if not parsed:
                continue

            best: dict[str, float] = {}
            for row in parsed:
                if row["market"] == "1x2":
                    field = f"odds_{row['selection']}"
                    if row["odds"] > best.get(field, 0):
                        best[field] = row["odds"]
                elif row["market"] == "btts":
                    field = f"odds_btts_{row['selection']}"
                    if row["odds"] > best.get(field, 0):
                        best[field] = row["odds"]
                else:
                    direction = "over" if row["selection"] == "over" else "under"
                    line_suffix = row["market"].replace("over_under_", "")
                    field = f"odds_{direction}_{line_suffix}"
                    if row["odds"] > best.get(field, 0):
                        best[field] = row["odds"]

            if not best:
                continue

            match_dict = fixture_to_match_dict(af_fix)
            league_path = match_dict["league_path"]
            tier = _league_path_to_tier(league_path)

            af_odds_fixtures.append({
                **match_dict,
                **best,
                "tier": tier,
                "bookmaker": "api-football",
            })
            af_odds_fetched += 1

            match_id = af_id_to_match_id.get(af_id)
            if match_id:
                _store_parsed_odds(match_id, parsed)

    except Exception as e:
        console.print(f"  [yellow]AF bulk odds error: {e}[/yellow]")

    console.print(f"  {af_odds_fetched} AF fixtures with odds (tier assigned)")
    return af_odds_fixtures


def _parallel_fetch(af_id_to_match_id, af_fixtures_raw, today_str, all_fixtures):
    """
    Run data fetching in parallel across 2 thread groups:
      A: API-Football (predictions + enrichment + bulk odds)
      B: Kambi — different API, fast
    """
    def _af_work():
        preds = {}
        if af_id_to_match_id:
            preds = _fetch_af_predictions(af_id_to_match_id)
            console.print(f"  AF predictions: {len(preds)} available out of {len(af_id_to_match_id)} fixtures")
        console.print("\n[cyan]Running morning enrichment (T2/T3/T9/T10)...[/cyan]")
        try:
            _fetch_morning_enrichment(af_fixtures_raw, af_id_to_match_id)
        except Exception as e:
            console.print(f"  [yellow]Enrichment error (non-fatal): {e}[/yellow]")
        odds = _fetch_af_bulk_odds(today_str, af_fixtures_raw, af_id_to_match_id)
        return preds, odds

    def _scraper_work():
        console.print("\n[cyan]Fetching odds from Kambi...[/cyan]")
        kambi = get_target_league_matches()
        console.print(f"  {len(kambi)} matches with Kambi odds")
        return kambi

    with ThreadPoolExecutor(max_workers=2) as executor:
        af_future = executor.submit(_af_work)
        scraper_future = executor.submit(_scraper_work)

        af_preds, af_odds_fixtures = af_future.result()
        kambi_matches = scraper_future.result()

    return af_preds, af_odds_fixtures, kambi_matches


def _next_day(date_str: str) -> str:
    """Return the next calendar day as YYYY-MM-DD."""
    from datetime import timedelta
    d = date.fromisoformat(date_str)
    return (d + timedelta(days=1)).isoformat()


def _load_today_from_db(today_str: str) -> tuple[list[dict], dict[str, dict]]:
    """
    PIPE-2: Load today's matches with best pre-match odds + AF predictions from DB.
    No API calls — reads odds_snapshots and predictions tables only.
    Returns (odds_matches list, af_preds dict keyed by match_id).
    """
    from collections import defaultdict as _dd
    client = get_client()
    next_day_str = _next_day(today_str)

    # 1. Load today's scheduled + recently-live matches with team + league info
    #    Include live matches so we don't miss games that kicked off between pipeline runs.
    #    The bet loop below skips any live match that's been going for >5 minutes.
    #    Duplicate bets prevented by DB unique constraint (bot_id, match_id, market, selection).
    #    Finished matches excluded — their pre-match odds are stale.
    matches_r = client.table("matches").select(
        "id, date, referee, season, "
        "home_team:home_team_id(name, country), "
        "away_team:away_team_id(name, country), "
        "league:league_id(name, country, tier, api_football_id)"
    ).gte("date", f"{today_str}T00:00:00Z").lt(
        "date", f"{next_day_str}T00:00:00Z"
    ).in_("status", ["scheduled", "live"]).execute()

    if matches_r.data:
        # Filter out live matches that kicked off > 5 minutes ago
        now_utc = datetime.now(timezone.utc)
        filtered = []
        for m in matches_r.data:
            kickoff_str = m.get("date", "")
            try:
                kickoff = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                minutes_since = (now_utc - kickoff).total_seconds() / 60
                if minutes_since <= 5:
                    filtered.append(m)  # Scheduled or just kicked off
            except (ValueError, AttributeError):
                filtered.append(m)  # Can't parse date — include it
        matches_r.data = filtered

    if not matches_r.data:
        return [], {}

    match_ids = [m["id"] for m in matches_r.data]

    # 2. Load best pre-match odds per match per (market, selection)
    #    Paginate in chunks of match IDs to avoid Supabase row limits (~1000 default)
    best: dict[str, dict[str, float]] = _dd(lambda: _dd(float))
    bm_sources: dict[str, set] = _dd(set)
    for i in range(0, len(match_ids), 50):
        chunk = match_ids[i:i + 50]
        odds_r = client.table("odds_snapshots").select(
            "match_id, market, selection, odds, bookmaker"
        ).in_("match_id", chunk).eq("is_closing", False).limit(10000).execute()

        for row in (odds_r.data or []):
            mid = row["match_id"]
            key = f"{row['market']}_{row['selection']}"
            if float(row["odds"]) > best[mid][key]:
                best[mid][key] = float(row["odds"])
            bm_sources[mid].add(row.get("bookmaker", "unknown"))

    MARKET_TO_FIELD = {
        "1x2_home": "odds_home", "1x2_draw": "odds_draw", "1x2_away": "odds_away",
        "over_under_05_over": "odds_over_05", "over_under_05_under": "odds_under_05",
        "over_under_15_over": "odds_over_15", "over_under_15_under": "odds_under_15",
        "over_under_25_over": "odds_over_25", "over_under_25_under": "odds_under_25",
        "over_under_35_over": "odds_over_35", "over_under_35_under": "odds_under_35",
        "over_under_45_over": "odds_over_45", "over_under_45_under": "odds_under_45",
        "btts_yes": "odds_btts_yes", "btts_no": "odds_btts_no",
    }

    # 3. Build match dicts
    odds_matches = []
    for m in matches_r.data:
        mid = m["id"]
        match_best = best.get(mid, {})
        if not match_best:
            continue  # no odds at all — skip

        ht = m.get("home_team") or {}
        at = m.get("away_team") or {}
        lg = m.get("league") or {}
        country = lg.get("country", "")
        league_name = lg.get("name", "")

        match_dict: dict = {
            "id": mid,
            "home_team": ht.get("name", ""),
            "away_team": at.get("name", ""),
            "start_time": m.get("date", ""),
            "league_path": f"{country} / {league_name}" if country and league_name else league_name,
            "tier": int(lg.get("tier") or 1),
            "league_api_id": lg.get("api_football_id"),
            "season": m.get("season"),
            "referee": m.get("referee"),
            "home_team_api_id": None,
            "away_team_api_id": None,
            "bookmaker": "+".join(sorted(bm_sources.get(mid, {"unknown"}))),
            "odds_home": 0, "odds_draw": 0, "odds_away": 0,
            "odds_over_05": 0, "odds_under_05": 0,
            "odds_over_15": 0, "odds_under_15": 0,
            "odds_over_25": 0, "odds_under_25": 0,
            "odds_over_35": 0, "odds_under_35": 0,
            "odds_over_45": 0, "odds_under_45": 0,
            "odds_btts_yes": 0, "odds_btts_no": 0,
        }
        for mkt_sel, field in MARKET_TO_FIELD.items():
            val = match_best.get(mkt_sel, 0)
            if val > 0:
                match_dict[field] = val
        odds_matches.append(match_dict)

    # 4. Load AF predictions from predictions table (source='af')
    preds_r = client.table("predictions").select(
        "match_id, market, model_probability"
    ).in_("match_id", match_ids).eq("source", "af").execute()

    af_preds: dict[str, dict] = {}
    for p in (preds_r.data or []):
        mid = p["match_id"]
        if mid not in af_preds:
            af_preds[mid] = {}
        mp = float(p["model_probability"])
        if p["market"] == "1x2_home":
            af_preds[mid]["af_home_prob"] = mp
        elif p["market"] == "1x2_draw":
            af_preds[mid]["af_draw_prob"] = mp
        elif p["market"] == "1x2_away":
            af_preds[mid]["af_away_prob"] = mp

    console.print(f"  {len(odds_matches)} matches with odds loaded from DB")
    console.print(f"  {len(af_preds)} AF predictions loaded from DB")
    return odds_matches, af_preds


def run_morning(skip_fetch: bool = False):
    """
    Fetch data → predict → store matches/odds/bets in Supabase.

    skip_fetch=True (Phase 2): reads pre-fetched data from DB — no API calls.
    skip_fetch=False (Phase 1 / manual): fetches from API-Football + Kambi first.
    """
    today_str = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Pipeline: {today_str} ═══[/bold green]\n")

    # 1. Ensure bots exist in DB
    console.print("[cyan]Creating/checking bots in Supabase...[/cyan]")
    bot_ids = ensure_bots(BOTS_CONFIG)
    console.print(f"  {len(bot_ids)} bots ready")

    if skip_fetch:
        # Phase 2: read from DB — upstream jobs already fetched everything
        console.print("\n[cyan]Loading today's data from DB (skip_fetch=True)...[/cyan]")
        odds_matches, af_preds = _load_today_from_db(today_str)
        if not odds_matches:
            console.print("[yellow]No matches with odds in DB today — predictions skipped.[/yellow]")
            return
        console.print(f"  [bold]{len(odds_matches)} matches in prediction pool[/bold]")
    else:
        # Phase 1: fetch from API-Football + Kambi
        # 2. Fetch ALL fixtures from API-Football
        console.print("\n[cyan]Fetching all fixtures from API-Football...[/cyan]")
        af_fixtures_raw = []
        all_fixtures = []
        try:
            af_fixtures_raw = get_fixtures_by_date(today_str)
            console.print(f"  {len(af_fixtures_raw)} fixtures from API-Football")
        except Exception as e:
            console.print(f"  [red]API-Football error: {e}[/red]")
            return

        if not af_fixtures_raw:
            console.print("[yellow]No fixtures from API-Football today.[/yellow]")
            return

        # 3. Store all fixtures in Supabase
        console.print("\n[cyan]Storing all fixtures in Supabase...[/cyan]")
        stored_fixture_count = 0
        af_id_to_match_id: dict[int, str] = {}

        for af_fix in af_fixtures_raw:
            match_dict = fixture_to_match_dict(af_fix)
            try:
                match_id = store_match(match_dict)
                af_id = af_fix.get("fixture", {}).get("id")
                if match_id and af_id:
                    af_id_to_match_id[af_id] = match_id
                all_fixtures.append({
                    "home_team": match_dict["home_team"],
                    "away_team": match_dict["away_team"],
                    "date": match_dict["start_time"],
                    "league_name": af_fix.get("league", {}).get("name", ""),
                    "country": af_fix.get("league", {}).get("country", ""),
                    "status": af_fix.get("fixture", {}).get("status", {}).get("short", "NS"),
                    "api_football_id": af_id,
                })
                stored_fixture_count += 1
            except Exception as e:
                console.print(f"  [yellow]Could not store {match_dict.get('home_team')} vs {match_dict.get('away_team')}: {e}[/yellow]")

        console.print(f"  {stored_fixture_count} fixtures stored")

        # === PARALLEL DATA FETCH ===
        console.print("\n[cyan]Running parallel data fetch (predictions + enrichment + odds)...[/cyan]")
        af_preds, af_odds_fixtures, kambi_matches = \
            _parallel_fetch(af_id_to_match_id, af_fixtures_raw, today_str, all_fixtures)

        # 6. Merge odds sources — AF primary, Kambi additive
        odds_matches = _merge_odds_sources(af_odds_fixtures, kambi_matches)

        console.print(f"\n  [bold]{len(odds_matches)} matches in prediction pool[/bold]")
        source_counts: dict[str, int] = {}
        for m in odds_matches:
            for src in m.get("bookmaker", "unknown").split("+"):
                source_counts[src] = source_counts.get(src, 0) + 1
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

    # Pre-compute team name sets once (avoid rebuilding per match — saves ~30s)
    v9_teams = set(hist_targets["home_team"].unique()) | set(hist_targets["away_team"].unique())
    global_teams = None
    if hist_targets_global is not None:
        global_teams = set(hist_targets_global["home_team"].unique()) | set(hist_targets_global["away_team"].unique())
    team_sets = (v9_teams, global_teams)

    # 8. Process each match with odds
    console.print("\n[cyan]Processing matches with odds...[/cyan]")
    total_bets = 0
    # 11.6: Track placed bets per bot per league for exposure management
    from collections import defaultdict
    league_bet_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for match in odds_matches:
        # Store match in Supabase (idempotent upsert — skipped if id pre-set from DB load)
        if match.get("id"):
            match_id = match["id"]
        else:
            try:
                match_id = store_match(match)
            except Exception as e:
                console.print(f"  [red]Error storing match: {e}[/red]")
                continue

        # Store odds — skipped when loading from DB (fetch_odds.py already stored them)
        if not match.get("id"):
            try:
                store_odds(match_id, {**match, "bookmaker": match.get("bookmaker", match.get("operator", "unknown"))})
            except Exception as e:
                console.print(f"  [yellow]Error storing odds: {e}[/yellow]")

        # Compute Poisson prediction
        poisson_pred = compute_prediction(
            match, hist_targets,
            hist_targets_global=hist_targets_global,
            _team_sets=team_sets,
        )
        # Tier D fallback: if Poisson has no historical data for this match,
        # use API-Football's own prediction probabilities (already fetched for
        # ~191/280 matches/day via the /predictions endpoint).  This ensures we
        # generate a prediction — and evaluate bets — for every match that has
        # odds, not just the subset our CSVs happen to cover.
        af_pred_for_match = af_preds.get(match_id)
        if not poisson_pred:
            if af_pred_for_match and af_pred_for_match.get("af_home_prob"):
                hp = af_pred_for_match["af_home_prob"]
                dp = af_pred_for_match["af_draw_prob"] or 0
                ap = af_pred_for_match["af_away_prob"] or 0
                total = hp + dp + ap
                if total > 0:
                    hp, dp, ap = hp / total, dp / total, ap / total
                over_p = 0.5  # AF doesn't give O/U; use neutral prior
                poisson_pred = {
                    "home_prob": hp,
                    "draw_prob": dp,
                    "away_prob": ap,
                    "over_25_prob": over_p,
                    "under_25_prob": 1 - over_p,
                    "exp_home": None,
                    "exp_away": None,
                    "data_tier": "D",
                }
            else:
                continue  # No Poisson data AND no AF prediction — truly skip

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
        data_tier = pred.get("data_tier", "A")

        # S1: Store Poisson predictions for all three 1x2 markets unconditionally.
        # poisson_pred is always available here (it's the base prediction, never None at this point).
        for market, prob_key, odds_field in [
            ("1x2_home", "home_prob",  "odds_home"),
            ("1x2_draw", "draw_prob",  "odds_draw"),
            ("1x2_away", "away_prob",  "odds_away"),
        ]:
            odds_val = match.get(odds_field, 0)
            if odds_val > 0 and poisson_pred.get(prob_key) is not None:
                try:
                    store_prediction(match_id, market, {
                        "model_prob": poisson_pred[prob_key],
                        "implied_prob": 1 / odds_val,
                        "edge": poisson_pred[prob_key] - (1 / odds_val),
                        "reasoning": f"data_tier={data_tier}",
                    }, source="poisson")
                except Exception:
                    pass

        # S1-XGB: Store XGBoost individual predictions when ensemble ran
        if xgb_pred:
            for market, xgb_key, odds_field in [
                ("1x2_home", "xgb_home_prob", "odds_home"),
                ("1x2_draw", "xgb_draw_prob", "odds_draw"),
                ("1x2_away", "xgb_away_prob", "odds_away"),
            ]:
                odds_val = match.get(odds_field, 0)
                if odds_val > 0 and xgb_pred.get(xgb_key) is not None:
                    try:
                        store_prediction(match_id, market, {
                            "model_prob": xgb_pred[xgb_key],
                            "implied_prob": 1 / odds_val,
                            "edge": xgb_pred[xgb_key] - (1 / odds_val),
                            "reasoning": f"data_tier={data_tier}",
                        }, source="xgboost")
                    except Exception:
                        pass

        for market, prob_key in [
            ("1x2_home", "home_prob"),
            ("1x2_draw", "draw_prob"),
            ("1x2_away", "away_prob"),
            ("over15", "over_15_prob"),
            ("under15", "under_15_prob"),
            ("over25", "over_25_prob"),
            ("under25", "under_25_prob"),
            ("over35", "over_35_prob"),
            ("under35", "under_35_prob"),
            ("btts_yes", "btts_yes_prob"),
            ("btts_no", "btts_no_prob"),
        ]:
            odds_key = {
                "1x2_home": "odds_home",
                "1x2_draw": "odds_draw",
                "1x2_away": "odds_away",
                "over15": "odds_over_15",
                "under15": "odds_under_15",
                "over25": "odds_over_25",
                "under25": "odds_under_25",
                "over35": "odds_over_35",
                "under35": "odds_under_35",
                "btts_yes": "odds_btts_yes",
                "btts_no": "odds_btts_no",
            }[market]

            odds_val = match.get(odds_key, 0)
            if odds_val > 0:
                try:
                    # Store ensemble prediction (source='ensemble')
                    store_prediction(match_id, market, {
                        "model_prob": pred[prob_key],
                        "implied_prob": 1 / odds_val,
                        "edge": pred[prob_key] - (1 / odds_val),
                        "odds": odds_val,
                        "reasoning": f"data_tier={data_tier}",
                    }, source="ensemble")
                except Exception:
                    pass

        # S3/S4/S5/BDM-1: Write morning signals to match_signals
        try:
            write_morning_signals(
                match_id,
                league_api_id=match.get("league_api_id"),
                season=match.get("season"),
                home_team_api_id=match.get("home_team_api_id"),
                away_team_api_id=match.get("away_team_api_id"),
                referee=match.get("referee"),
                opening_odds_home=match.get("odds_home"),
                opening_odds_draw=match.get("odds_draw"),
                opening_odds_away=match.get("odds_away"),
            )
        except Exception:
            pass  # non-critical

        # Place bets for each bot
        tier = match.get("tier", 1)
        country = match.get("league_path", "").split(" / ")[0] if " / " in match.get("league_path", "") else ""

        # Data-tier adjustments (conservative stake / extra edge for lower-quality data):
        #   A — our CSV + odds history, full calibration → no bump, full stake
        #   B — global ELO CSV, results only → +2% edge req, 50% stake cap
        #   D — AF prediction only → +8% edge req, 20% stake cap
        #   D — AF prediction probabilities only, no goals model → +8% edge req, 20% stake cap
        DATA_TIER_EDGE_BUMP = {"A": 0.00, "B": 0.02, "C": 0.05, "D": 0.08}
        edge_bump = DATA_TIER_EDGE_BUMP.get(data_tier, 0.00)
        tier_tag = f"[Tier {data_tier}] " if data_tier != "A" else ""

        # P2: Compute odds movement for this match (once per match, cached per market)
        odds_movement_cache = {}

        # T1: AF prediction for this match (already looked up for Tier D above)
        af_pred = af_pred_for_match

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

            # Build candidates: (market, selection, odds, raw_prob, os_market, os_selection, threshold)
            candidate_specs = []

            # 1X2: Home
            if "1x2" in config["markets"] and match["odds_home"] > 0 and (not sel_filter or "Home" in sel_filter):
                odds = match["odds_home"]
                me = (thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08))
                candidate_specs.append(("1X2", "Home", odds, pred["home_prob"], "1x2", "home", me))

            # 1X2: Draw
            if "1x2" in config["markets"] and match["odds_draw"] > 0 and (not sel_filter or "Draw" in sel_filter):
                candidate_specs.append(("1X2", "Draw", match["odds_draw"], pred["draw_prob"], "1x2", "draw", thresholds.get("1x2_long", 0.08)))

            # 1X2: Away
            if "1x2" in config["markets"] and match["odds_away"] > 0 and (not sel_filter or "Away" in sel_filter):
                candidate_specs.append(("1X2", "Away", match["odds_away"], pred["away_prob"], "1x2", "away", thresholds.get("1x2_long", 0.08)))

            # O/U 2.5
            if "ou" in config.get("markets", []) and match.get("odds_over_25", 0) > 0:
                candidate_specs.append(("O/U", "Over 2.5", match["odds_over_25"], pred["over_25_prob"], "over_under_25", "over", thresholds.get("ou", 0.05)))
            if "ou" in config.get("markets", []) and match.get("odds_under_25", 0) > 0:
                candidate_specs.append(("O/U", "Under 2.5", match["odds_under_25"], pred["under_25_prob"], "over_under_25", "under", thresholds.get("ou", 0.05)))

            # O/U 1.5
            if "ou15" in config.get("markets", []) and match.get("odds_over_15", 0) > 0:
                candidate_specs.append(("O/U", "Over 1.5", match["odds_over_15"], pred.get("over_15_prob", 0), "over_under_15", "over", thresholds.get("ou", 0.05)))
            if "ou15" in config.get("markets", []) and match.get("odds_under_15", 0) > 0:
                candidate_specs.append(("O/U", "Under 1.5", match["odds_under_15"], pred.get("under_15_prob", 0), "over_under_15", "under", thresholds.get("ou", 0.05)))

            # O/U 3.5
            if "ou35" in config.get("markets", []) and match.get("odds_over_35", 0) > 0:
                candidate_specs.append(("O/U", "Over 3.5", match["odds_over_35"], pred.get("over_35_prob", 0), "over_under_35", "over", thresholds.get("ou", 0.05)))
            if "ou35" in config.get("markets", []) and match.get("odds_under_35", 0) > 0:
                candidate_specs.append(("O/U", "Under 3.5", match["odds_under_35"], pred.get("under_35_prob", 0), "over_under_35", "under", thresholds.get("ou", 0.05)))

            # BTTS
            if "btts" in config.get("markets", []) and match.get("odds_btts_yes", 0) > 0:
                candidate_specs.append(("BTTS", "Yes", match["odds_btts_yes"], pred.get("btts_yes_prob", 0), "btts", "yes", thresholds.get("btts", 0.06)))
            if "btts" in config.get("markets", []) and match.get("odds_btts_no", 0) > 0:
                candidate_specs.append(("BTTS", "No", match["odds_btts_no"], pred.get("btts_no_prob", 0), "btts", "no", thresholds.get("btts", 0.06)))

            for mkt, selection, odds, raw_mp, os_market, os_selection, base_threshold in candidate_specs:
                ip = 1 / odds

                # P1: Calibrate probability (tier-specific shrinkage + Platt sigmoid)
                platt_market = f"{os_market}_{os_selection}"
                cal_prob = calibrate_prob(raw_mp, ip, tier=tier, market=platt_market)

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
                # T1: AF prediction agreement
                af_agrees = _af_agrees_with_bet(selection, af_pred)

                # 11.6: Exposure management — halve stake for 3rd+ bet in same league per bot
                _league_key = match.get("league_path", "unknown")
                _league_count = league_bet_counts[bot_name][_league_key]
                if _league_count >= 2:
                    stake = max(round(stake * 0.5, 2), 1.0)
                    console.print(f"  [dim]Exposure cap ({bot_name}): {_league_count} bets already in {_league_key} — stake halved to €{stake:.2f}[/dim]")

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
                        # T1: API-Football prediction comparison
                        "af_home_prob": af_pred.get("af_home_prob") if af_pred else None,
                        "af_draw_prob": af_pred.get("af_draw_prob") if af_pred else None,
                        "af_away_prob": af_pred.get("af_away_prob") if af_pred else None,
                        "af_agrees": af_agrees,
                    })
                    if bet_id:
                        total_bets += 1
                        league_bet_counts[bot_name][_league_key] += 1
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
        af_tag = ""
        if af_pred:
            hp = af_pred.get("af_home_prob", 0) or 0
            dp = af_pred.get("af_draw_prob", 0) or 0
            ap = af_pred.get("af_away_prob", 0) or 0
            af_tag = f" [AF: H{hp:.0%}/D{dp:.0%}/A{ap:.0%}]"
        console.print(f"  {match['home_team']} vs {match['away_team']} — predicted [Tier {data_tier}]{ensemble_tag}{disagree_tag}{af_tag}")

    console.print(f"\n[bold green]Done! {total_bets} bets placed across {len(BOTS_CONFIG)} bots[/bold green]")
    console.print(f"[green]All data stored in Supabase — frontend can display it now[/green]")

    # 11.6: Cross-match correlation check — warn about concentrated exposure
    _check_exposure_concentration()


def _check_exposure_concentration():
    """
    11.6: Cross-match correlation / exposure management — post-placement audit.
    Stakes are already reduced during placement (3rd+ bet per league per bot → 50% stake).
    This function logs a summary of any concentrated exposure after the fact.

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
    """Settle pending bets — delegates to settlement.py"""
    console.print("[yellow]Use settlement.py directly for settlement.[/yellow]")


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
