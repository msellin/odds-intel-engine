#!/usr/bin/env python3
"""
Enrich existing matches with API-Football data.
Run manually to backfill api_football_id, venue, referee, and post-match stats.

Usage:
  python scripts/enrich_matches.py                    # Enrich yesterday + today
  python scripts/enrich_matches.py --date 2026-04-27  # Enrich specific date
  python scripts/enrich_matches.py --stats             # Also fetch post-match stats
  python scripts/enrich_matches.py --odds              # Also fetch/store odds
"""

import sys
import argparse
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import (
    get_fixtures_by_date, get_fixture_statistics, parse_fixture_stats,
    get_fixture_odds, parse_fixture_odds, get_remaining_requests,
)
from workers.api_clients.supabase_client import get_client, store_match_stats
from rich.console import Console

console = Console()


def norm(name):
    """Normalize team name for matching: lowercase, strip suffixes, transliterate."""
    import unicodedata
    n = name.lower().strip()
    # Remove common suffixes
    for suffix in [" fc", " sc", " cf", " ac", " fk", " sk", " bk", " if", " afc",
                   " utd", " united", " city", " town", " rovers", " athletic",
                   " (w)", " w", " u21", " u20", " u19", " u23", " reserves",
                   " ii", " 2", " am"]:
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
    # Remove diacritics: ö→o, á→a, etc.
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode("ascii")
    return n


def _fuzzy_match_fixture(home: str, away: str, af_fixtures: list[dict], threshold: float = 70.0) -> dict | None:
    """
    Find the best matching API-Football fixture using rapidfuzz.
    Matches on combined home+away score. Returns fixture or None.
    """
    from rapidfuzz import fuzz

    home_n = norm(home)
    away_n = norm(away)
    best_score = 0
    best_fix = None

    for f in af_fixtures:
        af_home = norm(f["teams"]["home"]["name"])
        af_away = norm(f["teams"]["away"]["name"])

        # Exact match on normalized names
        if home_n == af_home and away_n == af_away:
            return f

        # Fuzzy match: use WRatio (handles partial, token sort, etc.)
        h_score = fuzz.WRatio(home_n, af_home)
        a_score = fuzz.WRatio(away_n, af_away)
        combined = (h_score + a_score) / 2

        if combined > best_score:
            best_score = combined
            best_fix = f

    if best_score >= threshold:
        return best_fix
    return None


def backfill_ids(target_date: str):
    """Backfill api_football_id, venue, referee on existing DB matches."""
    client = get_client()

    db_matches = client.table("matches").select(
        "id, api_football_id, venue_name, "
        "home_team:home_team_id(name), away_team:away_team_id(name)"
    ).gte("date", f"{target_date}T00:00:00").lte(
        "date", f"{target_date}T23:59:59"
    ).is_("api_football_id", "null").execute().data

    console.print(f"\n[cyan]Backfilling IDs for {target_date}...[/cyan]")
    console.print(f"  {len(db_matches)} matches without api_football_id")

    if not db_matches:
        return 0

    af_fixtures = get_fixtures_by_date(target_date)
    console.print(f"  {len(af_fixtures)} API-Football fixtures")

    # Build exact lookup first
    af_lookup = {}
    for f in af_fixtures:
        home = norm(f["teams"]["home"]["name"])
        away = norm(f["teams"]["away"]["name"])
        af_lookup[f"{home}|{away}"] = f

    # Track which AF fixtures have been used (avoid double-matching)
    used_af_ids = set()
    matched = 0
    unmatched = []

    for m in db_matches:
        home_raw = m["home_team"][0]["name"] if isinstance(m["home_team"], list) else m["home_team"]["name"]
        away_raw = m["away_team"][0]["name"] if isinstance(m["away_team"], list) else m["away_team"]["name"]
        home = norm(home_raw)
        away = norm(away_raw)

        # 1. Exact normalized match
        af = af_lookup.get(f"{home}|{away}")

        # 2. Fuzzy match with rapidfuzz
        if not af:
            available = [f for f in af_fixtures if f["fixture"]["id"] not in used_af_ids]
            af = _fuzzy_match_fixture(home_raw, away_raw, available, threshold=75.0)

        if af and af["fixture"]["id"] not in used_af_ids:
            af_id = af["fixture"]["id"]
            used_af_ids.add(af_id)
            updates = {"api_football_id": af_id}
            venue = af["fixture"].get("venue", {}).get("name")
            referee = af["fixture"].get("referee")
            if venue:
                updates["venue_name"] = venue
            if referee:
                updates["referee"] = referee

            af_home = af["teams"]["home"]["name"]
            af_away = af["teams"]["away"]["name"]
            if norm(home_raw) != norm(af_home) or norm(away_raw) != norm(af_away):
                console.print(f"    [dim]Fuzzy: {home_raw} vs {away_raw} → {af_home} vs {af_away}[/dim]")

            client.table("matches").update(updates).eq("id", m["id"]).execute()
            matched += 1
        else:
            unmatched.append(f"{home_raw} vs {away_raw}")

    if unmatched:
        console.print(f"  [yellow]{len(unmatched)} unmatched[/yellow]")
        for u in unmatched[:10]:
            console.print(f"    [yellow]{u}[/yellow]")
        if len(unmatched) > 10:
            console.print(f"    ... and {len(unmatched) - 10} more")

    console.print(f"  [green]{matched} matches backfilled[/green]")
    return matched


