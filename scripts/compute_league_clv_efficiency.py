"""LEAGUE-CLV-EFFICIENCY (2026-05-25).

Compute per-league CLV efficiency from `matches.pseudo_clv_home/draw/away`.
For each league, the score is the average pseudo_clv across all 3 selections
on matches finished in the rolling window. Higher = the league's closing line
is structurally beatable by our model; lower (negative) = the line is sharp.

Output:
  * Stored in `match_signals` table as signal_name='league_clv_efficiency'
    per match (each match in the league inherits the league-level value).
    This makes it directly consumable by future B-ML3 training as a feature.
  * CSV dump to dev/active/league_clv_efficiency_<YYYYMMDD>.csv for human
    inspection (sorted descending).
  * Console summary of top-10 / bottom-10 leagues.

The Scotland-League-2 discovery (high beatability) and Scottish Premiership
gate (structurally sharp, -48% ROI) both came from manual per-league CLV
inspection. This script formalises that intuition into a queryable signal.

Run weekly via scheduler (LEAGUE-CLV-EFFICIENCY-CRON, follow-up).

Usage:
    python3 scripts/compute_league_clv_efficiency.py [--days 60] [--write]
        --days N   rolling window (default 60)
        --write    actually write match_signals rows (default dry-run)
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, get_conn

console = Console()

MIN_MATCHES_FOR_SIGNAL = 20  # don't compute below this — too noisy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="Rolling window in days")
    ap.add_argument("--write", action="store_true", help="Persist to match_signals (default: dry-run)")
    args = ap.parse_args()

    console.print(f"[bold]Computing league CLV efficiency over last {args.days} days[/bold]")

    rows = execute_query("""
        SELECT m.league_id, CONCAT_WS('/', l.country, l.name) AS league_path, l.tier,
               m.id AS match_id,
               m.pseudo_clv_home, m.pseudo_clv_draw, m.pseudo_clv_away
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE m.status = 'finished'
          AND m.date >= NOW() - (%s || ' days')::interval
          AND (m.pseudo_clv_home IS NOT NULL
            OR m.pseudo_clv_draw IS NOT NULL
            OR m.pseudo_clv_away IS NOT NULL)
    """, (args.days,))
    console.print(f"  Loaded {len(rows):,} matches with CLV data")

    # Aggregate per league
    by_league: dict = defaultdict(lambda: {"path": None, "tier": None,
                                            "match_ids": [], "clv_sum": 0.0,
                                            "clv_count": 0})
    for r in rows:
        league_id = r["league_id"]
        entry = by_league[league_id]
        entry["path"] = r["league_path"]
        entry["tier"] = r["tier"]
        entry["match_ids"].append(r["match_id"])
        for sel in ("home", "draw", "away"):
            v = r[f"pseudo_clv_{sel}"]
            if v is not None:
                entry["clv_sum"] += float(v)
                entry["clv_count"] += 1

    # Compute mean CLV per league, filter by sample size
    aggregated = []
    for league_id, e in by_league.items():
        n_matches = len(e["match_ids"])
        n_clv_obs = e["clv_count"]
        if n_matches < MIN_MATCHES_FOR_SIGNAL or n_clv_obs == 0:
            continue
        mean_clv = e["clv_sum"] / n_clv_obs
        aggregated.append({
            "league_id": league_id,
            "path": e["path"],
            "tier": e["tier"],
            "n_matches": n_matches,
            "mean_clv": mean_clv,
            "match_ids": e["match_ids"],
        })
    aggregated.sort(key=lambda x: x["mean_clv"], reverse=True)
    console.print(f"  {len(aggregated):,} leagues clear the n>={MIN_MATCHES_FOR_SIGNAL} floor")

    # Top 10 / bottom 10
    for label, sample in [("Top 10 (most beatable)", aggregated[:10]),
                           ("Bottom 10 (sharpest)", aggregated[-10:][::-1])]:
        t = Table(title=label)
        for col in ("league_path", "tier", "n", "mean_clv"):
            t.add_column(col)
        for row in sample:
            t.add_row(
                str(row["path"] or "?")[:48],
                str(row["tier"] or ""),
                str(row["n_matches"]),
                f"{row['mean_clv']:+.4f}",
            )
        console.print(t)

    # CSV dump
    ts = datetime.now().strftime("%Y%m%d")
    csv_path = Path(__file__).resolve().parent.parent / "dev" / "active" / f"league_clv_efficiency_{ts}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w") as f:
        f.write("league_path,tier,n_matches,mean_clv\n")
        for r in aggregated:
            path = (r["path"] or "").replace(",", " ")
            f.write(f"{path},{r['tier'] or ''},{r['n_matches']},{r['mean_clv']:.6f}\n")
    console.print(f"  CSV → {csv_path}")

    if not args.write:
        console.print("[yellow]--write not set; not persisting to match_signals[/yellow]")
        return

    # Persist: one match_signals row per match per league, signal_name='league_clv_efficiency'
    console.print("\n[bold]Persisting league_clv_efficiency to match_signals...[/bold]")
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for row in aggregated:
                # Use execute_values for the per-match writes within a league
                from psycopg2.extras import execute_values
                tuples = [
                    (mid, "league_clv_efficiency", float(row["mean_clv"]),
                     "market", "derived")
                    for mid in row["match_ids"]
                ]
                # match_signals has no UNIQUE on (match_id, signal_name); each
                # write produces a new row. Readers use captured_at DESC so
                # the latest run wins. Idempotency: re-running today inserts
                # duplicate-for-today rows — acceptable; the table is meant
                # to be append-only history. Periodic cleanup if needed.
                execute_values(
                    cur,
                    """INSERT INTO match_signals
                         (match_id, signal_name, signal_value, signal_group, data_source)
                       VALUES %s""",
                    tuples,
                    page_size=500,
                )
                inserted += len(tuples)
        conn.commit()
    console.print(f"[green]✓ {inserted:,} match_signals rows written[/green]")


if __name__ == "__main__":
    main()
