"""
OddsIntel — BetExplorer Historical Odds Backfill

Fetches historical closing odds from BetExplorer for leagues where we
previously had no odds coverage (or weak coverage).

Usage:
  python scripts/backfill_betexplorer.py                    # All gap leagues
  python scripts/backfill_betexplorer.py singapore           # Single country
  python scripts/backfill_betexplorer.py --max-per-league 20 # Limit per league
  python scripts/backfill_betexplorer.py --dry-run           # Don't store, just report

This backfill targets completed matches (results pages) and stores
their odds in the same odds_snapshots format the pipeline uses.
"""

import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.scrapers.betexplorer_odds import (
    fetch_historical_odds, fetch_league_matches, fetch_match_odds,
    LEAGUE_MAP,
)
from workers.api_clients.supabase_client import (
    get_client, store_match, store_odds,
)

console = Console()

# Leagues that are our biggest gaps — prioritize these for backfill
PRIORITY_LEAGUES = [
    # Highest-signal leagues from backtest that lack odds in our DB
    ("singapore", "premier-league"),
    ("south-korea", "k-league-1"),
    ("south-korea", "k-league-2"),
    ("scotland", "championship"),
    ("scotland", "league-one"),
    ("scotland", "league-two"),
    ("austria", "2-liga"),
    ("ireland", "premier-division"),
    ("ireland", "first-division"),
    # Already have some coverage but BetExplorer may have better odds
    ("scotland", "premiership"),
    ("austria", "bundesliga"),
    ("greece", "super-league"),
    ("turkey", "super-lig"),
]


def backfill_league(
    country: str,
    league: str,
    max_matches: int = 0,
    dry_run: bool = False,
    delay: float = 1.0,
) -> dict:
    """
    Backfill historical odds for a single league.
    Returns stats dict with counts.
    """
    league_info = LEAGUE_MAP.get((country, league), {})
    league_code = league_info.get("fd_code", "")
    tier = league_info.get("tier", 0)

    console.print(f"\n[bold cyan]Backfilling {country}/{league} (fd_code={league_code}, tier={tier})[/bold cyan]")

    # Get match list from results page
    matches = fetch_league_matches(country, league, mode="results")
    if not matches:
        console.print("  [yellow]No matches found[/yellow]")
        return {"total": 0, "with_odds": 0, "stored": 0}

    if max_matches > 0:
        matches = matches[:max_matches]

    console.print(f"  {len(matches)} matches to process")

    stats = {"total": len(matches), "with_odds": 0, "stored": 0, "errors": 0}

    for i, match in enumerate(matches):
        match_id = match["match_id"]
        home = match.get("home_team", "Unknown")
        away = match.get("away_team", "Unknown")
        score = match.get("score", "")

        if not home or not away:
            continue

        # Fetch odds
        referer = f"https://www.betexplorer.com{match.get('url', '')}"
        odds = fetch_match_odds(match_id, referer_url=referer)

        if not odds.get("odds_home"):
            time.sleep(delay)
            continue

        stats["with_odds"] += 1

        if dry_run:
            console.print(
                f"  [dim]{home} vs {away} ({score}) — "
                f"H:{odds['odds_home']:.2f} D:{odds['odds_draw']:.2f} A:{odds['odds_away']:.2f} "
                f"O2.5:{odds.get('odds_over_25', 0):.2f} U2.5:{odds.get('odds_under_25', 0):.2f} "
                f"({odds.get('bookmaker_count', 0)} bookmakers)[/dim]"
            )
        else:
            # Store match + odds in Supabase
            try:
                match_dict = {
                    "home_team": home,
                    "away_team": away,
                    "start_time": "",  # Historical — no exact time
                    "league_path": f"{country.title()} / {league.replace('-', ' ').title()}",
                    "league_code": league_code,
                    "tier": tier,
                    "operator": "betexplorer",
                    "odds_home": odds["odds_home"],
                    "odds_draw": odds["odds_draw"],
                    "odds_away": odds["odds_away"],
                    "odds_over_25": odds.get("odds_over_25", 0),
                    "odds_under_25": odds.get("odds_under_25", 0),
                    "odds_over_05": odds.get("odds_over_05", 0),
                    "odds_under_05": odds.get("odds_under_05", 0),
                    "odds_over_15": odds.get("odds_over_15", 0),
                    "odds_under_15": odds.get("odds_under_15", 0),
                    "odds_over_35": odds.get("odds_over_35", 0),
                    "odds_under_35": odds.get("odds_under_35", 0),
                    "odds_over_45": odds.get("odds_over_45", 0),
                    "odds_under_45": odds.get("odds_under_45", 0),
                }
                db_match_id = store_match(match_dict)
                if db_match_id:
                    store_odds(db_match_id, {
                        **match_dict,
                        "bookmaker": "betexplorer",
                    })
                    stats["stored"] += 1
            except Exception as e:
                stats["errors"] += 1
                console.print(f"  [red]Error storing {home} vs {away}: {e}[/red]")

        if (i + 1) % 10 == 0:
            console.print(f"  ... {i + 1}/{len(matches)} processed ({stats['with_odds']} with odds)")

        time.sleep(delay)

    console.print(
        f"  [bold]Done: {stats['with_odds']}/{stats['total']} had odds, "
        f"{stats['stored']} stored, {stats['errors']} errors[/bold]"
    )
    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill historical odds from BetExplorer")
    parser.add_argument("country", nargs="?", help="Single country to backfill (e.g. 'singapore')")
    parser.add_argument("--max-per-league", type=int, default=0, help="Max matches per league (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Don't store, just report what would be fetched")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between requests (seconds)")
    parser.add_argument("--all-leagues", action="store_true", help="Process all leagues in LEAGUE_MAP, not just priority")
    args = parser.parse_args()

    console.print("[bold]OddsIntel — BetExplorer Historical Backfill[/bold]\n")
    if args.dry_run:
        console.print("[yellow]DRY RUN — no data will be stored[/yellow]\n")

    # Determine which leagues to process
    if args.country:
        leagues = [
            (c, l) for (c, l) in (PRIORITY_LEAGUES if not args.all_leagues else LEAGUE_MAP.keys())
            if c == args.country
        ]
        if not leagues:
            # Try all leagues for this country from LEAGUE_MAP
            leagues = [(c, l) for (c, l) in LEAGUE_MAP.keys() if c == args.country]
    elif args.all_leagues:
        leagues = list(LEAGUE_MAP.keys())
    else:
        leagues = PRIORITY_LEAGUES

    console.print(f"Processing {len(leagues)} leagues\n")

    totals = {"total": 0, "with_odds": 0, "stored": 0, "errors": 0}

    for country, league in leagues:
        try:
            stats = backfill_league(
                country, league,
                max_matches=args.max_per_league,
                dry_run=args.dry_run,
                delay=args.delay,
            )
            for k in totals:
                totals[k] += stats.get(k, 0)
        except Exception as e:
            console.print(f"[red]Failed {country}/{league}: {e}[/red]")

        time.sleep(3)  # Extra pause between leagues

    # Summary
    console.print(f"\n[bold green]═══ Backfill Complete ═══[/bold green]")
    t = Table(title="Backfill Summary")
    t.add_column("Metric")
    t.add_column("Count", justify="right")
    t.add_row("Total matches processed", str(totals["total"]))
    t.add_row("Matches with odds", str(totals["with_odds"]))
    t.add_row("Stored to Supabase", str(totals["stored"]))
    t.add_row("Errors", str(totals["errors"]))
    console.print(t)


if __name__ == "__main__":
    main()
