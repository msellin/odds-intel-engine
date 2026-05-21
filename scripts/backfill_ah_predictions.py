"""
Backfill Asian Handicap predictions for historical matches.

For each finished match that has Poisson 1x2 predictions but no AH predictions,
this script:
  1. Reads stored Poisson home/draw/away probabilities
  2. Deduplicates unique (p_home, p_draw) pairs and solves them in parallel
  3. Computes AH probabilities for lines: -1.5, -1.0, -0.5, 0.0, +0.5, +1.0, +1.5
  4. Stores with source='poisson', market='ah_{home|away}_{line:.2f}'

Market format examples: ah_home_-0.50, ah_away_0.00, ah_home_1.50

Usage:
    python3 scripts/backfill_ah_predictions.py
    python3 scripts/backfill_ah_predictions.py --dry-run
    python3 scripts/backfill_ah_predictions.py --limit 500      # test with 500 matches
    python3 scripts/backfill_ah_predictions.py --from-date 2023-01-01
    python3 scripts/backfill_ah_predictions.py --workers 8      # parallel workers (default: cpu_count)
"""

from __future__ import annotations

import argparse
import math
import multiprocessing
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

console = Console()

DIXON_COLES_RHO = -0.13
AH_LINES = (-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5)

FETCH_SQL = """
SELECT
    p.match_id,
    p.market,
    p.model_probability::float AS model_prob
FROM predictions p
WHERE p.source = 'poisson'
  AND p.market IN ('1x2_home', '1x2_draw', '1x2_away')
  AND p.match_id IN (
      SELECT m.id FROM matches m
      WHERE m.status = 'finished'
        AND m.score_home IS NOT NULL
        {date_filter}
  )
  AND NOT EXISTS (
      SELECT 1 FROM predictions q
      WHERE q.match_id = p.match_id
        AND q.source = 'poisson'
        AND q.market LIKE 'ah_%'
  )
"""


def _dc_tau(h: int, a: int, exp_h: float, exp_a: float, rho: float) -> float:
    if h == 0 and a == 0:
        return 1.0 - exp_h * exp_a * rho
    if h == 1 and a == 0:
        return 1.0 + exp_a * rho
    if h == 0 and a == 1:
        return 1.0 + exp_h * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def _poisson_probs(exp_h: float, exp_a: float, rho: float = DIXON_COLES_RHO) -> dict:
    from scipy.stats import poisson
    p_h = p_d = p_a = 0.0
    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_h) * poisson.pmf(a, exp_a)
            p *= _dc_tau(h, a, exp_h, exp_a, rho)
            if h > a:
                p_h += p
            elif h == a:
                p_d += p
            else:
                p_a += p
    total = p_h + p_d + p_a
    if total > 0:
        p_h /= total; p_d /= total; p_a /= total
    p_d_inf = p_d * 1.08
    scale = (1.0 - p_d_inf) / (p_h + p_a) if (p_h + p_a) > 0 else 1.0
    return {"home_prob": p_h * scale, "draw_prob": p_d_inf, "away_prob": p_a * scale}


