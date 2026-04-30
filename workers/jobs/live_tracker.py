"""
OddsIntel — Live Match Tracker (v2)
Runs every 5 minutes during match hours to capture in-play data.

Data sources (API-Football only):
  T5  /odds/live             — 1 call → all live odds
  T6  /fixtures?live=all     — 1 call → all live scores/minutes/status
  T7  /fixtures/lineups      — fired ~40min before KO for upcoming matches
  T8  /fixtures/events       — per live match (goals, cards, subs, VAR)

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
    get_fixture_statistics, parse_fixture_stats,
)
from workers.api_clients.supabase_client import (
    get_client,
    store_live_snapshot,
    store_live_odds,
    store_match_events_af,
    store_match_lineups,
    store_match_stats_full,
    update_match_status,
    get_match_by_teams_and_date,
)
from workers.api_clients.db import (
    build_af_id_map as _db_build_af_id_map,
    find_match_by_teams_and_date as _db_find_match,
    store_live_snapshots_batch,
    store_live_odds_batch,
    store_match_events_batch,
    update_match_status_sql,
    DATABASE_URL,
)

console = Console()


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

    # Fallback: team name + today's date (uses direct SQL with JOIN if available)
    today_str = date.today().isoformat()
    home = af_fix.get("home_team", "")
    away = af_fix.get("away_team", "")
    if home and away:
        if DATABASE_URL:
            return _db_find_match(home, away, today_str)
        return get_match_by_teams_and_date(home, away, today_str)

    return None


def _build_af_id_map(client) -> dict[int, dict]:
    """
    Build {api_football_id: match_record} for all of today's matches in DB.
    Uses direct SQL (no 1K row limit) when DATABASE_URL is set.
    """
    if DATABASE_URL:
        return _db_build_af_id_map()

    # Fallback: PostgREST (has 1K row cap risk)
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

    # Batch collection for direct SQL bulk writes
    _pending_snapshots = []
    _pending_odds = []
    _pending_status = []

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

        # ── T4-live: Fetch live match statistics (xG, shots, possession) ──
        if af_id and not dry_run:
            try:
                stats_resp = get_fixture_statistics(af_id)
                if stats_resp:
                    parsed_stats = parse_fixture_stats(stats_resp)
                    if parsed_stats:
                        snapshot["xg_home"] = parsed_stats.get("xg_home")
                        snapshot["xg_away"] = parsed_stats.get("xg_away")
                        snapshot["shots_home"] = parsed_stats.get("shots_home")
                        snapshot["shots_away"] = parsed_stats.get("shots_away")
                        snapshot["shots_on_target_home"] = parsed_stats.get("shots_on_target_home")
                        snapshot["shots_on_target_away"] = parsed_stats.get("shots_on_target_away")
                        snapshot["possession_home"] = parsed_stats.get("possession_home")
                        snapshot["corners_home"] = parsed_stats.get("corners_home")
                        snapshot["corners_away"] = parsed_stats.get("corners_away")
                        snapshot["attacks_home"] = parsed_stats.get("passes_home")  # proxy for attacking activity
                        snapshot["attacks_away"] = parsed_stats.get("passes_away")
            except Exception as e:
                console.print(f"  [yellow]Stats error {home} v {away}: {e}[/yellow]")

        # ── T8: Fetch match events ─────────────────────────────────────────
        new_events = 0
        red_cards_home = 0
        red_cards_away = 0
        if db_match and not dry_run and af_id:
            try:
                raw_events = get_fixture_events(af_id)
                parsed_events = parse_fixture_events(raw_events)
                if parsed_events:
                    if DATABASE_URL:
                        new_events = store_match_events_batch(
                            db_match["id"], parsed_events,
                            home_team_api_id=home_api_id
                        )
                    else:
                        new_events = store_match_events_af(
                            db_match["id"], parsed_events,
                            home_team_api_id=home_api_id
                        )
                    events_stored += new_events

                    # Derive red card state from events for snapshot context
                    for ev in parsed_events:
                        if ev.get("event_type") in ("red_card", "yellow_red_card"):
                            team_id = ev.get("team_api_id")
                            if team_id == home_api_id:
                                red_cards_home += 1
                            else:
                                red_cards_away += 1
            except Exception as e:
                console.print(f"  [yellow]Events error {home} v {away}: {e}[/yellow]")

        # ── Load pre-match model context (once per match) ──────────────────
        if db_match and not dry_run:
            try:
                preds = client.table("predictions").select(
                    "market, model_probability"
                ).eq("match_id", db_match["id"]).in_(
                    "source", ["poisson", "ensemble"]
                ).execute()
                for p in (preds.data or []):
                    if p["market"] == "over25":
                        snapshot["model_ou25_prob"] = float(p["model_probability"])
                    # Use 1x2_home implied xG as proxy for model expected goals
                    # (actual xG stored when available from Poisson output)
            except Exception:
                pass

        # ── Collect for DB write ───────────────────────────────────────────
        if db_match and not dry_run:
            match_id = db_match["id"]
            matched_to_db += 1
            snapshot["match_id"] = match_id

            # Collect for batch write
            _pending_snapshots.append(snapshot)

            for lr in fixture_live_odds:
                lr["match_id"] = match_id
            _pending_odds.extend(fixture_live_odds)

            # Status updates
            if db_match.get("status") == "scheduled" and minute > 0:
                _pending_status.append((match_id, "live"))
            if af_fix.get("status_short") in ("FT", "AET", "PEN"):
                _pending_status.append((match_id, "finished"))

        # ── Build display row ──────────────────────────────────────────────
        xg_h = snapshot.get("xg_home")
        xg_a = snapshot.get("xg_away")
        xg_str = f"{xg_h:.1f}-{xg_a:.1f}" if xg_h is not None and xg_a is not None else "-"
        sot_h = snapshot.get("shots_on_target_home")
        sot_a = snapshot.get("shots_on_target_away")
        sot_str = f"{sot_h}-{sot_a}" if sot_h is not None and sot_a is not None else "-"
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

    # ── Batch write to DB (direct SQL — much faster than PostgREST) ────────
    if not dry_run and DATABASE_URL:
        try:
            snapshots_stored = store_live_snapshots_batch(_pending_snapshots)
        except Exception as e:
            console.print(f"[red]Batch snapshot write failed: {e}[/red]")
        try:
            live_odds_stored = store_live_odds_batch(_pending_odds)
        except Exception as e:
            console.print(f"[yellow]Batch odds write failed: {e}[/yellow]")
        for match_id, status in _pending_status:
            try:
                update_match_status_sql(match_id, status)
            except Exception:
                pass
    elif not dry_run:
        # Fallback: PostgREST one-at-a-time (GH Actions / no DATABASE_URL)
        for s in _pending_snapshots:
            try:
                store_live_snapshot(s["match_id"], s)
                snapshots_stored += 1
            except Exception as e:
                console.print(f"[red]Snapshot error: {e}[/red]")
        for match_id_group in set(r["match_id"] for r in _pending_odds):
            match_odds = [r for r in _pending_odds if r["match_id"] == match_id_group]
            try:
                store_live_odds(match_id_group, match_odds)
                live_odds_stored += len(match_odds)
            except Exception as e:
                console.print(f"[yellow]Live odds store error: {e}[/yellow]")
        for match_id, status in _pending_status:
            try:
                update_match_status(match_id, status)
            except Exception:
                pass

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
