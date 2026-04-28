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

    existing = client.table("matches").select("id, sofascore_event_id, api_football_id, venue_name, referee").eq(
        "home_team_id", home_id
    ).eq(
        "away_team_id", away_id
    ).gte("date", f"{date_prefix}T00:00:00").lte("date", f"{date_prefix}T23:59:59").execute()

    if existing.data:
        match_id = existing.data[0]["id"]
        # Backfill IDs and metadata if we have them now but DB doesn't
        updates = {}
        event_id = match_data.get("sofascore_event_id") or match_data.get("event_id")
        if event_id and not existing.data[0].get("sofascore_event_id"):
            updates["sofascore_event_id"] = int(event_id)
        af_id = match_data.get("api_football_id")
        if af_id and not existing.data[0].get("api_football_id"):
            updates["api_football_id"] = int(af_id)
        if match_data.get("venue_name") and not existing.data[0].get("venue_name"):
            updates["venue_name"] = match_data["venue_name"]
        if match_data.get("referee") and not existing.data[0].get("referee"):
            updates["referee"] = match_data["referee"]
        if updates:
            client.table("matches").update(updates).eq("id", match_id).execute()
        return match_id

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

    # Store external IDs and metadata if available
    event_id = match_data.get("sofascore_event_id") or match_data.get("event_id")
    if event_id:
        match_record["sofascore_event_id"] = int(event_id)
    af_id = match_data.get("api_football_id")
    if af_id:
        match_record["api_football_id"] = int(af_id)
    if match_data.get("venue_name"):
        match_record["venue_name"] = match_data["venue_name"]
    if match_data.get("referee"):
        match_record["referee"] = match_data["referee"]

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

def store_prediction(match_id: str, market: str, prediction: dict,
                     source: str = "ensemble"):
    """
    Store a model prediction for a match.

    source: 'ensemble' (default) | 'poisson' | 'xgboost' | 'af'
    Each (match_id, market, source) combination is unique — upsert on conflict.
    """
    client = get_client()

    row = {
        "match_id": match_id,
        "market": market,
        "source": source,
        "model_probability": prediction["model_prob"],
        "implied_probability": prediction.get("implied_prob"),
        "edge_percent": prediction.get("edge"),
        "confidence": prediction.get("confidence", 0.5),
        "reasoning": prediction.get("reasoning"),
    }

    client.table("predictions").upsert(
        row,
        on_conflict="match_id,market,source",
    ).execute()


def store_match_signal(match_id: str, signal_name: str, signal_value: float | None,
                       signal_group: str, data_source: str = "derived",
                       signal_text: str | None = None,
                       captured_at: str | None = None):
    """
    Append a signal observation to match_signals.
    Same signal can be stored multiple times (different timestamps).
    ML training uses the value closest to kickoff.
    """
    client = get_client()

    row = {
        "match_id": match_id,
        "signal_name": signal_name,
        "signal_value": signal_value,
        "signal_group": signal_group,
        "data_source": data_source,
        "signal_text": signal_text,
    }
    if captured_at:
        row["captured_at"] = captured_at

    client.table("match_signals").insert(row).execute()


# ─── Pseudo-CLV ──────────────────────────────────────────────────────────────

def compute_and_store_pseudo_clv(client, match_id: str) -> dict | None:
    """
    Compute pseudo-CLV for all three 1x2 selections for a finished match.

    pseudo_clv = (1/opening_odds) / (1/closing_odds) - 1
    Positive = opening odds had more implied edge than closing (bet was +value at open).

    Opening odds = earliest snapshot (any bookmaker).
    Closing odds = latest snapshot before is_closing=True, or latest overall.

    Returns dict with home/draw/away values, or None if not enough data.
    """
    # Fetch all 1x2 snapshots for this match
    result = client.table("odds_snapshots").select(
        "selection, odds, timestamp, is_closing"
    ).eq("match_id", match_id).eq("market", "1x2").order(
        "timestamp", desc=False
    ).execute()

    if not result.data:
        return None

    # Group by selection
    by_selection: dict[str, list[dict]] = {}
    for row in result.data:
        sel = row["selection"].lower()
        by_selection.setdefault(sel, []).append(row)

    pseudo_clvs = {}
    for sel in ("home", "draw", "away"):
        snaps = by_selection.get(sel, [])
        if len(snaps) < 2:
            pseudo_clvs[sel] = None
            continue

        opening_odds = float(snaps[0]["odds"])   # earliest snapshot
        # Closing: prefer is_closing=True, else latest
        closing_snaps = [s for s in snaps if s.get("is_closing")]
        closing_odds = float(closing_snaps[-1]["odds"]) if closing_snaps else float(snaps[-1]["odds"])

        if opening_odds <= 1.0 or closing_odds <= 1.0:
            pseudo_clvs[sel] = None
            continue

        opening_implied = 1.0 / opening_odds
        closing_implied = 1.0 / closing_odds
        pseudo_clvs[sel] = round(opening_implied / closing_implied - 1, 5)

    if all(v is None for v in pseudo_clvs.values()):
        return None

    client.table("matches").update({
        "pseudo_clv_home": pseudo_clvs.get("home"),
        "pseudo_clv_draw": pseudo_clvs.get("draw"),
        "pseudo_clv_away": pseudo_clvs.get("away"),
    }).eq("id", match_id).execute()

    return pseudo_clvs


# ─── match_feature_vectors ETL ────────────────────────────────────────────────

