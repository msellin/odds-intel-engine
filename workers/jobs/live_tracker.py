"""
OddsIntel — Live Match Tracker
Runs every 5 minutes during match hours to capture in-play data.

For each live match, collects:
  - Current minute + score
  - Sofascore live stats (shots, xG, possession)
  - Sofascore match events (goals, cards, subs)
  - Kambi live odds (O/U 0.5–4.5, 1X2)

This dataset is the foundation for:
  - In-play O/U value analysis (e.g. high-xG game, 0-0 at min 12)
  - Understanding how odds react to goals/events (and how fast)
  - Building live bet timing models

Usage:
  python live_tracker.py          # Track all currently live matches
  python live_tracker.py --test   # Dry run, print what would be stored
"""

import sys
import os
import time
import argparse
import requests
from pathlib import Path
from datetime import datetime, date, timezone
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.scrapers.kambi_odds import fetch_live_odds
from workers.api_clients.supabase_client import (
    get_client,
    store_live_snapshot,
    store_match_event,
    store_match_stats,
    get_live_matches,
    get_match_by_sofascore_id,
    get_match_by_teams_and_date,
    update_match_status,
)

console = Console()

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}


# ============================================================
# Sofascore live data fetchers
# ============================================================

def fetch_sofascore_live_events() -> list[dict]:
    """
    Get all currently live football matches from Sofascore.
    Returns list with event_id, teams, score, minute, status.
    """
    try:
        resp = requests.get(
            "https://api.sofascore.com/api/v1/sport/football/events/live",
            headers=SOFASCORE_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            console.print(f"[yellow]Sofascore live returned {resp.status_code}[/yellow]")
            return []

        data = resp.json()
        events = data.get("events", [])
        matches = []

        for event in events:
            status = event.get("status", {})
            status_code = status.get("code", 0)
            status_desc = status.get("description", "")
            time_info = event.get("time", {})

            home = event.get("homeTeam", {})
            away = event.get("awayTeam", {})
            home_score = event.get("homeScore", {}).get("current", 0) or 0
            away_score = event.get("awayScore", {}).get("current", 0) or 0

            matches.append({
                "event_id": event.get("id"),
                "home_team": home.get("name", ""),
                "home_team_id": home.get("id"),
                "away_team": away.get("name", ""),
                "away_team_id": away.get("id"),
                "score_home": home_score,
                "score_away": away_score,
                "minute": time_info.get("played", 0) or 0,
                "added_time": time_info.get("periodLength", 0) - 45 if time_info.get("periodLength", 45) > 45 else 0,
                "status_code": status_code,
                "status_desc": status_desc,
                "league": event.get("tournament", {}).get("name", ""),
                "country": event.get("tournament", {}).get("category", {}).get("name", ""),
                "start_timestamp": event.get("startTimestamp", 0),
            })

        return matches

    except Exception as e:
        console.print(f"[red]Sofascore live events error: {e}[/red]")
        return []


def fetch_sofascore_match_stats(event_id: int) -> dict:
    """
    Fetch live statistics for a single match from Sofascore.
    Returns shots, xG, possession, corners, attacks.
    """
    try:
        resp = requests.get(
            f"https://api.sofascore.com/api/v1/event/{event_id}/statistics",
            headers=SOFASCORE_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return {}

        data = resp.json()
        stats = {}

        # Sofascore returns stats grouped by period
        # We want "ALL" period or the most complete one
        for group in data.get("statistics", []):
            if group.get("period") != "ALL":
                continue

            for stat_group in group.get("groups", []):
                for item in stat_group.get("statisticsItems", []):
                    name = item.get("name", "")
                    home_val = item.get("home")
                    away_val = item.get("away")

                    if name == "Ball possession":
                        try:
                            stats["possession_home"] = float(str(home_val).replace("%", ""))
                        except (ValueError, TypeError):
                            pass
                    elif name == "Total shots":
                        try:
                            stats["shots_home"] = int(home_val or 0)
                            stats["shots_away"] = int(away_val or 0)
                        except (ValueError, TypeError):
                            pass
                    elif name == "Shots on target":
                        try:
                            stats["shots_on_target_home"] = int(home_val or 0)
                            stats["shots_on_target_away"] = int(away_val or 0)
                        except (ValueError, TypeError):
                            pass
                    elif name == "Corner kicks":
                        try:
                            stats["corners_home"] = int(home_val or 0)
                            stats["corners_away"] = int(away_val or 0)
                        except (ValueError, TypeError):
                            pass
                    elif name == "Expected goals":
                        try:
                            stats["xg_home"] = float(home_val or 0)
                            stats["xg_away"] = float(away_val or 0)
                        except (ValueError, TypeError):
                            pass
                    elif name in ("Attacks", "Total attacks"):
                        try:
                            stats["attacks_home"] = int(home_val or 0)
                            stats["attacks_away"] = int(away_val or 0)
                        except (ValueError, TypeError):
                            pass

        return stats

    except Exception as e:
        console.print(f"  [yellow]Stats fetch error for {event_id}: {e}[/yellow]")
        return {}


def fetch_sofascore_match_incidents(event_id: int) -> list[dict]:
    """
    Fetch match incidents (goals, cards, subs) from Sofascore.
    Returns list of event dicts ready to store in match_events.
    """
    try:
        resp = requests.get(
            f"https://api.sofascore.com/api/v1/event/{event_id}/incidents",
            headers=SOFASCORE_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        incidents = data.get("incidents", [])
        events = []

        for inc in incidents:
            inc_type = inc.get("incidentType", "")
            inc_class = inc.get("incidentClass", "")

            event_type = None
            if inc_type == "goal":
                event_type = "own_goal" if inc_class == "ownGoal" else \
                             "penalty_scored" if inc_class == "penalty" else "goal"
            elif inc_type == "card":
                event_type = "yellow_card" if inc_class == "yellow" else \
                             "red_card" if inc_class == "red" else \
                             "yellow_red_card" if inc_class == "yellowRed" else None
            elif inc_type == "substitution":
                event_type = "substitution_in"
            elif inc_type == "missedPenalty":
                event_type = "penalty_missed"
            elif inc_type == "varDecision":
                event_type = "var_decision"

            if not event_type:
                continue

            # Determine team (home/away from isHome field)
            team = "home" if inc.get("isHome", False) else "away"

            # Player name
            player = inc.get("player", {})
            player_name = player.get("name") if player else None

            # Assist (for goals)
            assist = inc.get("assist1", {})
            assist_name = assist.get("name") if assist else None

            events.append({
                "minute": inc.get("time", 0),
                "added_time": inc.get("addedTime", 0) or 0,
                "event_type": event_type,
                "team": team,
                "player_name": player_name,
                "assist_name": assist_name,
                "detail": inc_class,
                "sofascore_event_id": inc.get("id"),
            })

        return events

    except Exception as e:
        console.print(f"  [yellow]Incidents fetch error for {event_id}: {e}[/yellow]")
        return []


# ============================================================
# Kambi live odds matching
# ============================================================

def build_live_odds_index(live_kambi: list[dict]) -> dict:
    """
    Index Kambi live matches by team name for fast lookup.
    Returns {normalized_key: match_data}
    """
    index = {}
    for m in live_kambi:
        home = m["home_team"].lower().strip()
        away = m["away_team"].lower().strip()
        key = f"{home[:8]}_{away[:8]}"
        index[key] = m
    return index


def find_kambi_odds_for_match(home_team: str, away_team: str,
                               kambi_index: dict) -> dict:
    """Try to find Kambi live odds for a Sofascore match by name fuzzy matching"""
    home = home_team.lower().strip()
    away = away_team.lower().strip()
    key = f"{home[:8]}_{away[:8]}"

    if key in kambi_index:
        return kambi_index[key]

    # Try prefix matching
    for k, v in kambi_index.items():
        k_parts = k.split("_")
        if len(k_parts) >= 2:
            if home[:6] in k_parts[0] and away[:6] in k_parts[1]:
                return v

    return {}


# ============================================================
# Main tracker
# ============================================================

def run_live_tracker(dry_run: bool = False):
    """
    Main entry point. Called every 5 minutes.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"[bold green]═══ OddsIntel Live Tracker: {now_str} ═══[/bold green]\n")

    # 1. Get all live matches from Sofascore
    console.print("[cyan]Fetching live matches from Sofascore...[/cyan]")
    live_events = fetch_sofascore_live_events()
    console.print(f"  {len(live_events)} matches currently live")

    if not live_events:
        console.print("[yellow]No live matches right now.[/yellow]")
        return

    # 2. Get Kambi live odds (one call for all matches)
    console.print("[cyan]Fetching live odds from Kambi...[/cyan]")
    kambi_live = fetch_live_odds("ub")
    console.print(f"  {len(kambi_live)} matches with live odds")
    kambi_index = build_live_odds_index(kambi_live)

    # 3. Process each live match
    console.print("\n[cyan]Processing live matches...[/cyan]\n")

    snapshots_stored = 0
    events_stored = 0
    matched_to_db = 0

    t = Table(title=f"Live Matches ({len(live_events)})")
    t.add_column("Match", style="cyan")
    t.add_column("Min", justify="right")
    t.add_column("Score")
    t.add_column("xG", justify="right")
    t.add_column("SOT", justify="right")
    t.add_column("OU0.5", justify="right")
    t.add_column("OU1.5", justify="right")
    t.add_column("OU2.5", justify="right")
    t.add_column("DB?")

    for event in live_events:
        event_id = event["event_id"]
        home = event["home_team"]
        away = event["away_team"]
        minute = event["minute"]

        # Look up in our DB
        db_match = get_match_by_sofascore_id(event_id)
        if not db_match:
            # Fallback: match by team name + today's date
            today_str = date.today().isoformat()
            db_match = get_match_by_teams_and_date(home, away, today_str)

        db_status = "[green]✓[/green]" if db_match else "[dim]—[/dim]"

        # 4. Fetch live stats from Sofascore
        stats = {}
        incidents = []
        if not dry_run and event_id:
            stats = fetch_sofascore_match_stats(event_id)
            incidents = fetch_sofascore_match_incidents(event_id)
            time.sleep(0.5)  # Be polite

        # 5. Find Kambi live odds
        kambi_match = find_kambi_odds_for_match(home, away, kambi_index)

        # 6. Build snapshot row
        snapshot = {
            "minute": minute,
            "added_time": event.get("added_time", 0),
            "score_home": event["score_home"],
            "score_away": event["score_away"],
            **stats,
        }

        # Add live odds if available
        if kambi_match:
            for field in ["live_ou_05_over", "live_ou_05_under",
                          "live_ou_15_over", "live_ou_15_under",
                          "live_ou_25_over", "live_ou_25_under",
                          "live_ou_35_over", "live_ou_35_under",
                          "live_ou_45_over", "live_ou_45_under",
                          "live_1x2_home", "live_1x2_draw", "live_1x2_away"]:
                val = kambi_match.get(field, 0)
                if val and val > 0:
                    snapshot[field] = val

        # 7. Store in DB (if match is in our DB)
        if db_match and not dry_run:
            match_id = db_match["id"]
            matched_to_db += 1

            try:
                store_live_snapshot(match_id, snapshot)
                snapshots_stored += 1
            except Exception as e:
                console.print(f"  [red]Snapshot error {home} v {away}: {e}[/red]")

            # Store events (goals, cards) — deduped by sofascore_event_id
            for inc in incidents:
                try:
                    stored = store_match_event(match_id, inc)
                    if stored:
                        events_stored += 1
                        event_emoji = {
                            "goal": "⚽",
                            "penalty_scored": "⚽P",
                            "own_goal": "⚽OG",
                            "yellow_card": "🟨",
                            "red_card": "🟥",
                        }.get(inc["event_type"], "")
                        if event_emoji:
                            console.print(
                                f"  [bold]{event_emoji} {inc['event_type']} {inc['minute']}' "
                                f"({inc['team']}) — {home} v {away}[/bold]"
                            )
                except Exception as e:
                    console.print(f"  [yellow]Event error: {e}[/yellow]")

            # Update match status to 'live' if currently scheduled
            if db_match.get("status") == "scheduled" and minute > 0:
                try:
                    update_match_status(match_id, "live")
                except Exception:
                    pass

            # P1.2: Save final stats to match_stats when match finishes
            # Sofascore status_code 100 = "Ended", also check for FT-like states
            if event.get("status_code") == 100 and stats:
                try:
                    store_match_stats(match_id, stats)
                except Exception as e:
                    console.print(f"  [yellow]match_stats save error: {e}[/yellow]")

        # Add to display table
        xg_str = (f"{stats.get('xg_home', 0):.1f}-{stats.get('xg_away', 0):.1f}"
                  if "xg_home" in stats else "-")
        sot_str = (f"{stats.get('shots_on_target_home', 0)}-{stats.get('shots_on_target_away', 0)}"
                   if "shots_on_target_home" in stats else "-")

        t.add_row(
            f"{home[:12]} v {away[:12]}",
            str(minute),
            f"{event['score_home']}-{event['score_away']}",
            xg_str,
            sot_str,
            f"{snapshot.get('live_ou_05_over', 0):.2f}" if snapshot.get("live_ou_05_over") else "-",
            f"{snapshot.get('live_ou_15_over', 0):.2f}" if snapshot.get("live_ou_15_over") else "-",
            f"{snapshot.get('live_ou_25_over', 0):.2f}" if snapshot.get("live_ou_25_over") else "-",
            db_status,
        )

    console.print(t)
    console.print(
        f"\n[bold green]Done:[/bold green] "
        f"{snapshots_stored} snapshots, {events_stored} new events stored | "
        f"{matched_to_db}/{len(live_events)} matches in DB"
    )

    if matched_to_db < len(live_events):
        unmatched = len(live_events) - matched_to_db
        console.print(
            f"[yellow]  {unmatched} matches not in DB — run morning pipeline first "
            f"or these are leagues we don't track yet[/yellow]"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OddsIntel live match tracker")
    parser.add_argument("--test", action="store_true",
                        help="Dry run — fetch data but don't store")
    args = parser.parse_args()

    run_live_tracker(dry_run=args.test)