def _ah_model_prob(exp_h: float, exp_a: float, selection: str, handicap_line: float,
                   rho: float = DIXON_COLES_RHO) -> float:
    from scipy.stats import poisson
    margin_pmf: dict[int, float] = {}
    for h in range(8):
        for a in range(8):
            p = poisson.pmf(h, exp_h) * poisson.pmf(a, exp_a) * _dc_tau(h, a, exp_h, exp_a, rho)
            m = h - a
            margin_pmf[m] = margin_pmf.get(m, 0.0) + p

    spread = -handicap_line
    floor_s = math.floor(spread)
    frac = spread - floor_s

    if frac < 0.01:
        s = round(spread)
        p_win = sum(p for m, p in margin_pmf.items() if m > s)
        p_lose = sum(p for m, p in margin_pmf.items() if m < s)
        total = p_win + p_lose
        home_prob = p_win / total if total > 0 else 0.5
    elif abs(frac - 0.5) < 0.01:
        p_win = sum(p for m, p in margin_pmf.items() if m > spread)
        p_lose = sum(p for m, p in margin_pmf.items() if m < spread)
        total = p_win + p_lose
        home_prob = p_win / total if total > 0 else 0.5
    elif frac < 0.5:
        p_full_win = sum(p for m, p in margin_pmf.items() if m >= floor_s + 1)
        p_half_loss = margin_pmf.get(floor_s, 0.0)
        p_full_lose = sum(p for m, p in margin_pmf.items() if m <= floor_s - 1)
        denom = p_full_win + 0.5 * p_half_loss + p_full_lose
        home_prob = p_full_win / denom if denom > 0 else 0.5
    else:
        p_full_win = sum(p for m, p in margin_pmf.items() if m >= floor_s + 2)
        p_half_win = margin_pmf.get(floor_s + 1, 0.0)
        p_full_lose = sum(p for m, p in margin_pmf.items() if m <= floor_s)
        numerator = p_full_win + 0.5 * p_half_win
        denom = numerator + p_full_lose
        home_prob = numerator / denom if denom > 0 else 0.5

    return 1.0 - home_prob if selection == "away" else home_prob


def _initial_guess(p_home: float, p_draw: float) -> list[float]:
    """Rough analytical starting point to cut optimizer iterations in half."""
    p_away = max(0.01, 1.0 - p_home - p_draw)
    ratio = p_home / p_away
    # Higher draws → lower-scoring game → lower total goals
    exp_total = max(0.8, 2.8 - 3.0 * max(0.0, p_draw - 0.25))
    exp_h = exp_total * (ratio ** 0.55) / (1.0 + ratio ** 0.55)
    exp_a = exp_total - exp_h
    return [max(0.2, exp_h), max(0.2, exp_a)]


def solve_lambdas(p_home: float, p_draw: float) -> tuple[float, float]:
    """Numerically invert Poisson 1x2 probs to recover (exp_h, exp_a)."""
    from scipy.optimize import minimize

    def loss(x: list[float]) -> float:
        eh, ea = max(0.15, x[0]), max(0.15, x[1])
        r = _poisson_probs(eh, ea)
        return (r["home_prob"] - p_home) ** 2 + (r["draw_prob"] - p_draw) ** 2

    x0 = _initial_guess(p_home, p_draw)
    result = minimize(loss, x0, method="Powell",
                      options={"xtol": 0.002, "ftol": 1e-5, "maxiter": 200})
    return max(0.15, result.x[0]), max(0.15, result.x[1])


