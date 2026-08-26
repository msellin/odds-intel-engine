"""Where is a bookmaker systematically WRONG, and in which direction?

Line-shopping currently takes the MAX price across books. That quietly means we
bet most often at whichever book prices highest — and the paired sharpness test
showed the books that win that auction most often (Unibet, Coolbet) are also the
worst calibrated. That is either the opportunity or the trap, and which one
depends on a question nobody has asked our data: when a book is generous, is it
generous because it is WRONG, or because it is offering genuine value?

This measures, per book and per probability band, the book's own de-vigged
implied probability against the realised outcome rate.

  actual > implied  ->  the book is offering too-LONG odds on that band.
                        That band is exploitable.
  actual < implied  ->  the book is short. Betting it is paying their edge.

A book being badly calibrated overall is worthless information for betting. A
book being badly calibrated in a CONSISTENT DIRECTION on an identifiable band is
a strategy.

Usage:
    python3 scripts/book_bias_probe.py --market 1x2
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402

SIDES = {"1x2": ["home", "draw", "away"], "btts": ["yes", "no"],
         "over_under_25": ["over", "under"], "over_under_35": ["over", "under"]}
BANDS = [(0.0, 0.15), (0.15, 0.25), (0.25, 0.35), (0.35, 0.50),
         (0.50, 0.65), (0.65, 1.01)]
ACCESSIBLE = {"Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Coolbet"}


def outcome(market, sel, sh, sa):
    if market == "1x2":
        return int({"home": sh > sa, "draw": sh == sa, "away": sa > sh}[sel])
    if market == "btts":
        y = sh > 0 and sa > 0
        return int(y if sel == "yes" else not y)
    line = float(market.replace("over_under_", "")) / 10.0
    t = sh + sa
    if t == line:
        return None
    return int(t > line if sel == "over" else t < line)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="1x2", choices=sorted(SIDES))
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--min-n", type=int, default=400)
    args = ap.parse_args()

    sides = SIDES[args.market]
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
               o.match_id, o.selection, o.bookmaker, o.odds,
               m.score_home, m.score_away
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.market = %s AND o.timestamp <= m.date AND m.status = 'finished'
           AND m.score_home IS NOT NULL AND m.date >= %s AND m.date < %s
         ORDER BY o.match_id, o.selection, o.bookmaker, o.timestamp DESC
        """,
        [args.market, args.start, args.end],
    )
    per = defaultdict(lambda: defaultdict(dict))
    scores = {}
    for r in rows:
        mid = str(r["match_id"])
        per[mid][r["bookmaker"]][r["selection"]] = float(r["odds"])
        scores[mid] = (int(r["score_home"]), int(r["score_away"]))

    # band -> book -> [(implied, actual)]
    acc = defaultdict(lambda: defaultdict(list))
    for mid, books in per.items():
        sh, sa = scores[mid]
        ys = [outcome(args.market, s, sh, sa) for s in sides]
        if any(y is None for y in ys):
            continue
        for bk, sel in books.items():
            if bk not in ACCESSIBLE:
                continue
            if any(s not in sel or sel[s] <= 1.0 for s in sides):
                continue
            probs = devig([sel[s] for s in sides])
            if not probs:
                continue
            for i, y in enumerate(ys):
                for lo, hi in BANDS:
                    if lo <= probs[i] < hi:
                        acc[(lo, hi)][bk].append((probs[i], y))
                        break

    print(f"market={args.market}   {args.start} → {args.end}")
    print("Book's own de-vigged probability vs what actually happened.")
    print("'gap' = actual - implied. POSITIVE = book's odds are too long "
          "= exploitable band.\n")
    for lo, hi in BANDS:
        band = acc[(lo, hi)]
        printed = False
        for bk in sorted(band, key=lambda b: -len(band[b])):
            pairs = band[bk]
            n = len(pairs)
            if n < args.min_n:
                continue
            if not printed:
                print(f"  probability band {lo:.0%}–{hi:.0%}")
                print(f"    {'book':14s} {'n':>7s} {'implied':>9s} {'actual':>9s} "
                      f"{'gap':>9s} {'t':>7s}")
                printed = True
            imp = sum(p for p, _ in pairs) / n
            act = sum(y for _, y in pairs) / n
            gap = act - imp
            se = (act * (1 - act) / n) ** 0.5
            t = gap / se if se else 0.0
            flag = "  <-- too long" if t > 2 else ("  <-- too short" if t < -2 else "")
            print(f"    {bk:14s} {n:7d} {imp*100:8.2f}% {act*100:8.2f}% "
                  f"{gap*100:+8.2f}pp {t:+7.2f}{flag}")
        if printed:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
