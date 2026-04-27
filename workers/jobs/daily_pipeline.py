"""
OddsIntel — Daily Paper Trading Pipeline
Runs automatically every day. No manual intervention needed.

Schedule:
  08:00 UTC — Fetch today's fixtures + odds
  09:00 UTC — Run predictions, place paper bets for all bots
  22:00 UTC — Settle completed matches, update P&L
  23:00 UTC — Generate daily report

This script is the single entry point for the entire daily cycle.
"""

import sys
import json
import os
import time
import joblib
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, date, timedelta
from scipy.stats import poisson
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.scrapers.kambi_odds import fetch_all_operators, get_target_league_matches
from workers.scrapers.flashscore import get_todays_matches_from_flashscore

console = Console()

ENGINE_DIR = Path(__file__).parent.parent.parent
PROCESSED_DIR = ENGINE_DIR / "data" / "processed"
MODELS_DIR = ENGINE_DIR / "data" / "models" / "soccer"
RESULTS_DIR = ENGINE_DIR / "data" / "model_results"
DAILY_DIR = ENGINE_DIR / "data" / "daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# BOT DEFINITIONS
# ============================================================

BOTS = {
    "bot_v10_all": {
        "description": "v10 model, all target leagues, tier-adjusted thresholds",
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
        "description": "Tier 2-4 only, 1X2 market only (our best signal)",
        "markets": ["1x2"],
        "tier_filter": [2, 3, 4],
        "edge_thresholds": {
            2: {"1x2_fav": 0.05, "1x2_long": 0.07, "ou": 99},
            3: {"1x2_fav": 0.04, "1x2_long": 0.06, "ou": 99},
            4: {"1x2_fav": 0.03, "1x2_long": 0.05, "ou": 99},
        },
        "odds_range": (1.35, 3.50),
        "min_prob": 0.35,
    },
    "bot_tier34": {
        "description": "Tier 3-4 only, all markets (historically most profitable)",
        "markets": ["1x2", "ou"],
        "tier_filter": [3, 4],
        "edge_thresholds": {
            3: {"1x2_fav": 0.04, "1x2_long": 0.05, "ou": 0.04},
            4: {"1x2_fav": 0.03, "1x2_long": 0.04, "ou": 0.03},
        },
        "odds_range": (1.30, 4.00),
        "min_prob": 0.30,
    },
    "bot_conservative": {
        "description": "Only bet when edge > 10%, very selective",
        "markets": ["1x2", "ou"],
        "tier_filter": None,
        "edge_thresholds": {
            1: {"1x2_fav": 0.10, "1x2_long": 0.15, "ou": 0.12},
            2: {"1x2_fav": 0.10, "1x2_long": 0.12, "ou": 0.10},
            3: {"1x2_fav": 0.08, "1x2_long": 0.10, "ou": 0.08},
            4: {"1x2_fav": 0.08, "1x2_long": 0.10, "ou": 0.08},
        },
        "odds_range": (1.50, 3.00),
        "min_prob": 0.40,
    },
    "bot_aggressive": {
        "description": "Low threshold (3% edge), high volume",
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
}

STAKE = 10.0
STARTING_BANKROLL = 1000.0


# ============================================================
# STEP 1: FETCH DATA
# ============================================================

def fetch_todays_data() -> tuple[list[dict], list[dict]]:
    """Fetch fixtures from Sofascore and odds from Kambi"""
    console.print("[bold cyan]Step 1: Fetching today's data...[/bold cyan]")

    # Fixtures
    console.print("  Fetching fixtures from Sofascore...")
    fixtures = get_todays_matches_from_flashscore()
    console.print(f"  → {len(fixtures)} matches found")

    # Odds
    console.print("  Fetching odds from Kambi (Unibet/Paf)...")
    odds = get_target_league_matches()
    console.print(f"  → {len(odds)} matches with odds in target leagues")

    return fixtures, odds


# ============================================================
# STEP 2: RUN PREDICTIONS
# ============================================================

def load_model_and_features():
    """Load the latest model and feature computation pipeline"""
    # Load pre-computed features for historical context
    features_path = PROCESSED_DIR / "features_v9.csv"
    targets_path = PROCESSED_DIR / "targets_v9.csv"

    if not features_path.exists():
        console.print("[red]No feature data found. Run model_v9_xg.py first.[/red]")
        return None, None, None

    hist_features = pd.read_csv(features_path)
    hist_targets = pd.read_csv(targets_path, parse_dates=["Date"])
    feature_cols = list(hist_features.columns)

    return hist_features, hist_targets, feature_cols


def compute_match_prediction(match_odds: dict, hist_features, hist_targets, feature_cols) -> dict:
    """
    Compute prediction for a single match using the ensemble approach.
    For live matches, we use the historical average for the teams involved
    as a feature approximation (since we don't have rolling stats computed).

    In production, we'd compute proper rolling features from the database.
    For now, use ELO + historical averages as a simplified approach.
    """
    # This is a simplified prediction for the MVP paper trading phase.
    # It uses the bookmaker odds as a baseline and looks for discrepancies
    # with our model's view of team strength.

    home = match_odds["home_team"]
    away = match_odds["away_team"]

    # Find historical data for these teams
    home_matches = hist_targets[
        (hist_targets["home_team"] == home) | (hist_targets["away_team"] == home)
    ].tail(20)

    away_matches = hist_targets[
        (hist_targets["home_team"] == away) | (hist_targets["away_team"] == away)
    ].tail(20)

    if len(home_matches) < 5 or len(away_matches) < 5:
        return None  # Not enough data

    # Compute basic stats from history
    home_goals_for = []
    home_goals_against = []
    away_goals_for = []
    away_goals_against = []

    for _, m in home_matches.iterrows():
        if m["home_team"] == home:
            home_goals_for.append(m["FTHG"])
            home_goals_against.append(m["FTAG"])
        else:
            home_goals_for.append(m["FTAG"])
            home_goals_against.append(m["FTHG"])

    for _, m in away_matches.iterrows():
        if m["home_team"] == away:
            away_goals_for.append(m["FTHG"])
            away_goals_against.append(m["FTAG"])
        else:
            away_goals_for.append(m["FTAG"])
            away_goals_against.append(m["FTHG"])

    # Expected goals (simplified Poisson)
    exp_home = max(0.3, np.mean(home_goals_for[-10:])) * 1.1  # Home advantage
    exp_away = max(0.3, np.mean(away_goals_for[-10:])) * 0.9

    # Adjust for opponent strength
    home_defense = np.mean(home_goals_against[-10:])
    away_defense = np.mean(away_goals_against[-10:])

    exp_home = (exp_home + away_defense) / 2
    exp_away = (exp_away + home_defense) / 2

    # Derive probabilities from Poisson
    p_home = p_draw = p_away = p_over = 0.0
    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_home) * poisson.pmf(a, exp_away)
            if h > a: p_home += p
            elif h == a: p_draw += p
            else: p_away += p
            if h + a > 2: p_over += p

    return {
        "home_prob": p_home,
        "draw_prob": p_draw,
        "away_prob": p_away,
        "over_25_prob": p_over,
        "under_25_prob": 1 - p_over,
        "exp_home_goals": exp_home,
        "exp_away_goals": exp_away,
    }