def build_match_feature_vectors(client, date_str: str) -> int:
    """
    Nightly ETL: build wide ML training rows for all finished matches on date_str.
    Pulls from predictions, team_elo_daily, team_form_cache, odds_snapshots, matches.
    Returns count of rows upserted.
    """
    # Fetch finished matches for this date
    matches_result = client.table("matches").select(
        "id, date, result, score_home, score_away, "
        "home_team_id, away_team_id, league_id, "
        "pseudo_clv_home, pseudo_clv_draw, pseudo_clv_away"
    ).eq("status", "finished").gte(
        "date", f"{date_str}T00:00:00"
    ).lte("date", f"{date_str}T23:59:59").execute()

    matches = matches_result.data
    if not matches:
        return 0

    upserted = 0

    for match in matches:
        match_id = match["id"]
        match_date = date_str

        try:
            row = _build_feature_row(client, match)
            if row:
                client.table("match_feature_vectors").upsert(
                    row, on_conflict="match_id"
                ).execute()
                upserted += 1
        except Exception:
            pass

    return upserted


def _build_feature_row(client, match: dict) -> dict | None:
    """Build a single match_feature_vectors row from all available sources."""
    match_id = match["id"]
    match_date = match["date"][:10] if match.get("date") else None

    # ── Outcome labels ────────────────────────────────────────────────────────
    outcome = match.get("result")
    score_home = match.get("score_home")
    score_away = match.get("score_away")
    total_goals = None
    over_25 = None
    if score_home is not None and score_away is not None:
        total_goals = int(score_home) + int(score_away)
        over_25 = total_goals > 2

    # ── League tier ───────────────────────────────────────────────────────────
    league_tier = None
    if match.get("league_id"):
        lr = client.table("leagues").select("tier").eq(
            "id", match["league_id"]
        ).execute()
        if lr.data:
            league_tier = lr.data[0].get("tier")

    # ── Ensemble prediction (1x2 home) ────────────────────────────────────────
    ens_home = ens_draw = ens_away = None
    pois_home = xgb_home = af_home = None
    model_disagreement = None

    pred_result = client.table("predictions").select(
        "source, model_probability, market"
    ).eq("match_id", match_id).eq("market", "1x2_home").execute()

    for p in (pred_result.data or []):
        src = p.get("source", "ensemble")
        prob = p.get("model_probability")
        if src == "ensemble":
            ens_home = prob
        elif src == "poisson":
            pois_home = prob
        elif src == "xgboost":
            xgb_home = prob
        elif src == "af":
            af_home = prob

    if pois_home is not None and xgb_home is not None:
        model_disagreement = round(abs(float(pois_home) - float(xgb_home)), 4)

    # Use ensemble; fall back to first available
    if ens_home is None:
        ens_home = pois_home or xgb_home or af_home

    # ── Opening implied odds ──────────────────────────────────────────────────
    opening_implied_home = opening_implied_draw = opening_implied_away = None
    odds_drift_home = steam_move = None

    # Earliest snapshot per selection
    for sel, col in [("home", "opening_implied_home"),
                     ("draw", "opening_implied_draw"),
                     ("away", "opening_implied_away")]:
        snap = client.table("odds_snapshots").select(
            "odds, timestamp"
        ).eq("match_id", match_id).eq("market", "1x2").eq(
            "selection", sel
        ).order("timestamp", desc=False).limit(1).execute()
        if snap.data:
            val = 1.0 / float(snap.data[0]["odds"])
            if col == "opening_implied_home":
                opening_implied_home = round(val, 4)
                # Compute drift: latest vs earliest
                latest = client.table("odds_snapshots").select("odds").eq(
                    "match_id", match_id
                ).eq("market", "1x2").eq("selection", "home").order(
                    "timestamp", desc=True
                ).limit(1).execute()
                if latest.data:
                    closing_implied = 1.0 / float(latest.data[0]["odds"])
                    odds_drift_home = round(closing_implied - val, 5)
                    steam_move = abs(odds_drift_home) > 0.03
            elif col == "opening_implied_draw":
                opening_implied_draw = round(val, 4)
            elif col == "opening_implied_away":
                opening_implied_away = round(val, 4)

    # ── ELO ───────────────────────────────────────────────────────────────────
    elo_home = elo_away = elo_diff = None
    home_team_id = match.get("home_team_id")
    away_team_id = match.get("away_team_id")

    if home_team_id and match_date:
        elo_r = client.table("team_elo_daily").select("elo_rating").eq(
            "team_id", home_team_id
        ).lte("date", match_date).order("date", desc=True).limit(1).execute()
        if elo_r.data:
            elo_home = float(elo_r.data[0]["elo_rating"])

    if away_team_id and match_date:
        elo_r = client.table("team_elo_daily").select("elo_rating").eq(
            "team_id", away_team_id
        ).lte("date", match_date).order("date", desc=True).limit(1).execute()
        if elo_r.data:
            elo_away = float(elo_r.data[0]["elo_rating"])

    if elo_home is not None and elo_away is not None:
        elo_diff = round(elo_home - elo_away, 2)

    # ── Form ──────────────────────────────────────────────────────────────────
    form_ppg_home = form_ppg_away = None
    form_momentum_home = form_momentum_away = None

    if home_team_id and match_date:
        form_r = client.table("team_form_cache").select(
            "ppg"
        ).eq("team_id", home_team_id).lte(
            "date", match_date
        ).order("date", desc=True).limit(1).execute()
        if form_r.data:
            form_ppg_home = form_r.data[0].get("ppg")

    if away_team_id and match_date:
        form_r = client.table("team_form_cache").select(
            "ppg"
        ).eq("team_id", away_team_id).lte(
            "date", match_date
        ).order("date", desc=True).limit(1).execute()
        if form_r.data:
            form_ppg_away = form_r.data[0].get("ppg")

    # ── Data tier (from predictions) ─────────────────────────────────────────
    data_tier = None
    tier_r = client.table("predictions").select("reasoning").eq(
        "match_id", match_id
    ).limit(1).execute()
    if tier_r.data and tier_r.data[0].get("reasoning"):
        reasoning = tier_r.data[0]["reasoning"]
        for t in ("A", "B", "C", "D"):
            if f"tier={t}" in reasoning or f"Tier {t}" in reasoning or f"data_tier={t}" in reasoning:
                data_tier = t
                break

    # ── Signals from match_signals (S3/S4/S5/BDM-1 + S3b-S3f + T2) ──────────
    # Pull the latest value for each signal name (closest to kickoff)
    fixture_importance = bookmaker_disagreement = referee_cards_avg = None
    injury_count_home = injury_count_away = None
    news_impact_score = lineup_confirmed = None
    league_position_home = league_position_away = None
    points_to_relegation_home = points_to_relegation_away = None
    points_to_title_home = points_to_title_away = None
    h2h_win_pct = overnight_line_move = None
    rest_days_home = rest_days_away = None
    referee_home_win_pct = referee_over25_pct = None
    goals_for_avg_home = goals_for_avg_away = None
    goals_against_avg_home = goals_against_avg_away = None

    signals_r = client.table("match_signals").select(
        "signal_name, signal_value, captured_at"
    ).eq("match_id", match_id).order("captured_at", desc=True).limit(500).execute()

    if signals_r.data:
        seen_signals: set[str] = set()
        for sig in signals_r.data:
            name = sig.get("signal_name")
            val = sig.get("signal_value")
            if name and name not in seen_signals:
                seen_signals.add(name)
                fval = float(val) if val is not None else None
                ival = int(val) if val is not None else None
                if name == "fixture_importance":
                    fixture_importance = fval
                elif name == "bookmaker_disagreement":
                    bookmaker_disagreement = fval
                elif name == "referee_cards_avg":
                    referee_cards_avg = fval
                elif name == "injury_count_home":
                    injury_count_home = ival
                elif name == "injury_count_away":
                    injury_count_away = ival
                elif name == "news_impact_score":
                    news_impact_score = fval
                elif name == "lineup_confirmed":
                    lineup_confirmed = bool(val) if val is not None else None
                elif name == "league_position_home":
                    league_position_home = fval
                elif name == "league_position_away":
                    league_position_away = fval
                elif name == "points_to_relegation_home":
                    points_to_relegation_home = ival
                elif name == "points_to_relegation_away":
                    points_to_relegation_away = ival
                elif name == "points_to_title_home":
                    points_to_title_home = ival
                elif name == "points_to_title_away":
                    points_to_title_away = ival
                elif name == "h2h_win_pct":
                    h2h_win_pct = fval
                elif name == "overnight_line_move":
                    overnight_line_move = fval
                elif name == "rest_days_home":
                    rest_days_home = ival
                elif name == "rest_days_away":
                    rest_days_away = ival
                elif name == "referee_home_win_pct":
                    referee_home_win_pct = fval
                elif name == "referee_over25_pct":
                    referee_over25_pct = fval
                elif name == "goals_for_avg_home":
                    goals_for_avg_home = fval
                elif name == "goals_for_avg_away":
                    goals_for_avg_away = fval
                elif name == "goals_against_avg_home":
                    goals_against_avg_home = fval
                elif name == "goals_against_avg_away":
                    goals_against_avg_away = fval

    return {
        "match_id": match_id,
        "match_date": match_date,
        "league_tier": league_tier,
        "data_tier": data_tier,
        # Group 1: Model
        "ensemble_prob_home": ens_home,
        "poisson_prob_home": pois_home,
        "xgboost_prob_home": xgb_home,
        "af_pred_prob_home": af_home,
        "model_disagreement": model_disagreement,
        # Group 2: Market
        "opening_implied_home": opening_implied_home,
        "opening_implied_draw": opening_implied_draw,
        "opening_implied_away": opening_implied_away,
        "odds_drift_home": odds_drift_home,
        "steam_move": steam_move,
        "bookmaker_disagreement": bookmaker_disagreement,
        "overnight_line_move": overnight_line_move,
        # Group 3: Quality
        "elo_home": elo_home,
        "elo_away": elo_away,
        "elo_diff": elo_diff,
        "form_ppg_home": float(form_ppg_home) if form_ppg_home is not None else None,
        "form_ppg_away": float(form_ppg_away) if form_ppg_away is not None else None,
        "league_position_home": league_position_home,
        "league_position_away": league_position_away,
        "points_to_relegation_home": points_to_relegation_home,
        "points_to_relegation_away": points_to_relegation_away,
        "points_to_title_home": points_to_title_home,
        "points_to_title_away": points_to_title_away,
        "h2h_win_pct": h2h_win_pct,
        "rest_days_home": rest_days_home,
        "rest_days_away": rest_days_away,
        "goals_for_avg_home": goals_for_avg_home,
        "goals_for_avg_away": goals_for_avg_away,
        "goals_against_avg_home": goals_against_avg_home,
        "goals_against_avg_away": goals_against_avg_away,
        # Group 4: Information
        "news_impact_score": news_impact_score,
        "lineup_confirmed": lineup_confirmed,
        "injury_count_home": injury_count_home,
        "injury_count_away": injury_count_away,
        # Group 5: Context
        "fixture_importance": fixture_importance,
        "referee_cards_avg": referee_cards_avg,
        "referee_home_win_pct": referee_home_win_pct,
        "referee_over25_pct": referee_over25_pct,
        # Outcome labels
        "match_outcome": outcome,
        "total_goals": total_goals,
        "over_25": over_25,
        "pseudo_clv_home": match.get("pseudo_clv_home"),
        "pseudo_clv_draw": match.get("pseudo_clv_draw"),
        "pseudo_clv_away": match.get("pseudo_clv_away"),
    }


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


