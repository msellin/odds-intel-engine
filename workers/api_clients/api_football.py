"""
OddsIntel — API-Football Client
Primary data source for fixtures, results, odds, lineups, injuries, standings, live stats, H2H.
$29/mo Ultra plan: 75,000 req/day, 450 req/min.

API docs: https://www.api-football.com/documentation-v3
"""

import os
import time
import requests
from datetime import date, datetime, timezone
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

console = Console()

BASE_URL = "https://v3.football.api-sports.io"
API_KEY = os.getenv("API_FOOTBALL_KEY", "")

# Rate limiting: 450 req/min on Ultra = 7.5 req/sec
# We use a conservative 5 req/sec to stay safe
MIN_REQUEST_INTERVAL = 0.2  # 200ms between requests
_last_request_time = 0.0


def _headers() -> dict:
    return {"x-apisports-key": API_KEY}


def _get(endpoint: str, params: dict = None) -> dict:
    """
    Make a rate-limited GET request to API-Football.
    Returns the parsed JSON response.
    Raises on HTTP errors or API errors.
    """
    global _last_request_time

    if not API_KEY:
        raise ValueError("API_FOOTBALL_KEY not set in .env")

    # Rate limiting
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()

    url = f"{BASE_URL}/{endpoint}"
    resp = requests.get(url, headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()

    data = resp.json()
    if data.get("errors") and len(data["errors"]) > 0:
        errors = data["errors"]
        if isinstance(errors, dict) and errors:
            raise ValueError(f"API-Football error: {errors}")
        if isinstance(errors, list) and errors:
            raise ValueError(f"API-Football error: {errors}")

    return data


def get_remaining_requests() -> dict:
    """Check how many API requests remain today."""
    data = _get("status")
    account = data.get("response", {}).get("account", {})
    requests_info = data.get("response", {}).get("requests", {})
    return {
        "plan": account.get("plan"),
        "limit_day": requests_info.get("limit_day"),
        "current": requests_info.get("current"),
        "remaining": (requests_info.get("limit_day") or 0) - (requests_info.get("current") or 0),
    }


# ─── Fixtures ────────────────────────────────────────────────────────────────

def get_fixtures_by_date(target_date: str = None) -> list[dict]:
    """
    Get all fixtures for a date. Returns raw API-Football fixture objects.
    target_date: YYYY-MM-DD format. Defaults to today.
    """
    if not target_date:
        target_date = date.today().isoformat()

    data = _get("fixtures", {"date": target_date})
    return data.get("response", [])


def get_finished_fixtures(target_date: str = None) -> list[dict]:
    """Get finished fixtures for a date (for settlement)."""
    fixtures = get_fixtures_by_date(target_date)
    return [f for f in fixtures if f.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")]


def get_live_fixtures() -> list[dict]:
    """Get all currently live fixtures."""
    data = _get("fixtures", {"live": "all"})
    return data.get("response", [])


def get_fixture_by_id(fixture_id: int) -> dict | None:
    """Get a single fixture by its API-Football ID."""
    data = _get("fixtures", {"id": fixture_id})
    results = data.get("response", [])
    return results[0] if results else None


# ─── Results (for settlement) ────────────────────────────────────────────────

def get_results_for_settlement(target_date: str = None) -> list[dict]:
    """
    Get finished match results in a format compatible with settlement pipeline.
    Returns list of dicts with: home_team, away_team, home_goals, away_goals, status, etc.
    """
    fixtures = get_finished_fixtures(target_date)
    results = []

    for f in fixtures:
        fixture = f.get("fixture", {})
        teams = f.get("teams", {})
        goals = f.get("goals", {})
        league = f.get("league", {})

        results.append({
            "api_football_id": fixture.get("id"),
            "home_team": teams.get("home", {}).get("name", ""),
            "away_team": teams.get("away", {}).get("name", ""),
            "home_goals": goals.get("home"),
            "away_goals": goals.get("away"),
            "status": "FT",
            "league_name": league.get("name", ""),
            "country": league.get("country", ""),
            "venue": fixture.get("venue", {}).get("name"),
            "referee": fixture.get("referee"),
            "source": "api-football",
        })

    return results


# ─── Statistics ──────────────────────────────────────────────────────────────

def get_fixture_statistics(fixture_id: int) -> list[dict]:
    """
    Get match statistics (shots, possession, corners, etc.).
    Returns list of team stat objects.
    """
    data = _get("fixtures/statistics", {"fixture": fixture_id})
    return data.get("response", [])


def parse_fixture_stats(stats_response: list[dict]) -> dict:
    """
    Parse API-Football statistics response into a flat dict.
    Returns: {home_team, away_team, shots_home, shots_away, possession_home, ...}
    """
    if len(stats_response) < 2:
        return {}

    result = {}
    for i, team_data in enumerate(stats_response):
        prefix = "home" if i == 0 else "away"
        team_name = team_data.get("team", {}).get("name", "")
        result[f"{prefix}_team"] = team_name

        stats = {s["type"]: s["value"] for s in team_data.get("statistics", [])}

        result[f"shots_{prefix}"] = _parse_int(stats.get("Total Shots"))
        result[f"shots_on_target_{prefix}"] = _parse_int(stats.get("Shots on Goal"))
        result[f"corners_{prefix}"] = _parse_int(stats.get("Corner Kicks"))

        # Possession comes as "53%" string
        poss = stats.get("Ball Possession", "")
        if isinstance(poss, str) and "%" in poss:
            result[f"possession_{prefix}"] = int(poss.replace("%", ""))
        elif isinstance(poss, (int, float)):
            result[f"possession_{prefix}"] = int(poss)

        result[f"fouls_{prefix}"] = _parse_int(stats.get("Fouls"))
        result[f"offsides_{prefix}"] = _parse_int(stats.get("Offsides"))
        result[f"yellow_cards_{prefix}"] = _parse_int(stats.get("Yellow Cards"))
        result[f"red_cards_{prefix}"] = _parse_int(stats.get("Red Cards"))
        result[f"saves_{prefix}"] = _parse_int(stats.get("Goalkeeper Saves"))
        result[f"passes_{prefix}"] = _parse_int(stats.get("Total passes"))
        result[f"pass_accuracy_{prefix}"] = _parse_int(stats.get("Passes accurate"))
        result[f"shots_on_target_{prefix}"] = _parse_int(stats.get("Shots on Goal"))

        # xG (API-Football has it for some top leagues)
        xg = stats.get("expected_goals") or stats.get("Expected Goals")
        if xg is not None:
            try:
                result[f"xg_{prefix}"] = float(xg)
            except (ValueError, TypeError):
                pass

    return result


def _parse_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ─── Odds ────────────────────────────────────────────────────────────────────

def get_fixture_odds(fixture_id: int) -> list[dict]:
    """
    Get pre-match odds for a single fixture from all available bookmakers.
    Returns raw bookmaker odds data.
    """
    data = _get("odds", {"fixture": fixture_id})
    return data.get("response", [])


def get_odds_by_date(date_str: str) -> dict[int, list[dict]]:
    """
    Bulk fetch all pre-match odds for a given date using the paginated /odds endpoint.
    Much more efficient than per-fixture calls (~10 calls vs ~200).
    Returns dict keyed by fixture_id -> list of raw bookmaker odds entries.
    """
    result: dict[int, list[dict]] = {}
    page = 1
    total_pages = 1

    while page <= total_pages:
        data = _get("odds", {"date": date_str, "page": page})
        paging = data.get("paging", {})
        total_pages = paging.get("total", 1)
        for entry in data.get("response", []):
            fid = entry.get("fixture", {}).get("id")
            if fid:
                if fid not in result:
                    result[fid] = []
                result[fid].append(entry)
        page += 1

    return result


def parse_fixture_odds(odds_response: list[dict]) -> list[dict]:
    """
    Parse API-Football odds into flat rows compatible with our odds_snapshots table.
    Returns list of dicts: {bookmaker, market, selection, odds}
    """
    rows = []
    if not odds_response:
        return rows

    for resp in odds_response:
        for bookmaker in resp.get("bookmakers", []):
            bm_name = bookmaker.get("name", "unknown")

            for bet in bookmaker.get("bets", []):
                bet_name = bet.get("name", "")

                if bet_name == "Match Winner":
                    for val in bet.get("values", []):
                        selection_map = {"Home": "home", "Draw": "draw", "Away": "away"}
                        sel = selection_map.get(val["value"])
                        if sel:
                            rows.append({
                                "bookmaker": bm_name,
                                "market": "1x2",
                                "selection": sel,
                                "odds": float(val["odd"]),
                            })

                elif "Over/Under" in bet_name or bet_name == "Goals Over/Under":
                    for val in bet.get("values", []):
                        v = val["value"]  # e.g. "Over 2.5", "Under 2.5"
                        if " " in v:
                            direction, line = v.split(" ", 1)
                            try:
                                line_num = float(line)
                                line_label = f"over_under_{str(line_num).replace('.', '')}"
                                rows.append({
                                    "bookmaker": bm_name,
                                    "market": line_label,
                                    "selection": direction.lower(),
                                    "odds": float(val["odd"]),
                                })
                            except ValueError:
                                pass

                elif bet_name == "Both Teams Score":
                    for val in bet.get("values", []):
                        rows.append({
                            "bookmaker": bm_name,
                            "market": "btts",
                            "selection": val["value"].lower(),
                            "odds": float(val["odd"]),
                        })

                elif bet_name == "Double Chance":
                    for val in bet.get("values", []):
                        sel_map = {"Home/Draw": "1x", "Home/Away": "12", "Draw/Away": "x2"}
                        sel = sel_map.get(val["value"])
                        if sel:
                            rows.append({
                                "bookmaker": bm_name,
                                "market": "double_chance",
                                "selection": sel,
                                "odds": float(val["odd"]),
                            })

    return rows


# ─── Predictions (T1) ────────────────────────────────────────────────────────

def get_prediction(fixture_id: int) -> dict | None:
    """
    Get API-Football's prediction for a fixture.
    Endpoint: GET /predictions?fixture={id}

    Returns the raw prediction object or None if unavailable.
    """
    data = _get("predictions", {"fixture": fixture_id})
    results = data.get("response", [])
    return results[0] if results else None


def parse_prediction(pred_response: dict) -> dict:
    """
    Parse API-Football prediction response into a flat dict suitable for storage.

    Returns:
        {
          af_home_prob: float,         # e.g. 0.50 (from "50%")
          af_draw_prob: float,
          af_away_prob: float,
          af_advice: str,              # e.g. "Home or Draw and under 2.5 goals"
          af_winner: str | None,       # "Home", "Away", or None
          af_under_over: str | None,   # e.g. "-2.5" or None
          af_goals_home: str | None,   # e.g. "1.7"
          af_goals_away: str | None,
          af_poisson_home: float | None,  # Their Poisson %
          af_poisson_away: float | None,
          af_attack_home: float | None,
          af_attack_away: float | None,
          af_defence_home: float | None,
          af_defence_away: float | None,
          raw: dict,                   # Full raw payload as JSONB
        }
    """
    if not pred_response:
        return {}

    predictions = pred_response.get("predictions", {})
    percent = predictions.get("percent", {})
    goals = predictions.get("goals", {})
    comparison = pred_response.get("comparison", {})
    winner = predictions.get("winner", {})

    def _pct(val) -> float | None:
        """Convert '45%' or 45 to 0.45."""
        if val is None:
            return None
        try:
            s = str(val).replace("%", "").strip()
            return round(float(s) / 100, 4)
        except (ValueError, TypeError):
            return None

    def _float(val) -> float | None:
        if val is None:
            return None
        try:
            s = str(val).replace("%", "").strip()
            return round(float(s), 4)
        except (ValueError, TypeError):
            return None

    # Determine predicted winner label
    winner_id = winner.get("id")
    teams = pred_response.get("teams", {})
    home_id = teams.get("home", {}).get("id")
    if winner_id and home_id:
        af_winner = "Home" if winner_id == home_id else "Away"
    elif winner_id is None and predictions.get("win_or_draw"):
        af_winner = None  # win_or_draw = True but no specific winner → draw is fine too
    else:
        af_winner = None

    poisson = comparison.get("poisson_distribution", {})
    attack = comparison.get("att", {})
    defence = comparison.get("def", {})

    return {
        "af_home_prob": _pct(percent.get("home")),
        "af_draw_prob": _pct(percent.get("draw")),
        "af_away_prob": _pct(percent.get("away")),
        "af_advice": predictions.get("advice"),
        "af_winner": af_winner,
        "af_under_over": predictions.get("under_over"),
        "af_goals_home": goals.get("home"),
        "af_goals_away": goals.get("away"),
        "af_poisson_home": _pct(poisson.get("home")),
        "af_poisson_away": _pct(poisson.get("away")),
        "af_attack_home": _pct(attack.get("home")),
        "af_attack_away": _pct(attack.get("away")),
        "af_defence_home": _pct(defence.get("home")),
        "af_defence_away": _pct(defence.get("away")),
        "raw": pred_response,
    }


# ─── Lineups ─────────────────────────────────────────────────────────────────

def get_fixture_lineups(fixture_id: int) -> list[dict]:
    """Get lineups for a fixture (available ~60min before KO)."""
    data = _get("fixtures/lineups", {"fixture": fixture_id})
    return data.get("response", [])


# ─── Injuries ────────────────────────────────────────────────────────────────

def get_injuries(fixture_id: int) -> list[dict]:
    """Get injuries/sidelined players for a fixture."""
    data = _get("injuries", {"fixture": fixture_id})
    return data.get("response", [])


# ─── H2H ─────────────────────────────────────────────────────────────────────

def get_h2h(team1_id: int, team2_id: int, last: int = 5) -> list[dict]:
    """Get head-to-head fixtures between two teams."""
    data = _get("fixtures/headtohead", {"h2h": f"{team1_id}-{team2_id}", "last": last})
    return data.get("response", [])


# ─── Standings ───────────────────────────────────────────────────────────────

def get_standings(league_id: int, season: int) -> list[dict]:
    """Get league standings."""
    data = _get("standings", {"league": league_id, "season": season})
    return data.get("response", [])


# ─── Team Statistics (T2) ────────────────────────────────────────────────────

def get_team_statistics(team_id: int, league_id: int, season: int) -> dict | None:
    """
    Get season aggregate stats for a team in a league.
    Endpoint: GET /teams/statistics?team={id}&league={id}&season={year}
    Returns the raw response object or None.
    """
    data = _get("teams/statistics", {"team": team_id, "league": league_id, "season": season})
    return data.get("response") or None


def parse_team_statistics(raw: dict) -> dict:
    """
    Parse /teams/statistics response into a flat dict for storage.
    Stores full raw + key extracted fields for fast querying.
    """
    if not raw:
        return {}

    fixtures = raw.get("fixtures", {})
    goals = raw.get("goals", {})
    biggest = raw.get("biggest", {})
    clean = raw.get("clean_sheet", {})
    failed = raw.get("failed_to_score", {})
    penalty = raw.get("penalty", {})
    lineups = raw.get("lineups", [])
    cards = raw.get("cards", {})

    # Goals for/against totals
    gf = goals.get("for", {})
    ga = goals.get("against", {})

    played_total = fixtures.get("played", {}).get("total") or 0
    cs_total = clean.get("total") or 0
    fts_total = failed.get("total") or 0

    # Most used formation
    most_formation = None
    if lineups:
        most_formation = max(lineups, key=lambda x: x.get("played", 0)).get("formation")

    return {
        "form": raw.get("form"),
        "played_total": played_total,
        "played_home": fixtures.get("played", {}).get("home"),
        "played_away": fixtures.get("played", {}).get("away"),
        "wins_total":   fixtures.get("wins", {}).get("total"),
        "wins_home":    fixtures.get("wins", {}).get("home"),
        "wins_away":    fixtures.get("wins", {}).get("away"),
        "draws_total":  fixtures.get("draws", {}).get("total"),
        "draws_home":   fixtures.get("draws", {}).get("home"),
        "draws_away":   fixtures.get("draws", {}).get("away"),
        "losses_total": fixtures.get("loses", {}).get("total"),
        "losses_home":  fixtures.get("loses", {}).get("home"),
        "losses_away":  fixtures.get("loses", {}).get("away"),
        "goals_for_total":     gf.get("total", {}).get("total"),
        "goals_for_home":      gf.get("total", {}).get("home"),
        "goals_for_away":      gf.get("total", {}).get("away"),
        "goals_against_total": ga.get("total", {}).get("total"),
        "goals_against_home":  ga.get("total", {}).get("home"),
        "goals_against_away":  ga.get("total", {}).get("away"),
        "goals_for_avg":       _float_safe(gf.get("average", {}).get("total")),
        "goals_against_avg":   _float_safe(ga.get("average", {}).get("total")),
        "clean_sheets_total": cs_total,
        "clean_sheets_home":  clean.get("home"),
        "clean_sheets_away":  clean.get("away"),
        "failed_to_score_total": fts_total,
        "failed_to_score_home":  failed.get("home"),
        "failed_to_score_away":  failed.get("away"),
        "clean_sheet_pct":       round(cs_total / played_total, 4) if played_total else None,
        "failed_to_score_pct":   round(fts_total / played_total, 4) if played_total else None,
        "biggest_win_home":  biggest.get("wins", {}).get("home"),
        "biggest_win_away":  biggest.get("wins", {}).get("away"),
        "biggest_loss_home": biggest.get("loses", {}).get("home"),
        "biggest_loss_away": biggest.get("loses", {}).get("away"),
        "streak_wins":   biggest.get("streak", {}).get("wins"),
        "streak_draws":  biggest.get("streak", {}).get("draws"),
        "streak_losses": biggest.get("streak", {}).get("loses"),
        "penalty_scored":    penalty.get("scored", {}).get("total"),
        "penalty_missed":    penalty.get("missed", {}).get("total"),
        "penalty_total":     penalty.get("total"),
        "penalty_scored_pct": penalty.get("scored", {}).get("percentage"),
        "most_used_formation": most_formation,
        "formations_jsonb":    lineups if lineups else None,
        "yellow_cards_by_minute": cards.get("yellow") or None,
        "red_cards_by_minute":    cards.get("red") or None,
        "goals_for_by_minute":    gf.get("minute") or None,
        "goals_against_by_minute": ga.get("minute") or None,
        "raw": raw,
    }


def _float_safe(val) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


# ─── Injuries (T3) ───────────────────────────────────────────────────────────

def get_injuries_batched(fixture_ids: list[int]) -> dict[int, list[dict]]:
    """
    Batch-fetch injuries for up to 20 fixtures per call.
    Returns {fixture_id: [injury_objects]}
    """
    results: dict[int, list[dict]] = {}
    # Batch in chunks of 20
    for i in range(0, len(fixture_ids), 20):
        chunk = fixture_ids[i:i + 20]
        ids_str = "-".join(str(fid) for fid in chunk)
        try:
            data = _get("injuries", {"ids": ids_str})
            for item in data.get("response", []):
                fid = item.get("fixture", {}).get("id")
                if fid:
                    results.setdefault(fid, []).append(item)
        except Exception:
            # Fall back to individual calls on batch failure
            for fid in chunk:
                try:
                    single = _get("injuries", {"fixture": fid})
                    results[fid] = single.get("response", [])
                except Exception:
                    results[fid] = []
    return results


def parse_injuries(injuries_response: list[dict], home_team_api_id: int = None) -> list[dict]:
    """
    Parse injuries response into flat dicts for storage.
    home_team_api_id used to resolve 'home'/'away' side.
    """
    rows = []
    for item in injuries_response:
        player = item.get("player", {})
        team = item.get("team", {})
        team_id = team.get("id")
        side = "home" if team_id == home_team_api_id else "away" if home_team_api_id else None
        rows.append({
            "team_api_id": team_id,
            "team_side": side,
            "player_id": player.get("id"),
            "player_name": player.get("name"),
            "player_type": player.get("type"),   # "Player" or "Coach"
            "status": player.get("type"),         # AF uses same field for status ("Missing Fixture")
            "reason": player.get("reason"),
            "raw": item,
        })
    return rows


# ─── Fixture Statistics with half-time (T4) ──────────────────────────────────

def get_fixture_statistics_halftime(fixture_id: int) -> dict:
    """
    Get fixture statistics split by half.
    Returns {first_half: [team_stats], second_half: [team_stats]}
    or empty dict if not available.
    """
    try:
        # Some docs suggest half=1 / half=2 for each period
        data_1h = _get("fixtures/statistics", {"fixture": fixture_id, "half": "1"})
        resp_1h = data_1h.get("response", [])
        data_2h = _get("fixtures/statistics", {"fixture": fixture_id, "half": "2"})
        resp_2h = data_2h.get("response", [])
        return {"first_half": resp_1h, "second_half": resp_2h}
    except Exception:
        return {}


def parse_fixture_stats_halftime(halftime_response: dict) -> dict:
    """
    Parse half-time stats response into flat dict with _ht suffix.
    Returns dict with keys like shots_home_ht, possession_home_ht, etc.
    """
    first_half = halftime_response.get("first_half", [])
    if not first_half:
        return {}

    result = {}
    for i, team_data in enumerate(first_half):
        prefix = "home" if i == 0 else "away"
        stats = {s["type"]: s["value"] for s in team_data.get("statistics", [])}

        result[f"shots_{prefix}_ht"] = _parse_int(stats.get("Total Shots"))
        result[f"shots_on_target_{prefix}_ht"] = _parse_int(stats.get("Shots on Goal"))
        result[f"corners_{prefix}_ht"] = _parse_int(stats.get("Corner Kicks"))
        result[f"fouls_{prefix}_ht"] = _parse_int(stats.get("Fouls"))
        result[f"offsides_{prefix}_ht"] = _parse_int(stats.get("Offsides"))
        result[f"yellow_cards_{prefix}_ht"] = _parse_int(stats.get("Yellow Cards"))
        result[f"passes_{prefix}_ht"] = _parse_int(stats.get("Total passes"))

        poss = stats.get("Ball Possession", "")
        if isinstance(poss, str) and "%" in poss:
            result[f"possession_{prefix}_ht"] = int(poss.replace("%", ""))
        elif isinstance(poss, (int, float)):
            result[f"possession_{prefix}_ht"] = int(poss)

        xg = stats.get("expected_goals") or stats.get("Expected Goals")
        if xg is not None:
            try:
                result[f"xg_{prefix}_ht"] = float(xg)
            except (ValueError, TypeError):
                pass

    return result


# ─── Live Odds (T5) ──────────────────────────────────────────────────────────

def get_live_odds() -> list[dict]:
    """
    Get live in-play odds for all currently live fixtures.
    One call returns everything. Called every 5min during match hours.
    Returns list of raw odd objects, each containing fixture.id.
    """
    data = _get("odds/live")
    return data.get("response", [])


def parse_live_odds(live_odds_response: list[dict]) -> dict[int, list[dict]]:
    """
    Parse live odds response into {fixture_id: [odds_rows]}.
    Each row: {bookmaker, market, selection, odds, minute, status}
    """
    result: dict[int, list[dict]] = {}

    for item in live_odds_response:
        fixture = item.get("fixture", {})
        fid = fixture.get("id")
        if not fid:
            continue

        minute = fixture.get("status", {}).get("elapsed")
        rows = []

        for bet in item.get("odds", []):
            market_name = bet.get("name", "")

            if market_name == "Match Winner":
                for val in bet.get("values", []):
                    if val.get("suspended"):
                        continue
                    sel_map = {"Home": "home", "Draw": "draw", "Away": "away"}
                    sel = sel_map.get(val.get("value"))
                    if sel:
                        rows.append({
                            "bookmaker": "api-football-live",
                            "market": "1x2",
                            "selection": sel,
                            "odds": float(val["odd"]),
                            "minute": minute,
                        })

            elif "Over/Under" in market_name or market_name == "Goals Over/Under":
                for val in bet.get("values", []):
                    if val.get("suspended"):
                        continue
                    v = val.get("value", "")
                    if " " in v:
                        direction, line = v.split(" ", 1)
                        try:
                            line_num = float(line)
                            label = f"over_under_{str(line_num).replace('.', '')}"
                            rows.append({
                                "bookmaker": "api-football-live",
                                "market": label,
                                "selection": direction.lower(),
                                "odds": float(val["odd"]),
                                "minute": minute,
                            })
                        except ValueError:
                            pass

            elif market_name == "Both Teams Score":
                for val in bet.get("values", []):
                    if val.get("suspended"):
                        continue
                    rows.append({
                        "bookmaker": "api-football-live",
                        "market": "btts",
                        "selection": val["value"].lower(),
                        "odds": float(val["odd"]),
                        "minute": minute,
                    })

        if rows:
            result[fid] = rows

    return result


# ─── Match Events (T8) ───────────────────────────────────────────────────────

def get_fixture_events(fixture_id: int) -> list[dict]:
    """Get all events (goals, cards, subs, VAR) for a fixture."""
    data = _get("fixtures/events", {"fixture": fixture_id})
    return data.get("response", [])


def parse_fixture_events(events_response: list[dict]) -> list[dict]:
    """
    Parse API-Football events into flat dicts compatible with match_events table.
    Returns list of event dicts with: minute, added_time, event_type, team, player_name, etc.
    """
    rows = []
    for idx, ev in enumerate(events_response):
        time_info = ev.get("time", {})
        minute = time_info.get("elapsed", 0) or 0
        extra = time_info.get("extra", 0) or 0

        team = ev.get("team", {})
        player = ev.get("player", {})
        assist = ev.get("assist", {})

        ev_type = ev.get("type", "")
        ev_detail = ev.get("detail", "")

        # Map to our event_type vocabulary
        event_type = None
        if ev_type == "Goal":
            if ev_detail == "Own Goal":
                event_type = "own_goal"
            elif ev_detail == "Penalty":
                event_type = "penalty_scored"
            elif ev_detail == "Missed Penalty":
                event_type = "penalty_missed"
            else:
                event_type = "goal"
        elif ev_type == "Card":
            if ev_detail == "Yellow Card":
                event_type = "yellow_card"
            elif ev_detail == "Red Card":
                event_type = "red_card"
            elif ev_detail in ("Yellow Red Card", "Second Yellow card"):
                event_type = "yellow_red_card"
        elif ev_type == "subst":
            event_type = "substitution_in"
        elif ev_type == "Var":
            event_type = "var_decision"

        if not event_type:
            continue

        rows.append({
            "minute": minute,
            "added_time": extra,
            "event_type": event_type,
            "team": "home" if team.get("id") else "unknown",  # resolved later with match context
            "team_api_id": team.get("id"),
            "player_name": player.get("name"),
            "player_id": player.get("id"),
            "assist_name": assist.get("name") if assist else None,
            "detail": ev_detail,
            "af_event_order": idx,  # sequential index for dedup
        })

    return rows


# ─── Lineups (T7) — parse function added (get_fixture_lineups already exists) ─

def parse_fixture_lineups(lineups_response: list[dict]) -> dict:
    """
    Parse lineups response into home/away dicts for storage.
    Returns: {
        formation_home, formation_away,
        coach_home, coach_away,
        lineups_home (full JSONB), lineups_away (full JSONB)
    }
    """
    if not lineups_response or len(lineups_response) < 1:
        return {}

    result = {}
    for i, team_data in enumerate(lineups_response[:2]):
        side = "home" if i == 0 else "away"
        result[f"formation_{side}"] = team_data.get("formation")
        coach = team_data.get("coach", {})
        result[f"coach_{side}"] = coach.get("name")
        result[f"lineups_{side}"] = team_data  # full JSONB

    return result


# ─── Standings (T9) — parse function added (get_standings already exists) ────

def parse_standings(standings_response: list[dict]) -> list[dict]:
    """
    Parse standings response into flat rows for league_standings table.
    Returns list of team-standing dicts.
    """
    rows = []
    for resp in standings_response:
        league = resp.get("league", {})
        league_api_id = league.get("id")
        season = league.get("season")

        for group in league.get("standings", []):
            for entry in group:
                team = entry.get("team", {})
                all_stats = entry.get("all", {})
                home_stats = entry.get("home", {})
                away_stats = entry.get("away", {})

                rows.append({
                    "league_api_id": league_api_id,
                    "season": season,
                    "team_api_id": team.get("id"),
                    "team_name": team.get("name"),
                    "rank": entry.get("rank"),
                    "points": entry.get("points"),
                    "goals_diff": entry.get("goalsDiff"),
                    "group_name": entry.get("group"),
                    "form": entry.get("form"),
                    "status": entry.get("status"),
                    "description": entry.get("description"),
                    "played":      all_stats.get("played"),
                    "wins":        all_stats.get("win"),
                    "draws":       all_stats.get("draw"),
                    "losses":      all_stats.get("lose"),
                    "goals_for":   all_stats.get("goals", {}).get("for"),
                    "goals_against": all_stats.get("goals", {}).get("against"),
                    "home_played":   home_stats.get("played"),
                    "home_wins":     home_stats.get("win"),
                    "home_draws":    home_stats.get("draw"),
                    "home_losses":   home_stats.get("lose"),
                    "home_goals_for":     home_stats.get("goals", {}).get("for"),
                    "home_goals_against": home_stats.get("goals", {}).get("against"),
                    "away_played":   away_stats.get("played"),
                    "away_wins":     away_stats.get("win"),
                    "away_draws":    away_stats.get("draw"),
                    "away_losses":   away_stats.get("lose"),
                    "away_goals_for":     away_stats.get("goals", {}).get("for"),
                    "away_goals_against": away_stats.get("goals", {}).get("against"),
                    "raw": entry,
                })

    return rows


# ─── H2H (T10) — parse function added (get_h2h already exists) ───────────────

def parse_h2h(h2h_response: list[dict], home_team_api_id: int = None) -> dict:
    """
    Parse last N H2H fixtures into summary stats + raw JSONB.
    Returns: {h2h_raw, h2h_home_wins, h2h_draws, h2h_away_wins}
    """
    if not h2h_response:
        return {}

    home_wins = draws = away_wins = 0
    for f in h2h_response:
        teams = f.get("teams", {})
        goals = f.get("goals", {})
        home_id = teams.get("home", {}).get("id")
        gf = goals.get("home")
        ga = goals.get("away")
        if gf is None or ga is None:
            continue

        # "home" in H2H is relative to the H2H fixture, not our match
        # We need to track wins for our home team specifically
        if home_team_api_id and home_id == home_team_api_id:
            if gf > ga:
                home_wins += 1
            elif gf == ga:
                draws += 1
            else:
                away_wins += 1
        elif home_team_api_id:
            # Our home team was away in this H2H fixture
            if ga > gf:
                home_wins += 1
            elif gf == ga:
                draws += 1
            else:
                away_wins += 1
        else:
            # No context — track relative to H2H fixture home
            if gf > ga:
                home_wins += 1
            elif gf == ga:
                draws += 1
            else:
                away_wins += 1

    return {
        "h2h_raw": h2h_response,
        "h2h_home_wins": home_wins,
        "h2h_draws": draws,
        "h2h_away_wins": away_wins,
    }


# ─── Player Sidelined (T11) ───────────────────────────────────────────────────

def get_sidelined(player_id: int) -> list[dict]:
    """Get full injury/sidelined history for a player."""
    data = _get("sidelined", {"player": player_id})
    return data.get("response", [])


def parse_sidelined(sidelined_response: list[dict], player_id: int,
                    player_name: str = None, team_api_id: int = None) -> list[dict]:
    """Parse sidelined response into flat rows for player_sidelined table."""
    rows = []
    for item in sidelined_response:
        start = item.get("start")
        end = item.get("end")
        rows.append({
            "player_id": player_id,
            "player_name": player_name,
            "team_api_id": team_api_id,
            "type": item.get("type"),
            "start_date": start[:10] if start else None,
            "end_date": end[:10] if end else None,
            "raw": item,
        })
    return rows


# ─── Per-Player Match Stats (T12) ─────────────────────────────────────────────

def get_fixture_players(fixture_id: int) -> list[dict]:
    """Get per-player statistics for a fixture."""
    data = _get("fixtures/players", {"fixture": fixture_id})
    return data.get("response", [])


def parse_fixture_players(players_response: list[dict],
                           home_team_api_id: int = None) -> list[dict]:
    """
    Parse fixture players response into flat rows for match_player_stats table.
    """
    rows = []
    for team_data in players_response:
        team = team_data.get("team", {})
        team_id = team.get("id")
        side = "home" if team_id == home_team_api_id else "away"

        for p in team_data.get("players", []):
            player = p.get("player", {})
            stats_list = p.get("statistics", [{}])
            s = stats_list[0] if stats_list else {}

            games = s.get("games", {})
            shots = s.get("shots", {})
            goals = s.get("goals", {})
            passes = s.get("passes", {})
            tackles = s.get("tackles", {})
            duels = s.get("duels", {})
            dribbles = s.get("dribbles", {})
            fouls = s.get("fouls", {})
            cards = s.get("cards", {})
            penalty = s.get("penalty", {})

            rating = None
            try:
                rating = round(float(games.get("rating") or 0), 2) or None
            except (ValueError, TypeError):
                pass

            pass_acc = None
            try:
                pass_acc = round(float(passes.get("accuracy") or 0), 2) or None
            except (ValueError, TypeError):
                pass

            rows.append({
                "team_api_id": team_id,
                "team_side": side,
                "player_id": player.get("id"),
                "player_name": player.get("name"),
                "shirt_number": games.get("number"),
                "position": games.get("position"),
                "minutes_played": games.get("minutes"),
                "rating": rating,
                "captain": games.get("captain", False),
                "goals": goals.get("total"),
                "assists": goals.get("assists"),
                "shots_total": shots.get("total"),
                "shots_on_target": shots.get("on"),
                "passes_total": passes.get("total"),
                "passes_key": passes.get("key"),
                "pass_accuracy": pass_acc,
                "tackles_total": tackles.get("total"),
                "blocks": tackles.get("blocks"),
                "interceptions": tackles.get("interceptions"),
                "duels_total": duels.get("total"),
                "duels_won": duels.get("won"),
                "dribbles_attempted": dribbles.get("attempts"),
                "dribbles_success": dribbles.get("success"),
                "fouls_drawn": fouls.get("drawn"),
                "fouls_committed": fouls.get("committed"),
                "yellow_cards": cards.get("yellow"),
                "red_cards": cards.get("red"),
                "goals_conceded": goals.get("conceded"),
                "saves": goals.get("saves"),
                "penalty_scored": penalty.get("scored"),
                "penalty_missed": penalty.get("missed"),
                "penalty_saved": penalty.get("saved"),
                "raw": p,
            })

    return rows


# ─── Transfers (T13) ──────────────────────────────────────────────────────────

def get_transfers(team_id: int) -> list[dict]:
    """Get all transfers for a team."""
    data = _get("transfers", {"team": team_id})
    return data.get("response", [])


def parse_transfers(transfers_response: list[dict], team_api_id: int) -> list[dict]:
    """Parse transfers response into flat rows for team_transfers table."""
    rows = []
    for item in transfers_response:
        player = item.get("player", {})
        player_id = player.get("id")
        player_name = player.get("name")

        for t in item.get("transfers", []):
            teams = t.get("teams", {})
            from_team = teams.get("out", {})
            to_team = teams.get("in", {})
            date_str = t.get("date")

            rows.append({
                "team_api_id": team_api_id,
                "player_id": player_id,
                "player_name": player_name,
                "transfer_date": date_str[:10] if date_str else None,
                "transfer_type": t.get("type"),
                "from_team_api_id": from_team.get("id"),
                "from_team_name": from_team.get("name"),
                "to_team_api_id": to_team.get("id"),
                "to_team_name": to_team.get("name"),
                "raw": t,
            })

    return rows


# ─── Team form ───────────────────────────────────────────────────────────────

def get_team_last_fixtures(team_id: int, last: int = 5) -> list[dict]:
    """Get a team's last N finished fixtures."""
    data = _get("fixtures", {"team": team_id, "last": last, "status": "FT"})
    return data.get("response", [])


# ─── Fixture conversion helpers ──────────────────────────────────────────────

def fixture_to_match_dict(fixture: dict) -> dict:
    """
    Convert an API-Football fixture to our match_dict format for store_match().
    """
    f = fixture.get("fixture", {})
    teams = fixture.get("teams", {})
    goals = fixture.get("goals", {})
    league = fixture.get("league", {})
    venue = f.get("venue", {})

    match_dict = {
        "home_team": teams.get("home", {}).get("name", ""),
        "away_team": teams.get("away", {}).get("name", ""),
        "start_time": f.get("date", ""),
        "league_path": f"{league.get('country', '')} / {league.get('name', '')}",
        "league_code": "",
        "tier": 0,
        "operator": "api-football",
        "api_football_id": f.get("id"),
        "sofascore_event_id": None,
        "venue_name": venue.get("name"),
        "venue_city": venue.get("city"),
        "referee": f.get("referee"),
        "home_team_api_id": teams.get("home", {}).get("id"),
        "away_team_api_id": teams.get("away", {}).get("id"),
        "league_api_id": league.get("id"),
        "season": league.get("season"),
        "odds_home": 0,
        "odds_draw": 0,
        "odds_away": 0,
        "odds_over_25": 0,
        "odds_under_25": 0,
    }

    # Include score if finished
    status = f.get("status", {}).get("short", "")
    if status in ("FT", "AET", "PEN") and goals.get("home") is not None:
        match_dict["home_goals"] = goals["home"]
        match_dict["away_goals"] = goals["away"]

    return match_dict


if __name__ == "__main__":
    console.print("[bold]API-Football Client Test[/bold]\n")

    # Check account status
    status = get_remaining_requests()
    console.print(f"Plan: {status['plan']}")
    console.print(f"Requests: {status['current']} used / {status['limit_day']} limit ({status['remaining']} remaining)\n")

    # Today's fixtures
    fixtures = get_fixtures_by_date()
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
    live = [f for f in fixtures if f["fixture"]["status"]["short"] in ("1H", "2H", "HT")]
    scheduled = [f for f in fixtures if f["fixture"]["status"]["short"] == "NS"]

    console.print(f"Today: {len(fixtures)} total — {len(finished)} finished, {len(live)} live, {len(scheduled)} scheduled\n")

    for f in finished[:3]:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        goals = f["goals"]
        league = f["league"]
        console.print(f"  {league['country']} - {league['name']}: {home} {goals['home']}-{goals['away']} {away}")
