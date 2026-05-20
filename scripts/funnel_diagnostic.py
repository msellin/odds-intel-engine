"""Run a one-off morning pipeline with verbose candidate-funnel logging.

Tells you, per bot, how many candidates the pipeline generated and where in the
filter chain they were dropped. Used to diagnose silent bots — e.g. why
bot_ou15_defensive has placed zero bets since 2026-05-08 even though Pinnacle
covers its leagues and ACCESSIBLE-BM isn't the cause (per audit section 6).

Usage:
    # Full funnel — every bot that ran in the current cohort
    python3 scripts/funnel_diagnostic.py

    # Focus on one bot (cleaner output)
    python3 scripts/funnel_diagnostic.py --bot bot_ou15_defensive

    # Run a specific cohort instead of auto-detecting
    python3 scripts/funnel_diagnostic.py --cohort midday --bot bot_ou15_defensive

Requirements:
    Morning pipeline must already have run for today (so matches/odds/
    predictions are in the DB). skip_fetch=True — zero API calls. No bets
    will be placed for real — bot bets get written to simulated_bets per
    the regular pipeline flow. To stay 100% read-only, use --shadow.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.jobs.daily_pipeline_v2 import run_morning


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bot", help="Focus on a single bot (e.g. bot_ou15_defensive)")
    ap.add_argument("--cohort", choices=("morning", "midday", "pre_ko"),
                    help="Only run bots assigned to this cohort. Default: all bots.")
    ap.add_argument("--shadow", action="store_true",
                    help="Run in shadow mode — writes to shadow_bets, not simulated_bets. "
                         "Safe to invoke ad-hoc without affecting real bot bankrolls.")
    args = ap.parse_args()

    kwargs = {
        "skip_fetch":         True,
        "cohort":             args.cohort,
        "verbose_funnel":     True,
        "verbose_funnel_bot": args.bot,
    }
    if args.shadow:
        kwargs["shadow_mode"]   = True
        # DB constraint shadow_bets_shadow_cohort_check restricts to
        # ('morning', 'midday', 'pre_ko'). Scheduler uses HHMM labels — a
        # separate bug, queued as SHADOW-COHORT-CONSTRAINT. For the diagnostic
        # use a valid label so writes go through; the cohort field is
        # informational for funnel-purpose runs.
        kwargs["shadow_cohort"] = "morning"

    run_morning(**kwargs)


if __name__ == "__main__":
    main()