# ============================================================
# T2: TEAM SEASON STATISTICS
# ============================================================

def store_team_season_stats(team_api_id: int, league_api_id: int, season: int,
                             parsed: dict) -> str | None:
    """
    Store or update team season stats. Upserts on (team_api_id, league_api_id, season, fetched_date).
    Returns row id or None on error.
    """
    client = get_client()
    today = date.today().isoformat()

    row = {
        "team_api_id": team_api_id,
        "league_api_id": league_api_id,
        "season": season,
        "fetched_date": today,
    }

    fields = [
        "form", "played_total", "played_home", "played_away",
        "wins_total", "wins_home", "wins_away",
        "draws_total", "draws_home", "draws_away",
        "losses_total", "losses_home", "losses_away",
        "goals_for_total", "goals_for_home", "goals_for_away",
        "goals_against_total", "goals_against_home", "goals_against_away",
        "goals_for_avg", "goals_against_avg",
        "clean_sheets_total", "clean_sheets_home", "clean_sheets_away",
        "failed_to_score_total", "failed_to_score_home", "failed_to_score_away",
        "clean_sheet_pct", "failed_to_score_pct",
        "biggest_win_home", "biggest_win_away", "biggest_loss_home", "biggest_loss_away",
        "streak_wins", "streak_draws", "streak_losses",
        "penalty_scored", "penalty_missed", "penalty_total", "penalty_scored_pct",
        "most_used_formation", "formations_jsonb",
        "yellow_cards_by_minute", "red_cards_by_minute",
        "goals_for_by_minute", "goals_against_by_minute",
        "raw",
    ]
    for f in fields:
        if f in parsed and parsed[f] is not None:
            row[f] = parsed[f]

    try:
        result = client.table("team_season_stats").upsert(
            row, on_conflict="team_api_id,league_api_id,season,fetched_date"
        ).execute()
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return None
        raise


