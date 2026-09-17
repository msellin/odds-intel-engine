#!/usr/bin/env python3
"""OWN-MARGIN-BY-TIER — is the vig we pay materially lower on big fixtures?

WHY (2026-09-17). The kill criterion measured ONE number for all of football:
best-of-3 de-vigged overround 5.66 pct against a 2 pct threshold, and closed
automated betting on that. But it is an average over everything we quote, and a
single hand-checked fixture (Besiktas v Marseille) came in at **2.61 pct
best-of-all** — less than half the median, and within touching distance of the
threshold.

If margin varies systematically with fixture profile, then "the vig is 5.66 pct"
is the wrong frame: the right question is whether some SUBSET is cheap enough to
be worth a strategy. That is a different claim from the one the kill criterion
tested, and it has never been measured.

METHOD — the same discipline as own_market_margin_by_market.py, because the
first version of THAT script was wrong for want of it:
  * each book's complete 1x2 triple assembled within a 15-min window
  * books compared only when aligned within 15 min of each other
  * pre-match only, and never a closing price
  * split by league tier, and separately by the market's own price level (a
    proxy for how much money a fixture attracts)

WHAT A POSITIVE RESULT WOULD AND WOULD NOT MEAN. It would NOT reopen automated
betting: a cheap subset still needs an edge to beat, and the model measured
alpha = 0 on a verified harness. It WOULD mean the 2 pct threshold is reachable
somewhere, which is the precondition for any strategy at all, and that is worth
knowing before concluding the market access question is closed.

RESULT (2026-09-17, 21 days, n=1,550 aligned fixtures): **the hypothesis is
dead, and the direction is the opposite of the one that prompted it.**

    overall best-of-3 median 6.40 pct, 5.7 pct of fixtures under 2 pct

    by league tier      tier 1  6.06 pct   tier 2  6.59   tier 3  7.06
    by favourite price  <1.50   6.45 pct   2.50+   5.17

Tier moves the number by ~1pp, and the CHEAPEST group is long-favourite (even,
obscure) matches rather than big fixtures. The hand-checked Besiktas v Marseille
at 2.61 pct was a tail observation, not a pattern.

AND THE 5.7 PCT TAIL IS A MIRAGE. Checked directly:

    fixtures under 2 pct : best SINGLE book's own overround +6.19 pct
                           cross-book spread 2.07pp
    all others           : best SINGLE book's own overround +7.74 pct
                           cross-book spread 1.45pp

The cheap group's individual books charge a NORMAL margin. What makes the
combined price cheap is that the books DISAGREE more — the sub-2 pct price
exists only by pairing one book's high home quote with another's high away
quote at a moment when one of them is stale. ANALYSIS_GOTCHAS s52/s55, the
best-of-books mirage, for the third time in this project.

So there is no cheap subset to build a strategy on, and the kill criterion's
single number was not hiding one.

Read-only.

    python3 scripts/own_margin_by_tier.py [--days 21] [--align-min 15]
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.utils.odds_assembly import (  # noqa: E402
    assemble as _shared_assemble, latest_market,
)

BOOKS = ["Coolbet", "Unibet-Site", "Epicbet"]
SIDES = ("home", "draw", "away")
ASSEMBLE_WINDOW_MIN = 15.0
KILL_THRESHOLD = 0.02


def load(days: int):
    return execute_query(
        """SELECT o.match_id, o.bookmaker, o.selection, o.odds::float AS odds,
                  o.timestamp, m.league_id::text AS lid,
                  COALESCE(l.tier, 0) AS tier, l.name AS league
             FROM odds_snapshots o
             JOIN matches m ON m.id = o.match_id
             LEFT JOIN leagues l ON l.id = m.league_id
            WHERE o.bookmaker = ANY(%s) AND o.market = '1x2'
              AND o.is_live IS NOT TRUE
              AND m.date > now() - make_interval(days => %s)
              AND o.timestamp < m.date""",
        (BOOKS, days),
    ) or []


def assemble(obs, window=None):
    """Delegates to workers.utils.odds_assembly (2026-09-17).

    The local copy took the FIRST occurrence of each selection in the window
    and callers then used the LAST assembled triple — which anchors on the
    latest row, i.e. the worse half of a Coolbet double-write. Measured at
    +1.86pp against the live quote; the shared helper's burst rule measures
    +0.00pp. See ANALYSIS_GOTCHAS §62 and COOLBET-DOUBLE-WRITE.
    """
    return (_shared_assemble(obs, SIDES) if window is None
            else _shared_assemble(obs, SIDES, window_s=float(window) * 60.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--align-min", type=float, default=15.0)
    ap.add_argument("--min-n", type=int, default=40)
    a = ap.parse_args()

    rows = load(a.days)
    per_match = defaultdict(lambda: defaultdict(list))
    meta = {}
    for r in rows:
        per_match[r["match_id"]][r["bookmaker"]].append(
            (r["timestamp"], r["selection"], r["odds"]))
        meta[r["match_id"]] = (int(r["tier"] or 0), r["league"])

    by_tier = defaultdict(list)
    by_fav = defaultdict(list)
    all_best = []
    for mid, bybook in per_match.items():
        comp = {b: assemble(v) for b, v in bybook.items()}
        comp = {b: v for b, v in comp.items() if v}
        if len(comp) < 2:
            continue
        bk = sorted(comp)
        best = None
        for t0, q0 in comp[bk[0]]:
            cand = [(t0, q0)] + [min(comp[b], key=lambda tq: abs((tq[0] - t0).total_seconds()))
                                 for b in bk[1:]]
            gap = (max(c[0] for c in cand) - min(c[0] for c in cand)).total_seconds() / 60.0
            if best is None or gap < best[0]:
                best = (gap, cand)
        if best is None or best[0] > a.align_min:
            continue
        quotes = dict(zip(bk, [c[1] for c in best[1]]))
        bo = {s: max(quotes[b][s] for b in bk) for s in SIDES}
        ov = sum(1.0 / bo[s] for s in SIDES) - 1.0
        tier, _league = meta[mid]
        by_tier[tier].append(ov)
        all_best.append(ov)
        # Favourite price as a crude proxy for how much money a fixture attracts:
        # short favourites are big matches, long ones are obscure.
        fav = min(bo.values())
        band = ("<1.50" if fav < 1.5 else "1.50-2.00" if fav < 2.0
                else "2.00-2.50" if fav < 2.5 else "2.50+")
        by_fav[band].append(ov)

    if not all_best:
        print("no aligned fixtures")
        return 1

    def show(title, d, order=None):
        print(f"\n  {title}")
        print(f"    {'group':12s} {'n':>6s} {'median':>9s} {'p25':>8s} {'under 2 pct':>12s}")
        keys = order or sorted(d)
        for k in keys:
            v = sorted(d.get(k, []))
            if len(v) < a.min_n:
                continue
            med = st.median(v)
            p25 = v[len(v) // 4]
            cheap = sum(1 for x in v if x < KILL_THRESHOLD) / len(v) * 100
            print(f"    {str(k):12s} {len(v):6,} {med*100:8.2f}% {p25*100:7.2f}% {cheap:11.1f}%")

    print(f"\n=== best-of-{len(BOOKS)} 1x2 overround, {a.days}d, time-aligned within "
          f"{a.align_min:.0f} min ===")
    print(f"  overall: n={len(all_best):,}  median {st.median(all_best)*100:.2f}%  "
          f"under 2 pct: {sum(1 for x in all_best if x < KILL_THRESHOLD)/len(all_best)*100:.1f}%")
    show("by league tier (1 = top)", by_tier)
    show("by favourite price (short favourite = big fixture)", by_fav,
         order=["<1.50", "1.50-2.00", "2.00-2.50", "2.50+"])
    print(f"\n  kill threshold = {KILL_THRESHOLD*100:.0f}% best-of-N overround")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
