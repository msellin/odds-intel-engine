"""
OddsIntel — Betting Pipeline

Pure model + betting logic. Reads ALL data from DB (fixtures, odds, predictions,
enrichment already stored by upstream jobs). No external API calls.

Upstream jobs (must complete before this runs):
  - fetch_fixtures.py (04:00 UTC)
  - fetch_enrichment.py (04:15 UTC)
  - fetch_odds.py (05:00 UTC)
  - fetch_predictions.py (05:30 UTC)

Schedule: 06:00 UTC daily
Workflow: .github/workflows/betting.yml

Usage:
  python -m workers.jobs.betting_pipeline
  python -m workers.jobs.betting_pipeline report
"""

import sys
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import model functions from the existing monolith (will be inlined later)
from workers.jobs.daily_pipeline_v2 import (
    BOTS_CONFIG, run_morning as _monolith_run_morning, run_report,
)
from workers.utils.pipeline_utils import (
    check_fixtures_ready, log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed, log_pipeline_skipped,
)

console = Console()


def run_betting():
    """
    Run the betting pipeline.

    Phase 1 (current): delegates to the existing monolith run_morning().
    Phase 2 (TODO): reads from DB only, no API calls.

    The monolith's fetch functions are idempotent — if upstream jobs already
    stored the data, the monolith will just re-fetch and upsert (harmless).
    Once validated, Phase 2 will strip the fetch code entirely.
    """
    today_str = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Betting Pipeline: {today_str} ═══[/bold green]")

    run_id = log_pipeline_start("betting_pipeline", today_str)

    try:
        # Phase 1: delegate to monolith (includes fetch + model + bet)
        _monolith_run_morning()

        log_pipeline_complete(run_id, metadata={"phase": 1})
        console.print(f"\n[bold green]Betting pipeline complete.[/bold green]")

    except Exception as e:
        console.print(f"\n[red]Betting pipeline failed: {e}[/red]")
        if run_id:
            log_pipeline_failed(run_id, str(e))
        raise


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        run_report()
    else:
        run_betting()


if __name__ == "__main__":
    main()
