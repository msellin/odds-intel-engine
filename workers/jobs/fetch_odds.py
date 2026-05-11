"""
OddsIntel — Fetch Odds

Pre-match odds collection from API-Football (13+ bookmakers, ~200 fixtures).
Kambi scraper removed 2026-05-06 (KAMBI-DROP) — empirical analysis showed
negligible incremental value (36 rows/30 days from non-Unibet sources).

Runs every 2 hours + pre-kickoff windows. All odds stored in odds_snapshots
with minutes_to_kickoff for CLV tracking.

Schedule: 07,08,10,12,14,16,18,22 UTC + 13:30,17:30,20:00 pre-kickoff

Usage:
  python -m workers.jobs.fetch_odds
  python -m workers.jobs.fetch_odds --date 2026-04-30
  python -m workers.jobs.fetch_odds --mark-closing    # for pre-kickoff runs
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, date, timezone, timedelta

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import get_odds_by_date, parse_fixture_odds
from workers.api_clients.db import execute_query, bulk_insert
from workers.utils.odds_quality import filter_garbage_ou_rows
from workers.utils.pipeline_utils import (
    log_pipeline_start, log_pipeline_complete, log_pipeline_failed,
)

console = Console()


def _compute_minutes_to_kickoff(kickoff_iso) -> int | None:
    """Minutes until kickoff. Negative = before, positive = after."""
    try:
        if isinstance(kickoff_iso, datetime):
            kickoff = kickoff_iso
        else:
            kickoff = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return int((kickoff - now).total_seconds() / 60)
    except (ValueError, AttributeError, TypeError):
        return None


def fetch_af_odds(target_date: str) -> int:
    """Fetch AF bulk odds and store in odds_snapshots. Returns matches stored."""
    console.print("\n[cyan]Fetching AF bulk odds...[/cyan]")

    now = datetime.now(timezone.utc).isoformat()

    # Get AF fixture ID → match UUID mapping from DB
    next_date = (date.fromisoformat(target_date) + timedelta(days=1)).isoformat()

    matches_result = execute_query(
        "SELECT id, api_football_id, date FROM matches "
        "WHERE date >= %s AND date < %s AND api_football_id IS NOT NULL",
        [f"{target_date}T00:00:00Z", f"{next_date}T00:00:00Z"]
    )

    af_id_to_match = {}
    match_kickoffs = {}
    for m in matches_result:
        af_id = m.get("api_football_id")
        if af_id:
            af_id_to_match[af_id] = m["id"]
            match_kickoffs[m["id"]] = m.get("date", "")

    if not af_id_to_match:
        console.print("  No AF matches found in DB for today")
        return 0

    console.print(f"  {len(af_id_to_match)} matches in DB to check for odds")

    # Fetch all odds for date (paginated, ~10 calls)
    try:
        bulk_odds = get_odds_by_date(target_date)
        console.print(f"  {len(bulk_odds)} fixtures with odds from AF")
    except Exception as e:
        console.print(f"  [red]AF odds error: {e}[/red]")
        return 0

    # Build all rows up-front, then write in a single bulk_insert call.
    # Previous version did one bulk_insert per fixture (~560 round-trips at
    # ~150ms pooler RTT = ~85s); a single call collapses that to ~5s.
    cols = ["match_id", "bookmaker", "market", "selection", "odds",
            "timestamp", "is_closing", "minutes_to_kickoff", "handicap_line", "is_opening"]
    all_tuples: list[tuple] = []
    fixtures_with_rows = 0

    # Pre-fetch which (match_id, bookmaker, market, selection) combos already
    # exist so we can mark truly-first inserts as is_opening=true.
    match_ids_today = list(af_id_to_match.values())
    existing_combos: set[tuple] = set()
    try:
        for i in range(0, len(match_ids_today), 200):
            chunk = match_ids_today[i:i + 200]
            rows = execute_query(
                """SELECT DISTINCT match_id, bookmaker, market, selection
                   FROM odds_snapshots WHERE match_id = ANY(%s::uuid[])""",
                (chunk,),
            )
            for r in rows:
                existing_combos.add((r["match_id"], r["bookmaker"], r["market"], r["selection"]))
    except Exception:
        pass  # on failure is_opening stays false — safe degradation

    for af_id, odds_data in bulk_odds.items():
        match_id = af_id_to_match.get(af_id)
        if not match_id:
            continue

        parsed = parse_fixture_odds(odds_data)
        if not parsed:
            continue

        # ODDS-QUALITY-CLEANUP: drop OU rows from blacklisted bookmakers and
        # both sides of impossible (1/over + 1/under < 1.02) OU pairs.
        # 1X2 / BTTS rows pass through untouched.
        parsed = filter_garbage_ou_rows(parsed)
        if not parsed:
            continue

        kickoff = match_kickoffs.get(match_id, "")
        minutes_to_kickoff = _compute_minutes_to_kickoff(kickoff)

        for row in parsed:
            combo = (match_id, row["bookmaker"], row["market"], row["selection"])
            is_opening = combo not in existing_combos
            # Add to seen so later rows in same batch don't double-mark
            existing_combos.add(combo)
            all_tuples.append((
                match_id,
                row["bookmaker"],
                row["market"],
                row["selection"],
                row["odds"],
                now,
                False,
                minutes_to_kickoff,
                row.get("handicap_line"),
                is_opening,
            ))
        fixtures_with_rows += 1

    if not all_tuples:
        console.print("  [yellow]No odds rows to store[/yellow]")
        return 0

    # page_size=5000 chosen from empirical benchmark on Supabase EU pooler:
    # 41s @ 500 vs 14s @ 5000 vs 13s @ 10000 for 100k rows. 5000 is the knee.
    bulk_insert("odds_snapshots", cols, all_tuples, page_size=5000)
    console.print(
        f"  [green]{fixtures_with_rows} AF fixtures stored with odds "
        f"({len(all_tuples)} rows in 1 bulk insert)[/green]"
    )
    return fixtures_with_rows


def run_odds(target_date: str = None, mark_closing: bool = False, **_kwargs):
    """Run odds fetch pipeline. Callable by scheduler or CLI."""
    target_date = target_date or date.today().isoformat()
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    console.print(f"[bold green]═══ OddsIntel Odds: {target_date} @ {now_str} ═══[/bold green]")

    run_id = log_pipeline_start("fetch_odds", target_date)

    try:
        total = fetch_af_odds(target_date)

        log_pipeline_complete(run_id, records_count=total)
        console.print(f"\n[bold green]Done. {total} matches with odds stored.[/bold green]")

        from workers.api_clients.supabase_client import write_ops_snapshot
        write_ops_snapshot(target_date)

    except Exception as e:
        console.print(f"\n[red]Failed: {e}[/red]")
        if run_id:
            log_pipeline_failed(run_id, str(e))
        raise


def main():
    parser = argparse.ArgumentParser(description="Fetch odds from API-Football")
    parser.add_argument("--date", type=str, default=None, help="Date (YYYY-MM-DD, default: today)")
    parser.add_argument("--mark-closing", action="store_true", help="Mark near-kickoff odds as closing")
    args = parser.parse_args()
    run_odds(target_date=args.date, mark_closing=args.mark_closing)


if __name__ == "__main__":
    main()
