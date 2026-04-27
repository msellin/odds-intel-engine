"""
OddsIntel — Supabase Client
Handles all database operations: storing matches, odds, predictions, bets.
Both the daily pipeline and the frontend read from the same database.
"""

import os
from datetime import datetime, date
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY", "")


def get_client() -> Client:
    """Get Supabase client (using service role key for write access)"""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set in .env")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# BOTS
# ============================================================

def ensure_bots(bots_config: dict) -> dict:
    """
    Create bot records in Supabase if they don't exist.
    Returns {bot_name: bot_uuid} mapping.
    """
    client = get_client()

    # Get existing bots
    result = client.table("bots").select("id, name").execute()
    existing = {b["name"]: b["id"] for b in result.data}

    for bot_name, config in bots_config.items():
        if bot_name not in existing:
            new_bot = client.table("bots").insert({
                "name": bot_name,
                "strategy": config.get("description", ""),
                "starting_bankroll": 1000.0,
                "current_bankroll": 1000.0,
                "is_active": True,
            }).execute()
            existing[bot_name] = new_bot.data[0]["id"]

    return existing


# ============================================================
# LEAGUES & TEAMS
# ============================================================

def ensure_league(league_path: str, tier: int = 1) -> str:
    """Get or create a league, return its UUID"""
    client = get_client()

    # Parse path like "England / Premier League"
    parts = league_path.split(" / ")
    country = parts[0] if len(parts) > 1 else "Unknown"
    name = parts[-1]

    # Check if exists
    result = client.table("leagues").select("id").eq("name", name).eq("country", country).execute()
    if result.data:
        return result.data[0]["id"]

    # Create
    new = client.table("leagues").insert({
        "name": name,
        "country": country,
        "tier": tier,
        "is_active": True,
    }).execute()
    return new.data[0]["id"]


def ensure_team(team_name: str, country: str = "Unknown") -> str:
    """Get or create a team, return its UUID"""
    client = get_client()

    result = client.table("teams").select("id").eq("name", team_name).execute()
    if result.data:
        return result.data[0]["id"]

    # We need a league_id (required field) — use a default
    league = ensure_league(f"{country} / Unknown", tier=0)

    new = client.table("teams").insert({
        "name": team_name,
        "country": country,
        "league_id": league,
    }).execute()
    return new.data[0]["id"]


# ============================================================
# MATCHES
# ============================================================

def store_match(match_data: dict) -> str:
    """Store a match in Supabase, return its UUID"""
    client = get_client()

    home_team = match_data["home_team"]
    away_team = match_data["away_team"]
    match_date = match_data.get("start_time", match_data.get("date", ""))

    # Check if match already exists (same teams + same date)
    # Use date prefix for matching
    date_prefix = match_date[:10] if match_date else date.today().isoformat()

    # Get or create teams
    country = match_data.get("league_path", "").split(" / ")[0] if " / " in match_data.get("league_path", "") else "Unknown"
    home_id = ensure_team(home_team, country)
    away_id = ensure_team(away_team, country)

    # Get or create league
    league_path = match_data.get("league_path", "Unknown / Unknown")
    tier = match_data.get("tier", 1)
    league_id = ensure_league(league_path, tier)

    # Check for existing match
    existing = client.table("matches").select("id").eq(
        "home_team_id", home_id
    ).eq(
        "away_team_id", away_id
    ).gte("date", f"{date_prefix}T00:00:00").lte("date", f"{date_prefix}T23:59:59").execute()

    if existing.data:
        return existing.data[0]["id"]

    # Determine season (year of start, or year-1 if before July)
    try:
        dt = datetime.fromisoformat(match_date.replace("Z", "+00:00"))
        season = dt.year if dt.month >= 7 else dt.year - 1
    except (ValueError, AttributeError):
        season = date.today().year if date.today().month >= 7 else date.today().year - 1

    # Create match
    match_record = {
        "date": match_date if match_date else datetime.now().isoformat(),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "league_id": league_id,
        "season": season,
        "status": "scheduled",
    }

    # Add scores if available
    if match_data.get("home_goals") is not None:
        match_record["score_home"] = int(match_data["home_goals"])
        match_record["score_away"] = int(match_data["away_goals"])
        hg, ag = match_record["score_home"], match_record["score_away"]
        match_record["result"] = "home" if hg > ag else "away" if ag > hg else "draw"
        match_record["status"] = "finished"

    new = client.table("matches").insert(match_record).execute()
    return new.data[0]["id"]


# ============================================================
# ODDS
# ============================================================

