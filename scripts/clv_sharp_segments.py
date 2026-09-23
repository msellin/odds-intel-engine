#!/usr/bin/env python3
"""CLV-SHARP SEGMENT REPORT ([[#024]] (b)) — which slices beat the sharp close?

Reads the `clv_sharp_legs` view (migration 387): every scored leg of every
ledger, one CLV definition (odds × Shin(fresh Pinnacle close) − 1). Groups by
any segment columns, and for each segment reports n, mean clv_sharp, its 95% CI
and t, the research's preferred t on log(odds × p_close) (= log of taken price
over fair close), and a Benjamini-Hochberg q across every segment in the report.

The "retire?" flag is a PROPOSED rule, not an action: n >= RETIRE_MIN_N, mean
clv_sharp <= 0 and q < RETIRE_Q. Retiring anything on it is the owner's call
(handover T2). The published forward test keeps its own pre-registered decision
variable; this is reported BESIDE it.

    python3 scripts/clv_sharp_segments.py                          # ledger × bot × feed
    python3 scripts/clv_sharp_segments.py --by bot,odds_band --ledger picks_forward_test
    python3 scripts/clv_sharp_segments.py --by book_feed,quote_freshness --since 2026-09-15
Columns: ledger bot arm grade version bookmaker book_feed market_group odds_band
         ttk_bucket quote_freshness league_tier odds_basis decided_day
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ALLOWED = {"ledger", "bot", "arm", "grade", "version", "bookmaker", "book_feed",
           "market_group", "odds_band", "ttk_bucket", "quote_freshness",
           "league_tier", "odds_basis", "decided_day", "market"}
RETIRE_MIN_N = 100
RETIRE_Q = 0.10


def bh(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted q-values, same order as the input."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [0.0] * m
    run = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        run = min(run, pvals[i] * m / rank)
        q[i] = run
    return q


def p_two_sided(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2))


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from workers.api_clients.db import execute_query

    ap = argparse.ArgumentParser()
    ap.add_argument("--by", default="ledger,bot,book_feed")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--since", default=None, help="decided_day >= this date")
    ap.add_argument("--min-n", type=int, default=30)
    a = ap.parse_args()
    by = [c.strip() for c in a.by.split(",") if c.strip()]
    bad = set(by) - ALLOWED
    if bad:
        raise SystemExit(f"unknown segment columns: {sorted(bad)}")
    where, params = ["clv_sharp IS NOT NULL", "p_close > 0"], []
    if a.ledger:
        where.append("ledger = %s"); params.append(a.ledger)
    if a.since:
        where.append("decided_day >= %s"); params.append(a.since)
    cols = ", ".join(by)
    rows = execute_query(f"""
        SELECT {cols}, count(*) n,
               avg(clv_sharp) m, stddev_samp(clv_sharp) sd,
               avg(ln(odds * p_close)) lm, stddev_samp(ln(odds * p_close)) lsd,
               avg(close_age_min) age
          FROM clv_sharp_legs WHERE {' AND '.join(where)}
         GROUP BY {cols} HAVING count(*) >= %s ORDER BY {cols}""", params + [a.min_n])
    if not rows:
        print("no segment reaches --min-n")
        return 0
    stats = []
    for r in rows:
        n, m, sd = r["n"], float(r["m"]), float(r["sd"] or 0)
        se = sd / math.sqrt(n) if n > 1 else float("nan")
        t = m / se if se and se > 0 else 0.0
        lse = float(r["lsd"] or 0) / math.sqrt(n) if n > 1 else float("nan")
        lt = float(r["lm"]) / lse if lse and lse > 0 else 0.0
        stats.append((r, n, m, se, t, lt, p_two_sided(lt)))
    qs = bh([s[6] for s in stats])
    w = max(len(" | ".join(str(r[c]) for c in by)) for r, *_ in stats)
    print(f"clv_sharp by {cols}  ({len(stats)} segments, n >= {a.min_n}; BH-FDR across all)\n")
    print(f"  {'segment':{w}s} {'n':>6} {'clv_sharp':>10} {'95% CI':>17} {'t':>6} {'t(log)':>7} {'q':>7}  close age")
    for (r, n, m, se, t, lt, p), q in zip(stats, qs):
        seg = " | ".join(str(r[c]) for c in by)
        ci = f"[{100*(m-1.96*se):+.2f}, {100*(m+1.96*se):+.2f}]"
        flag = "  ← retire?" if (n >= RETIRE_MIN_N and m <= 0 and q < RETIRE_Q) else ""
        star = "  ← positive" if (m > 0 and q < 0.05) else ""
        print(f"  {seg:{w}s} {n:6d} {100*m:+9.2f}% {ci:>17} {t:+6.1f} {lt:+7.1f} {q:7.3f}  "
              f"{float(r['age'] or 0):4.0f}m{flag}{star}")
    print(f"\n  retire? = n >= {RETIRE_MIN_N}, mean <= 0, q < {RETIRE_Q} (PROPOSED rule — owner decides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
