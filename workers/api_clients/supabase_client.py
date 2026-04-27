"""
OddsIntel — Supabase Client
Handles all database operations: storing matches, odds, predictions, bets,
live snapshots, and match events.
"""

import os
from datetime import datetime, date, timezone
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

    parts = league_path.split(" / ")
    country = parts[0] if len(parts) > 1 else "Unknown"
    name = parts[-1]

    result = client.table("leagues").select("id").eq("name", name).eq("country", country).execute()
    if result.data:
        return result.data[0]["id"]

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
    date_prefix = match_date[:10] if match_date else date.today().isoformat()

    country = match_data.get("league_path", "").split(" / ")[0] if " / " in match_data.get("league_path", "") else "Unknown"
    home_id = ensure_team(home_team, country)
    away_id = ensure_team(away_team, country)

    league_path = match_data.get("league_path", "Unknown / Unknown")
    tier = match_data.get("tier", 1)
    league_id = ensure_league(league_path, tier)

    existing = client.table("matches").select("id").eq(
        "home_team_id", home_id
    ).eq(
        "away_team_id", away_id
    ).gte("date", f"{date_prefix}T00:00:00").lte("date", f"{date_prefix}T23:59:59").execute()

    if existing.data:
        return existing.data[0]["id"]

    try:
        dt = datetime.fromisoformat(match_date.replace("Z", "+00:00"))
        season = dt.year if dt.month >= 7 else dt.year - 1
    except (ValueError, AttributeError):
        season = date.today().year if date.today().month >= 7 else date.today().year - 1

    match_record = {
        "date": match_date if match_date else datetime.now().isoformat(),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "league_id": league_id,
        "season": season,
        "status": "scheduled",
    }

    if match_data.get("home_goals") is not None:
        match_record["score_home"] = int(match_data["home_goals"])
        match_record["score_away"] = int(match_data["away_goals"])
        hg, ag = match_record["score_home"], match_record["score_away"]
        match_record["result"] = "home" if hg > ag else "away" if ag > hg else "draw"
        match_record["status"] = "finished"

    new = client.table("matches").insert(match_record).execute()
    return new.data[0]["id"]


def update_match_status(match_id: str, status: str):
    """Update a match status (scheduled → live → finished)"""
    client = get_client()
    client.table("matches").update({"status": status}).eq("id", match_id).execute()


# ============================================================
# ODDS (fixed: uses 'timestamp' column, not 'created_at')
# ============================================================

def store_odds(match_id: str, match_data: dict, minutes_to_kickoff: int = None):
    """
    Store odds snapshot for a match. One row per market/selection.
    Fixed: uses 'timestamp' column (schema column name, not 'created_at').
    minutes_to_kickoff: negative = pre-match (e.g. -120 = 2h before kickoff)
                        0 = at kickoff / closing line
                        positive = in-play minute
    """
    client = get_client()

    operator = match_data.get("bookmaker") or match_data.get("operator", "unibet")
    now = datetime.now(timezone.utc).isoformat()

    base = {
        "match_id": match_id,
        "bookmaker": operator,
        "timestamp": now,
        "is_closing": minutes_to_kickoff is not None and abs(minutes_to_kickoff) <= 5,
        "minutes_to_kickoff": minutes_to_kickoff,
    }

    odds_rows = []

    # 1X2
    for selection, key in [("home", "odds_home"), ("draw", "odds_draw"), ("away", "odds_away")]:
        if match_data.get(key, 0) > 0:
            odds_rows.append({**base, "market": "1x2", "selection": selection, "odds": match_data[key]})

    # All O/U lines
    for line_label, over_key, under_key in [
        ("over_under_05", "odds_over_05", "odds_under_05"),
        ("over_under_15", "odds_over_15", "odds_under_15"),
        ("over_under_25", "odds_over_25", "odds_under_25"),
        ("over_under_35", "odds_over_35", "odds_under_35"),
        ("over_under_45", "odds_over_45", "odds_under_45"),
    ]:
        if match_data.get(over_key, 0) > 0:
            odds_rows.append({**base, "market": line_label, "selection": "over",
                              "odds": match_data[over_key]})
        if match_data.get(under_key, 0) > 0:
            odds_rows.append({**base, "market": line_label, "selection": "under",
                              "odds": match_data[under_key]})

    if odds_rows:
        client.table("odds_snapshots").insert(odds_rows).execute()


# ============================================================
# LIVE TRACKING
# ============================================================