def store_odds(match_id: str, match_data: dict):
    """Store odds snapshot for a match. One row per selection."""
    client = get_client()

    operator = match_data.get("operator", "unibet")
    now = datetime.now().isoformat()

    odds_rows = []

    # 1X2 odds — one row per selection
    if match_data.get("odds_home", 0) > 0:
        odds_rows.append({"match_id": match_id, "bookmaker": operator, "market": "1x2",
                          "selection": "home", "odds": match_data["odds_home"], "created_at": now})
    if match_data.get("odds_draw", 0) > 0:
        odds_rows.append({"match_id": match_id, "bookmaker": operator, "market": "1x2",
                          "selection": "draw", "odds": match_data["odds_draw"], "created_at": now})
    if match_data.get("odds_away", 0) > 0:
        odds_rows.append({"match_id": match_id, "bookmaker": operator, "market": "1x2",
                          "selection": "away", "odds": match_data["odds_away"], "created_at": now})

    # O/U odds
    if match_data.get("odds_over_25", 0) > 0:
        odds_rows.append({"match_id": match_id, "bookmaker": operator, "market": "over_under_25",
                          "selection": "over", "odds": match_data["odds_over_25"], "created_at": now})
    if match_data.get("odds_under_25", 0) > 0:
        odds_rows.append({"match_id": match_id, "bookmaker": operator, "market": "over_under_25",
                          "selection": "under", "odds": match_data["odds_under_25"], "created_at": now})

    # Batch insert
    if odds_rows:
        client.table("odds_snapshots").insert(odds_rows).execute()


# ============================================================
# PREDICTIONS
# ============================================================

def store_prediction(match_id: str, market: str, prediction: dict):
    """Store a model prediction for a match"""
    client = get_client()

    client.table("predictions").insert({
        "match_id": match_id,
        "market": market,
        "model_probability": prediction["model_prob"],
        "implied_probability": prediction.get("implied_prob"),
        "edge_percent": prediction.get("edge"),
        "best_odds": prediction.get("odds"),
        "best_bookmaker": prediction.get("bookmaker", "unibet"),
        "confidence": "high" if prediction.get("edge", 0) > 0.08 else "medium" if prediction.get("edge", 0) > 0.05 else "low",
    }).execute()


# ============================================================
# SIMULATED BETS
# ============================================================

def store_bet(bot_id: str, match_id: str, bet_data: dict) -> str:
    """Store a paper bet in Supabase"""
    client = get_client()

    new = client.table("simulated_bets").insert({
        "bot_id": bot_id,
        "match_id": match_id,
        "market": bet_data["market"],
        "selection": bet_data["selection"].lower(),
        "odds_at_pick": bet_data["odds"],
        "pick_time": bet_data.get("placed_at", datetime.now().isoformat()),
        "stake": bet_data["stake"],
        "model_probability": bet_data["model_prob"],
        "edge_percent": bet_data["edge"],
        "result": "pending",
        "reasoning": f"Edge: {bet_data['edge']:.1%}, Model: {bet_data['model_prob']:.1%}, Implied: {bet_data['implied_prob']:.1%}",
    }).execute()

    return new.data[0]["id"]


def settle_bet(bet_id: str, result: str, pnl: float, bankroll_after: float):
    """Settle a paper bet with result and P&L"""
    client = get_client()

    client.table("simulated_bets").update({
        "result": result,
        "pnl": pnl,
        "bankroll_after": bankroll_after,
        "settled_at": datetime.now().isoformat(),
    }).eq("id", bet_id).execute()


def get_pending_bets() -> list[dict]:
    """Get all pending (unsettled) bets"""
    client = get_client()

    result = client.table("simulated_bets").select(
        "*, matches(date, home_team_id, away_team_id, score_home, score_away, result, status)"
    ).eq("result", "pending").execute()

    return result.data


def update_bot_bankroll(bot_id: str, new_bankroll: float):
    """Update a bot's current bankroll"""
    client = get_client()
    client.table("bots").update({"current_bankroll": new_bankroll}).eq("id", bot_id).execute()


# ============================================================
# MATCH RESULTS
# ============================================================

def update_match_result(match_id: str, home_goals: int, away_goals: int):
    """Update a match with its final score"""
    client = get_client()

    result = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"

    client.table("matches").update({
        "score_home": home_goals,
        "score_away": away_goals,
        "result": result,
        "status": "finished",
    }).eq("id", match_id).execute()


# ============================================================
# QUERIES (for reporting)
# ============================================================

def get_bot_performance(bot_name: str = None) -> list[dict]:
    """Get performance summary for bots"""
    client = get_client()

    query = client.table("simulated_bets").select("*")
    if bot_name:
        # Get bot ID first
        bot = client.table("bots").select("id").eq("name", bot_name).execute()
        if bot.data:
            query = query.eq("bot_id", bot.data[0]["id"])

    result = query.neq("result", "pending").execute()
    return result.data


def get_todays_matches() -> list[dict]:
    """Get today's matches from Supabase"""
    client = get_client()
    today = date.today().isoformat()

    result = client.table("matches").select(
        "*, leagues(name, country, tier)"
    ).gte("date", f"{today}T00:00:00").lte("date", f"{today}T23:59:59").execute()

    return result.data


if __name__ == "__main__":
    # Quick test
    client = get_client()
    print("Supabase connection OK")

    # Check table counts
    for table in ["bots", "matches", "simulated_bets", "predictions", "odds_snapshots", "leagues", "teams"]:
        result = client.table(table).select("id", count="exact").execute()
        print(f"  {table}: {result.count} rows")