def get_team_season_stats(team_api_id: int, season: int) -> dict | None:
    """Get the most recent team season stats for a team/season."""
    client = get_client()
    result = client.table("team_season_stats").select("*").eq(
        "team_api_id", team_api_id
    ).eq("season", season).order("fetched_date", desc=True).limit(1).execute()
    return result.data[0] if result.data else None


# ============================================================
# T3: MATCH INJURIES
# ============================================================

def store_match_injuries(match_id: str, af_fixture_id: int,
                          injuries: list[dict]) -> int:
    """
    Store injuries for a match. Upserts on (match_id, player_id).
    Returns count of rows stored.
    """
    client = get_client()
    stored = 0

    for inj in injuries:
        if not inj.get("player_id"):
            continue
        row = {
            "match_id": match_id,
            "af_fixture_id": af_fixture_id,
            **{k: v for k, v in inj.items() if k in (
                "team_api_id", "team_side", "player_id", "player_name",
                "player_type", "status", "reason", "raw"
            )},
        }
        try:
            client.table("match_injuries").upsert(
                row, on_conflict="match_id,player_id"
            ).execute()
            stored += 1
        except Exception:
            pass

    return stored


# ============================================================
# T4: MATCH STATS (half-time extension)
# ============================================================

def store_match_stats_full(match_id: str, stats: dict):
    """
    Extended version of store_match_stats — stores all fields including
    half-time stats (_ht suffix) and full-match fields (fouls, saves, etc.).
    Uses upsert — safe to call multiple times.
    """
    client = get_client()

    row = {"match_id": match_id}

    all_fields = [
        # Full match
        "xg_home", "xg_away",
        "shots_home", "shots_away",
        "shots_on_target_home", "shots_on_target_away",
        "possession_home",
        "corners_home", "corners_away",
        "fouls_home", "fouls_away",
        "offsides_home", "offsides_away",
        "saves_home", "saves_away",
        "passes_home", "passes_away",
        "pass_accuracy_home", "pass_accuracy_away",
        "yellow_cards_home", "yellow_cards_away",
        "red_cards_home", "red_cards_away",
        # Half-time
        "shots_home_ht", "shots_away_ht",
        "shots_on_target_home_ht", "shots_on_target_away_ht",
        "possession_home_ht",
        "corners_home_ht", "corners_away_ht",
        "fouls_home_ht", "fouls_away_ht",
        "offsides_home_ht", "offsides_away_ht",
        "yellow_cards_home_ht", "yellow_cards_away_ht",
        "xg_home_ht", "xg_away_ht",
        "passes_home_ht", "passes_away_ht",
    ]

    for field in all_fields:
        if field in stats and stats[field] is not None:
            row[field] = stats[field]

    if len(row) <= 1:
        return

    client.table("match_stats").upsert(row, on_conflict="match_id").execute()


# ============================================================
# T5: LIVE ODDS STORAGE
# ============================================================

def store_live_odds(match_id: str, odds_rows: list[dict], minute: int = None):
    """
    Store live in-play odds in odds_snapshots with is_live=true.
    Called every 5min during live matches.
    """
    client = get_client()
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for row in odds_rows:
        rows.append({
            "match_id": match_id,
            "bookmaker": row.get("bookmaker", "api-football-live"),
            "market": row["market"],
            "selection": row["selection"],
            "odds": row["odds"],
            "timestamp": now,
            "is_live": True,
            "is_closing": False,
            "minutes_to_kickoff": row.get("minute"),  # minute elapsed during match
        })

    if rows:
        try:
            client.table("odds_snapshots").insert(rows).execute()
        except Exception:
            pass  # Duplicate rows are fine


# ============================================================
# T7: LINEUPS
# ============================================================

def store_match_lineups(match_id: str, lineups_parsed: dict):
    """
    Store lineup data on the matches table.
    lineups_parsed keys: formation_home, formation_away, coach_home, coach_away,
                         lineups_home (JSONB), lineups_away (JSONB)
    """
    client = get_client()

    updates = {}
    for field in ["formation_home", "formation_away", "coach_home", "coach_away",
                  "lineups_home", "lineups_away"]:
        if lineups_parsed.get(field) is not None:
            updates[field] = lineups_parsed[field]

    if updates:
        from datetime import datetime, timezone
        updates["lineups_fetched_at"] = datetime.now(timezone.utc).isoformat()
        client.table("matches").update(updates).eq("id", match_id).execute()


# ============================================================
# T8: MATCH EVENTS (from API-Football)
# ============================================================

