"""
OddsIntel — Supabase Client (Direct PostgreSQL via psycopg2)
Handles all database operations: storing matches, odds, predictions, bets,
live snapshots, and match events.

All DB access uses psycopg2 directly (via workers/api_clients/db.py):
  - No 1K row cap (PostgREST default)
  - Real JOINs
  - Bulk INSERT via execute_values
  - No URL length limits on IN clauses
  - Persistent connection pool
"""

import os
import unicodedata
import re
from datetime import datetime, date, timezone

import psycopg2
import psycopg2.extras
from psycopg2.extras import Json
from dotenv import load_dotenv
from rich.console import Console

from workers.api_clients.db import get_conn, execute_query, execute_write, bulk_upsert

load_dotenv()

console = Console()


# ============================================================
# BOTS
# ============================================================

def ensure_bots(bots_config: dict) -> dict:
    """
    Create bot records if they don't exist.
    Returns {bot_name: bot_uuid} mapping.
    """
    rows = execute_query("SELECT id, name FROM bots")
    existing = {b["name"]: b["id"] for b in rows}

    for bot_name, config in bots_config.items():
        if bot_name not in existing:
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """INSERT INTO bots (name, strategy, starting_bankroll, current_bankroll, is_active)
                           VALUES (%s, %s, %s, %s, %s)
                           RETURNING id""",
                        (bot_name, config.get("description", ""), 1000.0, 1000.0, True),
                    )
                    conn.commit()
                    new_row = cur.fetchone()
                    existing[bot_name] = new_row["id"]

    return existing


# ============================================================
# LEAGUES & TEAMS
# ============================================================

def ensure_league(league_path: str, tier: int = 1) -> str:
    """Get or create a league, return its UUID."""
    parts = league_path.split(" / ")
    country = parts[0] if len(parts) > 1 else "Unknown"
    name = parts[-1]

    rows = execute_query(
        "SELECT id FROM leagues WHERE name = %s AND country = %s",
        (name, country),
    )
    if rows:
        return rows[0]["id"]

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO leagues (name, country, tier, is_active)
                   VALUES (%s, %s, %s, %s)
                   RETURNING id""",
                (name, country, tier, True),
            )
            conn.commit()
            return cur.fetchone()["id"]


def _normalize_team_name(name: str) -> str:
    """Strip accents, punctuation, and case for fuzzy team matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


def ensure_team(team_name: str, country: str = "Unknown", logo_url: str | None = None) -> str:
    """Get or create a team, return its UUID. Updates logo_url if provided and not yet stored.

    Uses normalized (accent/punctuation-stripped) matching to prevent duplicates
    from different data sources (e.g. AF 'Atletico Madrid' vs Kambi 'Atlético Madrid').
    """
    # 1. Try exact match first (fastest)
    rows = execute_query(
        "SELECT id, logo_url FROM teams WHERE name = %s", (team_name,)
    )
    if rows:
        team_id = rows[0]["id"]
        if logo_url and not rows[0].get("logo_url"):
            execute_write(
                "UPDATE teams SET logo_url = %s WHERE id = %s", (logo_url, team_id)
            )
        return team_id

    # 2. Fuzzy match: search by ilike prefix, then compare normalized names.
    norm_target = _normalize_team_name(team_name)
    prefix = team_name[:3]
    candidates = execute_query(
        "SELECT id, name, logo_url FROM teams WHERE name ILIKE %s",
        (f"{prefix}%",),
    )
    for row in candidates:
        if _normalize_team_name(row["name"]) == norm_target:
            team_id = row["id"]
            if logo_url and not row.get("logo_url"):
                execute_write(
                    "UPDATE teams SET logo_url = %s WHERE id = %s", (logo_url, team_id)
                )
            return team_id

    # 3. No match found -- create new team
    league = ensure_league(f"{country} / Unknown", tier=0)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if logo_url:
                cur.execute(
                    """INSERT INTO teams (name, country, league_id, logo_url)
                       VALUES (%s, %s, %s, %s)
                       RETURNING id""",
                    (team_name, country, league, logo_url),
                )
            else:
                cur.execute(
                    """INSERT INTO teams (name, country, league_id)
                       VALUES (%s, %s, %s)
                       RETURNING id""",
                    (team_name, country, league),
                )
            conn.commit()
            return cur.fetchone()["id"]


# ============================================================
# MATCHES
# ============================================================

def store_match(match_data: dict) -> str:
    """Store a match, return its UUID"""
    home_team = match_data["home_team"]
    away_team = match_data["away_team"]
    match_date = match_data.get("start_time", match_data.get("date", ""))
    date_prefix = match_date[:10] if match_date else date.today().isoformat()

    country = match_data.get("league_path", "").split(" / ")[0] if " / " in match_data.get("league_path", "") else "Unknown"
    home_id = ensure_team(home_team, country, logo_url=match_data.get("home_logo"))
    away_id = ensure_team(away_team, country, logo_url=match_data.get("away_logo"))

    league_path = match_data.get("league_path", "Unknown / Unknown")
    tier = match_data.get("tier", 1)
    league_id = ensure_league(league_path, tier)

    existing = execute_query(
        """SELECT id, api_football_id, venue_name, referee FROM matches
           WHERE home_team_id = %s AND away_team_id = %s
             AND date >= %s AND date <= %s""",
        (home_id, away_id, f"{date_prefix}T00:00:00", f"{date_prefix}T23:59:59"),
    )

    if existing:
        match_id = existing[0]["id"]
        current_status = existing[0].get("status", "")
        updates = {}

        # Backfill IDs and metadata if we have them now but DB doesn't
        af_id = match_data.get("api_football_id")
        if af_id and not existing[0].get("api_football_id"):
            updates["api_football_id"] = int(af_id)
        if match_data.get("venue_name") and not existing[0].get("venue_name"):
            updates["venue_name"] = match_data["venue_name"]
        if match_data.get("referee") and not existing[0].get("referee"):
            updates["referee"] = match_data["referee"]

        # Update status if fixture was postponed/cancelled since last fetch.
        # Only applies when DB still shows 'scheduled' — never override live/finished.
        af_status = match_data.get("af_status_short", "")
        if current_status == "scheduled" and af_status in ("PST", "CANC", "ABD", "WO", "AWD"):
            updates["status"] = "postponed"

        # Update kickoff time if it changed (rescheduled match).
        # Only applies when DB still shows 'scheduled'.
        if current_status == "scheduled":
            new_date = match_data.get("start_time") or match_data.get("date", "")
            existing_date = str(existing[0].get("date", ""))
            # Compare first 16 chars (YYYY-MM-DDTHH:MM) to avoid microsecond noise
            if new_date and new_date[:16] != existing_date[:16]:
                updates["date"] = new_date

        if updates:
            set_clauses = ", ".join(f"{k} = %s" for k in updates)
            params = list(updates.values()) + [match_id]
            execute_write(
                f"UPDATE matches SET {set_clauses} WHERE id = %s", tuple(params)
            )
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

    columns = list(match_record.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    col_str = ", ".join(columns)
    values = tuple(match_record[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"INSERT INTO matches ({col_str}) VALUES ({placeholders}) RETURNING id",
                values,
            )
            conn.commit()
            return cur.fetchone()["id"]


def update_match_status(match_id: str, status: str):
    """Update a match status (scheduled -> live -> finished)"""
    execute_write(
        "UPDATE matches SET status = %s WHERE id = %s", (status, match_id)
    )


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

    # BTTS
    if match_data.get("odds_btts_yes", 0) > 0:
        odds_rows.append({**base, "market": "btts", "selection": "yes",
                          "odds": match_data["odds_btts_yes"]})
    if match_data.get("odds_btts_no", 0) > 0:
        odds_rows.append({**base, "market": "btts", "selection": "no",
                          "odds": match_data["odds_btts_no"]})

    if odds_rows:
        tuples = [
            (r["match_id"], r["bookmaker"], r["market"], r["selection"],
             r["odds"], r["timestamp"], r["is_closing"], r["minutes_to_kickoff"])
            for r in odds_rows
        ]
        with get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """INSERT INTO odds_snapshots
                       (match_id, bookmaker, market, selection, odds, timestamp, is_closing, minutes_to_kickoff)
                       VALUES %s""",
                    tuples,
                    page_size=500,
                )
                conn.commit()


# ============================================================
# LIVE TRACKING
# ============================================================

def store_live_snapshot(match_id: str, snapshot: dict):
    """
    Store an in-play snapshot (called every ~5 min during live matches).
    snapshot keys: minute, score_home, score_away, shots_*, xg_*, possession_home,
                   live_ou_* odds, live_1x2_* odds, model_* context
    """
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
        "fouls_home", "fouls_away", "offsides_home", "offsides_away",
        "saves_home", "saves_away", "blocked_shots_home", "blocked_shots_away",
        "pass_accuracy_home", "pass_accuracy_away",
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

    columns = list(row.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    col_str = ", ".join(columns)
    values = tuple(row[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO live_match_snapshots ({col_str}) VALUES ({placeholders})",
                values,
            )
            conn.commit()


def store_match_event(match_id: str, event: dict) -> bool:
    """
    Store a match event (goal, card, sub).
    Returns False if event already exists (dedup via unique constraint).
    """
    row = {
        "match_id": match_id,
        "minute": event.get("minute", 0),
        "added_time": event.get("added_time", 0),
        "event_type": event["event_type"],
        "team": event["team"],
        "player_name": event.get("player_name"),
        "assist_name": event.get("assist_name"),
        "detail": event.get("detail"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    columns = list(row.keys())
    placeholders = ", ".join(["%s"] * len(columns))
    col_str = ", ".join(columns)
    values = tuple(row[c] for c in columns)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO match_events ({col_str}) VALUES ({placeholders})",
                    values,
                )
                conn.commit()
        return True
    except Exception as e:
        # Unique constraint violation = duplicate event, that's fine
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return False
        raise


def get_live_matches() -> list[dict]:
    """
    Get matches that are currently in-play or starting soon.
    Returns matches that are live or starting soon for the live tracker.
    """
    now = datetime.now(timezone.utc)
    from_time = now.replace(hour=max(0, now.hour - 3)).isoformat()

    rows = execute_query(
        """SELECT m.id, m.date, m.status,
                  th.name AS home_name,
                  ta.name AS away_name,
                  l.name AS league_name, l.country AS league_country
           FROM matches m
           LEFT JOIN teams th ON m.home_team_id = th.id
           LEFT JOIN teams ta ON m.away_team_id = ta.id
           LEFT JOIN leagues l ON m.league_id = l.id
           WHERE m.date >= %s AND m.status != 'finished'""",
        (from_time,),
    )

    # Restructure to match PostgREST format callers expect
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "date": r["date"],
            "status": r["status"],
            "home": {"name": r["home_name"]},
            "away": {"name": r["away_name"]},
            "leagues": {"name": r["league_name"], "country": r["league_country"]},
        })
    return result


def get_match_by_teams_and_date(home_team_name: str, away_team_name: str,
                                 match_date: str) -> dict | None:
    """Look up a match by team names and date."""
    date_prefix = match_date[:10]

    # Get team IDs
    home_result = execute_query("SELECT id FROM teams WHERE name = %s", (home_team_name,))
    away_result = execute_query("SELECT id FROM teams WHERE name = %s", (away_team_name,))

    if not home_result or not away_result:
        return None

    home_id = home_result[0]["id"]
    away_id = away_result[0]["id"]

    rows = execute_query(
        """SELECT id, status, date FROM matches
           WHERE home_team_id = %s AND away_team_id = %s
             AND date >= %s AND date <= %s""",
        (home_id, away_id, f"{date_prefix}T00:00:00", f"{date_prefix}T23:59:59"),
    )

    return rows[0] if rows else None


def get_todays_scheduled_matches() -> list[dict]:
    """Get all of today's scheduled (not yet started) matches with kickoff times"""
    today = date.today().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    rows = execute_query(
        """SELECT m.id, m.date,
                  th.name AS home_name,
                  ta.name AS away_name
           FROM matches m
           LEFT JOIN teams th ON m.home_team_id = th.id
           LEFT JOIN teams ta ON m.away_team_id = ta.id
           WHERE m.date >= %s AND m.date <= %s AND m.status = 'scheduled'""",
        (now_iso, f"{today}T23:59:59"),
    )

    # Restructure to match PostgREST format
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "date": r["date"],
            "home": {"name": r["home_name"]},
            "away": {"name": r["away_name"]},
        })
    return result


