"""
OddsIntel — Today-Recovery Script

Runs the daily pipeline jobs in sequence to backfill a day where the
scheduler died. Order matches the cron schedule.

6 steps total:
    1. fixtures     — today's matches from API-Football
    2. odds         — bookmaker odds for today
    3. enrichment   — standings, H2H, injuries, coaches, transfers, venues
    4. predictions  — AF probability column
    5. betting      — Poisson/XGB model + signal gen + paper bets
    6. snapshot     — refresh /ops dashboard counters

Usage:
    venv/bin/python scripts/recover_today.py              # run all 6 steps
    venv/bin/python scripts/recover_today.py 3            # start from step 3
    venv/bin/python scripts/recover_today.py 3 4 5        # run specific steps
    venv/bin/python scripts/recover_today.py --from 4     # start from step 4

Safe to re-run — every job uses ON CONFLICT upserts, no duplicates.
"""

import sys
import os
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

STEPS = [
    (1, "fixtures",    "Fixtures Fetch",    "workers.jobs.fetch_fixtures",    "run_fixtures"),
    (2, "odds",        "Odds Fetch",         "workers.jobs.fetch_odds",         "run_odds"),
    (3, "enrichment",  "Enrichment (full)",  "workers.jobs.fetch_enrichment",   "run_enrichment"),
    (4, "predictions", "AF Predictions",     "workers.jobs.fetch_predictions",  "run_predictions"),
    (5, "betting",     "Betting Pipeline",   "workers.jobs.daily_pipeline_v2",  "run_morning"),
    (6, "snapshot",    "Ops Snapshot",       "workers.api_clients.supabase_client", "write_ops_snapshot"),
]


def _resolve_steps(argv: list[str]) -> list[tuple]:
    """Parse CLI args into the list of steps to run."""
    args = argv[1:]

    if not args:
        return STEPS

    # --from N  →  steps N..6
    if args[0] == "--from" and len(args) >= 2:
        try:
            start = int(args[1])
            return [s for s in STEPS if s[0] >= start]
        except ValueError:
            pass

    # Single number N  →  steps N..6
    if len(args) == 1:
        try:
            start = int(args[0])
            return [s for s in STEPS if s[0] >= start]
        except ValueError:
            pass

    # Multiple numbers  →  exactly those steps
    try:
        nums = {int(a) for a in args}
        return [s for s in STEPS if s[0] in nums]
    except ValueError:
        pass

    print(f"Usage: recover_today.py [--from N | N [N ...]]")
    sys.exit(1)


def _run(num: int, label: str, module: str, fn_name: str, total: int, done: int) -> bool:
    remaining = total - done
    print(f"\n{'═' * 60}")
    print(f"▶ Step {num}/{total}  —  {label}")
    print(f"  {done} done · {remaining} remaining (including this one)")
    print(f"{'═' * 60}")
    t0 = time.monotonic()
    try:
        import importlib
        mod = importlib.import_module(module)
        fn = getattr(mod, fn_name)
        fn()
        elapsed = time.monotonic() - t0
        print(f"\n✅ Step {num} — {label} — {elapsed:.1f}s")
        return True
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"\n❌ Step {num} — {label} — {type(e).__name__}: {e} ({elapsed:.1f}s)")
        traceback.print_exc()
        return False


def main():
    steps = _resolve_steps(sys.argv)
    total = len(steps)
    skipped = [s for s in STEPS if s not in steps]

    print(f"\n{'═' * 60}")
    print(f"OddsIntel Recovery  —  {total} of {len(STEPS)} steps selected")
    print(f"{'═' * 60}")

    if skipped:
        print("  Skipping: " + ", ".join(f"{s[0]}. {s[2]}" for s in skipped))

    print("  Running:  " + ", ".join(f"{s[0]}. {s[2]}" for s in steps))

    results = []
    for i, step in enumerate(steps):
        num, _key, label, module, fn_name = step
        ok = _run(num, label, module, fn_name, total=total, done=i)
        results.append((num, label, ok))

    print(f"\n{'═' * 60}")
    print(f"Summary  —  {sum(ok for _, _, ok in results)}/{total} passed")
    print(f"{'═' * 60}")
    for num, label, ok in results:
        print(f"  {'✅' if ok else '❌'}  {num}. {label}")

    failed = [(n, l) for n, l, ok in results if not ok]
    if failed:
        nums = " ".join(str(n) for n, _ in failed)
        print(f"\n{len(failed)} step(s) failed. To retry just those:")
        print(f"  venv/bin/python scripts/recover_today.py {nums}")
        sys.exit(1)

    print("\nAll steps done. Refresh /ops to verify.")


if __name__ == "__main__":
    main()