# ============================================================
# STEP 3: PLACE PAPER BETS
# ============================================================

def place_paper_bets(matches_with_odds: list[dict], predictions: dict,
                     bot_name: str, bot_config: dict) -> list[dict]:
    """
    For a given bot, decide which bets to place based on predictions vs odds.
    Returns list of paper bets.
    """
    bets = []
    tier_filter = bot_config.get("tier_filter")
    edge_thresholds = bot_config["edge_thresholds"]
    odds_min, odds_max = bot_config["odds_range"]
    min_prob = bot_config["min_prob"]

    for match in matches_with_odds:
        match_key = f"{match['home_team']}_{match['away_team']}"
        pred = predictions.get(match_key)

        if not pred:
            continue

        tier = match.get("tier", 1)
        if tier_filter and tier not in tier_filter:
            continue

        thresholds = edge_thresholds.get(tier, edge_thresholds.get(1, {}))

        # 1X2: Home
        if "1x2" in bot_config["markets"] and match["odds_home"] > 0:
            odds = match["odds_home"]
            mp = pred["home_prob"]
            ip = 1 / odds
            edge = mp - ip
            me = thresholds.get("1x2_fav", 0.05) if odds < 2.0 else thresholds.get("1x2_long", 0.08)

            if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                bets.append({
                    "bot": bot_name,
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "league": match.get("league_path", ""),
                    "tier": tier,
                    "market": "1X2",
                    "selection": "Home",
                    "odds": odds,
                    "model_prob": mp,
                    "implied_prob": ip,
                    "edge": edge,
                    "stake": STAKE,
                    "kickoff": match.get("start_time", ""),
                    "placed_at": datetime.now().isoformat(),
                    "result": "pending",
                    "pnl": 0,
                })

        # 1X2: Away
        if "1x2" in bot_config["markets"] and match["odds_away"] > 0:
            odds = match["odds_away"]
            mp = pred["away_prob"]
            ip = 1 / odds
            edge = mp - ip
            me = thresholds.get("1x2_long", 0.08)

            if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                bets.append({
                    "bot": bot_name,
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "league": match.get("league_path", ""),
                    "tier": tier,
                    "market": "1X2",
                    "selection": "Away",
                    "odds": odds,
                    "model_prob": mp,
                    "implied_prob": ip,
                    "edge": edge,
                    "stake": STAKE,
                    "kickoff": match.get("start_time", ""),
                    "placed_at": datetime.now().isoformat(),
                    "result": "pending",
                    "pnl": 0,
                })

        # O/U: Over 2.5
        if "ou" in bot_config["markets"] and match.get("odds_over_25", 0) > 0:
            odds = match["odds_over_25"]
            mp = pred["over_25_prob"]
            ip = 1 / odds
            edge = mp - ip
            me = thresholds.get("ou", 0.05)

            if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                bets.append({
                    "bot": bot_name,
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "league": match.get("league_path", ""),
                    "tier": tier,
                    "market": "O/U",
                    "selection": "Over 2.5",
                    "odds": odds,
                    "model_prob": mp,
                    "implied_prob": ip,
                    "edge": edge,
                    "stake": STAKE,
                    "kickoff": match.get("start_time", ""),
                    "placed_at": datetime.now().isoformat(),
                    "result": "pending",
                    "pnl": 0,
                })

        # O/U: Under 2.5
        if "ou" in bot_config["markets"] and match.get("odds_under_25", 0) > 0:
            odds = match["odds_under_25"]
            mp = pred["under_25_prob"]
            ip = 1 / odds
            edge = mp - ip
            me = thresholds.get("ou", 0.05)

            if edge >= me and odds_min <= odds <= odds_max and mp >= min_prob:
                bets.append({
                    "bot": bot_name,
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "league": match.get("league_path", ""),
                    "tier": tier,
                    "market": "O/U",
                    "selection": "Under 2.5",
                    "odds": odds,
                    "model_prob": mp,
                    "implied_prob": ip,
                    "edge": edge,
                    "stake": STAKE,
                    "kickoff": match.get("start_time", ""),
                    "placed_at": datetime.now().isoformat(),
                    "result": "pending",
                    "pnl": 0,
                })

    return bets