# ============================================================
# PREDICTIONS
# ============================================================

def store_prediction(match_id: str, market: str, prediction: dict,
                     source: str = "ensemble"):
    """
    Store a model prediction for a match.

    source: 'ensemble' (default) | 'poisson' | 'xgboost' | 'af'
    Each (match_id, market, source) combination is unique -- upsert on conflict.
    """
    row = {
        "match_id": match_id,
        "market": market,
        "source": source,
        "model_probability": prediction["model_prob"],
        "confidence": prediction.get("confidence", 0.5),
        "reasoning": prediction.get("reasoning"),
    }
    # Only include these if actually provided (columns are NOT NULL in DB)
    if prediction.get("implied_prob") is not None:
        row["implied_probability"] = prediction["implied_prob"]
    if prediction.get("edge") is not None:
        row["edge_percent"] = prediction["edge"]

    # Sanitize: numpy scalars → native Python floats, NaN/Inf → None.
    # psycopg2 cannot handle numpy types — it interprets np.float64(x) as a
    # schema reference and raises 'schema "np" does not exist'.
    row = {k: _sanitize_for_json(v) for k, v in row.items()}

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c not in ("match_id", "market", "source")]
    update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    values = tuple(row[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO predictions ({col_str}) VALUES ({placeholders})
                    ON CONFLICT (match_id, market, source)
                    DO UPDATE SET {update_str}""",
                values,
            )
            conn.commit()


def store_match_signal(match_id: str, signal_name: str, signal_value: float | None,
                       signal_group: str, data_source: str = "derived",
                       signal_text: str | None = None,
                       captured_at: str | None = None):
    """
    Append a signal observation to match_signals.
    Same signal can be stored multiple times (different timestamps).
    ML training uses the value closest to kickoff.
    """
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

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    values = tuple(row[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO match_signals ({col_str}) VALUES ({placeholders})",
                values,
            )
            conn.commit()


# --- Pseudo-CLV ---------------------------------------------------------------

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
    rows = execute_query(
        """SELECT selection, odds, timestamp, is_closing
           FROM odds_snapshots
           WHERE match_id = %s AND market = '1x2'
           ORDER BY timestamp ASC""",
        (match_id,),
    )

    if not rows:
        return None

    # Group by selection
    by_selection: dict[str, list[dict]] = {}
    for row in rows:
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

    execute_write(
        """UPDATE matches
           SET pseudo_clv_home = %s, pseudo_clv_draw = %s, pseudo_clv_away = %s
           WHERE id = %s""",
        (pseudo_clvs.get("home"), pseudo_clvs.get("draw"), pseudo_clvs.get("away"), match_id),
    )

    return pseudo_clvs


# --- match_feature_vectors ETL ------------------------------------------------

def build_match_feature_vectors(client, date_str: str) -> int:
    """
    Nightly ETL: build wide ML training rows for all finished matches on date_str.
    Pulls from predictions, team_elo_daily, team_form_cache, odds_snapshots, matches.

    Uses batched bulk queries (one per table) instead of per-match queries.
    ~400 matches now takes ~8 queries total instead of ~4000.
    Returns count of rows upserted.
    """
    # Fetch finished matches for this date
    matches = execute_query(
        """SELECT id, date, result, score_home, score_away,
                  home_team_id, away_team_id, league_id,
                  pseudo_clv_home, pseudo_clv_draw, pseudo_clv_away
           FROM matches
           WHERE status = 'finished'
             AND date >= %s AND date <= %s""",
        (f"{date_str}T00:00:00", f"{date_str}T23:59:59"),
    )

    if not matches:
        return 0

    all_match_ids = [m["id"] for m in matches]
    all_team_ids = set()
    all_league_ids = set()
    for m in matches:
        if m.get("home_team_id"):
            all_team_ids.add(m["home_team_id"])
        if m.get("away_team_id"):
            all_team_ids.add(m["away_team_id"])
        if m.get("league_id"):
            all_league_ids.add(m["league_id"])

    # -- Batch load: leagues ---------------------------------------------------
    league_tier_map = {}
    if all_league_ids:
        lr = execute_query(
            "SELECT id, tier FROM leagues WHERE id = ANY(%s::uuid[])",
            (list(all_league_ids),),
        )
        league_tier_map = {r["id"]: r.get("tier") for r in lr}

    # -- Batch load: predictions (1x2_home) ------------------------------------
    preds_by_match: dict[str, list] = {}
    for chunk in _chunk_list(all_match_ids, 200):
        pr = execute_query(
            """SELECT match_id, source, model_probability, market, reasoning
               FROM predictions
               WHERE match_id = ANY(%s::uuid[]) AND market = '1x2_home'""",
            (chunk,),
        )
        for p in pr:
            preds_by_match.setdefault(p["match_id"], []).append(p)

    # Also get reasoning for data_tier (any market, just need one per match)
    reasoning_by_match: dict[str, str] = {}
    for chunk in _chunk_list(all_match_ids, 200):
        rr = execute_query(
            """SELECT match_id, reasoning
               FROM predictions
               WHERE match_id = ANY(%s::uuid[]) AND reasoning IS NOT NULL
               LIMIT 1000""",
            (chunk,),
        )
        for r in rr:
            if r["match_id"] not in reasoning_by_match and r.get("reasoning"):
                reasoning_by_match[r["match_id"]] = r["reasoning"]

    # -- Batch load: odds_snapshots (1x2, earliest + latest per selection) ------
    odds_by_match: dict[str, list] = {}
    for chunk in _chunk_list(all_match_ids, 200):
        odr = execute_query(
            """SELECT match_id, selection, odds, timestamp
               FROM odds_snapshots
               WHERE match_id = ANY(%s::uuid[]) AND market = '1x2'
               ORDER BY timestamp ASC
               LIMIT 10000""",
            (chunk,),
        )
        for o in odr:
            odds_by_match.setdefault(o["match_id"], []).append(o)

    # -- Batch load: ELO (latest per team up to date_str) ----------------------
    elo_by_team: dict[str, float] = {}
    for chunk in _chunk_list(list(all_team_ids), 200):
        er = execute_query(
            """SELECT team_id, elo_rating, date
               FROM team_elo_daily
               WHERE team_id = ANY(%s::uuid[]) AND date <= %s
               ORDER BY date DESC
               LIMIT 5000""",
            (chunk, date_str),
        )
        for e in er:
            # Keep only the most recent per team (first seen due to desc order)
            if e["team_id"] not in elo_by_team:
                elo_by_team[e["team_id"]] = float(e["elo_rating"])

    # -- Batch load: form cache (latest per team up to date_str) ---------------
    form_by_team: dict[str, float] = {}
    for chunk in _chunk_list(list(all_team_ids), 200):
        fr = execute_query(
            """SELECT team_id, ppg, date
               FROM team_form_cache
               WHERE team_id = ANY(%s::uuid[]) AND date <= %s
               ORDER BY date DESC
               LIMIT 5000""",
            (chunk, date_str),
        )
        for f in fr:
            if f["team_id"] not in form_by_team:
                form_by_team[f["team_id"]] = f.get("ppg")

    # -- Batch load: match_signals ---------------------------------------------
    signals_by_match: dict[str, list] = {}
    for chunk in _chunk_list(all_match_ids, 200):
        sr = execute_query(
            """SELECT match_id, signal_name, signal_value, captured_at
               FROM match_signals
               WHERE match_id = ANY(%s::uuid[])
               ORDER BY captured_at DESC
               LIMIT 50000""",
            (chunk,),
        )
        for s in sr:
            signals_by_match.setdefault(s["match_id"], []).append(s)

    # -- Build rows from cached data -------------------------------------------
    upserted = 0
    batch_rows = []

    build_errors = 0
    for match in matches:
        try:
            row = _build_feature_row_batched(
                match, league_tier_map, preds_by_match,
                reasoning_by_match, odds_by_match, elo_by_team,
                form_by_team, signals_by_match,
            )
            if row:
                batch_rows.append(row)
        except Exception as e:
            build_errors += 1
            console.print(f"  [yellow]feature row error ({match.get('id', '?')}): {e}[/yellow]")

    if build_errors:
        console.print(f"  [yellow]⚠ {build_errors}/{len(matches)} matches failed to build feature row[/yellow]")

    # Upsert in batches of 50
    for chunk in _chunk_list(batch_rows, 50):
        try:
            if not chunk:
                continue
            columns = list(chunk[0].keys())
            conflict_cols = ["match_id"]
            update_cols = [c for c in columns if c != "match_id"]
            tuples = [tuple(row.get(c) for c in columns) for row in chunk]
            upserted += bulk_upsert(
                "match_feature_vectors", columns, tuples, conflict_cols, update_cols
            )
        except Exception as e:
            console.print(f"  [yellow]bulk upsert failed ({len(chunk)} rows), falling back one-by-one: {e}[/yellow]")
            # Fall back to one-by-one
            for row in chunk:
                try:
                    columns = list(row.keys())
                    conflict_cols = ["match_id"]
                    update_cols = [c for c in columns if c != "match_id"]
                    tuples = [tuple(row.get(c) for c in columns)]
                    bulk_upsert(
                        "match_feature_vectors", columns, tuples, conflict_cols, update_cols
                    )
                    upserted += 1
                except Exception as e2:
                    console.print(f"  [yellow]single row upsert failed ({row.get('match_id', '?')}): {e2}[/yellow]")

    return upserted


def _chunk_list(lst: list, size: int) -> list[list]:
    """Split list into chunks of given size."""
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def _build_feature_row_batched(
    match: dict,
    league_tier_map: dict,
    preds_by_match: dict,
    reasoning_by_match: dict,
    odds_by_match: dict,
    elo_by_team: dict,
    form_by_team: dict,
    signals_by_match: dict,
) -> dict | None:
    """Build a single match_feature_vectors row from pre-loaded batch data."""
    match_id = match["id"]
    _d = match.get("date")
    match_date = _d.isoformat()[:10] if hasattr(_d, "isoformat") else str(_d)[:10] if _d else None

    # -- Outcome labels --------------------------------------------------------
    outcome = match.get("result")
    score_home = match.get("score_home")
    score_away = match.get("score_away")
    total_goals = None
    over_25 = None
    if score_home is not None and score_away is not None:
        total_goals = int(score_home) + int(score_away)
        over_25 = total_goals > 2

    # -- League tier -----------------------------------------------------------
    league_tier = league_tier_map.get(match.get("league_id"))

    # -- Predictions -----------------------------------------------------------
    ens_home = pois_home = xgb_home = af_home = None
    model_disagreement = None

    for p in preds_by_match.get(match_id, []):
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

    if ens_home is None:
        ens_home = pois_home or xgb_home or af_home

    # -- Data tier -------------------------------------------------------------
    data_tier = None
    reasoning = reasoning_by_match.get(match_id)
    if reasoning:
        for t in ("A", "B", "C", "D"):
            if f"tier={t}" in reasoning or f"Tier {t}" in reasoning or f"data_tier={t}" in reasoning:
                data_tier = t
                break

    # -- Opening/closing implied odds ------------------------------------------
    opening_implied_home = opening_implied_draw = opening_implied_away = None
    odds_drift_home = steam_move = None

    snaps = odds_by_match.get(match_id, [])
    # Group by selection, already ordered by timestamp asc
    snaps_by_sel: dict[str, list] = {}
    for s in snaps:
        snaps_by_sel.setdefault(s["selection"].lower(), []).append(s)

    for sel, attr in [("home", "opening_implied_home"),
                      ("draw", "opening_implied_draw"),
                      ("away", "opening_implied_away")]:
        sel_snaps = snaps_by_sel.get(sel, [])
        if sel_snaps:
            opening_odds = float(sel_snaps[0]["odds"])
            opening_implied = round(1.0 / opening_odds, 4) if opening_odds > 1.0 else None

            if sel == "home":
                opening_implied_home = opening_implied
                if len(sel_snaps) >= 2 and opening_implied:
                    closing_odds = float(sel_snaps[-1]["odds"])
                    if closing_odds > 1.0:
                        closing_implied = 1.0 / closing_odds
                        odds_drift_home = round(closing_implied - opening_implied, 5)
                        steam_move = abs(odds_drift_home) > 0.03
            elif sel == "draw":
                opening_implied_draw = opening_implied
            elif sel == "away":
                opening_implied_away = opening_implied

    # -- ELO -------------------------------------------------------------------
    home_team_id = match.get("home_team_id")
    away_team_id = match.get("away_team_id")
    elo_home = elo_by_team.get(home_team_id)
    elo_away = elo_by_team.get(away_team_id)
    elo_diff = round(elo_home - elo_away, 2) if elo_home is not None and elo_away is not None else None

    # -- Form ------------------------------------------------------------------
    form_ppg_home = form_by_team.get(home_team_id)
    form_ppg_away = form_by_team.get(away_team_id)

    # -- Signals ---------------------------------------------------------------
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
    market_implied_home = market_implied_draw = market_implied_away = None

    match_signals = signals_by_match.get(match_id, [])
    seen_signals: set[str] = set()
    for sig in match_signals:
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
            elif name == "market_implied_home":
                market_implied_home = fval
            elif name == "market_implied_draw":
                market_implied_draw = fval
            elif name == "market_implied_away":
                market_implied_away = fval

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
        "market_implied_home": market_implied_home,
        "market_implied_draw": market_implied_draw,
        "market_implied_away": market_implied_away,
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

def _sanitize_for_json(value):
    """Convert numpy types to native Python and replace NaN/Infinity with None."""
    import math
    if value is None:
        return None
    # numpy scalar -> native Python
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    return value


def store_bet(bot_id: str, match_id: str, bet_data: dict) -> str | None:
    """
    Store a paper bet.
    Returns the bet UUID, or None if this bet already exists (idempotent).

    Supports new model improvement fields (migration 006):
    - calibrated_prob, kelly_fraction, odds_at_open, odds_drift
    - dimension_scores, alignment_count, alignment_total, alignment_class
    - model_disagreement, news_impact_score, lineup_confirmed
    """
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

    # Model improvement fields (P1-P4, migration 006) + BOT-TIMING (migration 032)
    optional_fields = [
        "calibrated_prob", "kelly_fraction",
        "odds_at_open", "odds_drift",
        "dimension_scores", "alignment_count", "alignment_total", "alignment_class",
        "model_disagreement", "news_impact_score", "lineup_confirmed",
        "timing_cohort",
        "xg_source",  # inplay bots: 'live' | 'shot_proxy' (migration 057)
    ]
    for field in optional_fields:
        if field in bet_data and bet_data[field] is not None:
            row[field] = bet_data[field]

    # Sanitize all values: numpy types -> native Python, NaN/Inf -> None
    row = {k: _sanitize_for_json(v) for k, v in row.items()}

    # Wrap JSONB fields
    if "dimension_scores" in row and row["dimension_scores"] is not None:
        row["dimension_scores"] = Json(row["dimension_scores"])

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    values = tuple(row[c] for c in columns)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"INSERT INTO simulated_bets ({col_str}) VALUES ({placeholders}) RETURNING id",
                    values,
                )
                conn.commit()
                new_row = cur.fetchone()
                return new_row["id"]
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
        row["metadata"] = Json(metadata)

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    values = tuple(row[c] for c in columns)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"INSERT INTO prediction_snapshots ({col_str}) VALUES ({placeholders}) RETURNING id",
                    values,
                )
                conn.commit()
                return cur.fetchone()["id"]
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return None  # stage already recorded
        raise


def store_match_stats(match_id: str, stats: dict):
    """
    Store final match stats (xG, shots, possession, corners, cards).
    Uses upsert -- safe to call multiple times for the same match.
    """
    row = {"match_id": match_id}
    field_map = {
        "xg_home": "xg_home", "xg_away": "xg_away",
        "shots_home": "shots_home", "shots_away": "shots_away",
        "shots_on_target_home": "shots_on_target_home",
        "shots_on_target_away": "shots_on_target_away",
        "possession_home": "possession_home",
        "corners_home": "corners_home", "corners_away": "corners_away",
        "fouls_home": "fouls_home", "fouls_away": "fouls_away",
        "offsides_home": "offsides_home", "offsides_away": "offsides_away",
        "saves_home": "saves_home", "saves_away": "saves_away",
        "blocked_shots_home": "blocked_shots_home",
        "blocked_shots_away": "blocked_shots_away",
        "passes_home": "passes_home", "passes_away": "passes_away",
        "pass_accuracy_home": "pass_accuracy_home",
        "pass_accuracy_away": "pass_accuracy_away",
        "yellow_cards_home": "yellow_cards_home",
        "yellow_cards_away": "yellow_cards_away",
        "red_cards_home": "red_cards_home",
        "red_cards_away": "red_cards_away",
    }
    for src, dst in field_map.items():
        if src in stats and stats[src] is not None:
            row[dst] = stats[src]

    if len(row) <= 1:
        return  # no stats to store

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c != "match_id"]
    update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    values = tuple(row[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO match_stats ({col_str}) VALUES ({placeholders})
                    ON CONFLICT (match_id) DO UPDATE SET {update_str}""",
                values,
            )
            conn.commit()


def store_team_elo(team_id: str, elo_date: str, elo_rating: float):
    """
    Store or update a team's ELO rating for a given date.
    Uses upsert on (team_id, date) constraint.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO team_elo_daily (team_id, date, elo_rating)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (team_id, date) DO UPDATE SET elo_rating = EXCLUDED.elo_rating""",
                (team_id, elo_date, round(elo_rating, 2)),
            )
            conn.commit()


