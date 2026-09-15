#!/usr/bin/env python3
"""PROMO-REVIEW (2026-09-15, OWN Phase 2) — monthly realised-vs-EV check.

The promotions lever is pre-registered to be KILLED when realised P&L sits
more than 1.5 standard deviations below the summed EV for two consecutive
months. This prints that table. Read-only.

    python3 scripts/promo_review.py [--months 6]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

KILL_SD = 1.5
KILL_MONTHS = 2


def monthly(months: int) -> list[dict]:
    return execute_query(
        """SELECT date_trunc('month', taken_at)::date AS month,
                  count(*) AS n, count(settled_at) AS n_settled,
                  sum(ev_eur) AS ev_sum,
                  sum(realised_pnl_eur) FILTER (WHERE settled_at IS NOT NULL) AS realised,
                  -- per-bet variance approximated by stake^2 * p(1-p) * odds^2 is overkill for a
                  -- ledger this size; use the sample sd of (realised - ev) over settled rows
                  stddev_samp(realised_pnl_eur - ev_eur) FILTER (WHERE settled_at IS NOT NULL) AS sd_gap,
                  sum(stake_eur) AS staked
             FROM promo_ledger
            WHERE taken_at > now() - make_interval(months => %s)
            GROUP BY 1 ORDER BY 1""",
        (months,),
    ) or []


def verdict(rows: list[dict]) -> str:
    bad = 0
    for r in rows:
        n = int(r["n_settled"] or 0)
        if n < 5 or r["realised"] is None or r["sd_gap"] is None:
            bad = 0
            continue
        gap = float(r["realised"]) - float(r["ev_sum"] or 0)
        se = float(r["sd_gap"]) * math.sqrt(n)
        z = gap / se if se > 0 else 0.0
        bad = bad + 1 if z < -KILL_SD else 0
    if bad >= KILL_MONTHS:
        return "KILL — realised below EV by >1.5 sd for two consecutive months; the fair price is wrong"
    return "CONTINUE"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6)
    a = ap.parse_args()
    rows = monthly(a.months)
    print(f"{'month':10s} {'n':>4s} {'settled':>7s} {'staked':>8s} {'ΣEV':>8s} {'realised':>9s} {'gap':>8s}")
    for r in rows:
        ev = float(r["ev_sum"] or 0); rl = float(r["realised"]) if r["realised"] is not None else None
        print(f"{r['month']!s:10s} {r['n']:4d} {r['n_settled']:7d} {float(r['staked'] or 0):8.2f} {ev:8.2f} "
              f"{(f'{rl:9.2f}' if rl is not None else '        -')} {(f'{rl-ev:+8.2f}' if rl is not None else '       -')}")
    print("\nverdict:", verdict(rows) if rows else "no promo_ledger rows yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
