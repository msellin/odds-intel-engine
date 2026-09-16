#!/usr/bin/env python3
"""OWN-MARGIN-BY-MARKET (2026-09-16) — the kill criterion, per market.

`own_path_kill_criterion.py` measured ONE market: 1x2, best-of-3 de-vigged
overround 5.66% against a pre-specified 2.00% threshold. The owner's question
was the obvious next one: we collect 1x2, O/U at five lines, BTTS, Asian
handicap, team totals and first-half markets — is any of them CHEAPER to bet
than 1x2?

Same arithmetic, applied per market. Two-leg complements only (the overround of
a partial market is meaningless), grouped by handicap line so Asian handicap is
compared like-for-like. Pre-match rows only.

THE ANSWER, 10 days to 2026-09-16 (median overround):

    market                  single-book   best-of-3   recovered
    asian_handicap                5.20%       4.96%      0.24pp
    btts                          6.06%       5.13%      0.93pp
    team_total_home_15            6.06%       5.16%      0.90pp
    team_total_away_15            6.06%       5.22%      0.84pp
    over_under_1h_15              5.81%       5.76%      0.05pp
    over_under_45                 5.95%       5.84%      0.11pp
    over_under_25                 6.49%       5.99%      0.50pp
    over_under_35                 6.44%       6.01%      0.43pp
    over_under_15                 6.66%       6.36%      0.30pp
    (1x2, from the kill criterion:  7.71%       5.66%      2.05pp)

Two things worth keeping:

1. **Asian handicap is the cheapest market we can bet — 4.96%.** That is 0.70pp
   better than 1x2 and the best number on the board. It is still 2.5x the 2.00%
   threshold, so it does not reopen automation; it does say that if anything is
   ever run again, AH is where to run it.

2. **Line shopping barely works in derivatives.** 1x2 recovers 2.05pp across
   three books; every derivative here recovers 0.05-0.93pp. The books disagree
   about match odds and agree about everything downstream of them. So "more
   markets means more shopping opportunities" is backwards — breadth adds
   markets that are individually more expensive AND less shoppable.

WHAT THIS DOES NOT MEASURE, and must not be read as covering: cross-book
LINE discrepancies. Here every comparison is same-line-same-market. Whether
Coolbet's AH -0.5 and Unibet's AH -0.75 on the same fixture can be played
against each other is a different question, still unmeasured, and the one place
in derivatives where a real exploit could hide. (Coolbet carries no quarter
lines, which constrains it — see docs/COOLBET_OWN_BETTING.md.)

Read-only.

    python3 scripts/own_market_margin_by_market.py [--days 10] [--min-n 30]
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

BOOKS = ("Coolbet", "Unibet-Site", "Epicbet")
KILL_THRESHOLD = 0.02
MARKETS = (
    "btts", "over_under_25", "over_under_35", "over_under_15", "over_under_45",
    "team_total_home_15", "team_total_away_15", "over_under_1h_15", "asian_handicap",
)


def load(days: int) -> list[dict]:
    return execute_query(
        """SELECT market, match_id, bookmaker, selection, handicap_line, max(odds) odds
             FROM odds_snapshots
            WHERE bookmaker = ANY(%s)
              AND timestamp > now() - make_interval(days => %s)
              AND is_live IS NOT TRUE
              AND market = ANY(%s)
            GROUP BY 1,2,3,4,5""",
        (list(BOOKS), days, list(MARKETS)),
    ) or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--min-n", type=int, default=30)
    a = ap.parse_args()

    g: dict = defaultdict(lambda: defaultdict(dict))
    for r in load(a.days):
        key = (r["market"], r["match_id"], r.get("handicap_line"))
        g[key][r["bookmaker"]][str(r["selection"]).lower()] = float(r["odds"])

    per: dict = defaultdict(lambda: {"single": [], "best": []})
    for (mkt, _m, _l), books in g.items():
        best: dict[str, float] = {}
        for _bk, sels in books.items():
            # A partial market has no meaningful overround — require the complement.
            if len(sels) != 2:
                continue
            per[mkt]["single"].append(sum(1 / o for o in sels.values()) - 1)
            for s, o in sels.items():
                if s not in best or o > best[s]:
                    best[s] = o
        if len(best) == 2:
            per[mkt]["best"].append(sum(1 / o for o in best.values()) - 1)

    print(f"\n=== overround by market, {a.days}d, {'/'.join(BOOKS)} (median) ===")
    print(f"{'market':22s} {'n':>7s} {'single':>9s} {'best-of-3':>10s} {'recovered':>10s}  verdict")
    out = []
    for mkt, d in sorted(per.items(), key=lambda kv: st.median(kv[1]["best"]) if len(kv[1]["best"]) >= a.min_n else 9):
        if len(d["single"]) < a.min_n:
            continue
        s = st.median(d["single"])
        b = st.median(d["best"]) if len(d["best"]) >= a.min_n else None
        v = "—" if b is None else ("UNDER THRESHOLD" if b < KILL_THRESHOLD else "above 2% kill line")
        print(f"{mkt:22s} {len(d['single']):7d} {s*100:8.2f}% "
              f"{(f'{b*100:9.2f}%' if b is not None else '        -')} "
              f"{(f'{(s-b)*100:9.2f}pp' if b is not None else '        -')}  {v}")
        if b is not None:
            out.append((mkt, b))

    if out:
        mkt, b = min(out, key=lambda kv: kv[1])
        print(f"\ncheapest market we can bet: {mkt} at {b*100:.2f}% best-of-3 "
              f"({'UNDER' if b < KILL_THRESHOLD else 'still above'} the {KILL_THRESHOLD*100:.2f}% kill line)")
    print("\nNOTE: same-line comparisons only. Cross-book LINE discrepancies (Coolbet AH -0.5 vs")
    print("Unibet AH -0.75 on one fixture) are NOT measured here and remain an open question.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
