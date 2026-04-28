"""
OddsIntel — Live Match Tracker (v2)
Runs every 5 minutes during match hours to capture in-play data.

Data sources (API-Football primary, Sofascore fallback for rich stats):
  T5  /odds/live             — 1 call → all live odds (replaces Kambi scraping)
  T6  /fixtures?live=all     — 1 call → all live scores/minutes/status
  T7  /fixtures/lineups      — fired ~40min before KO for upcoming matches
  T8  /fixtures/events       — per live match (goals, cards, subs, VAR)

Sofascore kept as stats-only fallback (xG, shots, possession — not in AF live).

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
from datetime import datetime, date, timezone, timedelta
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import (
    get_live_fixtures,
    get_live_odds, parse_live_odds,
    get_fixture_events, parse_fixture_events,
    get_fixture_lineups, parse_fixture_lineups,
)
from workers.api_clients.supabase_client import (
    get_client,
    store_live_snapshot,
    store_live_odds,
    store_match_events_af,
    store_match_lineups,
    store_match_stats_full,
    update_match_status,
    get_match_by_sofascore_id,
    get_match_by_teams_and_date,
)

console = Console()

SOFASCORE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}


# ============================================================
# AF Live Data Parsing
# ============================================================

def _parse_af_live_fixture(af_fix: dict) -> dict:
    """
    Parse a single API-Football live fixture into a normalised dict.
    """
    fixture = af_fix.get("fixture", {})
    teams = af_fix.get("teams", {})
    goals = af_fix.get("goals", {})
    status = fixture.get("status", {})

    return {
        "af_fixture_id": fixture.get("id"),
        "home_team": teams.get("home", {}).get("name", ""),
        "home_team_api_id": teams.get("home", {}).get("id"),
        "away_team": teams.get("away", {}).get("name", ""),
        "away_team_api_id": teams.get("away", {}).get("id"),
        "score_home": goals.get("home", 0) or 0,
        "score_away": goals.get("away", 0) or 0,
        "minute": status.get("elapsed", 0) or 0,
        "added_time": 0,
        "status_short": status.get("short", ""),
        "league_name": af_fix.get("league", {}).get("name", ""),
        "country": af_fix.get("league", {}).get("country", ""),
    }


# ============================================================
# Sofascore fallback — stats only (xG, possession, shots)
# ============================================================

def _fetch_sofascore_stats(event_id: int) -> dict:
    """
    Fetch live xG, possession, shots from Sofascore for a single match.
    Returns empty dict on any failure (Sofascore is fragile).
    """
    try:
        resp = requests.get(
            f"https://api.sofascore.com/api/v1/event/{event_id}/statistics",
            headers=SOFASCORE_HEADERS,
            timeout=8,
        )
        if resp.status_code != 200:
            return {}

        data = resp.json()
        stats = {}

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
    except Exception:
        return {}


# ============================================================
# DB match lookup helpers
# ============================================================

def _lookup_db_match(af_fix: dict, af_id_map: dict, client) -> dict | None:
    """
    Find the DB match record for a live AF fixture.
    Tries: af_fixture_id → team name + date fallback.
    """
    af_id = af_fix.get("af_fixture_id")
    if af_id and af_id in af_id_map:
        return af_id_map[af_id]

    # Fallback: team name + today's date
    today_str = date.today().isoformat()
    home = af_fix.get("home_team", "")
    away = af_fix.get("away_team", "")
    if home and away:
        return get_match_by_teams_and_date(home, away, today_str)

    return None


def _build_af_id_map(client) -> dict[int, dict]:
    """
    Build {api_football_id: match_record} for all of today's matches in DB.
    Called once per tracker run.
    """
    today = date.today().isoformat()
    result = client.table("matches").select(
        "id, api_football_id, home_team_id, away_team_id, "
        "date, status, lineups_fetched_at"
    ).gte("date", f"{today}T00:00:00").lte("date", f"{today}T23:59:59").execute()

    mapping = {}
    for m in result.data or []:
        af_id = m.get("api_football_id")
        if af_id:
            mapping[int(af_id)] = m
    return mapping


# ============================================================
# T7: Lineup fetcher (called for pre-match matches within 60min of KO)
# ============================================================

def _fetch_lineups_for_upcoming(af_id_map: dict[int, dict], dry_run: bool = False):
    """
    T7: For matches starting within the next 60 minutes that don't yet have
    lineups, fetch and store them.
    """
    now = datetime.now(timezone.utc)
    lineups_fetched = 0

    for af_id, match in af_id_map.items():
        if match.get("lineups_fetched_at"):
            continue  # Already fetched

        if match.get("status") != "scheduled":
            continue  # Only pre-match

        # Check if kickoff is within 60 minutes
        try:
            kickoff = datetime.fromisoformat(match["date"].replace("Z", "+00:00"))
        except (ValueError, KeyError):
            continue

        mins_to_ko = (kickoff - now).total_seconds() / 60
        if not (0 < mins_to_ko <= 65):
            continue

        if dry_run:
            console.print(f"  [dim]DRY RUN: Would fetch lineups for AF fixture {af_id} "
                          f"({mins_to_ko:.0f}min to KO)[/dim]")
            continue

        try:
            raw = get_fixture_lineups(af_id)
            if not raw:
                continue
            parsed = parse_fixture_lineups(raw)
            if parsed:
                store_match_lineups(match["id"], parsed)
                lineups_fetched += 1
                console.print(
                    f"  [green]Lineups stored:[/green] AF {af_id} | "
                    f"{parsed.get('formation_home', '?')} vs {parsed.get('formation_away', '?')}"
                )
        except Exception as e:
            console.print(f"  [yellow]Lineup fetch error AF {af_id}: {e}[/yellow]")

    return lineups_fetched


# ============================================================
# Main tracker
# ============================================================

def run_live_tracker(dry_run: bool = False):
    """
    Main entry point. Called every 5 minutes via cron.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"[bold green]═══ OddsIntel Live Tracker v2: {now_str} ═══[/bold green]\n")

    client = get_client()

    # Build today's AF ID → match record map (one DB query)
    af_id_map = _build_af_id_map(client)
    console.print(f"[dim]{len(af_id_map)} today's matches in DB[/dim]")

    # ── T7: Lineup fetch for upcoming matches ──────────────────────────────
    console.print("[cyan]T7: Checking for pre-match lineups...[/cyan]")
    lineups_count = _fetch_lineups_for_upcoming(af_id_map, dry_run=dry_run)
    if lineups_count > 0:
        console.print(f"  {lineups_count} lineup sets fetched")

    # ── T6: Get all live fixtures (1 call) ─────────────────────────────────
    console.print("[cyan]T6: Fetching live fixtures from API-Football...[/cyan]")
    live_fixtures_raw = []
    try:
        live_fixtures_raw = get_live_fixtures()
        console.print(f"  {len(live_fixtures_raw)} matches currently live")
    except Exception as e:
        console.print(f"  [yellow]AF live fixtures error: {e}[/yellow]")

    if not live_fixtures_raw:
        console.print("[yellow]No live matches right now.[/yellow]")
        return

    # Parse live fixtures
    live_fixtures = [_parse_af_live_fixture(f) for f in live_fixtures_raw]

    # ── T5: Get all live odds (1 call) ─────────────────────────────────────
    console.print("[cyan]T5: Fetching live odds from API-Football...[/cyan]")
    live_odds_by_fixture: dict[int, list[dict]] = {}
    try:
        raw_live_odds = get_live_odds()
        live_odds_by_fixture = parse_live_odds(raw_live_odds)
        console.print(f"  {len(live_odds_by_fixture)} fixtures with live odds")
    except Exception as e:
        console.print(f"  [yellow]AF live odds error: {e}[/yellow]")

    # ── Process each live match ─────────────────────────────────────────────
    console.print("\n[cyan]Processing live matches...[/cyan]\n")

    snapshots_stored = 0
    events_stored = 0
    live_odds_stored = 0
    matched_to_db = 0

    t = Table(title=f"Live Matches ({len(live_fixtures)})")
    t.add_column("Match", style="cyan")
    t.add_column("Min", justify="right")
    t.add_column("Score")
    t.add_column("xG", justify="right")
    t.add_column("SOT", justify="right")
    t.add_column("OU2.5", justify="right")
    t.add_column("1X2", justify="right")
    t.add_column("Events", justify="right")
    t.add_column("DB?")

    for af_fix in live_fixtures:
        af_id = af_fix.get("af_fixture_id")
        home = af_fix["home_team"]
        away = af_fix["away_team"]
        minute = af_fix["minute"]
        home_api_id = af_fix.get("home_team_api_id")

        # Look up DB match
        db_match = _lookup_db_match(af_fix, af_id_map, client)
        db_status = "[green]✓[/green]" if db_match else "[dim]—[/dim]"

        # ── Sofascore stats fallback (xG, possession, shots — not in AF live) ─
        ss_stats = {}
        if not dry_run:
            # Only try Sofascore if we have a Sofascore event ID linked
            sofascore_id = db_match.get("sofascore_event_id") if db_match else None
            if sofascore_id:
                ss_stats = _fetch_sofascore_stats(sofascore_id)
                time.sleep(0.3)

        # ── T5: Resolve live odds for this fixture ─────────────────────────
        fixture_live_odds = live_odds_by_fixture.get(af_id, []) if af_id else []

        # Extract display values
        ou25_over = next(
            (r["odds"] for r in fixture_live_odds
             if r["market"] == "over_under_25" and r["selection"] == "over"),
            None
        )
        home_odds = next(
            (r["odds"] for r in fixture_live_odds
             if r["market"] == "1x2" and r["selection"] == "home"),
            None
        )
        away_odds = next(
            (r["odds"] for r in fixture_live_odds
             if r["market"] == "1x2" and r["selection"] == "away"),
            None
        )

        # ── Build snapshot row ─────────────────────────────────────────────
        snapshot = {
            "minute": minute,
            "added_time": af_fix.get("added_time", 0),
            "score_home": af_fix["score_home"],
            "score_away": af_fix["score_away"],
            **ss_stats,
        }

        # Embed live odds into snapshot for O/U lines
        for row in fixture_live_odds:
            mkt = row["market"]
            sel = row["selection"]
            if mkt.startswith("over_under_") and sel in ("over", "under"):
                key = f"live_{mkt.replace('over_under_', 'ou')}_{sel}"
                snapshot[key] = row["odds"]
            elif mkt == "1x2":
                snapshot[f"live_1x2_{sel}"] = row["odds"]

        # ── T8: Fetch match events ─────────────────────────────────────────
        new_events = 0
        if db_match and not dry_run and af_id:
            try:
                raw_events = get_fixture_events(af_id)
                parsed_events = parse_fixture_events(raw_events)
                if parsed_events:
                    new_events = store_match_events_af(
                        db_match["id"], parsed_events,
                        home_team_api_id=home_api_id
                    )
                    events_stored += new_events
            except Exception as e:
                console.print(f"  [yellow]Events error {home} v {away}: {e}[/yellow]")

        # ── Store in DB ────────────────────────────────────────────────────
        if db_match and not dry_run:
            match_id = db_match["id"]
            matched_to_db += 1

            # Live snapshot
            try:
                store_live_snapshot(match_id, snapshot)
                snapshots_stored += 1
            except Exception as e:
                console.print(f"  [red]Snapshot error {home} v {away}: {e}[/red]")

            # T5: Store live odds in odds_snapshots
            if fixture_live_odds:
                try:
                    store_live_odds(match_id, fixture_live_odds, minute=minute)
                    live_odds_stored += len(fixture_live_odds)
                except Exception as e:
                    console.print(f"  [yellow]Live odds store error: {e}[/yellow]")

            # Update match status to 'live' if currently scheduled
            if db_match.get("status") == "scheduled" and minute > 0:
                try:
                    update_match_status(match_id, "live")
                except Exception:
                    pass

            # When match finishes: store final stats
            if af_fix.get("status_short") in ("FT", "AET", "PEN") and ss_stats:
                try:
                    store_match_stats_full(match_id, ss_stats)
                except Exception:
                    pass
            # Also mark as finished in DB
            if af_fix.get("status_short") in ("FT", "AET", "PEN"):
                try:
                    update_match_status(match_id, "finished")
                except Exception:
                    pass

        # ── Build display row ──────────────────────────────────────────────
        xg_str = (f"{ss_stats.get('xg_home', 0):.1f}-{ss_stats.get('xg_away', 0):.1f}"
                  if "xg_home" in ss_stats else "-")
        sot_str = (f"{ss_stats.get('shots_on_target_home', 0)}-{ss_stats.get('shots_on_target_away', 0)}"
                   if "shots_on_target_home" in ss_stats else "-")
        ou25_str = f"{ou25_over:.2f}" if ou25_over else "-"
        odds_str = f"{home_odds:.2f}/{away_odds:.2f}" if home_odds and away_odds else "-"

        t.add_row(
            f"{home[:13]} v {away[:13]}",
            str(minute),
            f"{af_fix['score_home']}-{af_fix['score_away']}",
            xg_str,
            sot_str,
            ou25_str,
            odds_str,
            str(new_events) if new_events else "-",
            db_status,
        )

    console.print(t)
    console.print(
        f"\n[bold green]Done:[/bold green] "
        f"{snapshots_stored} snapshots | "
        f"{live_odds_stored} live odds rows | "
        f"{events_stored} new events | "
        f"{matched_to_db}/{len(live_fixtures)} matched to DB"
    )

    if matched_to_db < len(live_fixtures):
        unmatched = len(live_fixtures) - matched_to_db
        console.print(
            f"[yellow]  {unmatched} live matches not in DB — "
            f"run morning pipeline first or these are leagues we don't track[/yellow]"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OddsIntel live match tracker v2")
    parser.add_argument("--test", action="store_true",
                        help="Dry run — fetch data but don't store")
    args = parser.parse_args()

    run_live_tracker(dry_run=args.test)