def store_team_form(team_id: str, form_date: str, form: dict):
    """
    Store or update cached form metrics for a team on a given date.
    Uses upsert on (team_id, date) constraint.
    """
    row = {"team_id": team_id, "date": form_date}
    for key in ["matches_played", "win_pct", "draw_pct", "loss_pct", "ppg",
                "goals_scored_avg", "goals_conceded_avg", "goal_diff_avg",
                "clean_sheet_pct", "over25_pct", "btts_pct"]:
        if key in form and form[key] is not None:
            row[key] = form[key]

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c not in ("team_id", "date")]
    update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    values = tuple(row[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO team_form_cache ({col_str}) VALUES ({placeholders})
                    ON CONFLICT (team_id, date) DO UPDATE SET {update_str}""",
                values,
            )
            conn.commit()


# ============================================================
# T2: TEAM SEASON STATISTICS
# ============================================================

def store_team_season_stats(team_api_id: int, league_api_id: int, season: int,
                             parsed: dict) -> str | None:
    """
    Store or update team season stats. Upserts on (team_api_id, league_api_id, season, fetched_date).
    Returns row id or None on error.
    """
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

    # JSONB fields need wrapping
    jsonb_fields = {"formations_jsonb", "yellow_cards_by_minute", "red_cards_by_minute",
                    "goals_for_by_minute", "goals_against_by_minute", "raw"}

    for f in fields:
        if f in parsed and parsed[f] is not None:
            if f in jsonb_fields:
                row[f] = Json(parsed[f])
            else:
                row[f] = parsed[f]

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c not in ("team_api_id", "league_api_id", "season", "fetched_date")]
    update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    values = tuple(row[c] for c in columns)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"""INSERT INTO team_season_stats ({col_str}) VALUES ({placeholders})
                        ON CONFLICT (team_api_id, league_api_id, season, fetched_date)
                        DO UPDATE SET {update_str}
                        RETURNING id""",
                    values,
                )
                conn.commit()
                result = cur.fetchone()
                return result["id"] if result else None
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            return None
        raise


def get_team_season_stats(team_api_id: int, season: int) -> dict | None:
    """Get the most recent team season stats for a team/season."""
    rows = execute_query(
        """SELECT * FROM team_season_stats
           WHERE team_api_id = %s AND season = %s
           ORDER BY fetched_date DESC
           LIMIT 1""",
        (team_api_id, season),
    )
    return rows[0] if rows else None


# ============================================================
# T3: MATCH INJURIES
# ============================================================

def store_match_injuries(match_id: str, af_fixture_id: int,
                          injuries: list[dict]) -> int:
    """
    Store injuries for a match. Upserts on (match_id, player_id).
    Returns count of rows stored.
    """
    stored = 0

    for inj in injuries:
        if not inj.get("player_id"):
            continue
        row = {
            "match_id": match_id,
            "af_fixture_id": af_fixture_id,
        }
        allowed = {"team_api_id", "team_side", "player_id", "player_name",
                    "player_type", "status", "reason", "raw"}
        for k, v in inj.items():
            if k in allowed:
                if k == "raw":
                    row[k] = Json(v) if v is not None else None
                else:
                    row[k] = v

        columns = list(row.keys())
        col_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        update_cols = [c for c in columns if c not in ("match_id", "player_id")]
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        values = tuple(row[c] for c in columns)

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO match_injuries ({col_str}) VALUES ({placeholders})
                            ON CONFLICT (match_id, player_id) DO UPDATE SET {update_str}""",
                        values,
                    )
                    conn.commit()
            stored += 1
        except Exception as e:
            console.print(f"[yellow]store_match_injuries: {e}[/yellow]")

    return stored


# ============================================================
# T4: MATCH STATS (half-time extension)
# ============================================================

def store_match_stats_full(match_id: str, stats: dict):
    """
    Extended version of store_match_stats -- stores all fields including
    half-time stats (_ht suffix) and full-match fields (fouls, saves, etc.).
    Uses upsert -- safe to call multiple times.
    """
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
        "blocked_shots_home", "blocked_shots_away",
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

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    update_cols = [c for c in columns if c != "match_id"]
    update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    values = tuple(row[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO match_stats ({col_str}) VALUES ({placeholders})
                    ON CONFLICT (match_id) DO UPDATE SET {update_str}""",
                values,
            )
            conn.commit()


# ============================================================
# T5: LIVE ODDS STORAGE
# ============================================================

def store_live_odds(match_id: str, odds_rows: list[dict], minute: int = None):
    """
    Store live in-play odds in odds_snapshots with is_live=true.
    Called every 5min during live matches.
    """
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for r in odds_rows:
        rows.append((
            match_id,
            r.get("bookmaker", "api-football-live"),
            r["market"],
            r["selection"],
            r["odds"],
            now,
            True,
            False,
            r.get("minute"),  # minute elapsed during match
        ))

    if rows:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    psycopg2.extras.execute_values(
                        cur,
                        """INSERT INTO odds_snapshots
                           (match_id, bookmaker, market, selection, odds,
                            timestamp, is_live, is_closing, minutes_to_kickoff)
                           VALUES %s""",
                        rows,
                        page_size=500,
                    )
                    conn.commit()
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
    updates = {}
    for field in ["formation_home", "formation_away", "coach_home", "coach_away",
                  "lineups_home", "lineups_away"]:
        if lineups_parsed.get(field) is not None:
            if field in ("lineups_home", "lineups_away"):
                updates[field] = Json(lineups_parsed[field])
            else:
                updates[field] = lineups_parsed[field]

    if updates:
        updates["lineups_fetched_at"] = datetime.now(timezone.utc).isoformat()
        set_clauses = ", ".join(f"{k} = %s" for k in updates)
        params = list(updates.values()) + [match_id]
        execute_write(
            f"UPDATE matches SET {set_clauses} WHERE id = %s",
            tuple(params),
        )


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
    stored = 0

    for ev in events:
        # Resolve home/away from team_api_id
        team_side = "unknown"
        if home_team_api_id and ev.get("team_api_id"):
            team_side = "home" if ev["team_api_id"] == home_team_api_id else "away"

        row = (
            match_id,
            ev.get("minute", 0),
            ev.get("added_time", 0),
            ev["event_type"],
            team_side,
            ev.get("player_name"),
            ev.get("detail"),
            ev.get("af_event_order"),
            datetime.now(timezone.utc).isoformat(),
        )

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO match_events
                           (match_id, minute, added_time, event_type, team,
                            player_name, detail, af_event_order, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (match_id, af_event_order) DO UPDATE SET
                            minute = EXCLUDED.minute,
                            added_time = EXCLUDED.added_time,
                            event_type = EXCLUDED.event_type,
                            team = EXCLUDED.team,
                            player_name = EXCLUDED.player_name,
                            detail = EXCLUDED.detail""",
                        row,
                    )
                    conn.commit()
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
    today = date.today().isoformat()
    stored = 0

    for r in rows:
        row = {
            "league_api_id": league_api_id,
            "season": season,
            "fetched_date": today,
        }
        allowed = {
            "team_api_id", "team_name", "rank", "points", "goals_diff",
            "group_name", "form", "status", "description",
            "played", "wins", "draws", "losses", "goals_for", "goals_against",
            "home_played", "home_wins", "home_draws", "home_losses",
            "home_goals_for", "home_goals_against",
            "away_played", "away_wins", "away_draws", "away_losses",
            "away_goals_for", "away_goals_against",
            "raw",
        }
        for k, v in r.items():
            if k in allowed:
                if k == "raw":
                    row[k] = Json(v) if v is not None else None
                else:
                    row[k] = v

        columns = list(row.keys())
        col_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        update_cols = [c for c in columns if c not in ("league_api_id", "season", "fetched_date", "team_api_id")]
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        values = tuple(row[c] for c in columns)

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO league_standings ({col_str}) VALUES ({placeholders})
                            ON CONFLICT (league_api_id, season, fetched_date, team_api_id)
                            DO UPDATE SET {update_str}""",
                        values,
                    )
                    conn.commit()
            stored += 1
        except Exception as e:
            console.print(f"[yellow]store_league_standings: {e}[/yellow]")

    return stored


# ============================================================
# T10: H2H
# ============================================================

def store_match_h2h(match_id: str, h2h_parsed: dict):
    """Store H2H data on the matches table."""
    updates = {}
    for field in ["h2h_raw", "h2h_home_wins", "h2h_draws", "h2h_away_wins"]:
        if h2h_parsed.get(field) is not None:
            if field == "h2h_raw":
                updates[field] = Json(h2h_parsed[field])
            else:
                updates[field] = h2h_parsed[field]

    if updates:
        set_clauses = ", ".join(f"{k} = %s" for k in updates)
        params = list(updates.values()) + [match_id]
        execute_write(
            f"UPDATE matches SET {set_clauses} WHERE id = %s",
            tuple(params),
        )


# ============================================================
# T11: PLAYER SIDELINED
# ============================================================

def store_player_sidelined(rows: list[dict]) -> int:
    """Store player sidelined history. Upserts on (player_id, start_date, type)."""
    stored = 0

    for row_data in rows:
        if not row_data.get("player_id") or not row_data.get("start_date"):
            continue

        columns = list(row_data.keys())
        col_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        update_cols = [c for c in columns if c not in ("player_id", "start_date", "type")]
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols) if update_cols else "player_id = EXCLUDED.player_id"
        values = tuple(row_data[c] for c in columns)

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO player_sidelined ({col_str}) VALUES ({placeholders})
                            ON CONFLICT (player_id, start_date, type)
                            DO UPDATE SET {update_str}""",
                        values,
                    )
                    conn.commit()
            stored += 1
        except Exception as e:
            console.print(f"[yellow]store_player_sidelined: {e}[/yellow]")

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
    stored = 0

    for p in players:
        if not p.get("player_id"):
            continue
        row = {
            "match_id": match_id,
            "af_fixture_id": af_fixture_id,
        }
        allowed = {
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
        }
        for k, v in p.items():
            if k in allowed:
                if k == "raw":
                    row[k] = Json(v) if v is not None else None
                else:
                    row[k] = v

        columns = list(row.keys())
        col_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        update_cols = [c for c in columns if c not in ("match_id", "player_id")]
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        values = tuple(row[c] for c in columns)

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO match_player_stats ({col_str}) VALUES ({placeholders})
                            ON CONFLICT (match_id, player_id)
                            DO UPDATE SET {update_str}""",
                        values,
                    )
                    conn.commit()
            stored += 1
        except Exception as e:
            console.print(f"[yellow]store_match_player_stats: {e}[/yellow]")

    return stored


# ============================================================
# T13: TEAM TRANSFERS
# ============================================================

def store_team_transfers(team_api_id: int, rows: list[dict]) -> int:
    """Store team transfer records. Upserts on (team_api_id, player_id, transfer_date)."""
    stored = 0

    for row_data in rows:
        if not row_data.get("player_id") or not row_data.get("transfer_date"):
            continue

        columns = list(row_data.keys())
        col_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        update_cols = [c for c in columns if c not in ("team_api_id", "player_id", "transfer_date")]
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols) if update_cols else "team_api_id = EXCLUDED.team_api_id"
        values = tuple(row_data[c] for c in columns)

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO team_transfers ({col_str}) VALUES ({placeholders})
                            ON CONFLICT (team_api_id, player_id, transfer_date)
                            DO UPDATE SET {update_str}""",
                        values,
                    )
                    conn.commit()
            stored += 1
        except Exception as e:
            console.print(f"[yellow]store_team_transfers: {e}[/yellow]")

    return stored