def store_live_snapshot(match_id: str, snapshot: dict):
    """
    Store an in-play snapshot (called every ~5 min during live matches).
    snapshot keys: minute, score_home, score_away, shots_*, xg_*, possession_home,
                   live_ou_* odds, live_1x2_* odds, model_* context
    """
    client = get_client()

    row = {
        "match_id": match_id,
        "minute": snapshot.get("minute", 0),
        "added_time": snapshot.get("added_time", 0),
        "score_home": snapshot.get("score_home", 0),
        "score_away": snapshot.get("score_away", 0),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

    # Optional stats fields
    optional_fields = [
        "shots_home", "shots_away", "shots_on_target_home", "shots_on_target_away",
        "xg_home", "xg_away", "possession_home", "corners_home", "corners_away",
        "attacks_home", "attacks_away",
        "live_ou_05_over", "live_ou_05_under",
        "live_ou_15_over", "live_ou_15_under",
        "live_ou_25_over", "live_ou_25_under",
        "live_ou_35_over", "live_ou_35_under",
        "live_ou_45_over", "live_ou_45_under",
        "live_1x2_home", "live_1x2_draw", "live_1x2_away",
        "model_xg_home", "model_xg_away", "model_ou25_prob",
    ]
    for field in optional_fields:
        if snapshot.get(field) is not None:
            row[field] = snapshot[field]

    client.table("live_match_snapshots").insert(row).execute()


def store_match_event(match_id: str, event: dict) -> bool:
    """
    Store a match event (goal, card, sub).
    Returns False if event already exists (dedup via sofascore_event_id).
    """
    client = get_client()

    row = {
        "match_id": match_id,
        "minute": event.get("minute", 0),
        "added_time": event.get("added_time", 0),
        "event_type": event["event_type"],
        "team": event["team"],
        "player_name": event.get("player_name"),
        "assist_name": event.get("assist_name"),
        "detail": event.get("detail"),
        "sofascore_event_id": event.get("sofascore_event_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        client.table("match_events").insert(row).execute()
        return True
    except Exception as e:
        # Unique constraint violation = duplicate event, that's fine
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return False
        raise


def get_live_matches() -> list[dict]:
    """
    Get matches that are currently in-play or starting soon.
    Returns matches with sofascore_event_id so the live tracker can poll them.
    """
    client = get_client()
    now = datetime.now(timezone.utc)

    # Matches that started in the last 2.5 hours and are not yet finished
    from_time = now.replace(hour=max(0, now.hour - 3)).isoformat()

    result = client.table("matches").select(
        "id, date, sofascore_event_id, status, "
        "home:home_team_id(name), away:away_team_id(name), "
        "leagues(name, country)"
    ).gte("date", from_time).neq("status", "finished").execute()

    return result.data


def get_match_by_sofascore_id(sofascore_event_id: int) -> dict | None:
    """Look up a DB match by Sofascore event ID"""
    client = get_client()
    result = client.table("matches").select("id, status, date").eq(
        "sofascore_event_id", sofascore_event_id
    ).execute()
    return result.data[0] if result.data else None


def get_match_by_teams_and_date(home_team_name: str, away_team_name: str,
                                 match_date: str) -> dict | None:
    """
    Fallback lookup when sofascore_event_id is not stored.
    Joins through teams table to match by name.
    """
    client = get_client()
    date_prefix = match_date[:10]

    # Get team IDs
    home_result = client.table("teams").select("id").eq("name", home_team_name).execute()
    away_result = client.table("teams").select("id").eq("name", away_team_name).execute()

    if not home_result.data or not away_result.data:
        return None

    home_id = home_result.data[0]["id"]
    away_id = away_result.data[0]["id"]

    result = client.table("matches").select("id, status, date, sofascore_event_id").eq(
        "home_team_id", home_id
    ).eq(
        "away_team_id", away_id
    ).gte("date", f"{date_prefix}T00:00:00").lte("date", f"{date_prefix}T23:59:59").execute()

    return result.data[0] if result.data else None


def get_todays_scheduled_matches() -> list[dict]:
    """Get all of today's scheduled (not yet started) matches with kickoff times"""
    client = get_client()
    today = date.today().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    result = client.table("matches").select(
        "id, date, sofascore_event_id, "
        "home:home_team_id(name), away:away_team_id(name)"
    ).gte("date", now_iso).lte("date", f"{today}T23:59:59").eq("status", "scheduled").execute()

    return result.data


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
        "confidence": prediction.get("confidence", 0.5),
        "reasoning": prediction.get("reasoning"),
    }).execute()


# ============================================================
# SIMULATED BETS
# ============================================================

def store_bet(bot_id: str, match_id: str, bet_data: dict) -> str | None:
    """
    Store a paper bet in Supabase.
    Returns the bet UUID, or None if this bet already exists (idempotent).

    Supports new model improvement fields (migration 006):
    - calibrated_prob, kelly_fraction, odds_at_open, odds_drift
    - dimension_scores, alignment_count, alignment_total, alignment_class
    - model_disagreement, news_impact_score, lineup_confirmed
    """
    client = get_client()

    row = {
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
        "reasoning": bet_data.get("reasoning") or f"Edge: {bet_data['edge']:.1%}, Model: {bet_data['model_prob']:.1%}, Implied: {bet_data['implied_prob']:.1%}",
    }

    # Model improvement fields (P1-P4, migration 006)
    optional_fields = [
        "calibrated_prob", "kelly_fraction",
        "odds_at_open", "odds_drift",
        "dimension_scores", "alignment_count", "alignment_total", "alignment_class",
        "model_disagreement", "news_impact_score", "lineup_confirmed",
    ]
    for field in optional_fields:
        if field in bet_data and bet_data[field] is not None:
            row[field] = bet_data[field]

    try:
        new = client.table("simulated_bets").insert(row).execute()
        return new.data[0]["id"]
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower() or "uq_bet" in str(e).lower():
            return None  # already placed, skip silently
        raise


def store_prediction_snapshot(
    bet_id: str, stage: str, model_probability: float,
    implied_probability: float = None, edge_percent: float = None,
    odds_at_snapshot: float = None, metadata: dict = None,
) -> str | None:
    """
    Store a prediction snapshot for audit trail.
    Tracks model probability at each info stage: stats_only, post_ai, pre_kickoff, closing.
    Returns snapshot UUID, or None if this stage already exists for this bet.
    """
    client = get_client()

    row = {
        "bet_id": bet_id,
        "stage": stage,
        "model_probability": model_probability,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    if implied_probability is not None:
        row["implied_probability"] = implied_probability
    if edge_percent is not None:
        row["edge_percent"] = edge_percent
    if odds_at_snapshot is not None:
        row["odds_at_snapshot"] = odds_at_snapshot
    if metadata:
        row["metadata"] = metadata

    try:
        result = client.table("prediction_snapshots").insert(row).execute()
        return result.data[0]["id"]
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return None  # stage already recorded
        raise


def store_match_stats(match_id: str, stats: dict):
    """
    Store final match stats (xG, shots, possession, corners, cards).
    Uses upsert — safe to call multiple times for the same match.
    """
    client = get_client()

    row = {"match_id": match_id}
    field_map = {
        "xg_home": "xg_home", "xg_away": "xg_away",
        "shots_home": "shots_home", "shots_away": "shots_away",
        "possession_home": "possession_home",
        "corners_home": "corners_home", "corners_away": "corners_away",
    }
    for src, dst in field_map.items():
        if src in stats and stats[src] is not None:
            row[dst] = stats[src]

    if len(row) <= 1:
        return  # no stats to store

    client.table("match_stats").upsert(row, on_conflict="match_id").execute()


def store_team_elo(team_id: str, elo_date: str, elo_rating: float):
    """
    Store or update a team's ELO rating for a given date.
    Uses upsert on (team_id, date) constraint.
    """
    client = get_client()
    client.table("team_elo_daily").upsert({
        "team_id": team_id,
        "date": elo_date,
        "elo_rating": round(elo_rating, 2),
    }, on_conflict="team_id,date").execute()


def store_team_form(team_id: str, form_date: str, form: dict):
    """
    Store or update cached form metrics for a team on a given date.
    Uses upsert on (team_id, date) constraint.
    """
    client = get_client()

    row = {"team_id": team_id, "date": form_date}
    for key in ["matches_played", "win_pct", "draw_pct", "loss_pct", "ppg",
                "goals_scored_avg", "goals_conceded_avg", "goal_diff_avg",
                "clean_sheet_pct", "over25_pct", "btts_pct"]:
        if key in form and form[key] is not None:
            row[key] = form[key]

    client.table("team_form_cache").upsert(row, on_conflict="team_id,date").execute()


def store_model_evaluation(eval_date: str, league_id: str | None, market: str,
                           total_bets: int, hits: int, roi: float,
                           avg_clv: float | None, notes: str | None = None):
    """Store daily model evaluation metrics per league/market."""
    client = get_client()

    row = {
        "date": eval_date,
        "market": market,
        "total_bets": total_bets,
        "hits": hits,
        "hit_rate": round(hits / total_bets, 4) if total_bets > 0 else None,
        "roi": round(roi, 2),
    }
    if league_id:
        row["league_id"] = league_id
    if avg_clv is not None:
        row["avg_clv"] = round(avg_clv, 4)
    if notes:
        row["notes"] = notes

    client.table("model_evaluations").insert(row).execute()


def compute_market_implied_strength(team_id: str, window: int = 5) -> float | None:
    """
    Compute rolling average of a team's market-implied win probability
    from recent odds snapshots. The market's recent pricing of a team
    is a strong indicator of true team strength (especially in Tier 1-2).

    Returns average implied win probability (0.0-1.0) or None if insufficient data.
    See MODEL_ANALYSIS.md Section 11.3.
    """
    client = get_client()

    # Get last N matches where this team played, with 1X2 odds
    home_matches = client.table("matches").select(
        "id"
    ).eq("home_team_id", team_id).eq("status", "finished").order(
        "date", desc=True
    ).limit(window).execute().data or []

    away_matches = client.table("matches").select(
        "id"
    ).eq("away_team_id", team_id).eq("status", "finished").order(
        "date", desc=True
    ).limit(window).execute().data or []

    implied_probs = []

    # For home matches, get the 1x2 home odds
    for m in home_matches:
        odds_rows = client.table("odds_snapshots").select("odds").eq(
            "match_id", m["id"]
        ).eq("market", "1x2").eq("selection", "home").order(
            "timestamp", desc=True
        ).limit(1).execute().data

        if odds_rows and float(odds_rows[0]["odds"]) > 1.0:
            implied_probs.append(1.0 / float(odds_rows[0]["odds"]))

    # For away matches, get the 1x2 away odds
    for m in away_matches:
        odds_rows = client.table("odds_snapshots").select("odds").eq(
            "match_id", m["id"]
        ).eq("market", "1x2").eq("selection", "away").order(
            "timestamp", desc=True
        ).limit(1).execute().data

        if odds_rows and float(odds_rows[0]["odds"]) > 1.0:
            implied_probs.append(1.0 / float(odds_rows[0]["odds"]))

    if len(implied_probs) < 3:
        return None

    # Return average implied win probability (most recent N matches)
    return round(sum(implied_probs[:window]) / min(len(implied_probs), window), 4)


def compute_team_form_from_db(team_id: str, as_of_date: str, window: int = 10) -> dict | None:
    """
    Compute rolling form metrics for a team from recent finished matches in DB.
    Returns form dict or None if insufficient data.
    """
    client = get_client()

    # Get last N finished matches involving this team
    home_matches = client.table("matches").select(
        "score_home, score_away"
    ).eq("home_team_id", team_id).eq("status", "finished").lt(
        "date", f"{as_of_date}T23:59:59"
    ).order("date", desc=True).limit(window).execute().data or []

    away_matches = client.table("matches").select(
        "score_home, score_away"
    ).eq("away_team_id", team_id).eq("status", "finished").lt(
        "date", f"{as_of_date}T23:59:59"
    ).order("date", desc=True).limit(window).execute().data or []

    # Combine and compute stats
    results = []
    for m in home_matches:
        if m["score_home"] is None:
            continue
        gf, ga = m["score_home"], m["score_away"]
        results.append({"gf": gf, "ga": ga, "won": gf > ga, "draw": gf == ga, "lost": gf < ga})

    for m in away_matches:
        if m["score_away"] is None:
            continue
        gf, ga = m["score_away"], m["score_home"]
        results.append({"gf": gf, "ga": ga, "won": gf > ga, "draw": gf == ga, "lost": gf < ga})

    # Take most recent N
    results = results[:window]
    n = len(results)
    if n < 3:
        return None

    wins = sum(1 for r in results if r["won"])
    draws = sum(1 for r in results if r["draw"])
    losses = sum(1 for r in results if r["lost"])
    gf_list = [r["gf"] for r in results]
    ga_list = [r["ga"] for r in results]

    return {
        "matches_played": n,
        "win_pct": round(wins / n, 4),
        "draw_pct": round(draws / n, 4),
        "loss_pct": round(losses / n, 4),
        "ppg": round((wins * 3 + draws) / n, 3),
        "goals_scored_avg": round(sum(gf_list) / n, 3),
        "goals_conceded_avg": round(sum(ga_list) / n, 3),
        "goal_diff_avg": round((sum(gf_list) - sum(ga_list)) / n, 3),
        "clean_sheet_pct": round(sum(1 for g in ga_list if g == 0) / n, 4),
        "over25_pct": round(sum(1 for i in range(n) if gf_list[i] + ga_list[i] > 2) / n, 4),
        "btts_pct": round(sum(1 for i in range(n) if gf_list[i] > 0 and ga_list[i] > 0) / n, 4),
    }


def settle_bet(bet_id: str, result: str, pnl: float, bankroll_after: float):
    """Settle a paper bet with result and P&L"""
    client = get_client()

    client.table("simulated_bets").update({
        "result": result,
        "pnl": pnl,
        "bankroll_after": bankroll_after,
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
    client = get_client()
    print("Supabase connection OK")

    for table in ["bots", "matches", "simulated_bets", "predictions", "odds_snapshots",
                  "leagues", "teams", "live_match_snapshots", "match_events",
                  "prediction_snapshots", "match_stats", "model_evaluations",
                  "team_elo_daily", "team_form_cache"]:
        try:
            result = client.table(table).select("id", count="exact").execute()
            print(f"  {table}: {result.count} rows")
        except Exception as e:
            print(f"  {table}: ERROR — {e}")
