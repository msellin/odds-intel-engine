"""SHADOW-DISCRETION-BLEED-2026-08-26 — do the operator's hand-picked bets do
worse than the ones left alone?

`user_pick_marks.state` records the operator's review workflow on
/admin/shadow-bots: 1 = reviewed, 2 = bet placed with real money. Comparing the
placed subset against the untouched picks measures whether the discretionary
layer adds or destroys value.

The naive comparison treats every bet as an independent observation. It is not:
bets placed on the same day share match outcomes, the same market conditions and
the same model run, so their errors are correlated. Pooling them inflates the
apparent sample size and overstates significance — which matters here, because
the pooled number looks decisive and the clustered one does not.

This reports both, so the gap between them is visible rather than hidden.

Usage:
    python3 scripts/discretion_bleed_report.py
"""
from __future__ import annotations

import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402

SQL = """
SELECT s.id, s.pick_time::date AS d, s.odds_at_pick, s.result, b.name AS bot,
       COALESCE(u.state, 0) AS state
  FROM shadow_bets_unique s
  JOIN bots b ON b.id = s.bot_id
  LEFT JOIN user_pick_marks u ON u.pick_id = s.id
 WHERE s.result IN ('won','lost')
"""


def stats(rets: list[float]) -> tuple[float, float, float]:
    n = len(rets)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(rets) / n
    if n < 2:
        return mean, 0.0, 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    se = math.sqrt(var / n)
    return mean, se, (mean / se if se else 0.0)


def main() -> int:
    rows = execute_query(SQL, [])
    by_state: dict = defaultdict(list)
    by_day: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        ret = (float(r["odds_at_pick"]) - 1.0) if r["result"] == "won" else -1.0
        st = int(r["state"])
        by_state[st].append(ret)
        by_day[r["d"]][st].append(ret)

    labels = {0: "untouched", 1: "reviewed only", 2: "BET PLACED (real money)"}
    print("POOLED — every bet treated as an independent observation")
    print(f"{'group':26s} {'n':>6s} {'ROI':>9s} {'SE':>8s} {'t':>7s}")
    print("-" * 60)
    for st in (0, 1, 2):
        m, se, t = stats(by_state[st])
        print(f"{labels[st]:26s} {len(by_state[st]):6d} {m*100:+8.1f}% {se*100:7.1f}% {t:+7.2f}")

    m0, se0, _ = stats(by_state[0])
    m2, se2, _ = stats(by_state[2])
    diff = m2 - m0
    se_d = math.sqrt(se0**2 + se2**2)
    print(f"\nplaced - untouched = {diff*100:+.1f}pp   t = {diff/se_d if se_d else 0:+.2f}")

    print("\n" + "=" * 72)
    print("CLUSTERED BY DAY — the honest test")
    print("=" * 72)
    print("Bets on the same day share match outcomes and one model run, so they")
    print("are not independent draws. The unit of observation is the DAY.\n")
    print(f"{'date':>12s} {'n placed':>9s} {'placed ROI':>11s} {'n untouched':>12s}"
          f" {'untouched ROI':>14s} {'diff':>9s}")
    diffs = []
    for d in sorted(by_day):
        p, u = by_day[d].get(2, []), by_day[d].get(0, [])
        if not p or not u:
            continue
        mp = sum(p) / len(p)
        mu = sum(u) / len(u)
        diffs.append(mp - mu)
        print(f"{str(d):>12s} {len(p):9d} {mp*100:+10.1f}% {len(u):12d}"
              f" {mu*100:+13.1f}% {(mp-mu)*100:+8.1f}pp")

    k = len(diffs)
    if k < 2:
        print("\nnot enough days with both groups to cluster")
        return 0
    md = sum(diffs) / k
    vd = sum((x - md) ** 2 for x in diffs) / (k - 1)
    sed = math.sqrt(vd / k)
    t = md / sed if sed else 0.0
    print(f"\nmean daily difference: {md*100:+.1f}pp   SE {sed*100:.1f}pp   "
          f"t = {t:+.2f}   df = {k-1}")
    # Two-sided 5% critical values for small df — the whole point is that with a
    # handful of days the bar is much higher than the familiar 1.96.
    crit = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36,
            8: 2.31, 9: 2.26, 10: 2.23}.get(k - 1, 2.10)
    print(f"two-sided 5% critical t at df={k-1} is {crit:.2f} — "
          f"{'SIGNIFICANT' if abs(t) >= crit else 'NOT significant'}")
    worse = sum(1 for x in diffs if x < 0)
    print(f"placed did worse on {worse} of {k} days")
    print("\nInterpretation: the pooled t above is inflated because it counts "
          "correlated bets as independent.\nUse the clustered figure. More "
          "marking days is the only thing that settles this.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
