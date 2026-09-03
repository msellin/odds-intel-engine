#!/usr/bin/env python3
"""FEATURE-DENSIFY-ROUND-2 — fill rest_days / season_progress / league_draw_rate_ytd.

Same pattern as backfill_team_scoring_rates.py: the logic lives in
workers/model/feature_densify.py so the nightly job and this backfill cannot
drift. Existing values are never overwritten (COALESCE).

    python3 scripts/backfill_feature_densify.py --dry-run
    python3 scripts/backfill_feature_densify.py --apply
    python3 scripts/backfill_feature_densify.py --apply --since 2026-08-01
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

from workers.model.feature_densify import FILLS, fill_window  # noqa: E402

console = Console()

TRACKED = ("rest_days_home", "rest_days_away", "season_progress",
           "league_draw_rate_ytd")


def _coverage(cur, since: str) -> dict:
    sel = ", ".join(f"COUNT({c})" for c in TRACKED)
    cur.execute(f"SELECT COUNT(*), {sel} FROM match_feature_vectors "
                f"WHERE match_date >= %s", (since,))
    row = cur.fetchone()
    return {"total": row[0], **{c: row[i + 1] for i, c in enumerate(TRACKED)}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--dry-run", action="store_true", help="explicit no-op")
    ap.add_argument("--since", default="2023-01-01")
    ap.add_argument("--months", type=int, default=1)
    args = ap.parse_args()

    from workers.api_clients.db import get_conn

    console.print(f"\n[bold]feature densify round 2[/bold] — from {args.since}")
    console.print(f"[dim]{'APPLY' if args.apply else 'DRY RUN'}[/dim]\n")

    with get_conn() as conn, conn.cursor() as cur:
        before = _coverage(cur, args.since)

    totals = {name: 0 for name, _ in FILLS}
    t0 = time.time()
    start = date.fromisoformat(args.since)
    today = date.today() + timedelta(days=1)
    while start < today:
        end = min(start + timedelta(days=31 * args.months), today)
        for name, sql in FILLS:
            with get_conn() as conn, conn.cursor() as cur:
                n = fill_window(cur, name, sql, start.isoformat(), end.isoformat())
                if args.apply:
                    conn.commit()
                else:
                    conn.rollback()
            totals[name] += n
        start = end

    with get_conn() as conn, conn.cursor() as cur:
        after = _coverage(cur, args.since)

    tot = before["total"] or 1
    console.print(f"  rows in range: {before['total']}   ({time.time() - t0:.1f}s)")
    for name, _ in FILLS:
        console.print(f"    {name:24} {totals[name]:>8} rows updated")
    console.print("")
    for c in TRACKED:
        console.print(f"    {c:24} {100.0 * before[c] / tot:5.1f}%  ->  "
                      f"{100.0 * after[c] / tot:5.1f}%")
    if not args.apply:
        console.print("\n[yellow]Dry run — nothing written.[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