def store_match_events_af(match_id: str, events: list[dict],
                           home_team_api_id: int = None) -> int:
    """
    Store match events sourced from API-Football.
    Resolves team side (home/away) from team_api_id.
    Returns count of newly stored events.
    """
    client = get_client()
    stored = 0

    for ev in events:
        # Resolve home/away from team_api_id
        team_side = "unknown"
        if home_team_api_id and ev.get("team_api_id"):
            team_side = "home" if ev["team_api_id"] == home_team_api_id else "away"

        row = {
            "match_id": match_id,
            "minute": ev.get("minute", 0),
            "added_time": ev.get("added_time", 0),
            "event_type": ev["event_type"],
            "team": team_side,
            "player_name": ev.get("player_name"),
            "detail": ev.get("detail"),
            "af_event_order": ev.get("af_event_order"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            client.table("match_events").upsert(
                row, on_conflict="match_id,af_event_order"
            ).execute()
            stored += 1
        except Exception:
            pass

    return stored


# ============================================================
# T9: LEAGUE STANDINGS
# ============================================================

def store_league_standings(league_api_id: int, season: int,
                            rows: list[dict]) -> int:
    """
    Store league standings. Upserts on (league_api_id, season, fetched_date, team_api_id).
    Returns count stored.
    """
    client = get_client()
    today = date.today().isoformat()
    stored = 0

    for r in rows:
        row = {
            "league_api_id": league_api_id,
            "season": season,
            "fetched_date": today,
            **{k: v for k, v in r.items() if k in (
                "team_api_id", "team_name", "rank", "points", "goals_diff",
                "group_name", "form", "status", "description",
                "played", "wins", "draws", "losses", "goals_for", "goals_against",
                "home_played", "home_wins", "home_draws", "home_losses",
                "home_goals_for", "home_goals_against",
                "away_played", "away_wins", "away_draws", "away_losses",
                "away_goals_for", "away_goals_against",
                "raw",
            )},
        }
        try:
            client.table("league_standings").upsert(
                row, on_conflict="league_api_id,season,fetched_date,team_api_id"
            ).execute()
            stored += 1
        except Exception:
            pass

    return stored


# ============================================================
# T10: H2H
# ============================================================

def store_match_h2h(match_id: str, h2h_parsed: dict):
    """Store H2H data on the matches table."""
    client = get_client()

    updates = {}
    for field in ["h2h_raw", "h2h_home_wins", "h2h_draws", "h2h_away_wins"]:
        if h2h_parsed.get(field) is not None:
            updates[field] = h2h_parsed[field]

    if updates:
        client.table("matches").update(updates).eq("id", match_id).execute()


# ============================================================
# T11: PLAYER SIDELINED
# ============================================================

def store_player_sidelined(rows: list[dict]) -> int:
    """Store player sidelined history. Upserts on (player_id, start_date, type)."""
    client = get_client()
    stored = 0

    for row in rows:
        if not row.get("player_id") or not row.get("start_date"):
            continue
        try:
            client.table("player_sidelined").upsert(
                row, on_conflict="player_id,start_date,type"
            ).execute()
            stored += 1
        except Exception:
            pass

    return stored


# ============================================================
# T12: MATCH PLAYER STATS
# ============================================================

def store_match_player_stats(match_id: str, af_fixture_id: int,
                              players: list[dict]) -> int:
    """
    Store per-player match statistics. Upserts on (match_id, player_id).
    Returns count stored.
    """
    client = get_client()
    stored = 0

    for p in players:
        if not p.get("player_id"):
            continue
        row = {
            "match_id": match_id,
            "af_fixture_id": af_fixture_id,
            **{k: v for k, v in p.items() if k in (
                "team_api_id", "team_side", "player_id", "player_name",
                "shirt_number", "position", "minutes_played", "rating", "captain",
                "goals", "assists", "shots_total", "shots_on_target",
                "passes_total", "passes_key", "pass_accuracy",
                "tackles_total", "blocks", "interceptions",
                "duels_total", "duels_won",
                "dribbles_attempted", "dribbles_success",
                "fouls_drawn", "fouls_committed",
                "yellow_cards", "red_cards",
                "goals_conceded", "saves",
                "penalty_scored", "penalty_missed", "penalty_saved",
                "raw",
            )},
        }
        try:
            client.table("match_player_stats").upsert(
                row, on_conflict="match_id,player_id"
            ).execute()
            stored += 1
        except Exception:
            pass

    return stored


# ============================================================
# T13: TEAM TRANSFERS
# ============================================================

def store_team_transfers(team_api_id: int, rows: list[dict]) -> int:
    """Store team transfer records. Upserts on (team_api_id, player_id, transfer_date)."""
    client = get_client()
    stored = 0

    for row in rows:
        if not row.get("player_id") or not row.get("transfer_date"):
            continue
        try:
            client.table("team_transfers").upsert(
                row, on_conflict="team_api_id,player_id,transfer_date"
            ).execute()
            stored += 1
        except Exception:
            pass

    return stored


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


# ============================================================
# S3 / S4 / S5 / BDM-1: MORNING SIGNAL WIRING
# ============================================================

def compute_bookmaker_disagreement(match_id: str) -> float | None:
    """
    BDM-1: max(implied_prob) - min(implied_prob) across bookmakers for home 1x2.
    Uses the most recent snapshot per bookmaker. Requires ≥2 distinct bookmakers.
    """
    client = get_client()
    result = client.table("odds_snapshots").select(
        "bookmaker, odds, timestamp"
    ).eq("match_id", match_id).eq("market", "1x2").eq(
        "selection", "home"
    ).not_.is_("bookmaker", "null").order("timestamp", desc=True).limit(200).execute()

    if not result.data:
        return None

    # Latest odds per bookmaker
    seen: dict[str, float] = {}
    for row in result.data:
        bk = row.get("bookmaker")
        if bk and bk not in seen and float(row["odds"]) > 1.0:
            seen[bk] = 1.0 / float(row["odds"])

    if len(seen) < 2:
        return None

    values = list(seen.values())
    return round(max(values) - min(values), 4)


def compute_fixture_importance(
    league_api_id: int, season: int,
    home_team_api_id: int, away_team_api_id: int,
) -> float | None:
    """
    S5: Fixture importance from standings urgency.
    Returns 0.0–1.0. High = title/relegation 6-pointer.
    """
    if not (league_api_id and season and home_team_api_id and away_team_api_id):
        return None

    client = get_client()
    result = client.table("league_standings").select(
        "team_api_id, rank, points, played, description, status"
    ).eq("league_api_id", league_api_id).eq("season", season).order(
        "fetched_date", desc=True
    ).limit(40).execute()

    if not result.data:
        return None

    # Deduplicate: latest entry per team
    by_team: dict[int, dict] = {}
    for row in result.data:
        tid = row["team_api_id"]
        if tid not in by_team:
            by_team[tid] = row

    total_teams = len(by_team)
    if total_teams < 4:
        return None

    home_row = by_team.get(home_team_api_id)
    away_row = by_team.get(away_team_api_id)
    if not (home_row and away_row):
        return None

    relegation_threshold = max(1, total_teams - 2)

    def urgency(row: dict) -> float:
        rank = row.get("rank") or total_teams
        desc = (row.get("description") or "").lower()
        # Title / promotion zone
        if rank <= 2 or "champion" in desc or "promot" in desc:
            return 0.85
        # Relegation zone
        if rank >= relegation_threshold or "relegate" in desc:
            return 0.70
        # Playoff zone
        if rank <= 4 or "playoff" in desc or "play-off" in desc:
            return 0.50
        # Upper mid
        if rank / total_teams < 0.35:
            return 0.25
        return 0.10

    return round(max(urgency(home_row), urgency(away_row)), 3)


def get_referee_cards_avg(referee_name: str) -> float | None:
    """S4: Look up pre-computed cards_per_game for a referee."""
    if not referee_name:
        return None
    client = get_client()
    result = client.table("referee_stats").select(
        "cards_per_game"
    ).eq("referee_name", referee_name).execute()
    if result.data:
        return result.data[0].get("cards_per_game")
    return None


def build_referee_stats() -> int:
    """
    S4: (Re)compute referee_stats from all finished matches.
    Called from backfill_referee_stats.py and optionally from settlement.
    Returns number of referees upserted.
    """
    client = get_client()

    # Fetch all finished matches with referee name and score
    matches_r = client.table("matches").select(
        "id, referee, result, score_home, score_away"
    ).eq("status", "finished").not_.is_("referee", "null").execute()

    if not matches_r.data:
        return 0

    from collections import defaultdict
    stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "home": 0, "draw": 0, "away": 0,
        "over25": 0, "yellow": 0, "red": 0,
    })

    for m in matches_r.data:
        ref = m.get("referee", "").strip()
        if not ref:
            continue
        s = stats[ref]
        s["total"] += 1
        result = m.get("result")
        if result == "home":
            s["home"] += 1
        elif result == "draw":
            s["draw"] += 1
        elif result == "away":
            s["away"] += 1

        sh = m.get("score_home")
        sa = m.get("score_away")
        if sh is not None and sa is not None:
            if int(sh) + int(sa) > 2:
                s["over25"] += 1

    # Enrich with card data from match_stats
    match_ids_by_ref: dict[str, list[str]] = defaultdict(list)
    for m in matches_r.data:
        ref = m.get("referee", "").strip()
        if ref:
            match_ids_by_ref[ref].append(m["id"])

    # Fetch card totals from match_stats
    for ref, mids in match_ids_by_ref.items():
        # Batch queries — 100 at a time
        for i in range(0, len(mids), 100):
            batch = mids[i:i + 100]
            cards_r = client.table("match_stats").select(
                "yellow_cards_home, yellow_cards_away, red_cards_home, red_cards_away"
            ).in_("match_id", batch).execute()
            for row in (cards_r.data or []):
                yh = row.get("yellow_cards_home") or 0
                ya = row.get("yellow_cards_away") or 0
                rh = row.get("red_cards_home") or 0
                ra = row.get("red_cards_away") or 0
                stats[ref]["yellow"] += yh + ya
                stats[ref]["red"] += rh + ra

    upserted = 0
    for ref, s in stats.items():
        total = s["total"]
        if total < 3:
            continue
        cards_total = s["yellow"] + s["red"]
        row = {
            "referee_name": ref,
            "matches_total": total,
            "home_wins": s["home"],
            "draws_count": s["draw"],
            "away_wins": s["away"],
            "home_win_pct": round(s["home"] / total, 4),
            "cards_per_game": round(cards_total / total, 2),
            "over_25_count": s["over25"],
            "over_25_pct": round(s["over25"] / total, 4),
            "yellow_total": s["yellow"],
            "red_total": s["red"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            client.table("referee_stats").upsert(
                row, on_conflict="referee_name"
            ).execute()
            upserted += 1
        except Exception:
            pass

    return upserted


def write_morning_signals(
    match_id: str,
    league_api_id: int | None = None,
    season: int | None = None,
    home_team_api_id: int | None = None,
    away_team_api_id: int | None = None,
    referee: str | None = None,
    opening_odds_home: float | None = None,
    opening_odds_draw: float | None = None,
    opening_odds_away: float | None = None,
) -> None:
    """
    S3/S4/S5/BDM-1: Write all morning context signals to match_signals.
    Called once per match during the morning pipeline.
    """
    now_str = datetime.now(timezone.utc).isoformat()

    # ── Opening odds → market implied probs ────────────────────────────────
    if opening_odds_home and opening_odds_home > 1.0:
        store_match_signal(match_id, "market_implied_home",
                           round(1.0 / opening_odds_home, 4),
                           "market", "opening_odds", captured_at=now_str)
    if opening_odds_draw and opening_odds_draw > 1.0:
        store_match_signal(match_id, "market_implied_draw",
                           round(1.0 / opening_odds_draw, 4),
                           "market", "opening_odds", captured_at=now_str)
    if opening_odds_away and opening_odds_away > 1.0:
        store_match_signal(match_id, "market_implied_away",
                           round(1.0 / opening_odds_away, 4),
                           "market", "opening_odds", captured_at=now_str)

    # ── BDM-1: Bookmaker disagreement ──────────────────────────────────────
    try:
        bdm = compute_bookmaker_disagreement(match_id)
        if bdm is not None:
            store_match_signal(match_id, "bookmaker_disagreement",
                               bdm, "market", "derived", captured_at=now_str)
    except Exception:
        pass

    # ── S5: Fixture importance ──────────────────────────────────────────────
    try:
        if league_api_id and season and home_team_api_id and away_team_api_id:
            importance = compute_fixture_importance(
                league_api_id, season, home_team_api_id, away_team_api_id
            )
            if importance is not None:
                store_match_signal(match_id, "fixture_importance",
                                   importance, "context", "derived", captured_at=now_str)
    except Exception:
        pass

    # ── S4: Referee cards avg ───────────────────────────────────────────────
    try:
        if referee:
            cards_avg = get_referee_cards_avg(referee)
            if cards_avg is not None:
                store_match_signal(match_id, "referee_cards_avg",
                                   float(cards_avg), "context", "referee_stats",
                                   captured_at=now_str)
    except Exception:
        pass

    # ── Injury counts (from match_injuries already stored by T3) ───────────
    try:
        client = get_client()
        inj_r = client.table("match_injuries").select(
            "team_side, status"
        ).eq("match_id", match_id).execute()
        if inj_r.data:
            out_home = sum(1 for r in inj_r.data
                          if r.get("team_side") == "home" and r.get("status") == "Missing Fixture")
            out_away = sum(1 for r in inj_r.data
                          if r.get("team_side") == "away" and r.get("status") == "Missing Fixture")
            doubt_home = sum(1 for r in inj_r.data
                            if r.get("team_side") == "home" and r.get("status") == "Questionable")
            doubt_away = sum(1 for r in inj_r.data
                            if r.get("team_side") == "away" and r.get("status") == "Questionable")
            if out_home + doubt_home > 0:
                store_match_signal(match_id, "injury_count_home",
                                   float(out_home + doubt_home),
                                   "information", "af", captured_at=now_str)
                store_match_signal(match_id, "players_out_home",
                                   float(out_home),
                                   "information", "af", captured_at=now_str)
            if out_away + doubt_away > 0:
                store_match_signal(match_id, "injury_count_away",
                                   float(out_away + doubt_away),
                                   "information", "af", captured_at=now_str)
                store_match_signal(match_id, "players_out_away",
                                   float(out_away),
                                   "information", "af", captured_at=now_str)
    except Exception:
        pass

    # ── ELO diff ────────────────────────────────────────────────────────────
    try:
        client = get_client()
        match_r = client.table("matches").select(
            "home_team_id, away_team_id, date"
        ).eq("id", match_id).execute()
        if match_r.data:
            m = match_r.data[0]
            match_date = m["date"][:10] if m.get("date") else date.today().isoformat()
            elo_home = elo_away = None
            for team_id, attr in [(m.get("home_team_id"), "elo_home"),
                                   (m.get("away_team_id"), "elo_away")]:
                if team_id:
                    r = client.table("team_elo_daily").select("elo_rating").eq(
                        "team_id", team_id
                    ).lte("date", match_date).order("date", desc=True).limit(1).execute()
                    if r.data:
                        val = float(r.data[0]["elo_rating"])
                        if attr == "elo_home":
                            elo_home = val
                        else:
                            elo_away = val
            if elo_home is not None and elo_away is not None:
                store_match_signal(match_id, "elo_diff",
                                   round(elo_home - elo_away, 2),
                                   "quality", "derived", captured_at=now_str)
                store_match_signal(match_id, "elo_home",
                                   elo_home, "quality", "derived", captured_at=now_str)
                store_match_signal(match_id, "elo_away",
                                   elo_away, "quality", "derived", captured_at=now_str)

            # ── Form PPG ────────────────────────────────────────────────────
            for team_id, signal_name in [
                (m.get("home_team_id"), "form_ppg_home"),
                (m.get("away_team_id"), "form_ppg_away"),
            ]:
                if team_id:
                    fr = client.table("team_form_cache").select("ppg").eq(
                        "team_id", team_id
                    ).lte("date", match_date).order("date", desc=True).limit(1).execute()
                    if fr.data and fr.data[0].get("ppg") is not None:
                        store_match_signal(match_id, signal_name,
                                           float(fr.data[0]["ppg"]),
                                           "quality", "derived", captured_at=now_str)
    except Exception:
        pass

    # ── S3b: Standings signals ──────────────────────────────────────────────
    try:
        if league_api_id and season and home_team_api_id and away_team_api_id:
            client = get_client()
            st_r = client.table("league_standings").select(
                "team_api_id, rank, points, description"
            ).eq("league_api_id", league_api_id).eq(
                "season", season
            ).order("fetched_date", desc=True).limit(200).execute()

            if st_r.data:
                # Deduplicate — latest fetched_date first, keep first seen per team
                seen_tids: set = set()
                deduped: list = []
                for row in st_r.data:
                    tid = row.get("team_api_id")
                    if tid and tid not in seen_tids:
                        seen_tids.add(tid)
                        deduped.append(row)

                total_teams = len(deduped)
                if total_teams >= 4:
                    rows_by_rank = sorted(deduped, key=lambda r: r.get("rank") or 99)
                    leader_points = rows_by_rank[0].get("points") or 0
                    # Last safe position = 4th from bottom (relegation = bottom 3)
                    last_safe_points = rows_by_rank[-4].get("points") or 0

                    for api_id, suffix in [
                        (home_team_api_id, "home"),
                        (away_team_api_id, "away"),
                    ]:
                        team_row = next(
                            (r for r in deduped if r.get("team_api_id") == api_id), None
                        )
                        if not team_row:
                            continue
                        rank = team_row.get("rank") or 0
                        pts = team_row.get("points") or 0
                        pos_norm = round(rank / total_teams, 4)
                        pts_to_title = int(leader_points - pts)
                        pts_to_rel = int(pts - last_safe_points)
                        store_match_signal(match_id, f"league_position_{suffix}",
                                           pos_norm, "quality", "standings",
                                           captured_at=now_str)
                        store_match_signal(match_id, f"points_to_title_{suffix}",
                                           float(pts_to_title), "quality", "standings",
                                           captured_at=now_str)
                        store_match_signal(match_id, f"points_to_relegation_{suffix}",
                                           float(pts_to_rel), "quality", "standings",
                                           captured_at=now_str)
    except Exception:
        pass

    # ── S3c: H2H win pct ────────────────────────────────────────────────────
    try:
        client = get_client()
        h2h_r = client.table("matches").select(
            "h2h_home_wins, h2h_draws, h2h_away_wins"
        ).eq("id", match_id).execute()
        if h2h_r.data:
            d = h2h_r.data[0]
            hw = d.get("h2h_home_wins") or 0
            hd = d.get("h2h_draws") or 0
            ha = d.get("h2h_away_wins") or 0
            total_h2h = hw + hd + ha
            if total_h2h >= 3:
                store_match_signal(match_id, "h2h_win_pct",
                                   round(hw / total_h2h, 4),
                                   "quality", "af", captured_at=now_str)
                store_match_signal(match_id, "h2h_total",
                                   float(total_h2h),
                                   "quality", "af", captured_at=now_str)
    except Exception:
        pass

    # ── S3d: Referee home win pct + over 2.5 pct ────────────────────────────
    try:
        if referee:
            client = get_client()
            ref_r = client.table("referee_stats").select(
                "home_win_pct, over_25_pct"
            ).eq("referee_name", referee).execute()
            if ref_r.data:
                hwp = ref_r.data[0].get("home_win_pct")
                o25p = ref_r.data[0].get("over_25_pct")
                if hwp is not None:
                    store_match_signal(match_id, "referee_home_win_pct",
                                       float(hwp), "context", "referee_stats",
                                       captured_at=now_str)
                if o25p is not None:
                    store_match_signal(match_id, "referee_over25_pct",
                                       float(o25p), "context", "referee_stats",
                                       captured_at=now_str)
    except Exception:
        pass

    # ── S3e: Overnight line move ─────────────────────────────────────────────
    try:
        client = get_client()
        today_date = date.today().isoformat()
        midnight_utc = f"{today_date}T00:00:00+00:00"

        yest_r = client.table("odds_snapshots").select("odds").eq(
            "match_id", match_id
        ).eq("market", "1x2").eq("selection", "home").lt(
            "timestamp", midnight_utc
        ).order("timestamp", desc=True).limit(1).execute()

        today_r = client.table("odds_snapshots").select("odds").eq(
            "match_id", match_id
        ).eq("market", "1x2").eq("selection", "home").gte(
            "timestamp", midnight_utc
        ).order("timestamp", desc=False).limit(1).execute()

        if yest_r.data and today_r.data:
            last_yest = 1.0 / float(yest_r.data[0]["odds"])
            first_today = 1.0 / float(today_r.data[0]["odds"])
            store_match_signal(match_id, "overnight_line_move",
                               round(first_today - last_yest, 5),
                               "market", "derived", captured_at=now_str)
    except Exception:
        pass

    # ── S3f: Rest days home / away ───────────────────────────────────────────
    try:
        client = get_client()
        match_r2 = client.table("matches").select(
            "home_team_id, away_team_id, date"
        ).eq("id", match_id).execute()
        if match_r2.data:
            m2 = match_r2.data[0]
            match_date_str = (m2.get("date") or "")[:10]
            if match_date_str:
                for team_id, sig_name in [
                    (m2.get("home_team_id"), "rest_days_home"),
                    (m2.get("away_team_id"), "rest_days_away"),
                ]:
                    if not team_id:
                        continue
                    prev_r = client.table("matches").select("date").or_(
                        f"home_team_id.eq.{team_id},away_team_id.eq.{team_id}"
                    ).eq("status", "finished").lt(
                        "date", f"{match_date_str}T00:00:00"
                    ).order("date", desc=True).limit(1).execute()
                    if prev_r.data:
                        prev_date_str = prev_r.data[0]["date"][:10]
                        delta = date.fromisoformat(match_date_str) - date.fromisoformat(prev_date_str)
                        store_match_signal(match_id, sig_name,
                                           float(delta.days), "quality", "derived",
                                           captured_at=now_str)
    except Exception:
        pass

    # ── T2: Season goals avg (from team_season_stats, populated by T2 fetch) ─
    try:
        if home_team_api_id and away_team_api_id and season:
            for api_id, suffix in [
                (home_team_api_id, "home"),
                (away_team_api_id, "away"),
            ]:
                stats = get_team_season_stats(api_id, season)
                if stats:
                    gf_avg = stats.get("goals_for_avg")
                    ga_avg = stats.get("goals_against_avg")
                    if gf_avg is not None:
                        store_match_signal(match_id, f"goals_for_avg_{suffix}",
                                           float(gf_avg), "quality", "team_season_stats",
                                           captured_at=now_str)
                    if ga_avg is not None:
                        store_match_signal(match_id, f"goals_against_avg_{suffix}",
                                           float(ga_avg), "quality", "team_season_stats",
                                           captured_at=now_str)
    except Exception:
        pass


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
