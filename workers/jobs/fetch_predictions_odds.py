"""
OddsIntel — Fetch Predictions & Odds Job

Standalone job that fetches:
  - AF predictions for all today's fixtures (coverage-aware)
  - AF bulk odds
  - Kambi scraper odds

Stores odds in odds_snapshots and AF predictions on matches + predictions tables.

Schedule: 07:00 UTC daily (after fixtures at 06:00 and enrichment at 06:15)
Workflow: .github/workflows/fetch_odds_preds.yml

Usage:
  python -m workers.jobs.fetch_predictions_odds
  python -m workers.jobs.fetch_predictions_odds --date 2026-04-29
  python -m workers.jobs.fetch_predictions_odds --skip-scrapers  # AF only
"""

import sys
import argparse
from pathlib import Path
from datetime import date, datetime

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import (
    get_fixtures_by_date, fixture_to_match_dict,
    get_prediction, parse_prediction,
    get_odds_by_date, parse_fixture_odds,
)
from workers.api_clients.supabase_client import (
    get_client, store_prediction, store_odds,
)
from workers.scrapers.kambi_odds import get_target_league_matches
from workers.utils.pipeline_utils import (
    check_fixtures_ready, log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed, log_pipeline_skipped, get_league_coverage_map,
    league_has_coverage,
)

console = Console()


def _get_af_id_to_match_id(target_date: str) -> dict[int, str]:
    """Load AF fixture ID → match UUID mapping from DB."""
    client = get_client()
    next_day_num = int(target_date[8:]) + 1
    next_date = f"{target_date[:8]}{next_day_num:02d}"

    result = client.table("matches").select(
        "id, api_football_id, league_id"
    ).gte("date", f"{target_date}T00:00:00Z").lt(
        "date", f"{next_date}T00:00:00Z"
    ).not_.is_("api_football_id", "null").execute()

    mapping = {}
    league_by_match = {}
    for m in result.data:
        if m.get("api_football_id"):
            mapping[m["api_football_id"]] = m["id"]
            league_by_match[m["id"]] = m.get("league_id")

    return mapping, league_by_match


def fetch_af_predictions(af_id_to_match_id: dict, league_by_match: dict,
                         coverage_map: dict) -> int:
    """Fetch AF predictions for all fixtures, coverage-aware."""
    client = get_client()
    console.print(f"\n[cyan]Fetching AF predictions ({len(af_id_to_match_id)} fixtures)...[/cyan]")

    fetched = 0
    skipped_coverage = 0
    failed = 0

    for af_id, match_id in af_id_to_match_id.items():
        # Coverage check
        league_id = league_by_match.get(match_id)
        if league_id and not league_has_coverage(coverage_map, league_id, "predictions"):
            skipped_coverage += 1
            continue

        try:
            raw = get_prediction(af_id)
            if not raw:
                failed += 1
                continue

            parsed = parse_prediction(raw)
            if not parsed.get("af_home_prob"):
                failed += 1
                continue

            # Store full JSONB on match row
            try:
                client.table("matches").update({
                    "af_prediction": parsed["raw"]
                }).eq("id", match_id).execute()
            except Exception:
                pass

            # Store as prediction rows (source='af')
            for market, prob_key in [
                ("1x2_home", "af_home_prob"),
                ("1x2_draw", "af_draw_prob"),
                ("1x2_away", "af_away_prob"),
            ]:
                prob = parsed.get(prob_key)
                if prob is not None:
                    try:
                        store_prediction(match_id, market, {
                            "model_prob": prob,
                            "reasoning": "af_prediction",
                        }, source="af")
                    except Exception:
                        pass

            fetched += 1
        except Exception:
            failed += 1

    console.print(f"  {fetched} predictions stored, {failed} unavailable, {skipped_coverage} skipped (no coverage)")
    return fetched


def fetch_af_bulk_odds(target_date: str, af_id_to_match_id: dict) -> int:
    """Fetch odds from AF bulk endpoint and store in odds_snapshots."""
    console.print(f"\n[cyan]Fetching AF bulk odds for {target_date}...[/cyan]")

    # Need raw AF fixtures for fixture_to_match_dict (league_path etc)
    af_fixtures_raw = []
    try:
        af_fixtures_raw = get_fixtures_by_date(target_date)
    except Exception as e:
        console.print(f"  [yellow]Could not fetch AF fixtures: {e}[/yellow]")
        return 0

    af_fix_by_id = {f.get("fixture", {}).get("id"): f for f in af_fixtures_raw}

    stored = 0
    try:
        bulk_odds = get_odds_by_date(target_date)
        console.print(f"  {len(bulk_odds)} fixtures with odds from API-Football")

        client = get_client()
        now = datetime.now().astimezone().isoformat()

        for af_id, odds_data in bulk_odds.items():
            match_id = af_id_to_match_id.get(af_id)
            if not match_id:
                continue

            parsed = parse_fixture_odds(odds_data)
            if not parsed:
                continue

            # Store individual odds rows
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
                    "minutes_to_kickoff": None,
                })

            if rows:
                try:
                    client.table("odds_snapshots").insert(rows).execute()
                    stored += 1
                except Exception:
                    pass  # Dedup errors fine

    except Exception as e:
        console.print(f"  [yellow]AF bulk odds error: {e}[/yellow]")

    console.print(f"  {stored} fixtures with odds stored")
    return stored


def fetch_scraper_odds() -> int:
    """Fetch odds from Kambi scraper."""
    console.print("\n[cyan]Fetching Kambi odds...[/cyan]")
    try:
        kambi = get_target_league_matches()
        console.print(f"  {len(kambi)} matches with Kambi odds")
        return len(kambi)
    except Exception as e:
        console.print(f"  [yellow]Kambi error: {e}[/yellow]")
        return 0


def main():
    parser = argparse.ArgumentParser(description="Fetch AF predictions and odds from all sources")
    parser.add_argument("--date", type=str, default=None, help="Date (YYYY-MM-DD, default: today)")
    parser.add_argument("--skip-scrapers", action="store_true", help="Skip Kambi scraper")
    args = parser.parse_args()

    target_date = args.date or date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Predictions & Odds: {target_date} ═══[/bold green]")

    # Readiness check
    if not check_fixtures_ready(target_date):
        console.print("[yellow]Fixtures not ready yet — skipping.[/yellow]")
        log_pipeline_skipped("fetch_predictions_odds", "Fixtures not ready", target_date)
        return

    run_id = log_pipeline_start("fetch_predictions_odds", target_date)

    try:
        # Load fixture mappings from DB
        af_id_to_match_id, league_by_match = _get_af_id_to_match_id(target_date)
        console.print(f"  {len(af_id_to_match_id)} fixtures with AF IDs")

        # Load coverage
        coverage_map = get_league_coverage_map()

        total = 0

        # AF predictions (coverage-aware)
        total += fetch_af_predictions(af_id_to_match_id, league_by_match, coverage_map)

        # AF bulk odds
        total += fetch_af_bulk_odds(target_date, af_id_to_match_id)

        # Scraper odds
        if not args.skip_scrapers:
            total += fetch_scraper_odds()

        log_pipeline_complete(
            run_id,
            fixtures_count=len(af_id_to_match_id),
            records_count=total,
        )

        console.print(f"\n[bold green]Done. {total} records processed.[/bold green]")

    except Exception as e:
        console.print(f"\n[red]Failed: {e}[/red]")
        if run_id:
            log_pipeline_failed(run_id, str(e))
        raise


if __name__ == "__main__":
    main()
