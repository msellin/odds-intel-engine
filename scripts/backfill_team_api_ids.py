"""
Backfill home_team_api_id / away_team_api_id for existing matches.

These columns were added in migration 067. Matches stored before that have NULLs.
One get_fixtures_by_date() call per unique date — very cheap.

Usage:
    python scripts/backfill_team_api_ids.py
    python scripts/backfill_team_api_ids.py --dry-run
"""

import sys
import argparse
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import get_fixtures_by_date
from workers.api_clients.db import execute_query, execute_write

console = Console()


def run(dry_run: bool = False):
    # 1. Find all matches with missing team IDs
    rows = execute_query(
        """
        SELECT id, api_football_id,
               (date AT TIME ZONE 'UTC')::date AS match_date
        FROM matches
        WHERE home_team_api_id IS NULL
          AND api_football_id IS NOT NULL
        ORDER BY match_date
        """,
        [],
    )

    if not rows:
        console.print("[green]✓ No matches with missing team IDs — nothing to do[/green]")
        return

    console.print(f"Found [bold]{len(rows)}[/bold] matches with NULL home_team_api_id")

    # 2. Group by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[str(r["match_date"])].append(r)

    console.print(f"Unique dates to fetch: [bold]{len(by_date)}[/bold]")

    total_updated = 0

    for date_str, matches in sorted(by_date.items()):
        console.print(f"\n[dim]{date_str}[/dim] — {len(matches)} matches")

        # 3. Fetch all fixtures for this date (1 API call)
        fixtures = get_fixtures_by_date(date_str)
        console.print(f"  AF returned {len(fixtures)} fixtures")

        # 4. Build lookup: af_id → (home_team_api_id, away_team_api_id)
        lookup: dict[int, tuple[int, int]] = {}
        for fix in fixtures:
            af_id = fix.get("fixture", {}).get("id")
            home_id = fix.get("teams", {}).get("home", {}).get("id")
            away_id = fix.get("teams", {}).get("away", {}).get("id")
            if af_id and home_id and away_id:
                lookup[af_id] = (home_id, away_id)

        # 5. Update each match
        for m in matches:
            af_id = m["api_football_id"]
            if af_id not in lookup:
                console.print(f"  [yellow]⚠ AF {af_id} not found in fixtures response[/yellow]")
                continue

            home_id, away_id = lookup[af_id]
            if dry_run:
                console.print(f"  DRY RUN — {m['id']} → home={home_id} away={away_id}")
            else:
                execute_write(
                    """
                    UPDATE matches
                    SET home_team_api_id = %s,
                        away_team_api_id = %s
                    WHERE id = %s
                    """,
                    [home_id, away_id, m["id"]],
                )
            total_updated += 1

    verb = "Would update" if dry_run else "Updated"
    console.print(f"\n[bold green]✓ {verb} {total_updated} matches[/bold green]")


def main():
    parser = argparse.ArgumentParser(description="Backfill team API IDs for existing matches")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change, no writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
