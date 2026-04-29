"""
Backfill team logos from API-Football.

Re-fetches fixtures by date for the dates we have matches on, then uses
the team logo URLs from AF to populate teams.logo_url.

Uses ~7 API calls total (1 per date) instead of one per match.

Run: python scripts/backfill_team_logos.py [--apply]
"""
import argparse
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workers.api_clients.api_football import get_fixtures_by_date


def get_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])


def run(dry_run: bool = True):
    client = get_client()
    print(f"{'[DRY RUN] ' if dry_run else ''}Backfilling team logos from API-Football")

    # Find the date range of matches in our DB
    oldest = client.table("matches").select("date").order("date").limit(1).execute()
    newest = client.table("matches").select("date").order("date", desc=True).limit(1).execute()

    if not oldest.data or not newest.data:
        print("No matches in DB.")
        return

    start = date.fromisoformat(oldest.data[0]["date"][:10])
    end = date.fromisoformat(newest.data[0]["date"][:10])
    print(f"Match date range: {start} → {end}")

    # Collect all team logos from AF by fetching each date in range
    af_team_logos: dict[str, str] = {}  # team_name (lower) → logo_url
    af_team_id_logos: dict[int, str] = {}  # af_team_id → logo_url

    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        try:
            fixtures = get_fixtures_by_date(date_str)
            for fix in fixtures:
                teams = fix.get("teams", {})
                for side in ("home", "away"):
                    t = teams.get(side, {})
                    if t.get("logo") and t.get("name"):
                        af_team_logos[t["name"].lower()] = t["logo"]
                        if t.get("id"):
                            af_team_id_logos[t["id"]] = t["logo"]
            print(f"  {date_str}: {len(fixtures)} fixtures, logos collected so far: {len(af_team_logos)}")
        except Exception as e:
            print(f"  {date_str}: error — {e}")
        current += timedelta(days=1)

    print(f"\nTotal unique team logos found: {len(af_team_logos)}")

    # Update teams table
    teams_r = client.table("teams").select("id, name, logo_url").execute()
    teams_without_logo = [t for t in teams_r.data if not t.get("logo_url")]
    print(f"Teams without logo in DB: {len(teams_without_logo)}")

    updated = 0
    not_found = 0
    for team in teams_without_logo:
        # Match by name only (no api_football_id column on teams table)
        logo = af_team_logos.get(team["name"].lower())

        if logo:
            if not dry_run:
                client.table("teams").update({"logo_url": logo}).eq("id", team["id"]).execute()
            updated += 1
        else:
            not_found += 1

    print(f"\nSummary:")
    print(f"  Updated: {updated}")
    print(f"  Not found in AF response: {not_found}")
    if dry_run:
        print("\nDry run — pass --apply to write to DB.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    run(dry_run=not args.apply)
