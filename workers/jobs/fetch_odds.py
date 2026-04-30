"""
OddsIntel — Fetch Odds

Single unified job for all pre-match odds collection:
  1. AF bulk odds (/odds?date=) — 13+ bookmakers, ~200 fixtures
  2. Kambi odds (Unibet/Paf) — 68 leagues, best prices

Runs every 2 hours + pre-kickoff windows. All odds stored in odds_snapshots
with minutes_to_kickoff for CLV tracking.

Schedule: 05,07,08,10,12,14,16,18,20,22 UTC + 13:30,17:30 pre-kickoff
Workflow: .github/workflows/odds.yml

Usage:
  python -m workers.jobs.fetch_odds
  python -m workers.jobs.fetch_odds --date 2026-04-30
  python -m workers.jobs.fetch_odds --mark-closing    # for pre-kickoff runs
  python -m workers.jobs.fetch_odds --af-only          # skip Kambi
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
from workers.api_clients.supabase_client import get_client, store_match, store_odds
from workers.scrapers.kambi_odds import fetch_all_operators
from workers.utils.pipeline_utils import (
    log_pipeline_start, log_pipeline_complete, log_pipeline_failed,
    check_fixtures_ready,
)

console = Console()


def _compute_minutes_to_kickoff(kickoff_iso: str) -> int | None:
    """Minutes until kickoff. Negative = before, positive = after."""
    try:
        kickoff = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return int((kickoff - now).total_seconds() / 60)
    except (ValueError, AttributeError):
        return None


def fetch_af_odds(target_date: str) -> int:
    """Fetch AF bulk odds and store in odds_snapshots. Returns matches stored."""
    console.print("\n[cyan]Fetching AF bulk odds...[/cyan]")

    client = get_client()
    now = datetime.now(timezone.utc).isoformat()

    # Get AF fixture ID → match UUID mapping from DB
    next_date = (date.fromisoformat(target_date) + timedelta(days=1)).isoformat()

    matches_result = client.table("matches").select(
        "id, api_football_id, date"
    ).gte("date", f"{target_date}T00:00:00Z").lt(
        "date", f"{next_date}T00:00:00Z"
    ).not_.is_("api_football_id", "null").execute()

    af_id_to_match = {}
    match_kickoffs = {}
    for m in matches_result.data:
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

    stored = 0
    for af_id, odds_data in bulk_odds.items():
        match_id = af_id_to_match.get(af_id)
        if not match_id:
            continue

        parsed = parse_fixture_odds(odds_data)
        if not parsed:
            continue

        kickoff = match_kickoffs.get(match_id, "")
        minutes_to_kickoff = _compute_minutes_to_kickoff(kickoff)

        # Build rows for odds_snapshots
        rows = []
        for row in parsed:
            rows.append({
                "match_id": match_id,
                "bookmaker": row["bookmaker"],
                "market": row["market"],
                "selection": row["selection"],
                "odds": row["odds"],
                "timestamp": now,
                "is_closing": False,
                "minutes_to_kickoff": minutes_to_kickoff,
            })

        if rows:
            try:
                client.table("odds_snapshots").insert(rows).execute()
                stored += 1
            except Exception:
                pass  # dedup errors fine

    console.print(f"  [green]{stored} AF fixtures stored with odds[/green]")
    return stored


def fetch_kambi_odds(mark_closing: bool = False) -> int:
    """Fetch Kambi odds and store in odds_snapshots. Returns matches stored."""
    console.print("\n[cyan]Fetching Kambi odds...[/cyan]")

    try:
        matches = fetch_all_operators()
        console.print(f"  {len(matches)} matches from Kambi")
    except Exception as e:
        console.print(f"  [red]Kambi error: {e}[/red]")
        return 0

    if not matches:
        return 0

    stored = 0
    skipped = 0

    for match in matches:
        kickoff = match.get("start_time", "")
        minutes_to_kickoff = _compute_minutes_to_kickoff(kickoff)

        # Skip matches already kicked off >5 min ago
        if minutes_to_kickoff is not None and minutes_to_kickoff < -5:
            skipped += 1
            continue

        # Override closing flag if requested
        if mark_closing and minutes_to_kickoff is not None and minutes_to_kickoff <= 60:
            match["_force_closing"] = True

        try:
            # Ensure match exists in DB (idempotent upsert)
            match_id = store_match(match)

            # Store odds snapshot with timing
            store_odds(match_id, match, minutes_to_kickoff=minutes_to_kickoff)
            stored += 1
        except Exception as e:
            console.print(f"  [yellow]Error: {match.get('home_team', '?')} v {match.get('away_team', '?')}: {e}[/yellow]")

    console.print(f"  [green]{stored} Kambi matches stored[/green] | {skipped} skipped (already started)")
    return stored


def run_odds(target_date: str = None, mark_closing: bool = False, af_only: bool = False):
    """Run odds fetch pipeline. Callable by scheduler or CLI."""
    target_date = target_date or date.today().isoformat()
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")
    console.print(f"[bold green]═══ OddsIntel Odds: {target_date} @ {now_str} ═══[/bold green]")

    run_id = log_pipeline_start("fetch_odds", target_date)

    try:
        total = 0

        # AF bulk odds (primary — 13+ bookmakers)
        total += fetch_af_odds(target_date)

        # Kambi odds (supplementary — Unibet/Paf)
        if not af_only:
            total += fetch_kambi_odds(mark_closing=mark_closing)

        log_pipeline_complete(run_id, records_count=total)
        console.print(f"\n[bold green]Done. {total} matches with odds stored.[/bold green]")

    except Exception as e:
        console.print(f"\n[red]Failed: {e}[/red]")
        if run_id:
            log_pipeline_failed(run_id, str(e))
        raise


def main():
    parser = argparse.ArgumentParser(description="Fetch odds from AF + Kambi")
    parser.add_argument("--date", type=str, default=None, help="Date (YYYY-MM-DD, default: today)")
    parser.add_argument("--mark-closing", action="store_true", help="Mark near-kickoff odds as closing")
    parser.add_argument("--af-only", action="store_true", help="Skip Kambi, AF odds only")
    args = parser.parse_args()
    run_odds(target_date=args.date, mark_closing=args.mark_closing, af_only=args.af_only)


if __name__ == "__main__":
    main()
