"""
OddsIntel — API-Football Backfill Script (T2–T13)
Backfills today's (or a given date's) matches with all newly integrated data.

Runs all enrichment in priority order:
  T3  Injuries (batched)
  T2  Team season statistics
  T9  League standings
  T10 H2H history
  T7  Lineups (if available for today's matches)
  T4  Half-time stats (finished matches only)
  T8  Match events (finished/live matches)
  T12 Player stats (finished matches only)
  T11 Sidelined history (for injured players found in T3)
  T13 Transfers (one per unique team — weekly cadence, run manually)

Usage:
  python scripts/backfill_api_football.py               # Today
  python scripts/backfill_api_football.py --date 2026-04-28
  python scripts/backfill_api_football.py --tasks t2,t3,t9  # Only specific tasks
  python scripts/backfill_api_football.py --transfers   # Also run T13 (slow)
  python scripts/backfill_api_football.py --dry-run     # Print counts, no writes
"""

import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime, date, timezone
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import (
    get_fixtures_by_date,
    get_team_statistics, parse_team_statistics,
    get_injuries_batched, parse_injuries,
    get_fixture_statistics, parse_fixture_stats,
    get_fixture_statistics_halftime, parse_fixture_stats_halftime,
    get_live_odds, parse_live_odds,
    get_fixture_events, parse_fixture_events,
    get_fixture_lineups, parse_fixture_lineups,
    get_standings, parse_standings,
    get_h2h, parse_h2h,
    get_sidelined, parse_sidelined,
    get_fixture_players, parse_fixture_players,
    get_transfers, parse_transfers,
    get_remaining_requests,
)
from workers.api_clients.supabase_client import (
    get_client,
    store_team_season_stats,
    store_match_injuries,
    store_match_stats_full,
    store_match_events_af,
    store_match_lineups,
    store_league_standings,
    store_match_h2h,
    store_player_sidelined,
    store_match_player_stats,
    store_team_transfers,
)

console = Console()


def _get_todays_matches_with_meta(client, target_date: str) -> list[dict]:
    """
    Get all DB matches for target_date with their AF IDs and team IDs.
    Returns list of match dicts.
    """
    result = client.table("matches").select(
        "id, api_football_id, date, status, lineups_fetched_at"
    ).gte("date", f"{target_date}T00:00:00").lte(
        "date", f"{target_date}T23:59:59"
    ).execute()
    return result.data or []


def _enrich_with_af_fixtures(target_date: str) -> dict[int, dict]:
    """
    Fetch AF fixtures for target_date and build {af_id: fixture_meta} dict.
    Includes league_api_id, season, home/away team api IDs.
    """
    from workers.api_clients.api_football import get_fixtures_by_date
    fixtures = get_fixtures_by_date(target_date)

    meta: dict[int, dict] = {}
    for af_fix in fixtures:
        fid = af_fix.get("fixture", {}).get("id")
        if not fid:
            continue
        teams = af_fix.get("teams", {})
        league = af_fix.get("league", {})
        status = af_fix.get("fixture", {}).get("status", {}).get("short", "NS")
        meta[fid] = {
            "af_fixture_id": fid,
            "home_team_api_id": teams.get("home", {}).get("id"),
            "away_team_api_id": teams.get("away", {}).get("id"),
            "league_api_id": league.get("id"),
            "season": league.get("season"),
            "status": status,
            "is_finished": status in ("FT", "AET", "PEN"),
        }
    return meta