def fetch_stats(target_date: str):
    """Fetch post-match stats for finished matches with api_football_id."""
    client = get_client()

    finished = client.table("matches").select(
        "id, api_football_id"
    ).eq("status", "finished").gte(
        "date", f"{target_date}T00:00:00"
    ).lte("date", f"{target_date}T23:59:59").not_.is_(
        "api_football_id", "null"
    ).execute().data

    console.print(f"\n[cyan]Fetching post-match stats for {target_date}...[/cyan]")
    console.print(f"  {len(finished)} finished matches with api_football_id")

    # Skip matches that already have stats
    existing_stats = set()
    for m in finished:
        check = client.table("match_stats").select("match_id").eq("match_id", m["id"]).execute()
        if check.data:
            existing_stats.add(m["id"])

    to_fetch = [m for m in finished if m["id"] not in existing_stats]
    console.print(f"  {len(to_fetch)} need stats ({len(existing_stats)} already have them)")

    stored = 0
    for m in to_fetch:
        try:
            raw = get_fixture_statistics(m["api_football_id"])
            stats = parse_fixture_stats(raw)
            if stats:
                store_match_stats(m["id"], stats)
                stored += 1
                if stored % 10 == 0:
                    console.print(f"    ... {stored} stats stored")
        except Exception as e:
            console.print(f"    [yellow]Stats error for {m['api_football_id']}: {e}[/yellow]")

    console.print(f"  [green]{stored} match stats stored[/green]")
    return stored


def fetch_and_store_odds(target_date: str):
    """Fetch odds for today's scheduled matches from API-Football."""
    client = get_client()
    from datetime import datetime, timezone

    scheduled = client.table("matches").select(
        "id, api_football_id"
    ).gte("date", f"{target_date}T00:00:00").lte(
        "date", f"{target_date}T23:59:59"
    ).not_.is_("api_football_id", "null").execute().data

    # Filter to matches that don't have many odds yet
    to_fetch = []
    for m in scheduled:
        odds_count = client.table("odds_snapshots").select(
            "id", count="exact"
        ).eq("match_id", m["id"]).execute().count
        if (odds_count or 0) < 10:  # Less than 10 odds rows = probably no API-Football odds yet
            to_fetch.append(m)

    console.print(f"\n[cyan]Fetching odds for {target_date}...[/cyan]")
    console.print(f"  {len(scheduled)} matches with api_football_id, {len(to_fetch)} need odds")

    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    for m in to_fetch:
        try:
            raw = get_fixture_odds(m["api_football_id"])
            parsed = parse_fixture_odds(raw)
            if not parsed:
                continue

            rows = []
            for row in parsed:
                rows.append({
                    "match_id": m["id"],
                    "bookmaker": row["bookmaker"],
                    "market": row["market"],
                    "selection": row["selection"],
                    "odds": row["odds"],
                    "timestamp": now,
                    "is_closing": False,
                })

            if rows:
                try:
                    client.table("odds_snapshots").insert(rows).execute()
                    stored += 1
                except Exception:
                    pass  # Dedup

            if stored % 10 == 0 and stored > 0:
                console.print(f"    ... {stored} matches with odds stored")

        except Exception as e:
            continue

    console.print(f"  [green]{stored} matches with odds stored[/green]")
    return stored


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich matches with API-Football data")
    parser.add_argument("--date", help="Target date (YYYY-MM-DD), default yesterday+today")
    parser.add_argument("--stats", action="store_true", help="Also fetch post-match stats")
    parser.add_argument("--odds", action="store_true", help="Also fetch/store odds")
    parser.add_argument("--all", action="store_true", help="Run everything")
    args = parser.parse_args()

    if args.all:
        args.stats = True
        args.odds = True

    # Check API status
    status = get_remaining_requests()
    console.print(f"[bold]API-Football: {status['remaining']} requests remaining[/bold]")

    dates = []
    if args.date:
        dates = [args.date]
    else:
        dates = [
            (date.today() - timedelta(days=1)).isoformat(),
            date.today().isoformat(),
        ]

    total_ids = 0
    total_stats = 0
    total_odds = 0

    for d in dates:
        total_ids += backfill_ids(d)
        if args.stats:
            total_stats += fetch_stats(d)
        if args.odds:
            total_odds += fetch_and_store_odds(d)

    # Final status
    status = get_remaining_requests()
    console.print(f"\n[bold green]Done![/bold green]")
    console.print(f"  IDs backfilled: {total_ids}")
    if args.stats:
        console.print(f"  Stats stored: {total_stats}")
    if args.odds:
        console.print(f"  Odds stored: {total_odds}")
    console.print(f"  API requests remaining: {status['remaining']}")
