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
from datetime import date, timedelta

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.api_clients.api_football import get_prediction, parse_prediction
from workers.api_clients.supabase_client import (
    bulk_store_predictions, bulk_update_match_af_predictions,
)
from workers.api_clients.db import execute_query
from workers.utils.pipeline_utils import (
    check_fixtures_ready, log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed, log_pipeline_skipped,
    get_league_coverage_map, league_has_coverage,
)

console = Console()


def fetch_af_predictions(target_date: str) -> int:
    """Fetch AF predictions for all today's fixtures. Returns count stored."""
    next_date = (date.fromisoformat(target_date) + timedelta(days=1)).isoformat()

    rows = execute_query(
        """SELECT id, api_football_id, league_id FROM matches
           WHERE date >= %s AND date < %s AND api_football_id IS NOT NULL""",
        (f"{target_date}T00:00:00Z", f"{next_date}T00:00:00Z"),
    )

    if not rows:
        console.print("  No AF matches found")
        return 0

    # Load coverage map for filtering
    coverage_map = get_league_coverage_map()

    console.print(f"  {len(rows)} fixtures to process")

    fetched = 0
    skipped_coverage = 0
    failed = 0

    # BULK-STORE-PREDICTIONS: collect AF responses in memory, then write the
    # `matches.af_prediction` JSONB column and the `predictions` rows in two
    # bulk operations. The previous per-fixture loop did 4 round-trips/fixture
    # (1 UPDATE + 3 INSERTs) — at 150ms EU pooler RTT × 500 fixtures that was
    # ~5min of pure DB wait; now ~1s.
    import json as _json
    af_jsonb_rows: list[tuple[str, str]] = []   # (match_id, raw_json_str)
    pred_rows: list[dict] = []

    for m in rows:
        af_id = m["api_football_id"]
        match_id = m["id"]
        league_id = m.get("league_id")

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

            af_jsonb_rows.append((match_id, _json.dumps(parsed["raw"])))

            for market, prob_key in (
                ("1x2_home", "af_home_prob"),
                ("1x2_draw", "af_draw_prob"),
                ("1x2_away", "af_away_prob"),
            ):
                prob = parsed.get(prob_key)
                if prob is not None:
                    pred_rows.append({
                        "match_id": match_id,
                        "market": market,
                        "source": "af",
                        "model_prob": prob,
                        "reasoning": "af_prediction",
                    })

            fetched += 1
        except Exception as e:
            console.print(f"  [yellow]Prediction fetch failed for AF {af_id}: {e}[/yellow]")
            failed += 1

    # Two bulk writes, no per-fixture round-trips.
    try:
        n_jsonb = bulk_update_match_af_predictions(af_jsonb_rows)
        console.print(f"  [dim]bulk UPDATE matches.af_prediction: {n_jsonb} rows[/dim]")
    except Exception as e:
        console.print(f"  [yellow]bulk_update_match_af_predictions failed: {e}[/yellow]")

    try:
        n_pred = bulk_store_predictions(pred_rows)
        console.print(f"  [dim]bulk INSERT predictions (source=af): {n_pred} rows[/dim]")
    except Exception as e:
        console.print(f"  [red]bulk_store_predictions failed: {e}[/red]")
        failed += len(pred_rows)

    console.print(f"  [green]{fetched} predictions stored[/green] | {failed} unavailable | {skipped_coverage} skipped (no coverage)")
    return fetched


def run_predictions(target_date: str = None):
    """Run predictions fetch pipeline. Callable by scheduler or CLI."""
    target_date = target_date or date.today().isoformat()
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


def main():
    parser = argparse.ArgumentParser(description="Fetch AF predictions")
    parser.add_argument("--date", type=str, default=None, help="Date (YYYY-MM-DD)")
    args = parser.parse_args()
    run_predictions(target_date=args.date)


if __name__ == "__main__":
    main()
