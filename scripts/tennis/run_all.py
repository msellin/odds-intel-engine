#!/usr/bin/env python3
"""
Tennis daily cycle — one-command orchestrator.

Fires the full tennis pipeline in the right order, with sensible defaults
for manual operation. Used when the Railway scheduler is unreliable (the
7-day silent-failure incident on 2026-06-25 motivated this) AND as a
manual catch-up tool after outages.

Order matters:
  1. settle yesterday's finished matches (so the table reflects truth
     before we add new rows on top)
  2. scan today's tour-main pool via The Odds API (writes new picks)
  3. scan today's broader Coolbet pool (writes Pinnacle-anchored + coolbet_only)
  4. capture Pinnacle close-odds for any imminent fixtures (CLV for actionable picks)

Each step is independent — a failure in one doesn't stop the rest, so a
flaky network or quota issue doesn't lose the whole cycle. Exit code 0
if all 4 succeed, non-zero with a per-step summary otherwise.

Usage:
    python3 scripts/tennis/run_all.py                # live
    python3 scripts/tennis/run_all.py --dry-run      # all sub-scripts in dry-run
    python3 scripts/tennis/run_all.py --skip-coolbet # if Imperva is misbehaving
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).parents[2]

STEPS = [
    # (label, script, extra_args, timeout_s)
    ("settle yesterday's finished matches",
     "scripts/tennis/settle_value_bets.py", [], 180),
    ("scan tour-mains via The Odds API",
     "scripts/tennis/odds_api_scanner.py",  [], 300),
    ("scan full Coolbet pool",
     "scripts/tennis/place_coolbet_tennis.py", ["--record"], 300),
    ("capture Pinnacle closing odds",
     "scripts/tennis/capture_closing_odds.py", [], 120),
]


def run_step(label: str, script: str, extra_args: list[str], timeout_s: int,
             dry_run: bool, env_override: dict) -> tuple[bool, str]:
    args = [sys.executable, script] + extra_args
    if dry_run and "--record" in extra_args:
        # Coolbet uses --record to opt INTO writes; remove it for dry-run.
        args = [a for a in args if a != "--record"]
    if dry_run and script.endswith("settle_value_bets.py"):
        args.append("--dry-run")
    if dry_run and script.endswith("capture_closing_odds.py"):
        args.append("--dry-run")
    if dry_run and script.endswith("odds_api_scanner.py"):
        args.append("--dry-run")

    env = dict(os.environ)
    env.update(env_override)

    print(f"\n{'=' * 70}")
    print(f"▶ {label}")
    print(f"  {' '.join(args)}")
    print("=" * 70)

    try:
        result = subprocess.run(
            args, cwd=str(REPO), env=env,
            capture_output=False, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout_s}s"
    except Exception as e:
        return False, f"crashed: {e}"

    if result.returncode != 0:
        return False, f"exit {result.returncode}"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="all sub-scripts dry-run (no DB writes)")
    ap.add_argument("--skip-coolbet", action="store_true",
                    help="skip the Coolbet pass (use when Imperva cookie expired)")
    ap.add_argument("--skip-settlement", action="store_true",
                    help="skip the settlement step")
    args = ap.parse_args()

    # COOLBET-NO-FS — needed for the public-read scanner to bypass FlareSolverr
    # when running outside the operator's Mac daemon. Safe everywhere because
    # require_auth=False in place_coolbet_tennis.py.
    env_override = {"COOLBET_NO_FS": os.environ.get("COOLBET_NO_FS", "true")}

    print(f"TENNIS RUN-ALL  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}  "
          f"{'(DRY RUN)' if args.dry_run else '(LIVE)'}")

    results: list[tuple[str, bool, str]] = []
    for label, script, extra_args, timeout_s in STEPS:
        if args.skip_coolbet and "place_coolbet_tennis" in script:
            results.append((label, True, "skipped"))
            continue
        if args.skip_settlement and "settle_value_bets" in script:
            results.append((label, True, "skipped"))
            continue
        ok, note = run_step(label, script, extra_args, timeout_s,
                            args.dry_run, env_override)
        results.append((label, ok, note))

    print(f"\n{'=' * 70}")
    print("RUN-ALL SUMMARY")
    print("=" * 70)
    any_failed = False
    for label, ok, note in results:
        mark = "✓" if ok else "✗"
        print(f"  [{mark}] {label}  — {note}")
        if not ok and note != "skipped":
            any_failed = True
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
