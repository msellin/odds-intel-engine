#!/usr/bin/env python3
"""ONE table answering "which bots are working, and which are not".

The /admin/shadow-bots page lists dozens of rows with no verdict, so "is this
one working?" is unanswerable at a glance. This is the answer, on ONE honest
basis, for every bot that is actually producing picks.

THE BASIS, and why each choice matters:

  * MARGIN-CORRECTED OWN-BOOK CLV is the verdict column, not ROI.
    - own-book: `clv` is only meaningful against the close AT THE BOOK THE BOT
      PRICED AT. Rows with `closing_bookmaker IS NULL` came through the retired
      arbitrary-book fallback and read 4-10pp high (SHADOW-CLV-NO-ARBITRARY
      -FALLBACK); they are excluded, not corrected.
    - margin-corrected: `clv` is a RAW price ratio, so break-even is the closing
      book's own margin, not zero. EV = (1+clv)/(1+m) - 1 with m computed PER
      ROW. A flat average m inverted a verdict on 2026-09-14.
    - not ROI: per-bet return sd ~1.3, so confirming a true +3pct ROI at 80pct
      power needs ~15,600 bets. ROI cannot resolve on any realistic timescale;
      CLV converges ~200x faster (ANALYSIS_GOTCHAS §8).

  * n counted on USABLE rows (settled AND own-book close), not on picks. A bot
    with 568 settled picks and 0 own-book closes has no verdict, and saying so
    is the honest output.
"""
from __future__ import annotations
import argparse, math, sys
from collections import defaultdict
from workers.api_clients.db import execute_query
from workers.jobs.settlement import closing_book_margin

VERDICT_N = 300          # the pre-registered checkpoint
RETIRE_EV = -0.02


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-picks", type=int, default=1,
                    help="only bots with at least this many picks in 3 days")
    a = ap.parse_args()

    rows = execute_query(
        """
        SELECT s.bot_name, s.match_id, s.market, s.clv::float AS clv,
               s.pnl::float AS pnl, s.stake::float AS stake, s.closing_bookmaker
          FROM shadow_bets_unique s
         WHERE s.result IN ('won','lost') AND s.stake > 0
           AND s.bot_name IN (
               SELECT b.name FROM bots b
                JOIN shadow_bets_unique x ON x.bot_id = b.id
                WHERE b.is_active
                GROUP BY b.name
               HAVING count(*) FILTER (
                   WHERE x.created_at > now() - interval '3 days') >= %s)
        """,
        (a.min_picks,),
    )

    cache: dict = {}
    by = defaultdict(lambda: {"ev": [], "roi": [], "settled": 0, "nomargin": 0})
    for r in rows:
        b = by[r["bot_name"]]
        b["settled"] += 1
        b["roi"].append(r["pnl"] / r["stake"])
        bk = r["closing_bookmaker"]
        if not bk or r["clv"] is None:
            b["nomargin"] += 1
            continue
        key = (r["match_id"], r["market"], bk)
        if key not in cache:
            try:
                cache[key] = closing_book_margin(r["match_id"], r["market"], bk)
            except Exception:
                cache[key] = None
        m = cache[key]
        if m is None:
            b["nomargin"] += 1
            continue
        b["ev"].append((1 + r["clv"]) / (1 + m) - 1)

    print(f"{'bot':34s} {'usable':>7s} {'EV':>8s} {'95% CI':>18s}  {'ROI':>8s}  verdict")
    print("-" * 104)
    out = []
    for name, d in by.items():
        n = len(d["ev"])
        if n:
            ev = sum(d["ev"]) / n
            sd = math.sqrt(sum((x - ev) ** 2 for x in d["ev"]) / max(n - 1, 1))
            se = sd / math.sqrt(n)
            lo, hi = ev - 1.96 * se, ev + 1.96 * se
        else:
            ev = lo = hi = None
        roi = sum(d["roi"]) / len(d["roi"]) if d["roi"] else None
        out.append((name, n, ev, lo, hi, roi, d["settled"]))

    for name, n, ev, lo, hi, roi, settled in sorted(
            out, key=lambda x: (x[2] is None, -(x[2] or 0))):
        if n == 0:
            v = "NO VERDICT POSSIBLE — no own-book closes"
            print(f"{name:34s} {n:7d} {'—':>8s} {'—':>18s}  "
                  f"{(roi or 0)*100:+7.2f}%  {v}")
            continue
        if n < VERDICT_N:
            v = f"UNPROVEN — needs n={VERDICT_N} ({VERDICT_N - n} more)"
        elif lo > 0:
            v = "*** PROMOTION CANDIDATE — CLV>0, CI excludes 0"
        elif ev < RETIRE_EV:
            v = "RETIRE — EV below -2% at n>=300"
        else:
            v = "KEEP OBSERVING — inconclusive at n>=300"
        sig = "  (CI excludes 0)" if (lo > 0 or hi < 0) else ""
        print(f"{name:34s} {n:7d} {ev*100:+7.2f}% "
              f"[{lo*100:+6.2f},{hi*100:+6.2f}]  {(roi or 0)*100:+7.2f}%  {v}{sig}")

    print()
    print("EV = margin-corrected own-book CLV. Break-even is the closing book's")
    print("own margin, not zero. ROI is shown for context and CANNOT promote a")
    print("bot at any value — see the module docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
