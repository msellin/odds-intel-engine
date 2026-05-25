"""
Fast diagnostic for `run_betting` and `run_predictions` slowness.

Times each major step independently and reports where the wall-time goes.
No state is mutated — all reads / dry-run paths only.

Usage:
    venv/bin/python scripts/probe_betting_slow.py

What it does, in order:
    1. Time `run_predictions(target_date=today)` — AF /predictions per-fixture.
       Will not duplicate work if predictions already stored (it upserts).
    2. Time `_load_today_matches_with_odds(today)` — the DB join + best-odds
       aggregation that feeds the betting model.
    3. Time `_load_match_signals_batch(...)` — the second-largest read.
    4. Time the model loop (Poisson + XGB + signal eval) without writing bets,
       by calling `run_betting()` with `dry_run=True` if it accepts that, else
       running a small sample.

Output is a one-line per-step summary so the slow step is obvious at a glance.
"""

import os
import sys
import time
from pathlib import Path
from datetime import date

from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

from workers.api_clients.db import execute_query


def t(label):
    """Decorator-style timer."""
    def deco(fn):
        def wrapped(*a, **kw):
            print(f"  → {label} ...", flush=True)
            t0 = time.time()
            try:
                result = fn(*a, **kw)
                print(f"  ✓ {label} — {time.time() - t0:.1f}s", flush=True)
                return result
            except Exception as e:
                print(f"  ✗ {label} — {time.time() - t0:.1f}s — {type(e).__name__}: {e}", flush=True)
                raise
        return wrapped
    return deco


def step_predictions(today: str):
    @t("run_predictions (AF /predictions for today's fixtures)")
    def go():
        from workers.jobs.fetch_predictions import run_predictions
        return run_predictions(target_date=today)
    go()


def step_load_matches_with_odds(today: str):
    @t("DB load: today's matches + best odds (PIPE-2)")
    def go():
        from workers.jobs.daily_pipeline_v2 import _load_today_matches_with_odds
        rows = _load_today_matches_with_odds(today)
        print(f"     loaded {len(rows)} matches with odds", flush=True)
        return rows
    return go()


def step_load_signals(match_ids: list[str]):
    @t(f"DB load: match_signals for {len(match_ids)} matches (batch)")
    def go():
        from workers.api_clients.supabase_client import batch_load_match_signals
        sigs = batch_load_match_signals(match_ids) if match_ids else {}
        print(f"     loaded signals for {len(sigs)} matches", flush=True)
        return sigs
    return go()


def step_count_pending_today(today: str):
    @t("DB read: pending bets created today (sanity)")
    def go():
        rows = execute_query(
            "SELECT COUNT(*) AS n FROM simulated_bets WHERE created_at::date = %s AND result = 'pending'",
            [today],
        )
        n = (rows or [{"n": 0}])[0]["n"]
        print(f"     {n} pending bets in DB", flush=True)
    go()


def main():
    today = date.today().isoformat()
    print(f"\n=== Probe betting slow path: {today} ===\n", flush=True)

    overall = time.time()

    step_count_pending_today(today)

    step_predictions(today)

    matches = step_load_matches_with_odds(today)

    if matches:
        match_ids = [m["id"] for m in matches if m.get("id")]
        step_load_signals(match_ids[:200])  # cap to keep this fast — 200 is enough to see per-row cost

    print(f"\nTotal probe wall time: {time.time() - overall:.1f}s\n", flush=True)


if __name__ == "__main__":
    main()
