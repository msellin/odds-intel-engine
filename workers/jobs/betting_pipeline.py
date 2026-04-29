"""
OddsIntel — Betting Pipeline (Phase 2)

Pure model + betting logic. Reads ALL data from DB — no external API calls.
Upstream jobs store everything before this runs at 06:00 UTC.

Upstream jobs (must complete before this runs):
  - fetch_fixtures.py   (04:00 UTC) — stores matches
  - fetch_enrichment.py (04:15 UTC) — stores standings, H2H, injuries
  - fetch_odds.py       (05:00 UTC) — stores odds_snapshots
  - fetch_predictions.py(05:30 UTC) — stores predictions (source='af')

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

from workers.jobs.daily_pipeline_v2 import run_morning, run_report
from workers.utils.pipeline_utils import (
    check_fixtures_ready, log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed, log_pipeline_skipped,
)

console = Console()


def run_betting():
    """
    Run the betting pipeline (Phase 2 — DB-only, no API calls).
    Reads matches, odds, and predictions stored by upstream jobs.
    """
    today_str = date.today().isoformat()
    console.print(f"[bold green]═══ OddsIntel Betting Pipeline: {today_str} ═══[/bold green]")

    run_id = log_pipeline_start("betting_pipeline", today_str)

    try:
        # Phase 2: skip_fetch=True — upstream jobs already stored everything in DB
        run_morning(skip_fetch=True)

        log_pipeline_complete(run_id, metadata={"phase": 2, "skip_fetch": True})
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