def store_model_evaluation(eval_date: str, league_id: str | None, market: str,
                           total_bets: int, hits: int, roi: float,
                           avg_clv: float | None, notes: str | None = None):
    """Store daily model evaluation metrics per league/market."""
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

    columns = list(row.keys())
    col_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    values = tuple(row[c] for c in columns)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO model_evaluations ({col_str}) VALUES ({placeholders})",
                values,
            )
            conn.commit()


def compute_market_implied_strength(team_id: str, window: int = 5) -> float | None:
    """
    Compute rolling average of a team's market-implied win probability
    from recent odds snapshots. The market's recent pricing of a team
    is a strong indicator of true team strength (especially in Tier 1-2).

    Returns average implied win probability (0.0-1.0) or None if insufficient data.
    See MODEL_ANALYSIS.md Section 11.3.
    """
    # Get last N home and last N away finished matches — 2 queries
    home_matches = execute_query(
        """SELECT id FROM matches
           WHERE home_team_id = %s AND status = 'finished'
           ORDER BY date DESC LIMIT %s""",
        (team_id, window),
    )
    away_matches = execute_query(
        """SELECT id FROM matches
           WHERE away_team_id = %s AND status = 'finished'
           ORDER BY date DESC LIMIT %s""",
        (team_id, window),
    )

    home_ids = [m["id"] for m in home_matches]
    away_ids = [m["id"] for m in away_matches]
    all_ids = home_ids + away_ids
    if not all_ids:
        return None

    # Batch-fetch latest 1x2 odds for all matches in one query — was N+1
    odds_rows = execute_query(
        """SELECT DISTINCT ON (match_id, selection)
               match_id, selection, odds
           FROM odds_snapshots
           WHERE match_id = ANY(%s::uuid[])
             AND market = '1x2'
             AND selection IN ('home', 'away')
             AND odds > 1.0
           ORDER BY match_id, selection, timestamp DESC""",
        (all_ids,),
    )
    odds_lookup: dict[tuple[str, str], float] = {
        (r["match_id"], r["selection"]): float(r["odds"]) for r in odds_rows
    }

    implied_probs = []
    for m_id in home_ids:
        o = odds_lookup.get((m_id, "home"))
        if o:
            implied_probs.append(1.0 / o)
    for m_id in away_ids:
        o = odds_lookup.get((m_id, "away"))
        if o:
            implied_probs.append(1.0 / o)

    if len(implied_probs) < 3:
        return None

    # Return average implied win probability (most recent N matches)
    return round(sum(implied_probs[:window]) / min(len(implied_probs), window), 4)


def compute_team_form_from_db(team_id: str, as_of_date: str, window: int = 10) -> dict | None:
    """
    Compute rolling form metrics for a team from recent finished matches in DB.
    Returns form dict or None if insufficient data.
    """
    # Get last N finished matches involving this team
    home_matches = execute_query(
        """SELECT score_home, score_away FROM matches
           WHERE home_team_id = %s AND status = 'finished'
             AND date < %s
           ORDER BY date DESC LIMIT %s""",
        (team_id, f"{as_of_date}T23:59:59", window),
    )

    away_matches = execute_query(
        """SELECT score_home, score_away FROM matches
           WHERE away_team_id = %s AND status = 'finished'
             AND date < %s
           ORDER BY date DESC LIMIT %s""",
        (team_id, f"{as_of_date}T23:59:59", window),
    )

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
    execute_write(
        """UPDATE simulated_bets
           SET result = %s, pnl = %s, bankroll_after = %s
           WHERE id = %s""",
        (result, pnl, bankroll_after, bet_id),
    )


def get_pending_bets() -> list[dict]:
    """Get all pending (unsettled) bets with match data"""
    rows = execute_query(
        """SELECT sb.*,
                  m.date AS match_date, m.home_team_id, m.away_team_id,
                  m.score_home AS match_score_home, m.score_away AS match_score_away,
                  m.result AS match_result, m.status AS match_status
           FROM simulated_bets sb
           JOIN matches m ON sb.match_id = m.id
           WHERE sb.result = 'pending'""",
    )

    # Restructure to match PostgREST format with nested matches dict
    result = []
    for r in rows:
        row = dict(r)
        row["matches"] = {
            "date": row.pop("match_date"),
            "home_team_id": row.pop("home_team_id"),
            "away_team_id": row.pop("away_team_id"),
            "score_home": row.pop("match_score_home"),
            "score_away": row.pop("match_score_away"),
            "result": row.pop("match_result"),
            "status": row.pop("match_status"),
        }
        result.append(row)
    return result


