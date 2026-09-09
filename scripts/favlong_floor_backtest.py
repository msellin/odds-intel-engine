"""FAVLONG-SPLIT-FLOOR-BACKTEST — does 1x2 want DIFFERENT placement floors for
favourites vs longshots, instead of the single pooled 13% we place + publish at?

The model GENERATES 1x2 on a fav/long split (home <2.0 = 8%, every draw/away/
home-≥2.0 = 12%; daily_pipeline_v2.py:3375), but scripts/edge_floor_backtest.py
found "13% is best" on POOLED 1x2. This re-runs that exact methodology
(_folds/_roi/_sweep, walk-forward, executable price = the bots' actual picks at
COALESCE(odds_at_pick_live, odds_at_pick)) but partitions 1x2 into:
    FAV  = selection 'home' AND odds < 2.0        (the generation fav cut)
    LONG = everything else (draws, aways, home ≥ 2.0)
and sweeps floors on each side + the pooled baseline, per cohort.

READ THE ROBUSTNESS COLUMN, NOT THE POOLED ROI. Splitting ~halves the picks per
cell, so a per-side floor can look great in one window and be noise. A split
floor is only adoptable if EACH side is positive in EVERY fold (robust ✓) — else
the pooled 13% wins by default (§52: more knobs, less robust). This script only
MEASURES; changing the real-money floor / grade bands is a separate, owner-gated step.

Usage: python3 scripts/favlong_floor_backtest.py [--folds 3] [--stake 10]
"""
from __future__ import annotations

import argparse

from workers.api_clients.db import execute_query
from scripts.edge_floor_backtest import _sweep  # reuse the exact sweep + robustness logic


def _rows(active_only: bool):
    mat = "AND b.maturity_label IN ('calibrated','beta','active')" if active_only else ""
    return execute_query(f"""
        SELECT sb.edge_percent::float ep,
               lower(sb.selection) sel,
               COALESCE(sb.odds_at_pick_live, sb.odds_at_pick)::float odds,
               sb.pick_time,
               (CASE WHEN sb.result='won'
                     THEN (COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) - 1)
                     ELSE -1 END)::float ret
          FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
         WHERE lower(sb.market) = '1x2' AND sb.result IN ('won','lost')
           AND sb.edge_percent IS NOT NULL
           AND COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) IS NOT NULL {mat}
    """)


def _is_fav(sel: str, odds: float) -> bool:
    # matches daily_pipeline_v2.py:3375 — home pick under 2.0 is the favourite;
    # every draw/away and any home ≥ 2.0 is a longshot.
    return sel == "home" and odds is not None and odds < 2.0


def run(n_folds: int, stake: float):
    for active_only in (False, True):
        cohort = "active/calibrated cohort (real-money-relevant)" if active_only else "ALL bots"
        rows = _rows(active_only)
        fav = [(r["ep"], r["ret"], r["pick_time"]) for r in rows if _is_fav(r["sel"], r["odds"])]
        lng = [(r["ep"], r["ret"], r["pick_time"]) for r in rows if not _is_fav(r["sel"], r["odds"])]
        pooled = [(r["ep"], r["ret"], r["pick_time"]) for r in rows]
        print(f"\n{'='*74}\n1x2 fav/long floor sweep — {cohort} — "
              f"n={len(pooled)} (fav {len(fav)} / long {len(lng)})\n{'='*74}")
        print(f"\n--- POOLED 1x2 (the current single-floor baseline; 13% is today's gate) ---")
        _sweep(pooled, n_folds, stake)
        print(f"\n--- FAV only (home, odds < 2.0) ---")
        if fav:
            _sweep(fav, n_folds, stake)
        else:
            print("  (no fav bets)")
        print(f"\n--- LONG only (draws, aways, home ≥ 2.0) ---")
        if lng:
            _sweep(lng, n_folds, stake)
        else:
            print("  (no long bets)")


def main() -> int:
    ap = argparse.ArgumentParser(description="1x2 fav/long split floor backtest")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--stake", type=float, default=10.0)
    a = ap.parse_args()
    run(a.folds, a.stake)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
