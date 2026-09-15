#!/usr/bin/env python3
"""SHARP-TIGHT-SLOPE (2026-09-15, OWN Phase 1a) — the instrument's one number.

`bot_trigger_1x2_sharp_tight_v1` is an INSTRUMENT, not a strategy
(dev/active/own-sharp-tight-preregistration.md). The hypothesis it measures:
margin-corrected own-book CLV rises with the Pinnacle prob-edge at decision.
Two rounds measured the slope at **+1.31 (t=4.9)** on stale decision quotes and
**+0.35** once the quote had to be ≤60 min old (OWN-ANCHOR-GATE-VERIFICATION).
Nothing recorded the age per pick, so the two could not be separated on the
bot's own ledger. Now `decision_quote_age_min` is written on every leg and the
instrument refuses legs >60 min; this script reports the slope on FRESH legs
only, with the stale legs shown beside it for contrast.

Pre-registered stop (locked in the prereg amendment of 2026-09-15):
  * n_fresh ≥ 300 and the slope's 95% CI includes 0            → RETIRE
  * n_fresh ≥ 300, CI excludes 0, zero-crossing ≤ +6pp, ≥2 legs/day → Phase 3 candidate
  * otherwise                                                  → KEEP OBSERVING
ROI never promotes it.

Read-only. Cluster-robust (on match) OLS of clv_margin_corrected on edge_percent.

    python3 scripts/sharp_tight_slope.py [--bot bot_trigger_1x2_sharp_tight_v1] [--days 120]
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

FRESH_MAX_MIN = 60.0
N_DECIDE = 300
ZERO_CROSS_MAX = 0.06
LEGS_PER_DAY_MIN = 2.0


def ols_cluster(x: list[float], y: list[float], g: list[str]) -> dict:
    n = len(x)
    if n < 3:
        return {"n": n}
    mx = sum(x) / n; my = sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx == 0:
        return {"n": n}
    b = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / sxx
    a = my - b * mx
    # cluster-robust SE for b: sum over clusters of (sum resid*(x-mx))^2 / sxx^2
    by: dict[str, float] = defaultdict(float)
    for xi, yi, gi in zip(x, y, g):
        by[gi] += (yi - a - b * xi) * (xi - mx)
    var_b = sum(v * v for v in by.values()) / (sxx * sxx)
    se_b = math.sqrt(var_b) if var_b > 0 else 0.0
    zero_cross = (-a / b) if b else None
    return {"n": n, "clusters": len(by), "slope": b, "intercept": a, "se": se_b,
            "lo": b - 1.96 * se_b, "hi": b + 1.96 * se_b,
            "t": (b / se_b) if se_b else 0.0, "zero_crossing": zero_cross,
            "mean_mc_clv": my}


def load(bot: str, days: int) -> list[dict]:
    return execute_query(
        """SELECT match_id::text AS mid, edge_percent::float AS edge,
                  clv_margin_corrected::float AS mc, decision_quote_age_min::float AS age,
                  pick_time
             FROM shadow_bets_own_book_clv
            WHERE bot_name = %s AND clv_margin_corrected IS NOT NULL
              AND edge_percent IS NOT NULL
              AND pick_time > now() - make_interval(days => %s)
            ORDER BY pick_time""",
        (bot, days),
    ) or []


def report(label: str, rows: list[dict]) -> dict:
    r = ols_cluster([q["edge"] for q in rows], [q["mc"] for q in rows], [q["mid"] for q in rows])
    if r.get("n", 0) < 3:
        print(f"  {label:8s} n={r.get('n', 0)} — too few legs")
        return r
    zc = r["zero_crossing"]
    print(f"  {label:8s} n={r['n']:4d} (fixtures {r['clusters']})  slope {r['slope']:+.3f} "
          f"[{r['lo']:+.3f}, {r['hi']:+.3f}] t={r['t']:+.2f}  intercept {r['intercept']*100:+.2f}pp  "
          f"zero-crossing {'n/a' if zc is None else f'{zc*100:+.1f}pp edge'}  "
          f"mean mc-CLV {r['mean_mc_clv']*100:+.2f}pp")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="bot_trigger_1x2_sharp_tight_v1")
    ap.add_argument("--days", type=int, default=120)
    a = ap.parse_args()
    rows = load(a.bot, a.days)
    fresh = [r for r in rows if r["age"] is not None and r["age"] <= FRESH_MAX_MIN]
    stale = [r for r in rows if r["age"] is None or r["age"] > FRESH_MAX_MIN]
    print(f"\n=== {a.bot} — mc-CLV vs prob-edge, {a.days}d, own-book close only ===")
    print(f"legs with mc-CLV: {len(rows)}  fresh(≤{FRESH_MAX_MIN:.0f}m): {len(fresh)}  "
          f"stale/unknown: {len(stale)}")
    rf = report("FRESH", fresh)
    report("STALE", stale)
    report("ALL", rows)
    if fresh:
        span_days = max(((fresh[-1]["pick_time"] - fresh[0]["pick_time"]).total_seconds() / 86400.0), 1.0)
        lpd = len(fresh) / span_days
        print(f"  fresh legs/day: {lpd:.2f} over {span_days:.0f} days")
    else:
        lpd = 0.0
    print("\n--- pre-registered verdict (fresh legs only) ---")
    n = rf.get("n", 0)
    if n < N_DECIDE:
        print(f"  COLLECTING {n}/{N_DECIDE}")
    elif rf["lo"] <= 0 <= rf["hi"]:
        print("  RETIRE — slope CI includes 0 at n≥300")
    elif rf["lo"] > 0 and rf["zero_crossing"] is not None and rf["zero_crossing"] <= ZERO_CROSS_MAX and lpd >= LEGS_PER_DAY_MIN:
        print("  PHASE-3 CANDIDATE — owner decision; ROI is not admissible")
    else:
        print("  KEEP OBSERVING — slope significant but zero-crossing > +6pp or volume < 2/day")
    return 0


if __name__ == "__main__":
    sys.exit(main())