def update_bot_bankroll(bot_id: str, new_bankroll: float):
    """Update a bot's current bankroll"""
    execute_write(
        "UPDATE bots SET current_bankroll = %s WHERE id = %s",
        (new_bankroll, bot_id),
    )


# ============================================================
# MATCH RESULTS
# ============================================================

def update_match_result(match_id: str, home_goals: int, away_goals: int):
    """Update a match with its final score"""
    result = "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"

    execute_write(
        """UPDATE matches
           SET score_home = %s, score_away = %s, result = %s, status = 'finished'
           WHERE id = %s""",
        (home_goals, away_goals, result, match_id),
    )


# ============================================================
# QUERIES (for reporting)
# ============================================================

def get_bot_performance(bot_name: str = None) -> list[dict]:
    """Get performance summary for bots"""
    if bot_name:
        bot_rows = execute_query(
            "SELECT id FROM bots WHERE name = %s", (bot_name,)
        )
        if bot_rows:
            return execute_query(
                "SELECT * FROM simulated_bets WHERE bot_id = %s AND result != 'pending'",
                (bot_rows[0]["id"],),
            )
        return []

    return execute_query(
        "SELECT * FROM simulated_bets WHERE result != 'pending'"
    )


def get_todays_matches() -> list[dict]:
    """Get today's matches"""
    today = date.today().isoformat()

    rows = execute_query(
        """SELECT m.*,
                  l.name AS league_name, l.country AS league_country, l.tier AS league_tier
           FROM matches m
           LEFT JOIN leagues l ON m.league_id = l.id
           WHERE m.date >= %s AND m.date <= %s""",
        (f"{today}T00:00:00", f"{today}T23:59:59"),
    )

    # Restructure to match PostgREST format with nested leagues dict
    result = []
    for r in rows:
        row = dict(r)
        row["leagues"] = {
            "name": row.pop("league_name"),
            "country": row.pop("league_country"),
            "tier": row.pop("league_tier"),
        }
        result.append(row)
    return result


# ============================================================
# S3 / S4 / S5 / BDM-1: MORNING SIGNAL WIRING
# ============================================================

