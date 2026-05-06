"""
OddsIntel — Betting Pipeline (Phase 2)

Pure model + betting logic. Reads ALL data from DB — no external API calls.
Upstream jobs store everything before this runs at 06:00 UTC.

BOT-TIMING: Bots are split into 3 time-window cohorts to find the edge-maximizing
window. The cohort is determined by the current UTC hour:
  - morning  (06:00-10:59 UTC): early odds, full match slate
  - midday   (11:00-14:59 UTC): post-injury-news refresh
  - pre_ko   (15:00+     UTC): confirmed lineups, pre-kickoff
Each scheduler run (06:00, 11:00, 15:00, 19:00) only places bets for the
bots assigned to that cohort. See BOT_TIMING_COHORTS in daily_pipeline_v2.py.

Upstream jobs (must complete before this runs):
  - fetch_fixtures.py   (04:00 UTC) — stores matches
  - fetch_enrichment.py (04:15 UTC) — stores standings, H2H, injuries
  - fetch_odds.py       (05:00 UTC) — stores odds_snapshots
  - fetch_predictions.py(05:30 UTC) — stores predictions (source='af')

Schedule: 06:00 UTC daily (morning cohort)
Workflow: .github/workflows/betting.yml

Usage:
  python -m workers.jobs.betting_pipeline
  python -m workers.jobs.betting_pipeline report
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.jobs.daily_pipeline_v2 import run_morning, run_report
from workers.utils.pipeline_utils import (
    log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed,
)

console = Console()


def _current_cohort() -> str:
    """Determine which bot timing cohort is active based on current UTC hour."""
    hour = datetime.now(timezone.utc).hour
    if hour < 11:
        return "morning"
    elif hour < 15:
        return "midday"
    else:
        return "pre_ko"


def run_betting(cohort: str | None = None):
    """
    Run the betting pipeline (Phase 2 — DB-only, no API calls).
    Reads matches, odds, and predictions stored by upstream jobs.

    cohort: 'morning', 'midday', or 'pre_ko'. Defaults to current time window.
    """
    from datetime import date
    today_str = date.today().isoformat()

    active_cohort = cohort or _current_cohort()
    console.print(
        f"[bold green]═══ OddsIntel Betting Pipeline: {today_str} "
        f"[{active_cohort} cohort] ═══[/bold green]"
    )

    run_id = log_pipeline_start("betting_pipeline", today_str)

    try:
        # Phase 2: skip_fetch=True — upstream jobs already stored everything in DB
        run_morning(skip_fetch=True, cohort=active_cohort)

        log_pipeline_complete(run_id, metadata={"phase": 2, "skip_fetch": True, "cohort": active_cohort})
        console.print("\n[bold green]Betting pipeline complete.[/bold green]")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        console.print(f"\n[red]Betting pipeline failed: {e}[/red]")
        console.print(f"[red dim]{tb}[/red dim]")
        if run_id:
            # Store full traceback (not just str(e)) to help diagnose Railway failures
            full_error = f"{type(e).__name__}: {e}\n\nTraceback:\n{tb}"
            log_pipeline_failed(run_id, full_error[:2000])
        raise


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        run_report()
    else:
        run_betting()


if __name__ == "__main__":
    main()
