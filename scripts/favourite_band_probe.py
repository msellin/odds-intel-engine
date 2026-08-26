"""Is the favourite band actually bettable, or does the vig eat it?

book_bias_probe.py found a clean favourite-longshot bias across the soft books:
in the 50%+ probability bands their DE-VIGGED probabilities are systematically
too low (Betano +3.25pp at 65%+, t=+3.23; 10Bet +1.92pp at 50-65%, t=+2.20),
while in the longshot bands they are too high.

That is necessary but NOT sufficient. A de-vigged gap is not money: you bet the
RAW price, which is worse than the de-vigged one by whatever share of the
overround sits on that selection. The gap has to beat the vig you actually pay.

So this asks the only question that matters: taking the BEST raw price available
across accessible books on favourites, would flat-betting them have won?

Usage:
    python3 scripts/favourite_band_probe.py --min-prob 0.55
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402

SIDES = ["home", "draw", "away"]
ACCESSIBLE = {"Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Coolbet"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--min-prob", type=float, default=0.55)
    args = ap.parse_args()

    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
               o.match_id, o.selection, o.bookmaker, o.odds,
               m.score_home, m.score_away
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.market = '1x2' AND o.timestamp <= m.date AND m.status = 'finished'
           AND m.score_home IS NOT NULL AND m.date >= %s AND m.date < %s
         ORDER BY o.match_id, o.selection, o.bookmaker, o.timestamp DESC
        """,
        [args.start, args.end],
    )
    per = defaultdict(lambda: defaultdict(dict))
    scores = {}
    for r in rows:
        mid = str(r["match_id"])
        per[mid][r["bookmaker"]][r["selection"]] = float(r["odds"])
        scores[mid] = (int(r["score_home"]), int(r["score_away"]))

    buckets = defaultdict(list)
    for mid, books in per.items():
        sh, sa = scores[mid]
        won = {"home": sh > sa, "draw": sh == sa, "away": sa > sh}

        # Consensus anchor, leave-one-out is not needed here because we are not
        # gating on edge vs the anchor — the anchor only identifies WHICH
        # selection is the favourite.
        probs_list = []
        for bk, sel in books.items():
            if any(s not in sel or sel[s] <= 1.0 for s in SIDES):
                continue
            d = devig([sel[s] for s in SIDES])
            if d:
                probs_list.append(d)
        if len(probs_list) < 3:
            continue
        cons = [sum(p[i] for p in probs_list) / len(probs_list) for i in range(3)]
        t = sum(cons)
        cons = [p / t for p in cons]

        for i, s in enumerate(SIDES):
            if cons[i] < args.min_prob:
                continue
            offers = [sel[s] for bk, sel in books.items()
                      if bk in ACCESSIBLE and s in sel and sel[s] > 1.0]
            if not offers:
                continue
            best = max(offers)
            avg = sum(offers) / len(offers)
            ret_best = (best - 1.0) if won[s] else -1.0
            ret_avg = (avg - 1.0) if won[s] else -1.0
            band = "0.55-0.65" if cons[i] < 0.65 else ("0.65-0.75" if cons[i] < 0.75 else "0.75+")
            buckets[band].append((ret_best, ret_avg, cons[i], best, won[s]))

    print(f"1x2 favourites, consensus prob >= {args.min_prob:.0%}   "
          f"{args.start} → {args.end}\n")
    print(f"{'band':12s} {'n':>6s} {'cons p':>8s} {'best odds':>10s} "
          f"{'ROI @ best':>11s} {'t':>7s} {'ROI @ avg':>10s}")
    print("-" * 70)
    allb = []
    for band in ("0.55-0.65", "0.65-0.75", "0.75+"):
        rows_ = buckets[band]
        if len(rows_) < 100:
            continue
        allb.extend(rows_)
        n = len(rows_)
        rb = [r[0] for r in rows_]
        ra = [r[1] for r in rows_]
        mb = sum(rb) / n
        va = sum((x - mb) ** 2 for x in rb) / (n - 1)
        se = (va / n) ** 0.5
        print(f"{band:12s} {n:6d} {100*sum(r[2] for r in rows_)/n:7.1f}% "
              f"{sum(r[3] for r in rows_)/n:10.3f} {mb*100:+10.2f}% "
              f"{mb/se:+7.2f} {100*sum(ra)/n:+9.2f}%")
    if allb:
        n = len(allb)
        rb = [r[0] for r in allb]
        m = sum(rb) / n
        v = sum((x - m) ** 2 for x in rb) / (n - 1)
        se = (v / n) ** 0.5
        print("-" * 70)
        print(f"{'ALL':12s} {n:6d} {'':8s} {'':10s} {m*100:+10.2f}% {m/se:+7.2f}%")
        print(f"\nROI @ best = flat-betting the best accessible price. "
              f"ROI @ avg = the typical book.\nThe gap between them is what "
              f"line-shopping is worth in this band.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
