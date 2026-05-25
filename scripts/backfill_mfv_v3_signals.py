"""Backfill ML-NEW-FEATURES columns from match_signals → match_feature_vectors.

Five new MFV columns added by migration 132 need to be populated from the
existing match_signals rows produced by the nightly signal jobs:
  team_avg_player_rating_home / team_avg_player_rating_away
  injury_severity_score_home / injury_severity_score_away
  league_clv_efficiency

For each MFV row, we look up the LATEST match_signals row per (match_id,
signal_name) and write the value. Uses COPY + UPDATE FROM temp pattern for
speed (50× faster than per-row UPDATE over the EU pooler).

Idempotent: only touches rows where the column is NULL. Safe to re-run.

Run:
  python3 scripts/backfill_mfv_v3_signals.py             # dry run
  python3 scripts/backfill_mfv_v3_signals.py --write     # persists
  python3 scripts/backfill_mfv_v3_signals.py --write --since 2026-05-01
"""
from __future__ import annotations
import argparse
import io
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console

from workers.api_clients.db import execute_query, get_conn

console = Console()

SIGNAL_TO_COLUMN = {
    "team_avg_player_rating_home": "team_avg_player_rating_home",
    "team_avg_player_rating_away": "team_avg_player_rating_away",
    "injury_severity_score_home": "injury_severity_score_home",
    "injury_severity_score_away": "injury_severity_score_away",
    "league_clv_efficiency": "league_clv_efficiency",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-03-01")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    console.print(f"[bold]ML-NEW-FEATURES — backfilling MFV from match_signals since {args.since}[/bold]")

    # Latest signal value per (match_id, signal_name)
    console.print("Loading latest match_signals values per (match, name)...")
    rows = execute_query("""
        SELECT DISTINCT ON (ms.match_id, ms.signal_name)
               ms.match_id, ms.signal_name, ms.signal_value
        FROM match_signals ms
        JOIN match_feature_vectors mfv ON mfv.match_id = ms.match_id
        WHERE ms.signal_name = ANY(%s)
          AND ms.signal_value IS NOT NULL
          AND mfv.match_date >= %s
        ORDER BY ms.match_id, ms.signal_name, ms.captured_at DESC
    """, (list(SIGNAL_TO_COLUMN.keys()), args.since))
    console.print(f"  Loaded {len(rows):,} signal values")

    # Pivot to per-match-id
    by_match: dict = {}
    for r in rows:
        mid = str(r["match_id"])
        col = SIGNAL_TO_COLUMN[r["signal_name"]]
        by_match.setdefault(mid, {})[col] = float(r["signal_value"])

    console.print(f"  Pivoted into {len(by_match):,} unique match rows")
    cols = list(set(SIGNAL_TO_COLUMN.values()))
    cols.sort()

    if not args.write:
        sample = list(by_match.items())[:5]
        console.print("Sample (first 5 matches):")
        for mid, vals in sample:
            console.print(f"  {mid}: {vals}")
        console.print("[yellow]Dry run — pass --write to persist[/yellow]")
        return

    # COPY into temp table → UPDATE FROM temp join (fast)
    console.print(f"\n[bold]Writing {len(by_match):,} rows via COPY + UPDATE FROM...[/bold]")
    buf = io.StringIO()
    for mid, vals in by_match.items():
        row = [mid] + [str(vals.get(c)) if vals.get(c) is not None else "" for c in cols]
        buf.write("\t".join(row) + "\n")
    buf.seek(0)

    set_clause = ", ".join(
        f"{c} = COALESCE(mfv.{c}, t.{c})"
        for c in cols
    )
    col_specs = ", ".join(f"{c} NUMERIC" for c in cols)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TEMP TABLE _v3sig (
                    match_id UUID PRIMARY KEY,
                    {col_specs}
                ) ON COMMIT DROP
            """)
            cur.copy_expert(
                f"COPY _v3sig (match_id, {', '.join(cols)}) FROM STDIN WITH (FORMAT text, NULL '')",
                buf,
            )
            cur.execute(f"""
                UPDATE match_feature_vectors mfv
                SET {set_clause}
                FROM _v3sig t
                WHERE mfv.match_id = t.match_id
            """)
            n_updated = cur.rowcount
        conn.commit()
    console.print(f"[green]✓ Updated {n_updated:,} MFV rows[/green]")


if __name__ == "__main__":
    main()
