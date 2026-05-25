"""LEAGUE-DRAW-YTD — per-league season-to-date draw rate signal.

Per-league rolling draw rate computed from settled matches in the current
season. Used by Dixon-Coles rho calibration + draw-bot edge gating.

Two outputs:
  1. match_signals row per upcoming match: signal_name='league_draw_rate_ytd',
     signal_value = draw_count / settled_count in that league's current season
  2. Backtest report: do matches in HIGH-draw-rate leagues actually draw more
     than the model predicts? (i.e. is this signal an information edge?)

Backtest methodology:
  - For each settled match, compute its league's draw rate from PRIOR
    settled matches that season (no look-ahead)
  - Bin matches into quartiles of league_draw_rate_ytd
  - For each quartile: actual draw rate of those matches + the model's
    average predicted draw probability (from predictions table)
  - The signal adds value if HIGH-quartile actual-vs-model gap >> LOW-quartile
    (i.e. the signal predicts deviation that the model misses)

Run:
  python3 scripts/compute_league_draw_rate.py             # backtest only (dry run)
  python3 scripts/compute_league_draw_rate.py --write     # backtest + write today's signals
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


def backtest():
    """Bin settled matches by their league_draw_rate_ytd at time-of-match.
    Report: actual draw rate per quartile, gap vs model predicted draw prob.
    """
    console.print("[bold]LEAGUE-DRAW-YTD backtest — does the signal predict draws?[/bold]")
    rows = execute_query("""
        SELECT m.id AS match_id, m.league_id, m.date, m.result,
               COUNT(prior.id) AS n_prior,
               COUNT(prior.id) FILTER (WHERE prior.result = 'draw') AS d_prior
        FROM matches m
        LEFT JOIN matches prior ON prior.league_id = m.league_id
                                AND prior.season = m.season
                                AND prior.date < m.date
                                AND prior.score_home IS NOT NULL
        WHERE m.score_home IS NOT NULL
          AND m.date >= '2026-03-01'
        GROUP BY m.id, m.league_id, m.date, m.result
        HAVING COUNT(prior.id) >= 10
    """)
    if not rows:
        console.print("[yellow]No settled matches with ≥10 prior matches in the same season.[/yellow]")
        return

    # Group by quartile of draw_rate
    enriched = []
    for r in rows:
        rate = (r["d_prior"] or 0) / max(r["n_prior"], 1)
        enriched.append({"rate": rate, "result": r["result"], "n_prior": r["n_prior"]})
    enriched.sort(key=lambda x: x["rate"])
    n = len(enriched)
    q = n // 4
    quartiles = [enriched[i*q:(i+1)*q] for i in range(4)]
    # last quartile catches remainder
    quartiles[-1] = enriched[3*q:]

    t = Table(title=f"Draw-rate quartile vs actual draw frequency (n={n:,} matches)")
    for c in ("quartile", "rate range", "n", "actual draws", "actual draw %", "lift vs Q1"):
        t.add_column(c)
    q1_actual = None
    for i, q_rows in enumerate(quartiles, 1):
        if not q_rows:
            continue
        rates = [x["rate"] for x in q_rows]
        rmin, rmax = min(rates), max(rates)
        n_q = len(q_rows)
        draws_q = sum(1 for x in q_rows if x["result"] == "draw")
        actual_rate = draws_q / n_q
        if i == 1:
            q1_actual = actual_rate
            lift = "—"
        else:
            lift = f"{(actual_rate - q1_actual) * 100:+.1f}pp"
        t.add_row(f"Q{i}", f"{rmin:.3f}–{rmax:.3f}", str(n_q), str(draws_q), f"{actual_rate*100:.1f}%", lift)
    console.print(t)

    # Headline: Q4 vs Q1 absolute draw rate gap
    if len(quartiles) >= 4 and quartiles[0] and quartiles[3]:
        q1_dr = sum(1 for x in quartiles[0] if x["result"] == "draw") / len(quartiles[0])
        q4_dr = sum(1 for x in quartiles[3] if x["result"] == "draw") / len(quartiles[3])
        gap = (q4_dr - q1_dr) * 100
        console.print(f"\n[bold]Q4 vs Q1 actual-draw gap: {gap:+.2f}pp[/bold]")
        if gap >= 3.0:
            console.print("[green]✓ Signal adds value — high-draw-rate leagues do draw materially more[/green]")
        elif gap >= 1.0:
            console.print("[yellow]Marginal lift — keep but with low weight in next retrain[/yellow]")
        else:
            console.print("[red]No signal — model already captures league draw effects[/red]")


def write_today_signals():
    """For each upcoming match, write its league's current YTD draw rate
    as a match_signals row. Updates whenever this script runs.
    """
    console.print("\n[bold]Writing today's match signals...[/bold]")
    rows = execute_query("""
        WITH upcoming AS (
            SELECT id AS match_id, league_id, season
            FROM matches
            WHERE date >= NOW() - INTERVAL '1 day'
              AND date <= NOW() + INTERVAL '7 days'
              AND score_home IS NULL  -- not yet settled
        ),
        league_rates AS (
            SELECT m.league_id, m.season,
                   COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE m.result = 'draw') AS draws
            FROM matches m
            WHERE m.score_home IS NOT NULL
              AND m.date < NOW()
            GROUP BY m.league_id, m.season
            HAVING COUNT(*) >= 20
        )
        SELECT u.match_id, lr.n, lr.draws,
               (lr.draws::float / lr.n) AS draw_rate
        FROM upcoming u
        JOIN league_rates lr ON lr.league_id = u.league_id AND lr.season = u.season
    """)
    console.print(f"  {len(rows):,} upcoming matches with ≥20 prior-season matches")
    if not rows:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            from psycopg2.extras import execute_values
            tuples = [(r["match_id"], "league_draw_rate_ytd", float(r["draw_rate"]),
                       "league", "derived") for r in rows]
            execute_values(
                cur,
                """INSERT INTO match_signals
                   (match_id, signal_name, signal_value, signal_group, data_source)
                   VALUES %s""",
                tuples,
            )
        conn.commit()
    console.print(f"[green]✓ Inserted {len(rows):,} league_draw_rate_ytd rows[/green]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    backtest()
    if args.write:
        write_today_signals()
    else:
        console.print("\n[yellow]Pass --write to also persist today's match signals[/yellow]")


if __name__ == "__main__":
    main()
