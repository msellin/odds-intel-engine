#!/usr/bin/env python3
"""OWN-path kill criterion: can line shopping across EMTA-legal books pay its vig?

THE TEST (from the OWN-path audit, 2026-09-14): on fixtures all three
self-scraped books price, take the BEST price per selection across the three,
de-vig, and measure the residual overround.

  KILL CRITERION: if the median best-of-3 overround stays above ~2pct, no line
  shopping strategy on Estonian books can pay its own margin, and the automated
  betting product should be closed.

WHY THIS RUNS TODAY AND NOT IN TWO WEEKS. The audit concluded we "cannot compute
a trustworthy cross-book comparison at all" because only 5.9pct of co-priced
market-fixtures had quotes within 15 min, and recommended two weeks of
synchronised polling first. That 5.9pct was OUR OWN QUERY ARTEFACT: Coolbet
stamps each selection row individually (a 1x2 triple lands across ~100ms, e.g.
09:20:15.928 / .975 / :16.026), so grouping on exact equality finds a "complete
triple" in 0.1pct of Coolbet timestamp-groups against 99.9-100pct for Epicbet and
Unibet-Site. Assemble each book's triple from a small window and coverage is
1,073 fixtures with 33.5pct aligned inside 15 min — 5.7x the reported figure.

Nothing needed collecting. The data was always there.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from statistics import median

from workers.api_clients.db import execute_query
from workers.utils.odds_assembly import assemble as _shared_assemble
from workers.model.devig import devig

BOOKS = ["Coolbet", "Epicbet", "Unibet-Site"]   # EMTA-legal, SELF-SCRAPED only
SIDES = ["home", "draw", "away"]
ASSEMBLE_WINDOW_MIN = 2.0   # a book's own triple may straddle this
KILL_THRESHOLD = 0.02       # pre-registered: median above this closes OWN


def load(days_back: int, align_min: float):
    rows = execute_query(
        """
        SELECT o.match_id, o.bookmaker, o.selection,
               o.odds::float AS odds, o.timestamp
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.bookmaker = ANY(%s) AND o.market = '1x2'
           AND o.is_live IS NOT TRUE
           AND m.date > now() - (%s || ' days')::interval
           AND o.timestamp < m.date
        """,
        (BOOKS, str(days_back)),
    )
    d: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        d[r["match_id"]][r["bookmaker"]].append(
            (r["timestamp"], r["selection"], r["odds"]))
    return d


def assemble(obs, window=None):
    """Delegates to workers.utils.odds_assembly (2026-09-17).

    The local copy took the FIRST occurrence of each selection in the window
    and callers then used the LAST assembled triple — which anchors on the
    latest row, i.e. the worse half of a Coolbet double-write. Measured at
    +1.86pp against the live quote; the shared helper's burst rule measures
    +0.00pp. See ANALYSIS_GOTCHAS §62 and COOLBET-DOUBLE-WRITE.

    `window` is in MINUTES and defaults to THIS script's ASSEMBLE_WINDOW_MIN,
    never to the shared module's 15-minute default. The first delegation let it
    fall through, silently widening this script's assembly window 7.5x (2 min ->
    15 min) and admitting the within-book time smear the 2-minute figure exists
    to exclude.
    """
    win = ASSEMBLE_WINDOW_MIN if window is None else float(window)
    return _shared_assemble(obs, SIDES, window_s=win * 60.0)


def run(days_back: int, align_min: float):
    d = load(days_back, align_min)
    single, best3, pairs = defaultdict(list), [], defaultdict(list)
    aligned_fixtures = 0

    for mid, bybook in d.items():
        tri = {b: assemble(bybook.get(b, [])) for b in BOOKS}
        if not all(tri[b] for b in BOOKS):
            continue
        # tightest simultaneous window across all three books
        best = None
        for t0, q0 in tri[BOOKS[0]]:
            cand = [(t0, q0)]
            for b in BOOKS[1:]:
                cand.append(min(tri[b],
                                key=lambda tq: abs((tq[0] - t0).total_seconds())))
            gap = (max(c[0] for c in cand)
                   - min(c[0] for c in cand)).total_seconds() / 60.0
            if best is None or gap < best[0]:
                best = (gap, cand)
        if best is None or best[0] > align_min:
            continue
        aligned_fixtures += 1
        gap, cand = best
        quotes = dict(zip(BOOKS, [c[1] for c in cand]))

        for b in BOOKS:
            single[b].append(sum(1.0 / quotes[b][s] for s in SIDES) - 1.0)
        bo = {s: max(quotes[b][s] for b in BOOKS) for s in SIDES}
        best3.append(sum(1.0 / bo[s] for s in SIDES) - 1.0)
        for combo in (("Coolbet", "Epicbet"), ("Coolbet", "Unibet-Site"),
                      ("Epicbet", "Unibet-Site")):
            p = {s: max(quotes[b][s] for b in combo) for s in SIDES}
            pairs[" + ".join(combo)].append(sum(1.0 / p[s] for s in SIDES) - 1.0)

    return single, pairs, best3, aligned_fixtures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--align-min", type=float, default=15.0)
    a = ap.parse_args()

    single, pairs, best3, n = run(a.days, a.align_min)
    if not best3:
        print("no time-aligned fixtures — cannot evaluate")
        return 2

    print(f"time-aligned fixtures (<= {a.align_min:.0f} min, last {a.days}d): {n}\n")
    print("  median 1x2 overround, SAME fixtures, SAME moment")
    for b in BOOKS:
        print(f"    {b:24s} {median(single[b])*100:6.2f}%")
    print()
    for k, v in pairs.items():
        print(f"    {k:24s} {median(v)*100:6.2f}%")
    m3 = median(best3)
    print(f"    {'BEST OF ALL THREE':24s} {m3*100:6.2f}%   <-- the kill criterion")

    cheapest = min(median(single[b]) for b in BOOKS)
    print(f"\n  line shopping recovers {(cheapest - m3)*100:.2f}pp "
          f"of the cheapest single book's {cheapest*100:.2f}pp margin")
    print(f"  per-outcome residual margin: ~{m3/3*100:.2f}pp\n")

    if m3 > KILL_THRESHOLD:
        print(f"  ❌ KILL CRITERION MET — median {m3*100:.2f}% > "
              f"{KILL_THRESHOLD*100:.0f}%.")
        print("     No line-shopping strategy on these books can pay its own vig.")
        return 1
    print(f"  ✅ below the {KILL_THRESHOLD*100:.0f}% threshold — OWN path stays open.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
