"""
Stage 0b — Team form cache historical backfill.

`update_team_form_cache()` runs daily and writes one row per team for *today*
using `compute_team_form_from_db(team_id, today)` which queries the matches
table twice per call. For a forward backfill that would be:
  7,634 teams × ~150 dates/team × 2 queries = ~2.3M queries → days of wall time
on the EU pooler.

Instead, this script:
  1. Loads every finished match in one query, ordered by date ASC.
  2. Walks them forward, maintaining an in-memory deque of the last 10
     {gf, ga} results per team.
  3. For each match, BEFORE adding its result to the deques, computes form
     for both teams from their *current* deques and stages a row write
     keyed (team_id, match_date). That row represents "team's rolling form
     as of just before they played this match".
  4. Bulk-upserts in batches of 5000.

This produces one team_form_cache row per (team, match_date) — the snapshot
that match-feature-vector builds need at training time.

Usage:
    python3 scripts/backfill_team_form_historical.py
    python3 scripts/backfill_team_form_historical.py --window 10 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from workers.api_clients.supabase_client import bulk_upsert, execute_query

console = Console()


def _form_from_deque(buf: deque) -> dict | None:
    """Mirror of compute_team_form_from_db output, but from in-memory deque."""
    n = len(buf)
    if n < 3:
        return None
    wins = sum(1 for r in buf if r["gf"] > r["ga"])
    draws = sum(1 for r in buf if r["gf"] == r["ga"])
    losses = sum(1 for r in buf if r["gf"] < r["ga"])
    gf_list = [r["gf"] for r in buf]
    ga_list = [r["ga"] for r in buf]
    return {
        "matches_played": n,
        "win_pct": round(wins / n, 4),
        "draw_pct": round(draws / n, 4),
        "loss_pct": round(losses / n, 4),
        "ppg": round((wins * 3 + draws) / n, 3),
        "goals_scored_avg": round(sum(gf_list) / n, 3),
        "goals_conceded_avg": round(sum(ga_list) / n, 3),
        "goal_diff_avg": round((sum(gf_list) - sum(ga_list)) / n, 3),
        "clean_sheet_pct": round(sum(1 for g in ga_list if g == 0) / n, 4),
        "over25_pct": round(
            sum(1 for i in range(n) if gf_list[i] + ga_list[i] > 2) / n, 4
        ),
        "btts_pct": round(
            sum(1 for i in range(n) if gf_list[i] > 0 and ga_list[i] > 0) / n, 4
        ),
    }


def _normalize_day(d) -> str:
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="date_from", default=None)
    p.add_argument("--to", dest="date_to", default=None)
    p.add_argument("--window", type=int, default=10,
                   help="Rolling window size (default 10 — matches compute_team_form_from_db).")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch-size", type=int, default=5000)
    args = p.parse_args()

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    date_to = args.date_to or yesterday

    where = ["status = 'finished'", "score_home IS NOT NULL", "score_away IS NOT NULL",
             "home_team_id IS NOT NULL", "away_team_id IS NOT NULL", "date <= %s"]
    params = [f"{date_to}T23:59:59"]
    if args.date_from:
        where.insert(0, "date >= %s")
        params.insert(0, f"{args.date_from}T00:00:00")

    sql = (
        "SELECT date, home_team_id, away_team_id, score_home, score_away "
        "FROM matches WHERE " + " AND ".join(where) +
        " ORDER BY date ASC, id ASC"
    )

    console.print("[cyan]Loading finished matches...[/cyan]")
    matches = execute_query(sql, params)
    console.print(f"  {len(matches):,} matches in scope (≤ {date_to})")
    if not matches:
        return

    # team_id → deque of recent {gf, ga}
    rolling: dict[str, deque] = {}
    rows: list[tuple] = []

    columns = [
        "team_id", "date",
        "matches_played", "win_pct", "draw_pct", "loss_pct", "ppg",
        "goals_scored_avg", "goals_conceded_avg", "goal_diff_avg",
        "clean_sheet_pct", "over25_pct", "btts_pct",
    ]

    with Progress(TextColumn("[bold blue]form walk"), BarColumn(),
                  TextColumn("{task.completed}/{task.total}"),
                  TimeRemainingColumn(), console=console) as bar:
        task = bar.add_task("walk", total=len(matches))
        for m in matches:
            day = _normalize_day(m["date"])
            h_id = m["home_team_id"]
            a_id = m["away_team_id"]
            sh = int(m["score_home"])
            sa = int(m["score_away"])

            # Snapshot "form as of just before this match" for each team
            for tid in (h_id, a_id):
                buf = rolling.setdefault(tid, deque(maxlen=args.window))
                form = _form_from_deque(buf)
                if form is None:
                    continue
                rows.append((
                    tid, day,
                    form["matches_played"], form["win_pct"], form["draw_pct"],
                    form["loss_pct"], form["ppg"],
                    form["goals_scored_avg"], form["goals_conceded_avg"],
                    form["goal_diff_avg"], form["clean_sheet_pct"],
                    form["over25_pct"], form["btts_pct"],
                ))

            # Update deques with this match's result (gf/ga from each team's POV)
            rolling[h_id].append({"gf": sh, "ga": sa})
            rolling[a_id].append({"gf": sa, "ga": sh})

            bar.advance(task)

    # Dedup: keep the LAST snapshot per (team, date) — a team can play
    # twice on one date in cup weeks, only the latest pre-match form should win.
    seen: dict[tuple, tuple] = {}
    for row in rows:
        seen[(row[0], row[1])] = row
    deduped = list(seen.values())

    console.print(
        f"\n[cyan]Computed[/cyan] {len(rows):,} form snapshots → "
        f"{len(deduped):,} unique (team, date) rows. "
        f"{len(rolling):,} distinct teams."
    )

    if args.dry_run:
        console.print("[yellow]Dry run — no writes.[/yellow]")
        if deduped:
            sample = deduped[len(deduped) // 2]
            console.print(f"  Sample row: {dict(zip(columns, sample))}")
        return

    console.print("[cyan]Bulk upserting team_form_cache...[/cyan]")
    written = 0
    for i in range(0, len(deduped), args.batch_size):
        chunk = deduped[i:i + args.batch_size]
        bulk_upsert(
            table="team_form_cache",
            columns=columns,
            rows=chunk,
            conflict_columns=["team_id", "date"],
            update_columns=[c for c in columns if c not in ("team_id", "date")],
        )
        written += len(chunk)
        console.print(f"  Wrote {written:,} / {len(deduped):,}")

    console.print(f"[bold green]✓ Form backfill complete — {written:,} rows upserted.[/bold green]")


if __name__ == "__main__":
    main()
