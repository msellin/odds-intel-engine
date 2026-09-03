#!/usr/bin/env python3
"""TEAM-SCORING-RATES-OWN-RESULTS — fill goals_for/against averages from our own results.

`match_feature_vectors.goals_for_avg_*` is sourced from `match_signals` and was
populated on 27.8% of rows. The queued fix (UNDERSTAT-SCRAPER-BIG5-XG) covers
1.62% of the leagues we actually bet. We already hold 163,901 settled matches,
so we compute the rate ourselves — 73.2% of the O/U universe, no scraper.

Logic lives in workers/model/team_scoring_rates.py so the nightly job and this
backfill cannot drift apart. Existing values are never overwritten (COALESCE).

    python3 scripts/backfill_team_scoring_rates.py --dry-run
    python3 scripts/backfill_team_scoring_rates.py --apply
    python3 scripts/backfill_team_scoring_rates.py --apply --since 2026-01-01
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console  # noqa: E402

from workers.model.team_scoring_rates import (  # noqa: E402
    MIN_MATCHES, WINDOW_DAYS, fill_window,
)

console = Console()


def _coverage(cur, since: str) -> tuple:
    cur.execute("""
        SELECT COUNT(*),
               COUNT(goals_for_avg_home), COUNT(goals_for_avg_away),
               COUNT(goals_against_avg_home), COUNT(goals_against_avg_away)
          FROM match_feature_vectors WHERE match_date >= %s""", (since,))
    return cur.fetchone()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit no-op")
    ap.add_argument("--since", default="2023-01-01")
    ap.add_argument("--months", type=int, default=1,
                    help="months per batch (default 1) — keeps each statement short")
    args = ap.parse_args()

    from workers.api_clients.db import get_conn

    console.print(f"\n[bold]team scoring rates[/bold] — window {WINDOW_DAYS}d, "
                  f"min {MIN_MATCHES} matches, from {args.since}")
    console.print(f"[dim]{'APPLY' if args.apply else 'DRY RUN'}[/dim]\n")

    with get_conn() as conn, conn.cursor() as cur:
        before = _coverage(cur, args.since)

    start = date.fromisoformat(args.since)
    today = date.today() + timedelta(days=1)
    total = 0
    t0 = time.time()
    while start < today:
        # Month-ish batches: one statement over three years of fixtures holds a
        # long transaction against a table the nightly job also writes.
        end = min(start + timedelta(days=31 * args.months), today)
        with get_conn() as conn, conn.cursor() as cur:
            n = fill_window(cur, start.isoformat(), end.isoformat())
            if args.apply:
                conn.commit()
            else:
                conn.rollback()
        total += n
        if n:
            console.print(f"  {start} .. {end}  [bold]{n}[/bold] rows")
        start = end

    with get_conn() as conn, conn.cursor() as cur:
        after = _coverage(cur, args.since)

    tot = before[0] or 1
    console.print(f"\n  rows in range: {before[0]}   updated: {total}   "
                  f"({time.time() - t0:.1f}s)")
    for i, lab in enumerate(("goals_for_avg_home", "goals_for_avg_away",
                             "goals_against_avg_home", "goals_against_avg_away"), start=1):
        console.print(f"    {lab:26} {100.0 * before[i] / tot:5.1f}%  ->  "
                      f"{100.0 * after[i] / tot:5.1f}%")
    if not args.apply:
        console.print("\n[yellow]Dry run — nothing written. Re-run with --apply.[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