# ============================================================
# STEP 4: SETTLE BETS
# ============================================================

def settle_bets(bets: list[dict], results: list[dict]) -> list[dict]:
    """Match pending bets with results and calculate P&L"""

    # Build result lookup by team names
    result_map = {}
    for r in results:
        if r.get("home_goals") is not None and r.get("away_goals") is not None:
            key = f"{r['home_team']} vs {r['away_team']}"
            hg = int(r["home_goals"])
            ag = int(r["away_goals"])
            result_map[key] = {
                "home_goals": hg,
                "away_goals": ag,
                "result": "H" if hg > ag else "A" if ag > hg else "D",
                "total_goals": hg + ag,
            }

    settled = 0
    for bet in bets:
        if bet["result"] != "pending":
            continue

        match_result = result_map.get(bet["match"])
        if not match_result:
            continue

        won = False
        if bet["selection"] == "Home" and match_result["result"] == "H":
            won = True
        elif bet["selection"] == "Away" and match_result["result"] == "A":
            won = True
        elif bet["selection"] == "Over 2.5" and match_result["total_goals"] > 2:
            won = True
        elif bet["selection"] == "Under 2.5" and match_result["total_goals"] <= 2:
            won = True

        bet["result"] = "won" if won else "lost"
        bet["pnl"] = (bet["odds"] - 1) * bet["stake"] if won else -bet["stake"]
        bet["settled_at"] = datetime.now().isoformat()
        bet["actual_result"] = match_result
        settled += 1

    return bets


# ============================================================
# STEP 5: DAILY REPORT
# ============================================================

