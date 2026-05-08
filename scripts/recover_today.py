"""
OddsIntel — Today-Recovery Script

Runs the daily pipeline jobs in sequence to backfill a day where the
scheduler died (e.g. 2026-05-08 pool-exhaustion outage). Order matches
the cron schedule:

    1. Fixtures Fetch       (04:00 UTC) — today's matches
    2. Odds Fetch           (every 30min) — bookmaker odds for today
    3. Enrichment full      (04:15/12:00/16:00) — standings, H2H, injuries, coaches, venues
    4. AF Predictions       (05:30 UTC) — AF probability column
    5. Betting Pipeline     (06:00 UTC) — Poisson/XGB model + signal gen + paper bets
    6. Ops Snapshot         (hourly) — refresh /ops dashboard counters

Each job is wrapped so a single failure doesn't abort the rest. The
betting pipeline depends on odds + enrichment + predictions, so order
matters.

Usage:
    venv/bin/python scripts/recover_today.py

Safe to re-run — every job has ON CONFLICT DO UPDATE / DO NOTHING upserts
under the hood, so no duplicates. After the run, refresh /ops in the
browser to see counters populate.
"""

import sys
import os
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def _run(label: str, fn, *args, **kwargs):
    print(f"\n{'═' * 60}\n▶ {label}\n{'═' * 60}")
    t0 = time.monotonic()
    try:
        fn(*args, **kwargs)
        elapsed = time.monotonic() - t0
        print(f"\n✅ {label} — {elapsed:.1f}s")
        return True
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"\n❌ {label} — {type(e).__name__}: {e} ({elapsed:.1f}s)")
        traceback.print_exc()
        return False


def main():
    results = []

    from workers.jobs.fetch_fixtures import run_fixtures
    results.append(("Fixtures Fetch", _run("1. Fixtures Fetch", run_fixtures)))

    from workers.jobs.fetch_odds import run_odds
    results.append(("Odds Fetch", _run("2. Odds Fetch", run_odds)))

    from workers.jobs.fetch_enrichment import run_enrichment
    results.append(("Enrichment", _run("3. Enrichment (full)", run_enrichment)))

    from workers.jobs.fetch_predictions import run_predictions
    results.append(("AF Predictions", _run("4. AF Predictions", run_predictions)))

    from workers.jobs.daily_pipeline_v2 import run_morning
    results.append(("Betting Pipeline", _run("5. Betting Pipeline (run_morning)", run_morning)))

    from workers.api_clients.supabase_client import write_ops_snapshot
    results.append(("Ops Snapshot", _run("6. Ops Snapshot", write_ops_snapshot)))

    print(f"\n{'═' * 60}\nRecovery summary\n{'═' * 60}")
    for label, ok in results:
        print(f"  {'✅' if ok else '❌'}  {label}")

    failed = [l for l, ok in results if not ok]
    if failed:
        print(f"\n{len(failed)} job(s) failed: {', '.join(failed)}")
        sys.exit(1)
    print("\nAll jobs completed. Refresh /ops to verify dashboard counters.")


if __name__ == "__main__":
    main()