def compute_bookmaker_disagreement(match_id: str) -> float | None:
    """
    BDM-1: max(implied_prob) - min(implied_prob) across bookmakers for home 1x2.
    Uses the most recent snapshot per bookmaker. Requires >=2 distinct bookmakers.
    """
    rows = execute_query(
        """SELECT bookmaker, odds, timestamp
           FROM odds_snapshots
           WHERE match_id = %s AND market = '1x2' AND selection = 'home'
             AND bookmaker IS NOT NULL
           ORDER BY timestamp DESC
           LIMIT 200""",
        (match_id,),
    )

    if not rows:
        return None

    # Latest odds per bookmaker
    seen: dict[str, float] = {}
    for row in rows:
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
    Returns 0.0-1.0. High = title/relegation 6-pointer.
    """
    if not (league_api_id and season and home_team_api_id and away_team_api_id):
        return None

    rows = execute_query(
        """SELECT team_api_id, rank, points, played, description, status
           FROM league_standings
           WHERE league_api_id = %s AND season = %s
           ORDER BY fetched_date DESC
           LIMIT 40""",
        (league_api_id, season),
    )

    if not rows:
        return None

    # Deduplicate: latest entry per team
    by_team: dict[int, dict] = {}
    for row in rows:
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
    rows = execute_query(
        "SELECT cards_per_game FROM referee_stats WHERE referee_name = %s",
        (referee_name,),
    )
    if rows:
        return rows[0].get("cards_per_game")
    return None


def build_referee_stats() -> int:
    """
    S4: (Re)compute referee_stats from all finished matches.
    Called from backfill_referee_stats.py and optionally from settlement.
    Returns number of referees upserted.
    """
    # Fetch all finished matches with referee name and score
    matches_r = execute_query(
        """SELECT id, referee, result, score_home, score_away
           FROM matches
           WHERE status = 'finished' AND referee IS NOT NULL""",
    )

    if not matches_r:
        return 0

    from collections import defaultdict
    stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "home": 0, "draw": 0, "away": 0,
        "over25": 0, "yellow": 0, "red": 0,
    })

    for m in matches_r:
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
    for m in matches_r:
        ref = m.get("referee", "").strip()
        if ref:
            match_ids_by_ref[ref].append(m["id"])

    # Fetch card totals from match_stats
    for ref, mids in match_ids_by_ref.items():
        # Batch queries -- 100 at a time
        for i in range(0, len(mids), 100):
            batch = mids[i:i + 100]
            cards_r = execute_query(
                """SELECT yellow_cards_home, yellow_cards_away,
                          red_cards_home, red_cards_away
                   FROM match_stats
                   WHERE match_id = ANY(%s)""",
                (batch,),
            )
            for row in cards_r:
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

        columns = list(row.keys())
        col_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        update_cols = [c for c in columns if c != "referee_name"]
        update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        values = tuple(row[c] for c in columns)

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""INSERT INTO referee_stats ({col_str}) VALUES ({placeholders})
                            ON CONFLICT (referee_name) DO UPDATE SET {update_str}""",
                        values,
                    )
                    conn.commit()
            upserted += 1
        except Exception as e:
            console.print(f"[yellow]build_referee_stats: {e}[/yellow]")

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

    # -- Opening odds -> market implied probs -----------------------------------
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

    # -- BDM-1: Bookmaker disagreement -----------------------------------------
    try:
        bdm = compute_bookmaker_disagreement(match_id)
        if bdm is not None:
            store_match_signal(match_id, "bookmaker_disagreement",
                               bdm, "market", "derived", captured_at=now_str)
    except Exception:
        pass

    # -- S5: Fixture importance -------------------------------------------------
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

    # -- S4: Referee cards avg --------------------------------------------------
    try:
        if referee:
            cards_avg = get_referee_cards_avg(referee)
            if cards_avg is not None:
                store_match_signal(match_id, "referee_cards_avg",
                                   float(cards_avg), "context", "referee_stats",
                                   captured_at=now_str)
    except Exception:
        pass

    # -- Injury counts (from match_injuries already stored by T3) ---------------
    try:
        inj_rows = execute_query(
            "SELECT team_side, status FROM match_injuries WHERE match_id = %s",
            (match_id,),
        )
        if inj_rows:
            out_home = sum(1 for r in inj_rows
                          if r.get("team_side") == "home" and r.get("status") == "Missing Fixture")
            out_away = sum(1 for r in inj_rows
                          if r.get("team_side") == "away" and r.get("status") == "Missing Fixture")
            doubt_home = sum(1 for r in inj_rows
                            if r.get("team_side") == "home" and r.get("status") == "Questionable")
            doubt_away = sum(1 for r in inj_rows
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

    # -- ELO diff ---------------------------------------------------------------
    try:
        match_r = execute_query(
            "SELECT home_team_id, away_team_id, date FROM matches WHERE id = %s",
            (match_id,),
        )
        if match_r:
            m = match_r[0]
            match_date_val = m["date"]
            if isinstance(match_date_val, datetime):
                match_date_str = match_date_val.strftime("%Y-%m-%d")
            else:
                match_date_str = str(match_date_val)[:10] if match_date_val else date.today().isoformat()
            elo_home = elo_away = None
            for team_id, attr in [(m.get("home_team_id"), "elo_home"),
                                   (m.get("away_team_id"), "elo_away")]:
                if team_id:
                    r = execute_query(
                        """SELECT elo_rating FROM team_elo_daily
                           WHERE team_id = %s AND date <= %s
                           ORDER BY date DESC LIMIT 1""",
                        (team_id, match_date_str),
                    )
                    if r:
                        val = float(r[0]["elo_rating"])
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

            # -- Form PPG -------------------------------------------------------
            for team_id, signal_name in [
                (m.get("home_team_id"), "form_ppg_home"),
                (m.get("away_team_id"), "form_ppg_away"),
            ]:
                if team_id:
                    fr = execute_query(
                        """SELECT ppg FROM team_form_cache
                           WHERE team_id = %s AND date <= %s
                           ORDER BY date DESC LIMIT 1""",
                        (team_id, match_date_str),
                    )
                    if fr and fr[0].get("ppg") is not None:
                        store_match_signal(match_id, signal_name,
                                           float(fr[0]["ppg"]),
                                           "quality", "derived", captured_at=now_str)

            # -- SIG-9: Form slope (PPG trend: last 5 vs prior 5) ---------------
            for team_id, sig_name in [
                (m.get("home_team_id"), "form_slope_home"),
                (m.get("away_team_id"), "form_slope_away"),
            ]:
                if not team_id:
                    continue
                fm_r = execute_query(
                    """SELECT home_team_id, result FROM matches
                       WHERE (home_team_id = %s OR away_team_id = %s)
                         AND status = 'finished'
                         AND date < %s
                       ORDER BY date DESC LIMIT 10""",
                    (team_id, team_id, f"{match_date_str}T00:00:00"),
                )

                if not fm_r or len(fm_r) < 6:
                    continue

                def _pts(row: dict, tid: str) -> int | None:
                    res = row.get("result")
                    if not res:
                        return None
                    if res == "home":
                        return 3 if row.get("home_team_id") == tid else 0
                    if res == "away":
                        return 0 if row.get("home_team_id") == tid else 3
                    if res == "draw":
                        return 1
                    return None

                pts_list = [p for row in fm_r if (p := _pts(row, team_id)) is not None]
                if len(pts_list) < 6:
                    continue
                recent = pts_list[:5]
                prior = pts_list[5:min(10, len(pts_list))]
                if len(prior) < 3:
                    continue
                store_match_signal(match_id, sig_name,
                                   round(sum(recent) / len(recent) - sum(prior) / len(prior), 4),
                                   "quality", "derived", captured_at=now_str)
    except Exception:
        pass

    # -- S3b: Standings signals -------------------------------------------------
    try:
        if league_api_id and season and home_team_api_id and away_team_api_id:
            st_r = execute_query(
                """SELECT team_api_id, rank, points, description
                   FROM league_standings
                   WHERE league_api_id = %s AND season = %s
                   ORDER BY fetched_date DESC
                   LIMIT 200""",
                (league_api_id, season),
            )

            if st_r:
                # Deduplicate -- latest fetched_date first, keep first seen per team
                seen_tids: set = set()
                deduped: list = []
                for row in st_r:
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

                    # ML-3: Store form string (e.g. "WWDLW") directly on matches row
                    # Need to re-fetch standings with form column
                    st_form = execute_query(
                        """SELECT team_api_id, form
                           FROM league_standings
                           WHERE league_api_id = %s AND season = %s
                             AND team_api_id = ANY(%s)
                           ORDER BY fetched_date DESC
                           LIMIT 10""",
                        (league_api_id, season, [home_team_api_id, away_team_api_id]),
                    )
                    home_form = None
                    away_form = None
                    seen_form: set = set()
                    for row in st_form:
                        tid = row.get("team_api_id")
                        if tid in seen_form:
                            continue
                        seen_form.add(tid)
                        if row.get("form"):
                            if tid == home_team_api_id:
                                home_form = row["form"][:5]
                            elif tid == away_team_api_id:
                                away_form = row["form"][:5]

                    form_updates = {}
                    if home_form:
                        form_updates["form_home"] = home_form
                    if away_form:
                        form_updates["form_away"] = away_form
                    if form_updates:
                        try:
                            set_clauses = ", ".join(f"{k} = %s" for k in form_updates)
                            params = list(form_updates.values()) + [match_id]
                            execute_write(
                                f"UPDATE matches SET {set_clauses} WHERE id = %s",
                                tuple(params),
                            )
                        except Exception:
                            pass

    except Exception:
        pass

    # -- S3c: H2H win pct ------------------------------------------------------
    try:
        h2h_r = execute_query(
            "SELECT h2h_home_wins, h2h_draws, h2h_away_wins FROM matches WHERE id = %s",
            (match_id,),
        )
        if h2h_r:
            d = h2h_r[0]
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

    # -- S3d: Referee home win pct + over 2.5 pct -------------------------------
    try:
        if referee:
            ref_r = execute_query(
                "SELECT home_win_pct, over_25_pct FROM referee_stats WHERE referee_name = %s",
                (referee,),
            )
            if ref_r:
                hwp = ref_r[0].get("home_win_pct")
                o25p = ref_r[0].get("over_25_pct")
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

    # -- S3e: Overnight line move -----------------------------------------------
    try:
        today_date = date.today().isoformat()
        midnight_utc = f"{today_date}T00:00:00+00:00"

        yest_r = execute_query(
            """SELECT odds FROM odds_snapshots
               WHERE match_id = %s AND market = '1x2' AND selection = 'home'
                 AND timestamp < %s
               ORDER BY timestamp DESC LIMIT 1""",
            (match_id, midnight_utc),
        )

        today_r = execute_query(
            """SELECT odds FROM odds_snapshots
               WHERE match_id = %s AND market = '1x2' AND selection = 'home'
                 AND timestamp >= %s
               ORDER BY timestamp ASC LIMIT 1""",
            (match_id, midnight_utc),
        )

        if yest_r and today_r:
            last_yest = 1.0 / float(yest_r[0]["odds"])
            first_today = 1.0 / float(today_r[0]["odds"])
            store_match_signal(match_id, "overnight_line_move",
                               round(first_today - last_yest, 5),
                               "market", "derived", captured_at=now_str)
    except Exception:
        pass

    # -- S3f: Rest days home / away ---------------------------------------------
    try:
        match_r2 = execute_query(
            "SELECT home_team_id, away_team_id, date FROM matches WHERE id = %s",
            (match_id,),
        )
        if match_r2:
            m2 = match_r2[0]
            m2_date = m2.get("date")
            if isinstance(m2_date, datetime):
                match_date_str2 = m2_date.strftime("%Y-%m-%d")
            else:
                match_date_str2 = str(m2_date)[:10] if m2_date else ""
            if match_date_str2:
                for team_id, sig_name in [
                    (m2.get("home_team_id"), "rest_days_home"),
                    (m2.get("away_team_id"), "rest_days_away"),
                ]:
                    if not team_id:
                        continue
                    prev_r = execute_query(
                        """SELECT date FROM matches
                           WHERE (home_team_id = %s OR away_team_id = %s)
                             AND status = 'finished'
                             AND date < %s
                           ORDER BY date DESC LIMIT 1""",
                        (team_id, team_id, f"{match_date_str2}T00:00:00"),
                    )
                    if prev_r:
                        prev_date_val = prev_r[0]["date"]
                        if isinstance(prev_date_val, datetime):
                            prev_date_str = prev_date_val.strftime("%Y-%m-%d")
                        else:
                            prev_date_str = str(prev_date_val)[:10]
                        delta = date.fromisoformat(match_date_str2) - date.fromisoformat(prev_date_str)
                        store_match_signal(match_id, sig_name,
                                           float(delta.days), "quality", "derived",
                                           captured_at=now_str)
    except Exception:
        pass

    # -- T2: Season goals avg + SIG-8: venue-specific splits --------------------
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

                    # SIG-8: venue-specific avg -- home team's home stats, away team's away stats
                    if suffix == "home":
                        played = stats.get("played_home") or 0
                        gf = stats.get("goals_for_home")
                        ga = stats.get("goals_against_home")
                    else:
                        played = stats.get("played_away") or 0
                        gf = stats.get("goals_for_away")
                        ga = stats.get("goals_against_away")
                    if played >= 3:
                        if gf is not None:
                            store_match_signal(
                                match_id, f"goals_for_venue_{suffix}",
                                round(int(gf) / played, 3),
                                "quality", "team_season_stats", captured_at=now_str,
                            )
                        if ga is not None:
                            store_match_signal(
                                match_id, f"goals_against_venue_{suffix}",
                                round(int(ga) / played, 3),
                                "quality", "team_season_stats", captured_at=now_str,
                            )
    except Exception:
        pass

    # -- SIG-7: Fixture importance per team + asymmetry -------------------------
    try:
        if league_api_id and season and home_team_api_id and away_team_api_id:
            st7 = execute_query(
                """SELECT team_api_id, rank, description
                   FROM league_standings
                   WHERE league_api_id = %s AND season = %s
                   ORDER BY fetched_date DESC
                   LIMIT 200""",
                (league_api_id, season),
            )

            if st7:
                seen7: set = set()
                deduped7: list = []
                for row in st7:
                    tid = row.get("team_api_id")
                    if tid and tid not in seen7:
                        seen7.add(tid)
                        deduped7.append(row)
                total7 = len(deduped7)

                if total7 >= 4:
                    rel7 = max(1, total7 - 2)

                    def _urgency(row: dict) -> float:
                        rank = row.get("rank") or total7
                        desc = (row.get("description") or "").lower()
                        if rank <= 2 or "champion" in desc or "promot" in desc:
                            return 0.85
                        if rank >= rel7 or "relegate" in desc:
                            return 0.70
                        if rank <= 4 or "playoff" in desc or "play-off" in desc:
                            return 0.50
                        if rank / total7 < 0.35:
                            return 0.25
                        return 0.10

                    home7 = next((r for r in deduped7 if r.get("team_api_id") == home_team_api_id), None)
                    away7 = next((r for r in deduped7 if r.get("team_api_id") == away_team_api_id), None)
                    if home7 and away7:
                        urg_h = _urgency(home7)
                        urg_a = _urgency(away7)
                        store_match_signal(match_id, "fixture_importance_home",
                                           urg_h, "context", "derived", captured_at=now_str)
                        store_match_signal(match_id, "fixture_importance_away",
                                           urg_a, "context", "derived", captured_at=now_str)
                        store_match_signal(match_id, "importance_diff",
                                           round(urg_h - urg_a, 3),
                                           "context", "derived", captured_at=now_str)
    except Exception:
        pass

    # -- SIG-10: Odds volatility (std of implied prob over last 24h) ------------
    try:
        from statistics import stdev
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        vol_r = execute_query(
            """SELECT odds FROM odds_snapshots
               WHERE match_id = %s AND market = '1x2' AND selection = 'home'
                 AND is_live = false AND timestamp >= %s""",
            (match_id, cutoff),
        )
        if vol_r and len(vol_r) >= 3:
            implied = [1.0 / float(r["odds"]) for r in vol_r if float(r["odds"]) > 1.0]
            if len(implied) >= 3:
                store_match_signal(match_id, "odds_volatility",
                                   round(stdev(implied), 6),
                                   "market", "derived", captured_at=now_str)
    except Exception:
        pass

    # -- SIG-11: League meta-features (home win pct, draw pct, avg goals) -------
    try:
        lm_match = execute_query(
            "SELECT league_id FROM matches WHERE id = %s",
            (match_id,),
        )
        if lm_match:
            league_uuid = lm_match[0].get("league_id")
            if league_uuid:
                lm_r = execute_query(
                    """SELECT result, score_home, score_away
                       FROM matches
                       WHERE league_id = %s AND status = 'finished'
                         AND result IS NOT NULL
                       ORDER BY date DESC
                       LIMIT 200""",
                    (league_uuid,),
                )

                if lm_r and len(lm_r) >= 20:
                    total_lm = len(lm_r)
                    home_wins = sum(1 for r in lm_r if r.get("result") == "home")
                    draws_count = sum(1 for r in lm_r if r.get("result") == "draw")
                    goal_totals = [
                        int(r["score_home"]) + int(r["score_away"])
                        for r in lm_r
                        if r.get("score_home") is not None and r.get("score_away") is not None
                    ]
                    store_match_signal(match_id, "league_home_win_pct",
                                       round(home_wins / total_lm, 4),
                                       "context", "derived", captured_at=now_str)
                    store_match_signal(match_id, "league_draw_pct",
                                       round(draws_count / total_lm, 4),
                                       "context", "derived", captured_at=now_str)
                    if goal_totals:
                        store_match_signal(match_id, "league_avg_goals",
                                           round(sum(goal_totals) / len(goal_totals), 3),
                                           "context", "derived", captured_at=now_str)
    except Exception:
        pass


def batch_write_morning_signals(matches: list[dict]) -> int:
    """
    High-performance batch replacement for write_morning_signals.
    Processes ALL matches in ~10 bulk DB queries instead of 25-40 queries per match.
    Returns total number of signal rows inserted.
    """
    if not matches:
        return 0

    from psycopg2.extras import execute_values
    from statistics import stdev
    from datetime import timedelta
    from collections import defaultdict

    now_str = datetime.now(timezone.utc).isoformat()
    today_str = date.today().isoformat()

    match_ids = [m["id"] for m in matches]

    # Collect unique IDs for bulk lookups
    all_team_uuids = list({
        tid for m in matches
        for tid in [m.get("home_team_id"), m.get("away_team_id")]
        if tid
    })
    all_referee_names = list({m["referee"] for m in matches if m.get("referee")})
    all_league_api_ids = list({m["league_api_id"] for m in matches if m.get("league_api_id")})
    all_seasons = list({m["season"] for m in matches if m.get("season")})
    all_home_api_ids = [m["home_team_api_id"] for m in matches if m.get("home_team_api_id")]
    all_away_api_ids = [m["away_team_api_id"] for m in matches if m.get("away_team_api_id")]
    all_team_api_ids = list(set(all_home_api_ids + all_away_api_ids))
    all_league_uuids = list({m.get("league_id") for m in matches if m.get("league_id")})

    signals: list[tuple] = []  # (match_id, signal_name, value, group, source, captured_at)

    def add(mid: str, name: str, val, group: str, source: str):
        if val is None:
            return
        try:
            fval = float(val)
        except (TypeError, ValueError):
            return
        if fval != fval:  # NaN check
            return
        signals.append((mid, name, fval, group, source, now_str))

    # ── 1. Opening odds implied probs (no DB query) ──────────────────────────
    for m in matches:
        mid = m["id"]
        if m.get("odds_home", 0) > 1.0:
            add(mid, "market_implied_home", round(1.0 / m["odds_home"], 4), "market", "opening_odds")
        if m.get("odds_draw", 0) > 1.0:
            add(mid, "market_implied_draw", round(1.0 / m["odds_draw"], 4), "market", "opening_odds")
        if m.get("odds_away", 0) > 1.0:
            add(mid, "market_implied_away", round(1.0 / m["odds_away"], 4), "market", "opening_odds")

    # ── 2. H2H win pct (from match dict — no DB query) ───────────────────────
    for m in matches:
        mid = m["id"]
        hw = m.get("h2h_home_wins") or 0
        hd = m.get("h2h_draws") or 0
        ha = m.get("h2h_away_wins") or 0
        total = hw + hd + ha
        if total >= 3:
            add(mid, "h2h_win_pct", round(hw / total, 4), "quality", "af")
            add(mid, "h2h_total", float(total), "quality", "af")

    # ── 3. Odds snapshots: BDM-1 + overnight line move + volatility ──────────
    try:
        cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        midnight_utc = f"{today_str}T00:00:00+00:00"

        snap_rows = execute_query(
            """SELECT match_id, bookmaker, odds, timestamp
               FROM odds_snapshots
               WHERE match_id = ANY(%s::uuid[])
                 AND market = '1x2' AND selection = 'home'
                 AND bookmaker IS NOT NULL AND is_live = false
               ORDER BY match_id, timestamp DESC""",
            (match_ids,),
        )
        snaps_by_match: dict[str, list] = defaultdict(list)
        for row in snap_rows:
            snaps_by_match[str(row["match_id"])].append(row)

        # P5.1: Bookmaker sharpness classification
        # Tier 1 = sharp (Pinnacle, exchange) — prices closest to true prob
        # Tier 3 = soft (recreational) — higher margins, less informed
        _SHARP_BMS = {"Pinnacle", "Betfair Exchange", "Betfair", "Marathon Bet"}
        _SOFT_BMS = {"Bwin", "Unibet", "Sportingbet", "Betway", "NordicBet", "10Bet", "1xBet"}

        for mid, rows in snaps_by_match.items():
            # BDM-1: spread across bookmakers (latest per bm)
            seen_bm: dict[str, float] = {}
            for row in rows:
                bk = row.get("bookmaker")
                if bk and bk not in seen_bm and float(row["odds"]) > 1.0:
                    seen_bm[bk] = 1.0 / float(row["odds"])
            if len(seen_bm) >= 2:
                vals = list(seen_bm.values())
                add(mid, "bookmaker_disagreement", round(max(vals) - min(vals), 4), "market", "derived")

            # P5.1: Sharp consensus signal — sharp books vs soft books on home 1x2
            # Positive: sharp books price home HIGHER than soft → sharp money on home
            # Negative: sharp books fade home vs soft books
            sharp_probs = [v for k, v in seen_bm.items() if k in _SHARP_BMS]
            soft_probs = [v for k, v in seen_bm.items() if k in _SOFT_BMS]
            if sharp_probs and len(soft_probs) >= 2:
                sharp_avg = sum(sharp_probs) / len(sharp_probs)
                soft_avg = sum(soft_probs) / len(soft_probs)
                add(mid, "sharp_consensus_home", round(sharp_avg - soft_avg, 5), "market", "derived")

            # PIN-1: Pinnacle anchor signal — Pinnacle implied home probability
            # Positive anchor_home (model_prob - pinnacle_implied) = model rates home higher than Pinnacle
            # Near-zero = model agrees with Pinnacle (sharp market confirmation)
            # Negative = Pinnacle rates home much higher than model (model may be wrong)
            # Note: rows are already filtered to market='1x2' AND selection='home' by the bulk query above.
            pinnacle_rows = [
                r for r in rows
                if r.get("bookmaker") == "Pinnacle" and float(r.get("odds") or 0) > 1.0
            ]
            if pinnacle_rows:
                # Rows are sorted DESC by timestamp — first entry is most recent
                pin_implied = 1.0 / float(pinnacle_rows[0]["odds"])
                add(mid, "pinnacle_implied_home", round(pin_implied, 5), "market", "derived")

                # PIN-4: Pinnacle line movement (opening implied → current implied)
                # Positive = home shortened over time = sharp money backing home
                # Negative = home drifted = sharps fading home
                # Only meaningful when we have 2+ snapshots
                if len(pinnacle_rows) >= 2:
                    pin_open = 1.0 / float(pinnacle_rows[-1]["odds"])   # oldest
                    pin_current = 1.0 / float(pinnacle_rows[0]["odds"]) # most recent
                    add(mid, "pinnacle_line_move_home", round(pin_current - pin_open, 5), "market", "derived")

            # Overnight line move
            yest = [r for r in rows if str(r["timestamp"]) < midnight_utc]
            today_sorted = sorted(
                [r for r in rows if str(r["timestamp"]) >= midnight_utc],
                key=lambda r: r["timestamp"],
            )
            if yest and today_sorted:
                last_yest = 1.0 / float(yest[0]["odds"])  # rows are DESC
                first_today = 1.0 / float(today_sorted[0]["odds"])
                add(mid, "overnight_line_move", round(first_today - last_yest, 5), "market", "derived")

            # Odds volatility
            recent = [r for r in rows if str(r["timestamp"]) >= cutoff_24h]
            if len(recent) >= 3:
                implied = [1.0 / float(r["odds"]) for r in recent if float(r["odds"]) > 1.0]
                if len(implied) >= 3:
                    try:
                        add(mid, "odds_volatility", round(stdev(implied), 6), "market", "derived")
                    except Exception:
                        pass
    except Exception:
        pass

    # ── 3b. PIN-2: Pinnacle implied for draw / away / O/U markets ────────────
    try:
        for _market, _selection, _signal_name in [
            ("1x2",           "draw",  "pinnacle_implied_draw"),
            ("1x2",           "away",  "pinnacle_implied_away"),
            ("over_under_25", "over",  "pinnacle_implied_over25"),
            ("over_under_25", "under", "pinnacle_implied_under25"),
        ]:
            pin2_rows = execute_query(
                """SELECT DISTINCT ON (match_id) match_id, odds
                   FROM odds_snapshots
                   WHERE match_id = ANY(%s::uuid[])
                     AND market = %s AND selection = %s
                     AND bookmaker = 'Pinnacle'
                     AND odds > 1.0 AND is_live = false
                   ORDER BY match_id, timestamp DESC""",
                (match_ids, _market, _selection),
            )
            for row in pin2_rows:
                mid = str(row["match_id"])
                add(mid, _signal_name, round(1.0 / float(row["odds"]), 5), "market", "derived")

            # PIN-4: line movement for draw and away (same query, need oldest too)
            if _selection in ("draw", "away"):
                _move_name = f"pinnacle_line_move_{_selection}"
                pin2_open_rows = execute_query(
                    """SELECT DISTINCT ON (match_id) match_id, odds
                       FROM odds_snapshots
                       WHERE match_id = ANY(%s::uuid[])
                         AND market = %s AND selection = %s
                         AND bookmaker = 'Pinnacle'
                         AND odds > 1.0 AND is_live = false
                       ORDER BY match_id, timestamp ASC""",
                    (match_ids, _market, _selection),
                )
                open_by_match = {str(r["match_id"]): 1.0 / float(r["odds"]) for r in pin2_open_rows}
                for row in pin2_rows:
                    mid = str(row["match_id"])
                    if mid in open_by_match:
                        current = 1.0 / float(row["odds"])
                        opening = open_by_match[mid]
                        if abs(current - opening) > 1e-6:   # skip if same snapshot
                            add(mid, _move_name, round(current - opening, 5), "market", "derived")
    except Exception:
        pass

    # ── 4. Referee stats bulk query ───────────────────────────────────────────
    try:
        if all_referee_names:
            ref_rows = execute_query(
                "SELECT referee_name, cards_per_game, home_win_pct, over_25_pct "
                "FROM referee_stats WHERE referee_name = ANY(%s)",
                (all_referee_names,),
            )
            ref_by_name = {r["referee_name"]: r for r in ref_rows}
            for m in matches:
                mid = m["id"]
                r = ref_by_name.get(m.get("referee") or "")
                if r:
                    add(mid, "referee_cards_avg", r.get("cards_per_game"), "context", "referee_stats")
                    add(mid, "referee_home_win_pct", r.get("home_win_pct"), "context", "referee_stats")
                    add(mid, "referee_over25_pct", r.get("over_25_pct"), "context", "referee_stats")
    except Exception:
        pass

    # ── 5. Injuries bulk query ────────────────────────────────────────────────
    try:
        inj_rows = execute_query(
            "SELECT match_id, team_side, status FROM match_injuries WHERE match_id = ANY(%s::uuid[])",
            (match_ids,),
        )
        inj_by_match: dict[str, list] = defaultdict(list)
        for row in inj_rows:
            inj_by_match[str(row["match_id"])].append(row)
        for mid, rows in inj_by_match.items():
            out_h = sum(1 for r in rows if r.get("team_side") == "home" and r.get("status") == "Missing Fixture")
            out_a = sum(1 for r in rows if r.get("team_side") == "away" and r.get("status") == "Missing Fixture")
            dbt_h = sum(1 for r in rows if r.get("team_side") == "home" and r.get("status") == "Questionable")
            dbt_a = sum(1 for r in rows if r.get("team_side") == "away" and r.get("status") == "Questionable")
            if out_h + dbt_h > 0:
                add(mid, "injury_count_home", float(out_h + dbt_h), "information", "af")
                add(mid, "players_out_home", float(out_h), "information", "af")
            if out_a + dbt_a > 0:
                add(mid, "injury_count_away", float(out_a + dbt_a), "information", "af")
                add(mid, "players_out_away", float(out_a), "information", "af")
    except Exception:
        pass

    # ── 6. ELO bulk query ─────────────────────────────────────────────────────
    try:
        if all_team_uuids:
            elo_rows = execute_query(
                """SELECT DISTINCT ON (team_id) team_id, elo_rating
                   FROM team_elo_daily
                   WHERE team_id = ANY(%s::uuid[]) AND date <= %s
                   ORDER BY team_id, date DESC""",
                (all_team_uuids, today_str),
            )
            elo_by_team = {str(r["team_id"]): float(r["elo_rating"]) for r in elo_rows}
            for m in matches:
                mid = m["id"]
                elo_h = elo_by_team.get(m.get("home_team_id") or "")
                elo_a = elo_by_team.get(m.get("away_team_id") or "")
                if elo_h is not None:
                    add(mid, "elo_home", elo_h, "quality", "derived")
                if elo_a is not None:
                    add(mid, "elo_away", elo_a, "quality", "derived")
                if elo_h is not None and elo_a is not None:
                    add(mid, "elo_diff", round(elo_h - elo_a, 2), "quality", "derived")
    except Exception:
        pass

    # ── 7. Form PPG bulk query ────────────────────────────────────────────────
    try:
        if all_team_uuids:
            ppg_rows = execute_query(
                """SELECT DISTINCT ON (team_id) team_id, ppg
                   FROM team_form_cache
                   WHERE team_id = ANY(%s::uuid[]) AND date <= %s
                   ORDER BY team_id, date DESC""",
                (all_team_uuids, today_str),
            )
            ppg_by_team = {
                str(r["team_id"]): float(r["ppg"])
                for r in ppg_rows if r.get("ppg") is not None
            }
            for m in matches:
                mid = m["id"]
                ppg_h = ppg_by_team.get(m.get("home_team_id") or "")
                ppg_a = ppg_by_team.get(m.get("away_team_id") or "")
                if ppg_h is not None:
                    add(mid, "form_ppg_home", ppg_h, "quality", "derived")
                if ppg_a is not None:
                    add(mid, "form_ppg_away", ppg_a, "quality", "derived")
    except Exception:
        pass

    # ── 8. Historical matches: form slope + rest days ─────────────────────────
    try:
        if all_team_uuids:
            hist_rows = execute_query(
                """SELECT home_team_id, away_team_id, result, date
                   FROM matches
                   WHERE (home_team_id = ANY(%s::uuid[]) OR away_team_id = ANY(%s::uuid[]))
                     AND status = 'finished'
                     AND date < %s
                   ORDER BY date DESC
                   LIMIT 20000""",
                (all_team_uuids, all_team_uuids, f"{today_str}T00:00:00"),
            )

            # Per-team history: list of (date, pts) already in date DESC order
            team_hist: dict[str, list] = defaultdict(list)
            for row in hist_rows:
                hid = str(row["home_team_id"]) if row.get("home_team_id") else None
                aid = str(row["away_team_id"]) if row.get("away_team_id") else None
                res = row.get("result")
                dt = row.get("date")
                if hid:
                    pts = 3 if res == "home" else (1 if res == "draw" else (0 if res == "away" else None))
                    team_hist[hid].append((dt, pts))
                if aid:
                    pts = 3 if res == "away" else (1 if res == "draw" else (0 if res == "home" else None))
                    team_hist[aid].append((dt, pts))

            for m in matches:
                mid = m["id"]
                match_date_str = str(m.get("start_time", ""))[:10] or today_str

                for team_id, slope_sig, rest_sig in [
                    (m.get("home_team_id"), "form_slope_home", "rest_days_home"),
                    (m.get("away_team_id"), "form_slope_away", "rest_days_away"),
                ]:
                    if not team_id:
                        continue
                    hist = team_hist.get(team_id, [])
                    if not hist:
                        continue

                    # Rest days: days since last finished match
                    last_dt = hist[0][0]
                    if last_dt:
                        last_str = (
                            last_dt.strftime("%Y-%m-%d")
                            if isinstance(last_dt, datetime)
                            else str(last_dt)[:10]
                        )
                        try:
                            delta = date.fromisoformat(match_date_str) - date.fromisoformat(last_str)
                            add(mid, rest_sig, float(delta.days), "quality", "derived")
                        except (ValueError, TypeError):
                            pass

                    # Form slope: recent 5 vs prior 5 avg pts
                    pts_list = [p for _, p in hist[:10] if p is not None]
                    if len(pts_list) >= 6:
                        recent = pts_list[:5]
                        prior = pts_list[5:min(10, len(pts_list))]
                        if len(prior) >= 3:
                            add(
                                mid, slope_sig,
                                round(sum(recent) / len(recent) - sum(prior) / len(prior), 4),
                                "quality", "derived",
                            )
    except Exception:
        pass

    # ── 9. Standings: S3b + fixture importance + SIG-7 ────────────────────────
    try:
        if all_league_api_ids and all_seasons:
            st_rows = execute_query(
                """SELECT DISTINCT ON (league_api_id, season, team_api_id)
                          team_api_id, rank, points, description, form, league_api_id, season
                   FROM league_standings
                   WHERE league_api_id = ANY(%s) AND season = ANY(%s)
                   ORDER BY league_api_id, season, team_api_id, fetched_date DESC""",
                (all_league_api_ids, all_seasons),
            )

            # standings_map[(league_api_id, season)] = {team_api_id: row}
            standings_map: dict[tuple, dict] = defaultdict(dict)
            for row in st_rows:
                key = (row["league_api_id"], row["season"])
                standings_map[key][row["team_api_id"]] = row

            form_updates: list[tuple] = []  # (form_home, form_away, match_id) for batch UPDATE

            for m in matches:
                mid = m["id"]
                lai = m.get("league_api_id")
                seas = m.get("season")
                hat = m.get("home_team_api_id")
                aat = m.get("away_team_api_id")
                if not (lai and seas and hat and aat):
                    continue

                by_team = standings_map.get((lai, seas), {})
                if not by_team:
                    continue

                deduped = list(by_team.values())
                total_teams = len(deduped)
                if total_teams < 4:
                    continue

                rows_by_rank = sorted(deduped, key=lambda r: r.get("rank") or 99)
                leader_pts = rows_by_rank[0].get("points") or 0
                last_safe_pts = rows_by_rank[-4].get("points") or 0
                rel_threshold = max(1, total_teams - 2)

                # S3b: standings per team
                for api_id, suffix in [(hat, "home"), (aat, "away")]:
                    tr = by_team.get(api_id)
                    if not tr:
                        continue
                    rank = tr.get("rank") or 0
                    pts = tr.get("points") or 0
                    add(mid, f"league_position_{suffix}", round(rank / total_teams, 4), "quality", "standings")
                    add(mid, f"points_to_title_{suffix}", float(int(leader_pts - pts)), "quality", "standings")
                    add(mid, f"points_to_relegation_{suffix}", float(int(pts - last_safe_pts)), "quality", "standings")

                # Form string update (batched)
                home_form = (by_team.get(hat) or {}).get("form")
                away_form = (by_team.get(aat) or {}).get("form")
                if home_form or away_form:
                    form_updates.append((
                        home_form[:5] if home_form else None,
                        away_form[:5] if away_form else None,
                        mid,
                    ))

                # Fixture importance (S5 + SIG-7)
                def _urg(tr: dict, total: int, rel: int) -> float:
                    rk = tr.get("rank") or total
                    desc = (tr.get("description") or "").lower()
                    if rk <= 2 or "champion" in desc or "promot" in desc:
                        return 0.85
                    if rk >= rel or "relegate" in desc:
                        return 0.70
                    if rk <= 4 or "playoff" in desc or "play-off" in desc:
                        return 0.50
                    if rk / total < 0.35:
                        return 0.25
                    return 0.10

                home_tr = by_team.get(hat)
                away_tr = by_team.get(aat)
                if home_tr and away_tr:
                    urg_h = _urg(home_tr, total_teams, rel_threshold)
                    urg_a = _urg(away_tr, total_teams, rel_threshold)
                    add(mid, "fixture_importance", round(max(urg_h, urg_a), 3), "context", "derived")
                    add(mid, "fixture_importance_home", urg_h, "context", "derived")
                    add(mid, "fixture_importance_away", urg_a, "context", "derived")
                    add(mid, "importance_diff", round(urg_h - urg_a, 3), "context", "derived")

            # Batch UPDATE form strings
            if form_updates:
                try:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            for home_form, away_form, mid in form_updates:
                                updates = {}
                                if home_form:
                                    updates["form_home"] = home_form
                                if away_form:
                                    updates["form_away"] = away_form
                                if updates:
                                    set_clauses = ", ".join(f"{k} = %s" for k in updates)
                                    params = list(updates.values()) + [mid]
                                    cur.execute(
                                        f"UPDATE matches SET {set_clauses} WHERE id = %s",
                                        tuple(params),
                                    )
                            conn.commit()
                except Exception:
                    pass
    except Exception:
        pass

    # ── 10. Team season stats bulk query ─────────────────────────────────────
    try:
        if all_team_api_ids and all_seasons:
            tss_rows = execute_query(
                """SELECT DISTINCT ON (team_api_id, season)
                          team_api_id, season,
                          goals_for_avg, goals_against_avg,
                          played_home, played_away,
                          goals_for_home, goals_against_home,
                          goals_for_away, goals_against_away
                   FROM team_season_stats
                   WHERE team_api_id = ANY(%s) AND season = ANY(%s)
                   ORDER BY team_api_id, season, fetched_date DESC""",
                (all_team_api_ids, all_seasons),
            )
            tss_lookup = {(r["team_api_id"], r["season"]): r for r in tss_rows}

            for m in matches:
                mid = m["id"]
                seas = m.get("season")
                if not seas:
                    continue
                for api_id, suffix in [
                    (m.get("home_team_api_id"), "home"),
                    (m.get("away_team_api_id"), "away"),
                ]:
                    if not api_id:
                        continue
                    stats = tss_lookup.get((api_id, seas))
                    if not stats:
                        continue
                    add(mid, f"goals_for_avg_{suffix}", stats.get("goals_for_avg"), "quality", "team_season_stats")
                    add(mid, f"goals_against_avg_{suffix}", stats.get("goals_against_avg"), "quality", "team_season_stats")
                    played = stats.get(f"played_{suffix}") or 0
                    gf = stats.get(f"goals_for_{suffix}")
                    ga = stats.get(f"goals_against_{suffix}")
                    if played >= 3:
                        if gf is not None:
                            add(mid, f"goals_for_venue_{suffix}", round(int(gf) / played, 3), "quality", "team_season_stats")
                        if ga is not None:
                            add(mid, f"goals_against_venue_{suffix}", round(int(ga) / played, 3), "quality", "team_season_stats")
    except Exception:
        pass

    # ── 11. League meta-features (SIG-11) ─────────────────────────────────────
    try:
        if all_league_uuids:
            lm_rows = execute_query(
                """SELECT league_id, result, score_home, score_away
                   FROM (
                       SELECT league_id, result, score_home, score_away,
                              ROW_NUMBER() OVER (PARTITION BY league_id ORDER BY date DESC) AS rn
                       FROM matches
                       WHERE league_id = ANY(%s::uuid[])
                         AND status = 'finished' AND result IS NOT NULL
                   ) sub
                   WHERE rn <= 200""",
                (all_league_uuids,),
            )
            lm_by_league: dict[str, list] = defaultdict(list)
            for row in lm_rows:
                lm_by_league[str(row["league_id"])].append(row)

            for m in matches:
                mid = m["id"]
                lid = m.get("league_id")
                if not lid:
                    continue
                rows = lm_by_league.get(lid, [])
                if len(rows) < 20:
                    continue
                total = len(rows)
                home_wins = sum(1 for r in rows if r.get("result") == "home")
                draws_count = sum(1 for r in rows if r.get("result") == "draw")
                goal_totals = [
                    int(r["score_home"]) + int(r["score_away"])
                    for r in rows
                    if r.get("score_home") is not None and r.get("score_away") is not None
                ]
                add(mid, "league_home_win_pct", round(home_wins / total, 4), "context", "derived")
                add(mid, "league_draw_pct", round(draws_count / total, 4), "context", "derived")
                if goal_totals:
                    add(mid, "league_avg_goals", round(sum(goal_totals) / len(goal_totals), 3), "context", "derived")
    except Exception:
        pass

    # ── 12. Bulk INSERT all signals ───────────────────────────────────────────
    if not signals:
        return 0

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """INSERT INTO match_signals
                       (match_id, signal_name, signal_value, signal_group, data_source, captured_at)
                       VALUES %s""",
                    signals,
                    page_size=1000,
                )
                conn.commit()
        return len(signals)
    except Exception as e:
        console.print(f"[yellow]batch_write_morning_signals INSERT failed: {e}[/yellow]")
        return 0


if __name__ == "__main__":
    from workers.api_clients.db import execute_query as eq

    console.print("[green]psycopg2 direct connection OK[/green]")

    for table in ["bots", "matches", "simulated_bets", "predictions", "odds_snapshots",
                  "leagues", "teams", "live_match_snapshots", "match_events",
                  "prediction_snapshots", "match_stats", "model_evaluations",
                  "team_elo_daily", "team_form_cache"]:
        try:
            result = eq(f"SELECT COUNT(*) AS cnt FROM {table}")
            print(f"  {table}: {result[0]['cnt']} rows")
        except Exception as e:
            print(f"  {table}: ERROR -- {e}")
