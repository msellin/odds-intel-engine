"""
Backfill morning signals for today's matches.

Useful after write_morning_signals was extended (S3b-S3f, T2) and you want
to populate the new signals without waiting for tomorrow's pipeline run.

Calls write_morning_signals() for every match stored for today, pulling
the required context (team API IDs, league API ID, opening odds) directly
from the database instead of re-fetching from the API.

Usage:
    python scripts/backfill_signals.py           # today's matches
    python scripts/backfill_signals.py 2026-04-28 # specific date
"""

import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from workers.api_clients.supabase_client import get_client, write_morning_signals


def _get_opening_odds(client, match_id: str) -> dict:
    """Get the earliest 1x2 odds snapshot for home/draw/away."""
    odds = {}
    for selection, key in [("home", "opening_odds_home"),
                            ("draw", "opening_odds_draw"),
                            ("away", "opening_odds_away")]:
        r = client.table("odds_snapshots").select("odds").eq(
            "match_id", match_id
        ).eq("market", "1x2").eq("selection", selection).eq(
            "is_live", False
        ).order("timestamp", desc=False).limit(1).execute()
        if r.data:
            odds[key] = float(r.data[0]["odds"])
    return odds


def _get_team_api_ids(client, match_id: str) -> tuple[int | None, int | None]:
    """Get home/away team API IDs from match_injuries (populated by T3)."""
    r = client.table("match_injuries").select(
        "team_api_id, team_side"
    ).eq("match_id", match_id).execute()

    home_api_id = away_api_id = None
    for row in (r.data or []):
        if row.get("team_side") == "home" and row.get("team_api_id"):
            home_api_id = row["team_api_id"]
        elif row.get("team_side") == "away" and row.get("team_api_id"):
            away_api_id = row["team_api_id"]
        if home_api_id and away_api_id:
            break

    return home_api_id, away_api_id


def _get_league_api_id(client, team_api_id: int, season: int) -> int | None:
    """Get league_api_id from league_standings via team API ID."""
    if not team_api_id:
        return None
    r = client.table("league_standings").select("league_api_id").eq(
        "team_api_id", team_api_id
    ).eq("season", season).order("fetched_date", desc=True).limit(1).execute()
    if r.data:
        return r.data[0].get("league_api_id")
    return None


def backfill_signals(target_date: str) -> None:
    client = get_client()

    matches = client.table("matches").select(
        "id, season, referee, date"
    ).gte("date", f"{target_date}T00:00:00").lte(
        "date", f"{target_date}T23:59:59"
    ).execute()

    if not matches.data:
        print(f"No matches found for {target_date}")
        return

    print(f"Backfilling signals for {len(matches.data)} matches on {target_date}...")
    ok = skipped = 0

    for m in matches.data:
        match_id = m["id"]
        season = m.get("season")
        referee = m.get("referee")

        # Opening odds from first snapshot
        opening_odds = _get_opening_odds(client, match_id)

        # Team API IDs from match_injuries (T3 data)
        home_api_id, away_api_id = _get_team_api_ids(client, match_id)

        # League API ID from standings (T9 data)
        league_api_id = _get_league_api_id(client, home_api_id, season) if home_api_id and season else None

        try:
            write_morning_signals(
                match_id,
                league_api_id=league_api_id,
                season=season,
                home_team_api_id=home_api_id,
                away_team_api_id=away_api_id,
                referee=referee,
                **opening_odds,
            )
            ok += 1
        except Exception as e:
            print(f"  [WARN] match {match_id}: {e}")
            skipped += 1

    print(f"Done: {ok} matches signalled, {skipped} errors")
    if not all([home_api_id, away_api_id, league_api_id]):
        print("Note: some matches may have partial signals if T3/T9 data wasn't populated this morning.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    backfill_signals(target)
