"""SHADOW-PROMOTION-GATE-2026-08-26 — how often does the graduation gate lie?

/admin/shadow-bots promotes a shadow bot when it has >= 50 settled picks,
>= 14 days of observation, and ROI >= 3%; it retires at ROI <= -8%.

Whether those thresholds are sane is not a matter of opinion — it is a question
about the sampling distribution of ROI at n=50, which is fully determined by the
odds distribution the bots actually bet at. This simulates it.

Each trial draws n bets from the empirical odds distribution of the live shadow
bots, assigns wins with a chosen TRUE edge, and applies the gate. Repeat many
times and count how often the gate reaches the wrong verdict.

Usage:
    python3 scripts/promotion_gate_simulation.py
    python3 scripts/promotion_gate_simulation.py --trials 40000
"""
from __future__ import annotations

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402

CURRENT_MIN_N = 50
CURRENT_PROMOTE_ROI = 0.03
CURRENT_RETIRE_ROI = -0.08


def empirical_odds() -> list[float]:
    """Odds the live shadow bots actually bet at, deduped across cohorts."""
    rows = execute_query(
        """
        SELECT s.odds_at_pick
          FROM shadow_bets_unique s
          JOIN bots b ON b.id = s.bot_id
         WHERE s.result IN ('won','lost')
           AND (b.name LIKE 'bot_sweep%%' OR b.name LIKE 'bot_pin%%'
                OR b.name LIKE 'bot_no_pin%%')
        """,
        [],
    )
    return [float(r["odds_at_pick"]) for r in rows]


def trial(odds_pool: list[float], n: int, true_edge: float, rng: random.Random) -> float:
    """One bot-lifetime of n bets at a known true edge. Returns realised ROI."""
    total = 0.0
    for _ in range(n):
        o = rng.choice(odds_pool)
        # True edge e means the bet's expected return is e, so the true win
        # probability is (1 + e) / o.
        p = (1.0 + true_edge) / o
        if p >= 1.0:
            p = 0.999
        total += (o - 1.0) if rng.random() < p else -1.0
    return total / n


def simulate(odds_pool, n, true_edge, promote_at, retire_at, trials, rng):
    promoted = retired = watched = 0
    for _ in range(trials):
        roi = trial(odds_pool, n, true_edge, rng)
        if roi >= promote_at:
            promoted += 1
        elif roi <= retire_at:
            retired += 1
        else:
            watched += 1
    return promoted / trials, retired / trials, watched / trials


def t_gate_sim(odds_pool, n, true_edge, t_min, trials, rng):
    """Same, but the gate requires a t-statistic instead of a raw ROI level."""
    promoted = 0
    for _ in range(trials):
        rets = []
        for _ in range(n):
            o = rng.choice(odds_pool)
            p = min((1.0 + true_edge) / o, 0.999)
            rets.append((o - 1.0) if rng.random() < p else -1.0)
        mean = sum(rets) / n
        var = sum((r - mean) ** 2 for r in rets) / (n - 1)
        se = (var / n) ** 0.5
        if se > 0 and mean / se >= t_min and mean > 0:
            promoted += 1
    return promoted / trials


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260826)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    pool = empirical_odds()
    if len(pool) < 50:
        print("not enough settled shadow picks to build an odds distribution")
        return 1
    avg = sum(pool) / len(pool)
    print(f"empirical odds pool: n={len(pool)}  mean={avg:.2f}  "
          f"min={min(pool):.2f}  max={max(pool):.2f}\n")

    print("=" * 76)
    print(f"CURRENT GATE — n>={CURRENT_MIN_N}, promote at ROI>=+3%, retire at ROI<=-8%")
    print("=" * 76)
    print(f"{'true edge':>10s} {'promote':>10s} {'retire':>10s} {'watch':>10s}   verdict")
    for edge in (-0.10, -0.05, 0.0, 0.03, 0.05, 0.10):
        p, r, w = simulate(
            pool, CURRENT_MIN_N, edge, CURRENT_PROMOTE_ROI, CURRENT_RETIRE_ROI,
            args.trials, rng,
        )
        note = ""
        if edge <= 0 and p > 0.10:
            note = f"<- promotes a losing bot {p:.0%} of the time"
        if edge >= 0.05 and r > 0.10:
            note = f"<- retires a winning bot {r:.0%} of the time"
        print(f"{edge:+9.0%} {p:10.1%} {r:10.1%} {w:10.1%}   {note}")

    print("\n" + "=" * 76)
    print("HOW BIG DOES n NEED TO BE? (probability of promoting a TRULY BREAK-EVEN bot)")
    print("=" * 76)
    print(f"{'n':>7s} {'false promote':>15s} {'true promote (edge=+5%)':>26s}")
    for n in (50, 100, 200, 500, 1000, 2000):
        fp, _, _ = simulate(pool, n, 0.0, CURRENT_PROMOTE_ROI, CURRENT_RETIRE_ROI,
                            args.trials // 2, rng)
        tp, _, _ = simulate(pool, n, 0.05, CURRENT_PROMOTE_ROI, CURRENT_RETIRE_ROI,
                            args.trials // 2, rng)
        print(f"{n:7d} {fp:15.1%} {tp:26.1%}")

    print("\n" + "=" * 76)
    print("t-STATISTIC GATE — promote only when mean/SE >= t_min")
    print("=" * 76)
    print(f"{'n':>7s} {'t_min':>7s} {'false promote (edge=0)':>24s} {'true promote (edge=+5%)':>26s}")
    for n in (100, 200, 500, 1000):
        for t_min in (1.65, 2.0):
            fp = t_gate_sim(pool, n, 0.0, t_min, args.trials // 4, rng)
            tp = t_gate_sim(pool, n, 0.05, t_min, args.trials // 4, rng)
            print(f"{n:7d} {t_min:7.2f} {fp:24.1%} {tp:26.1%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
