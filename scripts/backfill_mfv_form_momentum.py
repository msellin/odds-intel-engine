"""Backfill MFV.form_momentum_home and .form_momentum_away (MFV-FORM-MOMENTUM-BUG fix).

Discovered 2026-05-24 by META-FEATURE-DESIGN coverage audit: both columns
exist in match_feature_vectors but `_build_feature_row_batched` in
supabase_client.py never writes them — 100% NULL across 53K rows.

This script computes momentum = (last-3-game ppg) − (last-10-game ppg) per
team at each match's date, by querying the `matches` table directly.
Positive momentum = team's recent form is better than longer-term average
(getting hotter); negative = cooling down.

Idempotent: only touches rows where form_momentum_home OR form_momentum_away
is NULL. Backfill is one-shot — once the live MFV builder is patched
(separate task), new rows will be populated at MFV build time.

Run: python3 scripts/backfill_mfv_form_momentum.py
     python3 scripts/backfill_mfv_form_momentum.py --since 2026-05-06
"""
from __future__ import annotations
import sys, argparse
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.progress import Progress

from workers.api_clients.db import execute_query, get_conn

console = Console()


def _ppg_from_results(team_id, results: list[dict]) -> float:
    """Compute points-per-game given a list of {score_home, score_away, home_team_id, away_team_id}."""
    if not results:
        return 0.0
    points = 0
    for r in results:
        sh, sa = r["score_home"], r["score_away"]
        if sh is None or sa is None:
            continue
        if r["home_team_id"] == team_id:
            if sh > sa: points += 3
            elif sh == sa: points += 1
        else:
            if sa > sh: points += 3
            elif sh == sa: points += 1
    return points / max(len(results), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-06")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Get all MFV rows needing backfill, with home + away team IDs and match date
    console.print(f"[bold]Loading MFV rows needing form_momentum since {args.since}...[/bold]")
    rows = execute_query("""
        SELECT mfv.match_id, m.home_team_id, m.away_team_id, m.date AS match_date
        FROM match_feature_vectors mfv
        JOIN matches m ON m.id = mfv.match_id
        WHERE mfv.match_date >= %s
          AND (mfv.form_momentum_home IS NULL OR mfv.form_momentum_away IS NULL)
          AND m.home_team_id IS NOT NULL AND m.away_team_id IS NOT NULL
        ORDER BY m.date ASC
    """, (args.since,))
    console.print(f"  {len(rows):,} rows to backfill")
    if not rows:
        return

    # Collect unique team_ids
    all_team_ids = set()
    for r in rows:
        all_team_ids.add(r["home_team_id"])
        all_team_ids.add(r["away_team_id"])
    console.print(f"  {len(all_team_ids):,} unique teams")

    # For each team, bulk-fetch last 30 settled matches (date ASC). We'll slice
    # the relevant window per MFV row in Python.
    console.print("[bold]Loading per-team match history (last 30 settled, ASC)...[/bold]")
    team_history: dict = defaultdict(list)
    # Get latest match_date in window — fetch any settled matches before then
    latest_date = max(r["match_date"] for r in rows)
    earliest_date = min(r["match_date"] for r in rows)
    # Cast list to chunks of 200 to avoid huge IN clauses
    team_ids_list = list(all_team_ids)
    for i in range(0, len(team_ids_list), 200):
        chunk = team_ids_list[i:i + 200]
        history = execute_query("""
            SELECT id, home_team_id, away_team_id, date, score_home, score_away
            FROM matches
            WHERE status = 'finished'
              AND score_home IS NOT NULL AND score_away IS NOT NULL
              AND date <= %s
              AND (home_team_id = ANY(%s::uuid[]) OR away_team_id = ANY(%s::uuid[]))
            ORDER BY date ASC
        """, (latest_date, chunk, chunk))
        for h in history:
            # A match contributes to BOTH teams' histories.
            for tid in (h["home_team_id"], h["away_team_id"]):
                if tid in all_team_ids:
                    team_history[tid].append(h)

    console.print(f"  loaded {sum(len(v) for v in team_history.values()):,} team-match rows")

    # Compute form_momentum for each MFV row
    console.print("[bold]Computing form_momentum + writing rows...[/bold]")
    updates = []
    with Progress(console=console) as progress:
        task = progress.add_task("Computing", total=len(rows))
        for r in rows:
            mid = r["match_id"]
            md = r["match_date"]
            ftm_home = None
            ftm_away = None
            for tid_key, out_key in [(r["home_team_id"], "form_momentum_home"),
                                      (r["away_team_id"], "form_momentum_away")]:
                # Filter to matches strictly BEFORE this match's date
                hist = [h for h in team_history.get(tid_key, []) if h["date"] < md]
                if len(hist) < 3:
                    val = None
                else:
                    last_3 = hist[-3:]
                    last_10 = hist[-10:] if len(hist) >= 10 else hist
                    ppg_3 = _ppg_from_results(tid_key, last_3)
                    ppg_n = _ppg_from_results(tid_key, last_10)
                    val = round(ppg_3 - ppg_n, 4)
                if out_key == "form_momentum_home":
                    ftm_home = val
                else:
                    ftm_away = val
            if ftm_home is not None or ftm_away is not None:
                updates.append((mid, ftm_home, ftm_away))
            progress.update(task, advance=1)

    console.print(f"  computed {len(updates):,} rows")

    if args.dry_run:
        console.print("[yellow]--dry-run: not updating[/yellow]")
        return

    # Bulk UPDATE via temp table — per-row UPDATEs over the EU pooler are slow
    # (50-100ms each → 10+ min for 7K rows). COPY-into-temp + UPDATE FROM = ~5s.
    console.print("[bold]Writing updates to DB (batched via temp table)...[/bold]")
    rows_to_copy = [(str(mid), fh, fa) for mid, fh, fa in updates]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TEMP TABLE _bf_fm_chunk (
                    match_id UUID PRIMARY KEY,
                    form_momentum_home FLOAT,
                    form_momentum_away FLOAT
                ) ON COMMIT DROP
            """)
            import psycopg2.extras as _pgext
            _pgext.execute_values(
                cur,
                "INSERT INTO _bf_fm_chunk (match_id, form_momentum_home, form_momentum_away) VALUES %s",
                rows_to_copy,
                page_size=500,
            )
            # COALESCE so we don't overwrite existing values with NULLs (idempotent)
            cur.execute("""
                UPDATE match_feature_vectors AS mfv
                SET form_momentum_home = COALESCE(t.form_momentum_home, mfv.form_momentum_home),
                    form_momentum_away = COALESCE(t.form_momentum_away, mfv.form_momentum_away)
                FROM _bf_fm_chunk AS t
                WHERE mfv.match_id = t.match_id
            """)
        conn.commit()
    console.print(f"[green]✓ Updated {len(updates):,} MFV rows[/green]")


if __name__ == "__main__":
    main()
