"""SIG-12 — xG overperformance rolling signal (regression-to-mean indicator).

For each settled match's final live snapshot (closest to minute 90):
  per_match_overperf_home = score_home - xg_home
  per_match_overperf_away = score_away - xg_away

Then rolling 10-match mean per team at each match's date:
  xg_overperf_home/away = mean over team's last 10 matches

Positive value = team scores MORE than xG predicts → expect downward
regression (next bet against them is +EV by ~50% of overperf delta).
Negative = team scores LESS than xG predicts → expect upward regression.

Stored in match_signals as:
  signal_name = 'xg_overperf_home' | 'xg_overperf_away'
  signal_value = float (typical range -1.0 .. +1.0)
  signal_group = 'team', data_source = 'derived'

Run:
  python3 scripts/compute_xg_overperformance.py            # dry run
  python3 scripts/compute_xg_overperformance.py --write    # persist
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, get_conn

console = Console()
WINDOW = 10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    console.print("[bold]SIG-12 — xG overperformance rolling signal[/bold]")
    console.print("Loading per-(match, side) final xG vs goals...")

    # For each match, pick the latest live snapshot with xg_home not null
    # AND minute closest to full-time. DISTINCT ON (match_id) ORDER BY minute DESC.
    rows = execute_query("""
        WITH latest_xg AS (
            SELECT DISTINCT ON (match_id)
                match_id, xg_home, xg_away, minute, captured_at
            FROM live_match_snapshots
            WHERE xg_home IS NOT NULL
              AND minute >= 80
            ORDER BY match_id, minute DESC, captured_at DESC
        )
        SELECT
            m.id AS match_id, m.date AS match_date,
            m.home_team_id, m.away_team_id,
            m.score_home, m.score_away,
            lxg.xg_home, lxg.xg_away
        FROM matches m
        JOIN latest_xg lxg ON lxg.match_id = m.id
        WHERE m.score_home IS NOT NULL AND m.score_away IS NOT NULL
        ORDER BY m.date ASC
    """)
    console.print(f"  Loaded {len(rows):,} settled matches with late-game xG")

    # Per-team chronological list of (match_date, match_id, overperf)
    team_history: dict = defaultdict(list)
    for r in rows:
        op_h = float(r["score_home"]) - float(r["xg_home"])
        op_a = float(r["score_away"]) - float(r["xg_away"])
        team_history[r["home_team_id"]].append((r["match_date"], r["match_id"], op_h))
        team_history[r["away_team_id"]].append((r["match_date"], r["match_id"], op_a))

    # Rolling 10-match mean per (team, match)
    rolling: dict = {}  # (team_id, match_id) → rolling mean
    for tid, hist in team_history.items():
        hist.sort(key=lambda r: r[0])
        for i, (mdate, mid, _op) in enumerate(hist):
            prior = hist[max(0, i - WINDOW):i]  # exclude current match
            if len(prior) < 3:  # require ≥3 prior matches for signal
                continue
            vals = [p[2] for p in prior]
            rolling[(tid, mid)] = sum(vals) / len(vals)
    console.print(f"  Computed {len(rolling):,} (team, match) rolling overperf entries")

    # Build write_rows: per match, look up home + away rolling overperf
    write_rows = []
    for r in rows:
        mid = r["match_id"]
        h_op = rolling.get((r["home_team_id"], mid))
        a_op = rolling.get((r["away_team_id"], mid))
        if h_op is not None:
            write_rows.append((mid, "xg_overperf_home", round(h_op, 4), "team", "derived"))
        if a_op is not None:
            write_rows.append((mid, "xg_overperf_away", round(a_op, 4), "team", "derived"))

    # Distribution stats
    h_vals = [v for (_, sn, v, _, _) in write_rows if sn == "xg_overperf_home"]
    if h_vals:
        t = Table(title="xg_overperf_home distribution")
        t.add_column("metric")
        t.add_column("value")
        h_vals.sort()
        t.add_row("n", str(len(h_vals)))
        t.add_row("mean", f"{sum(h_vals)/len(h_vals):+.4f}")
        t.add_row("median", f"{h_vals[len(h_vals)//2]:+.4f}")
        t.add_row("min", f"{h_vals[0]:+.4f}")
        t.add_row("max", f"{h_vals[-1]:+.4f}")
        console.print(t)

    console.print(f"\nWould write {len(write_rows):,} match_signals rows")
    if not args.write:
        console.print("[yellow]Dry run — pass --write to persist[/yellow]")
        return

    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values
            for cs in range(0, len(write_rows), 1000):
                chunk = write_rows[cs: cs + 1000]
                execute_values(
                    cur,
                    """INSERT INTO match_signals
                       (match_id, signal_name, signal_value, signal_group, data_source)
                       VALUES %s""",
                    chunk,
                )
                inserted += len(chunk)
        conn.commit()
    console.print(f"[green]✓ Inserted {inserted:,} match_signals rows[/green]")


if __name__ == "__main__":
    main()
