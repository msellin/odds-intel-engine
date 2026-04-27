"""
OddsIntel — Hourly Pre-Match Odds Snapshot
Takes timed odds snapshots throughout the day so we can analyze:
  - How odds move from T-48h to closing
  - Closing Line Value (CLV): did our pick beat the closing line?
  - Which time window gives the best odds / most edge

Run this every 1-2 hours via GitHub Actions cron.
Each run stores a snapshot with minutes_to_kickoff calculated from match time.

Usage:
  python odds_snapshot.py            # Snapshot all of today's matches
  python odds_snapshot.py --mark-closing  # Mark as closing (run ~30min before kickoffs)
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.scrapers.kambi_odds import fetch_all_operators
from workers.api_clients.supabase_client import get_client, store_match, store_odds

console = Console()


def compute_minutes_to_kickoff(kickoff_iso: str) -> int:
    """
    Compute minutes until kickoff (negative = before, positive = after).
    e.g. -120 = 2 hours before kickoff
    """
    try:
        kickoff = datetime.fromisoformat(kickoff_iso.replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta_minutes = int((kickoff - now).total_seconds() / 60)
        return delta_minutes
    except (ValueError, AttributeError):
        return None


def run_snapshot(mark_closing: bool = False):
    """
    Fetch current odds for all available matches and store with timing info.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"[bold cyan]═══ OddsIntel Odds Snapshot: {now_str} ═══[/bold cyan]\n")

    # Fetch current odds from all Kambi operators
    console.print("[cyan]Fetching odds from Kambi...[/cyan]")
    matches = fetch_all_operators()
    console.print(f"  {len(matches)} matches found\n")

    if not matches:
        console.print("[yellow]No matches available.[/yellow]")
        return

    stored = 0
    skipped = 0
    timing_summary = {}

    t = Table(title="Odds Snapshot")
    t.add_column("Match", style="cyan")
    t.add_column("Kickoff", justify="right")
    t.add_column("T-min", justify="right")
    t.add_column("1", justify="right", style="green")
    t.add_column("X", justify="right", style="yellow")
    t.add_column("2", justify="right", style="red")
    t.add_column("O0.5", justify="right")
    t.add_column("O1.5", justify="right")
    t.add_column("O2.5", justify="right")
    t.add_column("O3.5", justify="right")

    for match in matches:
        kickoff = match.get("start_time", "")
        minutes_to_kickoff = compute_minutes_to_kickoff(kickoff)

        # Skip matches that have already kicked off by more than 5 min
        # (pre-match snapshot job — live tracker handles in-play)
        if minutes_to_kickoff is not None and minutes_to_kickoff < -5:
            skipped += 1
            continue

        # Override is_closing if flag is set
        if mark_closing and minutes_to_kickoff is not None and minutes_to_kickoff <= 60:
            match["_force_closing"] = True

        try:
            # Ensure match is in DB (idempotent)
            match_id = store_match(match)

            # Store this snapshot with timing
            store_odds(match_id, match, minutes_to_kickoff=minutes_to_kickoff)
            stored += 1

            # Track timing buckets for summary
            if minutes_to_kickoff is not None:
                bucket = _timing_bucket(minutes_to_kickoff)
                timing_summary[bucket] = timing_summary.get(bucket, 0) + 1

            # Format for table
            t.add_row(
                f"{match['home_team'][:14]} v {match['away_team'][:14]}",
                kickoff[11:16] if len(kickoff) > 11 else "-",
                f"{minutes_to_kickoff:+d}" if minutes_to_kickoff is not None else "-",
                f"{match['odds_home']:.2f}" if match.get("odds_home") else "-",
                f"{match['odds_draw']:.2f}" if match.get("odds_draw") else "-",
                f"{match['odds_away']:.2f}" if match.get("odds_away") else "-",
                f"{match['odds_over_05']:.2f}" if match.get("odds_over_05") else "-",
                f"{match['odds_over_15']:.2f}" if match.get("odds_over_15") else "-",
                f"{match['odds_over_25']:.2f}" if match.get("odds_over_25") else "-",
                f"{match['odds_over_35']:.2f}" if match.get("odds_over_35") else "-",
            )

        except Exception as e:
            console.print(f"  [red]Error storing {match['home_team']} v {match['away_team']}: {e}[/red]")

    console.print(t)
    console.print(f"\n[green]Stored {stored} snapshots[/green] | Skipped {skipped} started matches")

    if timing_summary:
        console.print("\n[bold]Timing distribution:[/bold]")
        for bucket, count in sorted(timing_summary.items()):
            console.print(f"  {bucket}: {count} matches")


def _timing_bucket(minutes: int) -> str:
    """Group minutes_to_kickoff into named buckets for summary"""
    if minutes > 2880:
        return ">48h"
    elif minutes > 1440:
        return "24-48h"
    elif minutes > 360:
        return "6-24h"
    elif minutes > 120:
        return "2-6h"
    elif minutes > 60:
        return "1-2h"
    elif minutes > 30:
        return "30-60min"
    elif minutes > 0:
        return "0-30min"
    else:
        return "at/after kickoff"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OddsIntel hourly odds snapshot")
    parser.add_argument("--mark-closing", action="store_true",
                        help="Mark snapshots within 60min of kickoff as closing line")
    args = parser.parse_args()

    run_snapshot(mark_closing=args.mark_closing)
