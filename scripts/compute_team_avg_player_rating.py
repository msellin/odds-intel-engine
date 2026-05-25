"""AF-PLAYER-RATINGS — rolling team rating from API-Football player ratings.

For every settled match, compute:
  team_avg_player_rating_home = mean of (avg player rating per match) over
                                the home team's last 10 played matches
  team_avg_player_rating_away = same for the away team

Per-match team rating = mean of ratings where rating IS NOT NULL AND
minutes_played >= 60 (filters benchwarmers / substitutes).

Stores results in match_signals as:
  signal_name = 'team_avg_player_rating_home' | 'team_avg_player_rating_away'
  signal_value = float (range ~6.0 — 8.0)
  signal_type = 'team' | data_source = 'derived'

This feature lands in match_signals (not MFV directly) so it can be
loaded the same way as league_clv_efficiency. The next MFV rebuild can
pivot it into a column for B-ML3 v3+ training.

Run:
  python3 scripts/compute_team_avg_player_rating.py           # dry run
  python3 scripts/compute_team_avg_player_rating.py --write   # persists

Scheduled nightly via workers/scheduler.py:job_team_avg_rating (22:50 UTC),
between the existing MFV-refresh jobs.
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
WINDOW = 10  # last 10 matches per team


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="Actually insert into match_signals")
    ap.add_argument("--limit", type=int, default=None, help="Only process N matches (smoke)")
    args = ap.parse_args()

    console.print("[bold]AF-PLAYER-RATINGS — rolling team rating[/bold]")
    console.print("Loading per-(match, team_side) rating averages...")
    # Group by (match_id, team_api_id, team_side): mean of rating where
    # minutes_played >= 60. team_api_id is the stable team identifier (our
    # teams table has no api_id column, so we work directly with AF's id).
    team_match_rows = execute_query("""
        SELECT mps.match_id, mps.team_api_id, mps.team_side,
               AVG(mps.rating) AS team_rating,
               m.date AS match_date
        FROM match_player_stats mps
        JOIN matches m ON m.id = mps.match_id
        WHERE mps.rating IS NOT NULL
          AND mps.minutes_played >= 60
        GROUP BY mps.match_id, mps.team_api_id, mps.team_side, m.date
        HAVING COUNT(*) FILTER (WHERE mps.rating IS NOT NULL) >= 5
        ORDER BY mps.team_api_id, m.date ASC
    """)
    console.print(f"  Loaded {len(team_match_rows):,} (team, match) ratings")

    # Build per-team chronological list of (match_date, match_id, rating)
    team_history: dict[int, list[tuple]] = defaultdict(list)
    for r in team_match_rows:
        team_history[r["team_api_id"]].append((r["match_date"], r["match_id"], float(r["team_rating"])))

    # For each (team, match) compute mean of the prior WINDOW ratings
    rolling: dict[tuple, float] = {}  # (team_api_id, match_id) → rolling avg
    for team_id, history in team_history.items():
        for i, (mdate, mid, _r) in enumerate(history):
            prior = history[max(0, i - WINDOW):i]  # exclusive of current match
            if not prior:
                continue
            vals = [p[2] for p in prior]
            rolling[(team_id, mid)] = sum(vals) / len(vals)

    console.print(f"  Computed {len(rolling):,} rolling team-rating entries")

    # Use the team_side column from match_player_stats to label home/away
    # per match — no need to join through teams.api_id.
    side_map: dict[tuple, str] = {}  # (match_id, team_api_id) → 'home' | 'away'
    for r in team_match_rows:
        side_map[(r["match_id"], r["team_api_id"])] = r["team_side"]

    write_rows: list[tuple] = []
    for (team_id, mid), rating in rolling.items():
        side = side_map.get((mid, team_id))
        if side not in ("home", "away"):
            continue
        signal_name = f"team_avg_player_rating_{side}"
        # match_signals columns: (match_id, signal_name, signal_value, signal_group, data_source)
        write_rows.append((mid, signal_name, rating, "team", "derived"))

    if args.limit:
        write_rows = write_rows[: args.limit * 2]

    # Stats
    h_vals = [v for (_, sn, v, _, _) in write_rows if sn == "team_avg_player_rating_home"]
    a_vals = [v for (_, sn, v, _, _) in write_rows if sn == "team_avg_player_rating_away"]
    if h_vals:
        t = Table(title="Rolling team rating distribution (home side)")
        t.add_column("metric")
        t.add_column("value")
        t.add_row("matches", str(len(h_vals)))
        t.add_row("mean", f"{sum(h_vals)/len(h_vals):.3f}")
        t.add_row("min", f"{min(h_vals):.3f}")
        t.add_row("max", f"{max(h_vals):.3f}")
        console.print(t)

    if not args.write:
        console.print("\n[yellow]Dry run — pass --write to insert into match_signals[/yellow]")
        return

    console.print(f"\n[bold]Inserting {len(write_rows):,} rows into match_signals...[/bold]")
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values
            for chunk_start in range(0, len(write_rows), 1000):
                chunk = write_rows[chunk_start: chunk_start + 1000]
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