def _solve_worker(args: tuple[float, float]) -> tuple[tuple[float, float], tuple[float, float] | None]:
    """Worker function for multiprocessing: returns (key, result) or (key, None) on failure."""
    ph, pd = args
    try:
        return (ph, pd), solve_lambdas(ph, pd)
    except Exception:
        return (ph, pd), None


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill AH predictions for historical matches")
    ap.add_argument("--dry-run", action="store_true", help="Compute but don't write to DB")
    ap.add_argument("--limit", type=int, default=None, metavar="N",
                    help="Process at most N matches (for testing)")
    ap.add_argument("--from-date", default=None, metavar="YYYY-MM-DD",
                    help="Only process matches on or after this date")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 4), metavar="N",
                    help="Parallel workers for lambda solving (default: cpu_count)")
    args = ap.parse_args()

    date_filter = f"AND m.date >= '{args.from_date}'" if args.from_date else ""
    sql = FETCH_SQL.format(date_filter=date_filter)

    console.print("\n[bold]AH Predictions Backfill[/bold]")
    if args.dry_run:
        console.print("[yellow]  DRY RUN — no writes[/yellow]")

    from workers.api_clients.db import execute_query
    console.print("[cyan]Fetching matches with Poisson 1x2 predictions...[/cyan]")
    rows = execute_query(sql)
    if not rows:
        console.print("[yellow]No matches to backfill.[/yellow]")
        return

    # Group by match_id: collect home_prob, draw_prob, away_prob
    from collections import defaultdict
    by_match: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        by_match[row["match_id"]][row["market"]] = row["model_prob"]

    # Keep only matches that have all three 1x2 probs
    complete = {
        mid: probs for mid, probs in by_match.items()
        if all(k in probs for k in ("1x2_home", "1x2_draw", "1x2_away"))
    }

    if args.limit:
        complete = dict(list(complete.items())[: args.limit])

    console.print(f"[green]Matches to process: {len(complete):,}[/green]")
    console.print(f"[dim]Lines per match: {len(AH_LINES)} × 2 sides = {len(AH_LINES) * 2} predictions[/dim]")

    # Deduplicate: round to 3dp — many matches share identical model outputs
    unique_pairs: list[tuple[float, float]] = list({
        (round(p["1x2_home"], 3), round(p["1x2_draw"], 3))
        for p in complete.values()
    })
    console.print(f"[dim]Unique (p_home, p_draw) pairs: {len(unique_pairs):,} "
                  f"(dedup ratio: {len(unique_pairs)/len(complete)*100:.0f}%) "
                  f"| workers={args.workers}[/dim]")

    # Solve unique pairs in parallel
    lambda_cache: dict[tuple[float, float], tuple[float, float]] = {}
    n_failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=36),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        console=console,
    ) as bar:
        solve_task = bar.add_task("solving lambdas", total=len(unique_pairs))

        with multiprocessing.Pool(processes=args.workers) as pool:
            for key, result in pool.imap_unordered(_solve_worker, unique_pairs, chunksize=32):
                bar.advance(solve_task)
                if result is not None:
                    lambda_cache[key] = result
                else:
                    n_failed += 1

    console.print(f"[dim]Solved {len(lambda_cache):,} / {len(unique_pairs):,} pairs[/dim]")

    # Generate prediction rows using cached lambdas
    pred_rows: list[dict] = []
    for match_id, probs in complete.items():
        key = (round(probs["1x2_home"], 3), round(probs["1x2_draw"], 3))
        cached = lambda_cache.get(key)
        if cached is None:
            n_failed += 1
            continue
        exp_h, exp_a = cached
        for ah_line in AH_LINES:
            for sel in ("home", "away"):
                try:
                    ah_prob = _ah_model_prob(exp_h, exp_a, sel, ah_line)
                    pred_rows.append({
                        "match_id": match_id,
                        "market": f"ah_{sel}_{ah_line:.2f}",
                        "source": "poisson",
                        "model_prob": float(ah_prob),
                        "implied_prob": None,
                        "edge": None,
                        "reasoning": "backfill_ah",
                    })
                except Exception:
                    n_failed += 1

    console.print(f"\n[green]Generated {len(pred_rows):,} AH prediction rows[/green]")
    if n_failed:
        console.print(f"[yellow]  Skipped / failed: {n_failed}[/yellow]")

    if args.dry_run or not pred_rows:
        console.print("[yellow]Dry run — no writes.[/yellow]")
        if pred_rows:
            console.print(f"[dim]Sample row: {pred_rows[0]}[/dim]")
        return

    # Bulk write in batches of 2000
    from workers.api_clients.supabase_client import bulk_store_predictions
    batch_size = 2000
    total_written = 0
    for i in range(0, len(pred_rows), batch_size):
        batch = pred_rows[i: i + batch_size]
        try:
            n = bulk_store_predictions(batch)
            total_written += n
        except Exception as e:
            console.print(f"[red]Batch {i // batch_size + 1} failed: {e}[/red]")

    console.print(f"[bold green]✓ Wrote {total_written:,} AH predictions to DB[/bold green]")


if __name__ == "__main__":
    main()
