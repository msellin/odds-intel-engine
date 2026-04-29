"""
OddsIntel — Fetch AF Predictions

Fetches API-Football's statistical predictions for all today's fixtures.
Coverage-aware: skips leagues where AF has no prediction coverage.

Stores:
  - matches.af_prediction (full JSONB)
  - predictions table rows (source='af') for 1X2 home/draw/away

Schedule: 05:30 UTC daily (after fixtures + enrichment, before betting)
Workflow: .github/workflows/predictions.yml

Usage:
  python -m workers.jobs.fetch_predictions
  python -m workers.jobs.fetch_predictions --date 2026-04-30
"""

import sys
import argparse
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import get_prediction, parse_prediction
from workers.api_clients.supabase_client import get_client, store_prediction
from workers.utils.pipeline_utils import (
    check_fixtures_ready, log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed, log_pipeline_skipped,
    get_league_coverage_map, league_has_coverage,
)

console = Console()


def fetch_af_predictions(target_date: str) -> int:
    """Fetch AF predictions for all today's fixtures. Returns count stored."""
    client = get_client()

    # Load today's fixtures with AF IDs
    next_day_num = int(target_date[8:]) + 1
    next_date = f"{target_date[:8]}{next_day_num:02d}"

    result = client.table("matches").select(
        "id, api_football_id, league_id"
    ).gte("date", f"{target_date}T00:00:00Z").lt(
        "date", f"{next_date}T00:00:00Z"
    ).not_.is_("api_football_id", "null").execute()

    if not result.data:
        console.print("  No AF matches found")
        return 0

    # Load coverage map for filtering
    coverage_map = get_league_coverage_map()

    console.print(f"  {len(result.data)} fixtures to process")

    fetched = 0
    skipped_coverage = 0
    failed = 0

    for m in result.data:
        af_id = m["api_football_id"]
        match_id = m["id"]
        league_id = m.get("league_id")

        # Coverage check
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

    console.print(f"  [green]{fetched} predictions stored[/green] | {failed} unavailable | {skipped_coverage} skipped (no coverage)")
    return fetched


def main():
    parser = argparse.ArgumentParser(description="Fetch AF predictions")
    parser.add_argument("--date", type=str, default=None, help="Date (YYYY-MM-DD)")
    args = parser.parse_args()

    target_date = args.date or date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Predictions: {target_date} ═══[/bold green]")

    if not check_fixtures_ready(target_date):
        console.print("[yellow]Fixtures not ready — skipping.[/yellow]")
        log_pipeline_skipped("fetch_predictions", "Fixtures not ready", target_date)
        return

    run_id = log_pipeline_start("fetch_predictions", target_date)

    try:
        count = fetch_af_predictions(target_date)
        log_pipeline_complete(run_id, records_count=count)
        console.print(f"\n[bold green]Done. {count} predictions stored.[/bold green]")
    except Exception as e:
        console.print(f"\n[red]Failed: {e}[/red]")
        if run_id:
            log_pipeline_failed(run_id, str(e))
        raise


if __name__ == "__main__":
    main()