def generate_report(all_bets: list[dict], today: str):
    """Generate daily summary report"""
    console.print(f"\n[bold green]═══ Daily Report: {today} ═══[/bold green]\n")

    if not all_bets:
        console.print("[yellow]No bets placed today.[/yellow]")
        return

    df = pd.DataFrame(all_bets)
    settled = df[df["result"].isin(["won", "lost"])]
    pending = df[df["result"] == "pending"]

    console.print(f"Total bets: {len(df)} | Settled: {len(settled)} | Pending: {len(pending)}\n")

    if len(settled) == 0:
        console.print("[yellow]No settled bets yet. Run settle later.[/yellow]")

        # Show pending bets per bot
        for bot in df["bot"].unique():
            bot_bets = df[df["bot"] == bot]
            console.print(f"  {bot}: {len(bot_bets)} pending bets")
        return

    # Per-bot summary
    t = Table(title="Bot Performance (Today)")
    t.add_column("Bot", style="cyan")
    t.add_column("Bets", justify="right")
    t.add_column("Won", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Pending", justify="right")

    for bot in sorted(df["bot"].unique()):
        bot_settled = settled[settled["bot"] == bot]
        bot_pending = pending[pending["bot"] == bot]

        if len(bot_settled) == 0:
            t.add_row(bot, "0", "0", "-", "-", str(len(bot_pending)))
            continue

        wins = (bot_settled["result"] == "won").sum()
        pnl = bot_settled["pnl"].sum()
        color = "green" if pnl > 0 else "red"

        t.add_row(
            bot,
            str(len(bot_settled)),
            str(wins),
            f"{wins/len(bot_settled):.0%}",
            f"[{color}]EUR {pnl:+.2f}[/{color}]",
            str(len(bot_pending)),
        )

    console.print(t)


# ============================================================
# MAIN PIPELINE
# ============================================================

def run_morning():
    """Morning job: fetch data + make predictions + place bets"""
    today = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Morning Pipeline: {today} ═══[/bold green]\n")

    # Step 1: Fetch data
    fixtures, odds_matches = fetch_todays_data()

    if not odds_matches:
        console.print("[yellow]No matches with odds today. Nothing to do.[/yellow]")
        return

    # Step 2: Load model data
    hist_features, hist_targets, feature_cols = load_model_and_features()
    if hist_features is None:
        return

    # Step 3: Compute predictions
    console.print("\n[bold cyan]Step 2: Computing predictions...[/bold cyan]")
    predictions = {}

    for match in odds_matches:
        match_key = f"{match['home_team']}_{match['away_team']}"
        pred = compute_match_prediction(match, hist_features, hist_targets, feature_cols)
        if pred:
            predictions[match_key] = pred

    console.print(f"  → Predictions computed for {len(predictions)}/{len(odds_matches)} matches")

    # Step 4: Place paper bets for each bot
    console.print(f"\n[bold cyan]Step 3: Placing paper bets...[/bold cyan]")
    all_bets = []

    for bot_name, bot_config in BOTS.items():
        bets = place_paper_bets(odds_matches, predictions, bot_name, bot_config)
        all_bets.extend(bets)
        console.print(f"  {bot_name}: {len(bets)} bets placed")

    # Save today's bets
    bets_file = DAILY_DIR / f"bets_{today}.json"
    with open(bets_file, "w") as f:
        json.dump(all_bets, f, indent=2, default=str)

    console.print(f"\n[green]Saved {len(all_bets)} bets to {bets_file}[/green]")

    # Also save odds snapshot
    odds_file = DAILY_DIR / f"odds_{today}.json"
    with open(odds_file, "w") as f:
        json.dump(odds_matches, f, indent=2, default=str)

    # Show what was bet
    generate_report(all_bets, today)

    return all_bets


def run_evening():
    """Evening job: settle bets with results"""
    today = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Evening Settlement: {today} ═══[/bold green]\n")

    # Load today's bets
    bets_file = DAILY_DIR / f"bets_{today}.json"
    if not bets_file.exists():
        console.print("[yellow]No bets file for today.[/yellow]")
        return

    with open(bets_file) as f:
        all_bets = json.load(f)

    # Fetch results
    console.print("Fetching match results...")
    results = get_todays_matches_from_flashscore()
    finished = [r for r in results if r.get("status") in ["FT", "finished"]]
    console.print(f"  → {len(finished)} finished matches")

    # Settle
    all_bets = settle_bets(all_bets, finished)

    # Save updated bets
    with open(bets_file, "w") as f:
        json.dump(all_bets, f, indent=2, default=str)

    # Report
    generate_report(all_bets, today)

    # Append to cumulative log
    cumulative_file = DAILY_DIR / "all_bets.json"
    if cumulative_file.exists():
        with open(cumulative_file) as f:
            cumulative = json.load(f)
    else:
        cumulative = []

    # Add settled bets
    for bet in all_bets:
        if bet["result"] in ["won", "lost"]:
            cumulative.append(bet)

    with open(cumulative_file, "w") as f:
        json.dump(cumulative, f, indent=2, default=str)

    console.print(f"\n[green]Cumulative bets: {len(cumulative)} total[/green]")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "settle":
        run_evening()
    else:
        run_morning()
