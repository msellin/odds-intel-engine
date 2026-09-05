#!/usr/bin/env python3
"""Backfill match_events for the window where the writer was silently broken.

MATCH-EVENTS-SILENT-WRITE-FAILURE-2026-09-06
============================================
`match_events` stopped receiving rows on 2026-08-21 while settlement and
live_tracker both kept calling AF `/fixtures/events` every night. Two bugs,
either of which alone was enough:

  1. `store_match_events_af` used `ON CONFLICT (match_id, af_event_order)`
     against `idx_match_events_af_dedup`, which is a PARTIAL unique index
     (`WHERE af_event_order IS NOT NULL`). Postgres only matches a partial
     unique index when the statement repeats the predicate, so every insert
     raised InvalidColumnReference — and `except Exception: pass` ate it.
  2. Settlement resolved `home_team_api_id` from `match_injuries`, which
     covers 3.2% of finished matches. On the other 96.8% the side was
     'unknown' and `chk_match_events_team` rejected the row.

Both are fixed. This script recovers the window. It is idempotent — the upsert
means re-running it cannot duplicate — so it is safe to re-run after an
interruption.

WHAT THIS ALSO MEASURES
-----------------------
`AF-WASTE-SETTLEMENT-FANOUT` gated /fixtures/statistics and /fixtures/players
on league coverage flags but left /fixtures/events ungated. The obvious next
step was to gate it the same way, EXCEPT that the only available measurement of
"do no-coverage leagues produce events" was taken while the writer was broken
and returned 0.0% for BOTH groups — a measurement of the bug, not of coverage.

So this script records the real events-per-fixture rate split by
`leagues.coverage_events`, and prints it at the end. That number is what should
decide whether settlement gates the call. Gating on the broken measurement
would have been the third time today a number was trusted without checking what
produced it.

Usage:
    python3 scripts/backfill_match_events.py --since 2026-08-21 --dry-run
    python3 scripts/backfill_match_events.py --since 2026-08-21 --apply
    python3 scripts/backfill_match_events.py --since 2026-08-21 --apply --limit 500
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from workers.api_clients.api_football import get_fixture_events, parse_fixture_events
from workers.api_clients.db import execute_query, store_match_events_batch

console = Console()


def candidates(since: str, limit: int | None) -> list[dict]:
    """Finished matches in the window that have no events at all.

    Deliberately `NOT EXISTS` rather than a left join with a count: a match that
    genuinely had zero events (a 0-0 with no cards) is indistinguishable from a
    match whose write failed, so re-attempting the empty ones is the only safe
    read. They cost one call each and settle the question permanently.
    """
    sql = """
        SELECT m.id, m.api_football_id, m.home_team_api_id, m.date,
               COALESCE(l.coverage_events, false) AS cov_events
          FROM matches m
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished'
           AND m.date >= %s
           AND m.api_football_id IS NOT NULL
           AND m.home_team_api_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM match_events e WHERE e.match_id = m.id)
         ORDER BY m.date DESC
    """
    params: list = [since]
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    return execute_query(sql, params)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-21",
                    help="start of the broken window (default: the day writes stopped)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.apply and not args.dry_run:
        console.print("[red]Pass --apply or --dry-run explicitly.[/red]")
        return 2

    rows = candidates(args.since, args.limit)
    console.print(
        f"[cyan]{len(rows):,} finished matches since {args.since} have no events "
        f"({sum(1 for r in rows if r['cov_events']):,} in coverage_events leagues)[/cyan]"
    )
    if args.dry_run:
        console.print("[yellow]Dry run — no AF calls made, nothing written.[/yellow]")
        return 0

    # by coverage flag: [fixtures_tried, fixtures_with_events, events_stored]
    stats: dict[bool, list[int]] = defaultdict(lambda: [0, 0, 0])
    failures = 0
    started = time.time()

    for i, m in enumerate(rows, 1):
        cov = bool(m["cov_events"])
        stats[cov][0] += 1
        try:
            parsed = parse_fixture_events(get_fixture_events(m["api_football_id"]))
        except Exception as exc:
            failures += 1
            if failures <= 5:
                console.print(f"[yellow]fetch failed for {m['api_football_id']}: {exc}[/yellow]")
            continue

        if not parsed:
            continue

        stored = store_match_events_batch(
            m["id"], parsed, home_team_api_id=m["home_team_api_id"]
        )
        if stored:
            stats[cov][1] += 1
            stats[cov][2] += stored

        if i % 250 == 0:
            rate = i / max(time.time() - started, 1e-9)
            console.print(
                f"  {i:,}/{len(rows):,} · {sum(s[2] for s in stats.values()):,} events "
                f"· {rate:.1f} fixtures/s"
            )

    console.print("\n[bold]Result by league coverage_events flag[/bold]")
    for cov in (True, False):
        tried, with_ev, stored = stats[cov]
        if not tried:
            continue
        pct = 100.0 * with_ev / tried
        console.print(
            f"  coverage_events={str(cov):<5} tried={tried:>6,} "
            f"produced_events={with_ev:>6,} ({pct:5.1f}%) rows={stored:>7,}"
        )
    console.print(
        f"\n[dim]Fetch failures: {failures}. "
        f"The coverage=False row above is the number that decides whether "
        f"settlement should gate /fixtures/events on the flag — if it is near "
        f"zero the gate is free, if it is not the gate would lose real data."
        f"[/dim]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
