"""
Stage 0a — ELO historical backfill.

The daily settlement job `update_elo_ratings()` only walks yesterday → today.
Backfilled matches from 2023 have no ELO chain because no run computed them
when they happened. This script replays ELO forward through every finished
match in the DB so `team_elo_daily` reflects the full history.

Mirrors the math in `workers/jobs/settlement.py:update_elo_ratings`:
  K = 30
  HOME_ADV = 100
  gd_mult = max(1, (|goal_diff| + 1) ** 0.5)
  New ELO baseline = 1500

Idempotent: re-running rebuilds the whole chain. Today's row (if any) is
left to the daily settlement run.

Usage:
    python3 scripts/backfill_elo_historical.py                       # full history up to yesterday
    python3 scripts/backfill_elo_historical.py --from 2023-01-01     # restrict start date
    python3 scripts/backfill_elo_historical.py --dry-run             # walk + print, no writes
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

from workers.api_clients.supabase_client import bulk_upsert, execute_query

console = Console()

K = 30
HOME_ADV = 100
DEFAULT_ELO = 1500.0


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", default=None,
                   help="ISO date (YYYY-MM-DD). Default: earliest finished match.")
    p.add_argument("--to", dest="date_to", default=None,
                   help="ISO date (YYYY-MM-DD). Default: yesterday (avoid clobbering today's settlement run).")
    p.add_argument("--dry-run", action="store_true",
                   help="Walk matches and compute, but don't write team_elo_daily.")
    p.add_argument("--batch-size", type=int, default=2000,
                   help="Upsert in chunks of this many ELO rows.")
    return p.parse_args()


def main():
    args = _parse_args()

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    date_to = args.date_to or yesterday

    where = ["status = 'finished'", "score_home IS NOT NULL", "score_away IS NOT NULL",
             "home_team_id IS NOT NULL", "away_team_id IS NOT NULL", "date <= %s"]
    params = [f"{date_to}T23:59:59"]
    if args.date_from:
        where.insert(0, "date >= %s")
        params.insert(0, f"{args.date_from}T00:00:00")

    sql = (
        "SELECT id, date, home_team_id, away_team_id, score_home, score_away "
        "FROM matches WHERE " + " AND ".join(where) +
        " ORDER BY date ASC, id ASC"
    )

    console.print("[cyan]Loading finished matches...[/cyan]")
    matches = execute_query(sql, params)
    console.print(f"  {len(matches):,} matches in scope (≤ {date_to})")
    if not matches:
        return

    elo: dict[str, float] = {}
    rows_to_upsert: list[tuple] = []

    with Progress(TextColumn("[bold blue]ELO walk"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as bar:
        task = bar.add_task("walk", total=len(matches))

        for m in matches:
            h_id = m["home_team_id"]
            a_id = m["away_team_id"]
            sh = int(m["score_home"])
            sa = int(m["score_away"])

            h_elo_pre = elo.get(h_id, DEFAULT_ELO)
            a_elo_pre = elo.get(a_id, DEFAULT_ELO)

            h_eff = h_elo_pre + HOME_ADV
            a_eff = a_elo_pre

            exp_h = 1 / (1 + 10 ** ((a_eff - h_eff) / 400))
            exp_a = 1 - exp_h

            gd = abs(sh - sa)
            gd_mult = max(1.0, (gd + 1) ** 0.5)

            if sh > sa:
                actual_h, actual_a = 1.0, 0.0
            elif sh < sa:
                actual_h, actual_a = 0.0, 1.0
            else:
                actual_h, actual_a = 0.5, 0.5

            new_h = h_elo_pre + K * gd_mult * (actual_h - exp_h)
            new_a = a_elo_pre + K * gd_mult * (actual_a - exp_a)

            elo[h_id] = new_h
            elo[a_id] = new_a

            # `m["date"]` may be datetime or string — normalize to YYYY-MM-DD
            d = m["date"]
            if isinstance(d, datetime):
                day = d.date().isoformat()
            elif isinstance(d, date):
                day = d.isoformat()
            else:
                day = str(d)[:10]

            rows_to_upsert.append((h_id, day, round(new_h, 2)))
            rows_to_upsert.append((a_id, day, round(new_a, 2)))

            bar.advance(task)

    # Dedupe: keep the LAST ELO per (team_id, date) — a team can play
    # multiple matches on one date in cup-week scenarios, only the final
    # ELO of the day should be persisted.
    seen: dict[tuple, tuple] = {}
    for row in rows_to_upsert:
        seen[(row[0], row[1])] = row
    deduped = list(seen.values())

    console.print(
        f"\n[cyan]Computed[/cyan] {len(rows_to_upsert):,} ELO updates → "
        f"{len(deduped):,} (team, date) rows after dedup. "
        f"{len(elo):,} distinct teams in chain."
    )

    if args.dry_run:
        console.print("[yellow]Dry run — no writes.[/yellow]")
        # Sanity preview: 3 highest, 3 lowest current ELOs
        ranked = sorted(elo.items(), key=lambda kv: kv[1], reverse=True)
        console.print("\nTop 3 by final ELO:")
        for tid, e in ranked[:3]:
            console.print(f"  {tid}  {e:.1f}")
        console.print("\nBottom 3 by final ELO:")
        for tid, e in ranked[-3:]:
            console.print(f"  {tid}  {e:.1f}")
        return

    console.print("[cyan]Bulk upserting team_elo_daily...[/cyan]")
    written = 0
    for i in range(0, len(deduped), args.batch_size):
        chunk = deduped[i:i + args.batch_size]
        n = bulk_upsert(
            table="team_elo_daily",
            columns=["team_id", "date", "elo_rating"],
            rows=chunk,
            conflict_columns=["team_id", "date"],
            update_columns=["elo_rating"],
        )
        written += n or len(chunk)
        console.print(f"  Wrote {written:,} / {len(deduped):,}")

    console.print(f"[bold green]✓ ELO backfill complete — {written:,} rows upserted.[/bold green]")


if __name__ == "__main__":
    main()
