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
import numpy as np
import pandas as pd
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


def compute_prediction(match, hist_targets):
    """Compute prediction for a live match using historical team data"""
    from workers.utils.team_names import normalize_team_name, fuzzy_match_team

    home_raw = match["home_team"]
    away_raw = match["away_team"]

    # Map Kambi names to football-data names
    home = normalize_team_name(home_raw, source="kambi")
    away = normalize_team_name(away_raw, source="kambi")

    # Try to find in historical data — first exact, then fuzzy
    known_teams = set(hist_targets["home_team"].unique()) | set(hist_targets["away_team"].unique())

    home_matched = fuzzy_match_team(home, known_teams) or fuzzy_match_team(home_raw, known_teams)
    away_matched = fuzzy_match_team(away, known_teams) or fuzzy_match_team(away_raw, known_teams)

    if not home_matched or not away_matched:
        return None

    home_hist = hist_targets[
        (hist_targets["home_team"] == home_matched) |
        (hist_targets["away_team"] == home_matched)
    ].tail(20)

    away_hist = hist_targets[
        (hist_targets["home_team"] == away_matched) |
        (hist_targets["away_team"] == away_matched)
    ].tail(20)

    if len(home_hist) < 3 or len(away_hist) < 3:
        return None

    # Compute expected goals
    home_gf = []
    home_ga = []
    for _, m in home_hist.iterrows():
        if m["home_team"] == home_matched:
            home_gf.append(m["FTHG"])
            home_ga.append(m["FTAG"])
        else:
            home_gf.append(m["FTAG"])
            home_ga.append(m["FTHG"])

    away_gf = []
    away_ga = []
    for _, m in away_hist.iterrows():
        if m["home_team"] == away_matched:
            away_gf.append(m["FTHG"])
            away_ga.append(m["FTAG"])
        else:
            away_gf.append(m["FTAG"])
            away_ga.append(m["FTHG"])

    exp_h = max(0.3, np.mean(home_gf[-10:])) * 1.08  # Slight home advantage
    exp_a = max(0.3, np.mean(away_gf[-10:])) * 0.92
    exp_h = (exp_h + np.mean(away_ga[-10:])) / 2
    exp_a = (exp_a + np.mean(home_ga[-10:])) / 2

    # Poisson probabilities
    p_h = p_d = p_a = p_over = 0.0
    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_h) * poisson.pmf(a, exp_a)
            if h > a: p_h += p
            elif h == a: p_d += p
            else: p_a += p
            if h + a > 2: p_over += p

    return {
        "home_prob": p_h, "draw_prob": p_d, "away_prob": p_a,
        "over_25_prob": p_over, "under_25_prob": 1 - p_over,
        "exp_home": exp_h, "exp_away": exp_a,
    }


def run_morning():
    """Fetch data → predict → store matches/odds/bets in Supabase"""
    today_str = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Pipeline: {today_str} ═══[/bold green]\n")

    # 1. Ensure bots exist in DB
    console.print("[cyan]Creating/checking bots in Supabase...[/cyan]")
    bot_ids = ensure_bots(BOTS_CONFIG)
    console.print(f"  {len(bot_ids)} bots ready")

    # 2. Fetch odds
    console.print("\n[cyan]Fetching odds from Kambi...[/cyan]")
    odds_matches = get_target_league_matches()
    console.print(f"  {len(odds_matches)} matches with odds")

    if not odds_matches:
        console.print("[yellow]No matches today.[/yellow]")
        return

    # 3. Load historical data for predictions
    console.print("\n[cyan]Loading historical data...[/cyan]")
    targets_path = PROCESSED_DIR / "targets_v9.csv"
    if not targets_path.exists():
        targets_path = PROCESSED_DIR / "targets_fast.csv"
    hist_targets = pd.read_csv(targets_path)
    console.print(f"  {len(hist_targets):,} historical matches loaded")

    # 4. Process each match
    console.print("\n[cyan]Processing matches...[/cyan]")
    total_bets = 0

    for match in odds_matches:
        # Store match in Supabase
        try:
            match_id = store_match(match)
        except Exception as e:
            console.print(f"  [red]Error storing match: {e}[/red]")
            continue

        # Store odds
        try:
            store_odds(match_id, match)
        except Exception as e:
            console.print(f"  [yellow]Error storing odds: {e}[/yellow]")

        # Compute prediction
        pred = compute_prediction(match, hist_targets)
        if not pred:
            continue

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
                me = thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08)
                if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                    bet_candidates.append(("1X2", "Home", odds, mp, ip, edge))

            # 1X2 Away
            if "1x2" in config["markets"] and match["odds_away"] > 0:
                odds = match["odds_away"]
                mp = pred["away_prob"]
                ip = 1 / odds
                edge = mp - ip
                me = thresholds.get("1x2_long", 0.08)
                if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                    bet_candidates.append(("1X2", "Away", odds, mp, ip, edge))

            # O/U Over
            if "ou" in config.get("markets", []) and match.get("odds_over_25", 0) > 0:
                odds = match["odds_over_25"]
                mp = pred["over_25_prob"]
                ip = 1 / odds
                edge = mp - ip
                me = thresholds.get("ou", 0.05)
                if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                    bet_candidates.append(("O/U", "Over 2.5", odds, mp, ip, edge))

            # O/U Under
            if "ou" in config.get("markets", []) and match.get("odds_under_25", 0) > 0:
                odds = match["odds_under_25"]
                mp = pred["under_25_prob"]
                ip = 1 / odds
                edge = mp - ip
                me = thresholds.get("ou", 0.05)
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
                        "stake": STAKE,
                        "placed_at": datetime.now().isoformat(),
                    })
                    total_bets += 1
                except Exception as e:
                    console.print(f"  [red]Error storing bet: {e}[/red]")

        # Brief status
        console.print(f"  {match['home_team']} vs {match['away_team']} — predicted")

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