def run_backfill(target_date: str, tasks: set[str], run_transfers: bool,
                 dry_run: bool):
    console.print(f"\n[bold green]═══ OddsIntel API-Football Backfill: {target_date} ═══[/bold green]")
    if dry_run:
        console.print("[yellow]DRY RUN — no data will be written[/yellow]")

    # Check remaining request budget
    try:
        budget = get_remaining_requests()
        console.print(
            f"API Budget: {budget['current']} used / {budget['limit_day']} limit "
            f"({budget['remaining']} remaining)\n"
        )
    except Exception as e:
        console.print(f"[yellow]Could not check API budget: {e}[/yellow]\n")

    client = get_client()

    # ── Load DB matches ────────────────────────────────────────────────────
    db_matches = _get_todays_matches_with_meta(client, target_date)
    console.print(f"DB matches for {target_date}: {len(db_matches)}")

    if not db_matches:
        console.print("[yellow]No matches found in DB for this date. Run the morning pipeline first.[/yellow]")
        return

    # ── Load AF fixture metadata ───────────────────────────────────────────
    console.print("[cyan]Fetching AF fixtures metadata...[/cyan]")
    af_meta = _enrich_with_af_fixtures(target_date)
    console.print(f"  {len(af_meta)} AF fixtures found")

    # Build unified list: DB match + AF meta
    enriched: list[dict] = []
    for m in db_matches:
        af_id = m.get("api_football_id")
        af_info = af_meta.get(int(af_id)) if af_id else {}
        enriched.append({
            "match_id": m["id"],
            "af_fixture_id": int(af_id) if af_id else None,
            "home_team_api_id": af_info.get("home_team_api_id") or m.get("home_team_api_id"),
            "away_team_api_id": af_info.get("away_team_api_id") or m.get("away_team_api_id"),
            "league_api_id": af_info.get("league_api_id"),
            "season": af_info.get("season"),
            "status": m.get("status"),
            "is_finished": af_info.get("is_finished", m.get("status") == "finished"),
            "lineups_fetched_at": m.get("lineups_fetched_at"),
        })

    with_af = [m for m in enriched if m["af_fixture_id"]]
    finished = [m for m in enriched if m["is_finished"]]
    console.print(f"  {len(with_af)} with AF fixture ID | {len(finished)} finished\n")

    counts = {}

    # ────────────────────────────────────────────────────────────────────────
    # T3: Injuries (batched — ~7 calls for 130 fixtures)
    # ────────────────────────────────────────────────────────────────────────
    if "t3" in tasks:
        console.print("[bold cyan]T3: Injuries[/bold cyan]")
        fixture_ids = [m["af_fixture_id"] for m in with_af if m["af_fixture_id"]]
        injuries_by_fixture: dict[int, list] = {}
        try:
            injuries_by_fixture = get_injuries_batched(fixture_ids)
        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")

        inj_stored = 0
        injured_player_ids: list[dict] = []  # for T11

        for m in with_af:
            fid = m["af_fixture_id"]
            injuries = injuries_by_fixture.get(fid, [])
            if not injuries:
                continue
            parsed = parse_injuries(injuries, home_team_api_id=m["home_team_api_id"])
            if not dry_run:
                inj_stored += store_match_injuries(m["match_id"], fid, parsed)
            else:
                inj_stored += len(parsed)
            # Collect player IDs for T11
            for inj in parsed:
                if inj.get("player_id"):
                    injured_player_ids.append({
                        "player_id": inj["player_id"],
                        "player_name": inj.get("player_name"),
                        "team_api_id": inj.get("team_api_id"),
                    })

        console.print(f"  [green]{inj_stored} injury records[/green] "
                      f"({len(injured_player_ids)} unique players for T11)")
        counts["t3"] = inj_stored
    else:
        injured_player_ids = []

    # ────────────────────────────────────────────────────────────────────────
    # T2: Team season statistics (~1 call per unique team/league/season)
    # ────────────────────────────────────────────────────────────────────────
    if "t2" in tasks:
        console.print("[bold cyan]T2: Team Season Statistics[/bold cyan]")
        seen: set[tuple] = set()
        t2_stored = 0

        for m in with_af:
            league_id = m.get("league_api_id")
            season = m.get("season")
            if not league_id or not season:
                continue

            for team_api_id in [m["home_team_api_id"], m["away_team_api_id"]]:
                if not team_api_id:
                    continue
                key = (team_api_id, league_id, season)
                if key in seen:
                    continue
                seen.add(key)

                try:
                    raw = get_team_statistics(team_api_id, league_id, season)
                    if not raw:
                        continue
                    parsed = parse_team_statistics(raw)
                    if not dry_run:
                        store_team_season_stats(team_api_id, league_id, season, parsed)
                    t2_stored += 1
                except Exception as e:
                    console.print(f"  [yellow]Team {team_api_id} error: {e}[/yellow]")
                    continue

        console.print(f"  [green]{t2_stored} team stat records[/green] "
                      f"({len(seen)} unique team/league/season combos)")
        counts["t2"] = t2_stored

    # ────────────────────────────────────────────────────────────────────────
    # T9: League standings (~1 call per unique league)
    # ────────────────────────────────────────────────────────────────────────
    if "t9" in tasks:
        console.print("[bold cyan]T9: League Standings[/bold cyan]")
        seen_leagues: set[tuple] = set()
        t9_rows = 0

        for m in with_af:
            league_id = m.get("league_api_id")
            season = m.get("season")
            if not league_id or not season:
                continue
            key = (league_id, season)
            if key in seen_leagues:
                continue
            seen_leagues.add(key)

            try:
                raw = get_standings(league_id, season)
                if not raw:
                    continue
                rows = parse_standings(raw)
                if not dry_run:
                    t9_rows += store_league_standings(league_id, season, rows)
                else:
                    t9_rows += len(rows)
            except Exception as e:
                console.print(f"  [yellow]League {league_id} standings error: {e}[/yellow]")
                continue

        console.print(f"  [green]{t9_rows} standing rows[/green] "
                      f"across {len(seen_leagues)} leagues")
        counts["t9"] = t9_rows

    # ────────────────────────────────────────────────────────────────────────
    # T10: H2H (~1 call per fixture)
    # ────────────────────────────────────────────────────────────────────────
    if "t10" in tasks:
        console.print("[bold cyan]T10: H2H History[/bold cyan]")
        t10_stored = 0

        for m in with_af:
            home_id = m["home_team_api_id"]
            away_id = m["away_team_api_id"]
            if not home_id or not away_id:
                continue

            try:
                raw = get_h2h(home_id, away_id, last=10)
                if not raw:
                    continue
                parsed = parse_h2h(raw, home_team_api_id=home_id)
                if not dry_run:
                    store_match_h2h(m["match_id"], parsed)
                t10_stored += 1
            except Exception as e:
                console.print(f"  [yellow]H2H error {m['match_id'][:8]}: {e}[/yellow]")
                continue

        console.print(f"  [green]{t10_stored} H2H records[/green]")
        counts["t10"] = t10_stored

    # ────────────────────────────────────────────────────────────────────────
    # T7: Lineups (all matches — may or may not be available)
    # ────────────────────────────────────────────────────────────────────────
    if "t7" in tasks:
        console.print("[bold cyan]T7: Lineups[/bold cyan]")
        t7_stored = 0

        for m in with_af:
            if m.get("lineups_fetched_at"):
                continue  # Already have them

            try:
                raw = get_fixture_lineups(m["af_fixture_id"])
                if not raw:
                    continue
                parsed = parse_fixture_lineups(raw)
                if not parsed:
                    continue
                if not dry_run:
                    store_match_lineups(m["match_id"], parsed)
                t7_stored += 1
            except Exception:
                continue

        console.print(f"  [green]{t7_stored} lineup sets[/green]")
        counts["t7"] = t7_stored

    # ────────────────────────────────────────────────────────────────────────
    # T4: Half-time stats + full stats (finished matches only)
    # ────────────────────────────────────────────────────────────────────────
    if "t4" in tasks:
        console.print("[bold cyan]T4: Full + Half-Time Statistics[/bold cyan]")
        t4_full = t4_ht = 0

        for m in finished:
            if not m["af_fixture_id"]:
                continue

            try:
                # Full match stats
                raw_full = get_fixture_statistics(m["af_fixture_id"])
                full_stats = parse_fixture_stats(raw_full)

                # Half-time stats
                ht_response = get_fixture_statistics_halftime(m["af_fixture_id"])
                ht_stats = parse_fixture_stats_halftime(ht_response)

                merged = {**full_stats, **ht_stats}
                if merged and not dry_run:
                    store_match_stats_full(m["match_id"], merged)

                if full_stats:
                    t4_full += 1
                if ht_stats:
                    t4_ht += 1
            except Exception as e:
                console.print(f"  [yellow]Stats error {m['af_fixture_id']}: {e}[/yellow]")
                continue

        console.print(f"  [green]{t4_full} full stats | {t4_ht} with half-time splits[/green]")
        counts["t4"] = t4_full

    # ────────────────────────────────────────────────────────────────────────
    # T8: Match events (finished + live matches)
    # ────────────────────────────────────────────────────────────────────────
    if "t8" in tasks:
        console.print("[bold cyan]T8: Match Events[/bold cyan]")
        t8_total = 0
        eligible = [m for m in enriched if m.get("af_fixture_id") and
                    m.get("is_finished")]

        for m in eligible:
            try:
                raw = get_fixture_events(m["af_fixture_id"])
                parsed = parse_fixture_events(raw)
                if parsed and not dry_run:
                    stored = store_match_events_af(
                        m["match_id"], parsed,
                        home_team_api_id=m["home_team_api_id"]
                    )
                    t8_total += stored
                else:
                    t8_total += len(parsed)
            except Exception as e:
                console.print(f"  [yellow]Events error {m['af_fixture_id']}: {e}[/yellow]")
                continue

        console.print(f"  [green]{t8_total} events stored[/green]")
        counts["t8"] = t8_total

    # ────────────────────────────────────────────────────────────────────────
    # T12: Per-player stats (finished matches only)
    # ────────────────────────────────────────────────────────────────────────
    if "t12" in tasks:
        console.print("[bold cyan]T12: Player Match Statistics[/bold cyan]")
        t12_total = 0

        for m in finished:
            if not m["af_fixture_id"]:
                continue

            try:
                raw = get_fixture_players(m["af_fixture_id"])
                parsed = parse_fixture_players(raw, home_team_api_id=m["home_team_api_id"])
                if parsed and not dry_run:
                    stored = store_match_player_stats(
                        m["match_id"], m["af_fixture_id"], parsed
                    )
                    t12_total += stored
                else:
                    t12_total += len(parsed)
            except Exception as e:
                console.print(f"  [yellow]Player stats error {m['af_fixture_id']}: {e}[/yellow]")
                continue

        console.print(f"  [green]{t12_total} player stat rows[/green]")
        counts["t12"] = t12_total

    # ────────────────────────────────────────────────────────────────────────
    # T11: Player sidelined history (for players found injured in T3)
    # ────────────────────────────────────────────────────────────────────────
    if "t11" in tasks and injured_player_ids:
        console.print(f"[bold cyan]T11: Player Sidelined History "
                      f"({len(injured_player_ids)} players)[/bold cyan]")
        t11_total = 0
        seen_players: set[int] = set()

        for p in injured_player_ids:
            pid = p["player_id"]
            if pid in seen_players:
                continue
            seen_players.add(pid)

            try:
                raw = get_sidelined(pid)
                if not raw:
                    continue
                parsed = parse_sidelined(
                    raw, pid,
                    player_name=p.get("player_name"),
                    team_api_id=p.get("team_api_id"),
                )
                if parsed and not dry_run:
                    stored = store_player_sidelined(parsed)
                    t11_total += stored
                else:
                    t11_total += len(parsed)
            except Exception as e:
                console.print(f"  [yellow]Sidelined error player {pid}: {e}[/yellow]")
                continue

        console.print(f"  [green]{t11_total} sidelined history rows[/green]")
        counts["t11"] = t11_total

    # ────────────────────────────────────────────────────────────────────────
    # T13: Transfers (slow — run with --transfers flag only)
    # ────────────────────────────────────────────────────────────────────────
    if "t13" in tasks and run_transfers:
        console.print("[bold cyan]T13: Team Transfers[/bold cyan]")
        seen_teams: set[int] = set()
        t13_total = 0

        for m in with_af:
            for team_id in [m["home_team_api_id"], m["away_team_api_id"]]:
                if not team_id or team_id in seen_teams:
                    continue
                seen_teams.add(team_id)

                try:
                    raw = get_transfers(team_id)
                    parsed = parse_transfers(raw, team_api_id=team_id)
                    if parsed and not dry_run:
                        stored = store_team_transfers(team_id, parsed)
                        t13_total += stored
                    else:
                        t13_total += len(parsed)
                except Exception as e:
                    console.print(f"  [yellow]Transfers error team {team_id}: {e}[/yellow]")
                    continue

        console.print(f"  [green]{t13_total} transfer rows[/green]")
        counts["t13"] = t13_total
    elif "t13" in tasks and not run_transfers:
        console.print("[dim]T13: Skipped (run with --transfers to include)[/dim]")

    # ── Summary ────────────────────────────────────────────────────────────
    console.print(f"\n[bold green]═══ Backfill Complete ═══[/bold green]")

    t = Table(title="Backfill Summary")
    t.add_column("Task")
    t.add_column("Description")
    t.add_column("Records", justify="right")

    task_labels = {
        "t2": "Team Season Stats",
        "t3": "Match Injuries",
        "t4": "Full + Half-time Stats",
        "t7": "Lineups",
        "t8": "Match Events",
        "t9": "League Standings",
        "t10": "H2H History",
        "t11": "Player Sidelined",
        "t12": "Player Match Stats",
        "t13": "Team Transfers",
    }

    for task, label in task_labels.items():
        if task in counts:
            t.add_row(task.upper(), label, str(counts[task]))

    console.print(t)

    total = sum(counts.values())
    console.print(f"\nTotal records processed: [bold green]{total:,}[/bold green]")
    if dry_run:
        console.print("[yellow]DRY RUN — nothing was actually written[/yellow]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill API-Football enrichment data")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--tasks", default="t2,t3,t4,t7,t8,t9,t10,t11,t12,t13",
                        help="Comma-separated task list (default: all)")
    parser.add_argument("--transfers", action="store_true",
                        help="Include T13 transfers (slow, ~200 calls/100 teams)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count what would be stored without writing")
    args = parser.parse_args()

    task_set = set(t.strip().lower() for t in args.tasks.split(","))

    run_backfill(
        target_date=args.date,
        tasks=task_set,
        run_transfers=args.transfers,
        dry_run=args.dry_run,
    )
