#!/usr/bin/env python3
"""OWN per-market bots — what the LIVE paper bots already say, per market.

Read-only. Writes nothing. See docs/OWN_PER_MARKET_BOTS_2026_09_14.md.

WHY. `docs/OWN_MARKET_EXPANSION_2026_09_14.md` asks "which additional market can
carry a sharp edge?" purely as a BACKTEST question and never looks at the fleet.
But three of the markets it ranks — corners, team totals and first-half 1x2 —
have had a paper bot running for days, and two of them were RETIRED on
2026-09-14 (migration 348) on exactly the metric the backtest cannot produce:
margin-corrected OWN-BOOK closing-line value. That is stronger evidence than any
cell in the sweep, and it is already in the database.

WHAT IT COMPUTES, per bot:

  * ROI on `shadow_bets_unique` (never `shadow_bets` — 8.43x duplication,
    outcome-correlated, ANALYSIS_GOTCHAS §5).
  * RAW own-book CLV. `clv` is a bare price ratio; its break-even is the
    CLOSING BOOK'S OWN MARGIN, not zero (a flat 7.6% assumption inverted a
    verdict this morning), so raw CLV is reported only as an input.
  * MARGIN-CORRECTED own-book CLV = (1+clv)/(1+m) - 1, with `m` computed PER ROW
    as that book's own closing overround on that fixture and market — the same
    definition as `workers.jobs.settlement.closing_book_margin`, re-derived here
    in SQL so this script does not import the settlement module under edit.
  * OWN-BOOK ROWS ONLY. `closing_bookmaker IS NULL` rows came through the
    arbitrary-book fallback retired 2026-09-14 and read 4-10pp high.
  * Cluster-robust SEs on match_id.

It also prints, per bot, WHICH BOOKS its picks are priced at — because a
per-market instrument priced at a book we cannot place at measures somebody
else's market (§52 / §55), and one of the live ones is doing exactly that.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query

# The complement set per market family, mirroring
# settlement._market_complement_selections. A market absent here has NO
# computable own-book margin today, which is itself a finding: its instrument
# cannot ever reach a CLV stopping rule.
TWO_WAY = ("over", "under")
# (selections, what the inverse odds must sum to when the margin is zero).
# Double chance is 2.0 because each of 1X/12/X2 covers two of three outcomes —
# settlement._market_complement_selections excludes DC on the grounds that the
# outcomes "do not form a partition", which is true and is not an obstacle to a
# MARGIN: the three DC prices still cover every outcome exactly twice, so
# sum(1/o)/2 - 1 is that book's own overround on its own DC book. Including it
# here is how the DC instrument's stopping-rule metric becomes computable at
# all; asian_handicap stays out because its margin needs the handicap line
# threaded through and a fixed line is not a close (§16).
COMPLEMENTS = {
    "1x2": (("home", "draw", "away"), 1.0),
    "1x2_1h": (("home", "draw", "away"), 1.0),
    "btts": (("yes", "no"), 1.0),
    "double_chance": (("1x", "12", "x2"), 2.0),
}


def complement(market: str):
    m = (market or "").strip().lower()
    if m in COMPLEMENTS:
        return COMPLEMENTS[m]
    if m.startswith(("over_under", "corners_", "team_total_")):
        return (TWO_WAY, 1.0)
    # cards_*, asian_handicap: deliberately absent, see header.
    return None


def rows_for(pattern: str, days: int):
    return execute_query(
        """
        SELECT s.bot_name, s.match_id::text AS match_id, s.market, s.selection,
               s.recommended_bookmaker AS book, s.closing_bookmaker AS cbook,
               s.odds_at_pick::float AS odds, s.closing_odds::float AS cod,
               s.clv::float AS clv, s.pnl::float AS pnl, s.stake::float AS stake,
               s.result, s.created_at::date AS d, s.bot_retired_at IS NOT NULL AS retired
          FROM shadow_bets_unique s
         WHERE s.bot_name ~ %s
           AND s.created_at > now() - (%s || ' days')::interval
        """,
        (pattern, str(days)),
    )


def margins(keys):
    """{(match_id, market, book): overround} from each book's own closing quotes.

    One query per complement shape, not one per row — 7,670 DC picks through a
    per-row helper is minutes of round-trips for a number that is a group-by."""
    out = {}
    by_shape = defaultdict(list)
    for mid, mk, bk in keys:
        c = complement(mk)
        if c:
            by_shape[c].append((mid, mk, bk))
    for (sels, fair), want in by_shape.items():
        mids = sorted({m for m, _, _ in want})
        mks = sorted({k for _, k, _ in want})
        bks = sorted({b for _, _, b in want if b})
        if not (mids and mks and bks):
            continue
        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.market, o.bookmaker, o.selection)
                   o.match_id::text AS mid, o.market AS mk, o.bookmaker AS bk,
                   o.selection AS sel, o.odds::float AS odds
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s)
               AND o.bookmaker = ANY(%s) AND o.is_closing AND o.timestamp <= m.date
               AND o.selection = ANY(%s)
             ORDER BY o.match_id, o.market, o.bookmaker, o.selection, o.timestamp DESC
            """,
            (mids, mks, bks, list(sels)),
        )
        book = defaultdict(dict)
        for r in rows:
            book[(r["mid"], r["mk"], r["bk"])][r["sel"]] = r["odds"]
        for k, q in book.items():
            if all(s in q and q[s] > 1.0 for s in sels):
                m = sum(1.0 / q[s] for s in sels) / fair - 1.0
                if 0.0 <= m <= 0.5:
                    out[k] = m
    return out


def clustered(vals, clusters):
    n = len(vals)
    if n == 0:
        return None
    mean = sum(vals) / n
    by = defaultdict(float)
    for v, c in zip(vals, clusters):
        by[c] += v - mean
    g = len(by)
    if g > 1:
        var = sum(x * x for x in by.values()) / (n * n) * g / (g - 1)
    else:
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / max(n - 1, 1))
        var = sd * sd / n
    se = math.sqrt(max(var, 0.0))
    return {"n": n, "mean": mean, "lo": mean - 1.96 * se, "hi": mean + 1.96 * se,
            "se": se, "t": mean / se if se else 0.0, "clusters": g}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="corner|card|team_total|1h|btts|ah_|dc_|handicap")
    ap.add_argument("--days", type=int, default=200)
    a = ap.parse_args()

    rows = rows_for(a.pattern, a.days)
    by_bot = defaultdict(list)
    for r in rows:
        by_bot[r["bot_name"]].append(r)

    need = {(r["match_id"], r["market"], r["cbook"])
            for r in rows if r["cbook"] and r["clv"] is not None}
    mg = margins(need)
    # Report the INTERSECTION, not len(mg): `margins` queries a cross-product of
    # the fixtures/markets/books it was asked about, so it legitimately resolves
    # combinations no pick used, and printing its raw size reads as >100%.
    print(f"own-book closing margins resolved: {len(set(need) & set(mg)):,} of "
          f"{len(need):,} (fixture,market,book) keys needed\n")

    for bot in sorted(by_bot):
        rs = by_bot[bot]
        settled = [r for r in rs if r["result"] is not None and r["stake"]]
        roi = clustered([(r["pnl"] or 0.0) / r["stake"] for r in settled],
                        [r["match_id"] for r in settled]) if settled else None
        books = defaultdict(int)
        for r in rs:
            books[r["book"] or "?"] += 1
        own = [r for r in rs if r["cbook"] and r["clv"] is not None]
        raw = clustered([r["clv"] for r in own], [r["match_id"] for r in own]) if own else None
        mc, mcc = [], []
        for r in own:
            m = mg.get((r["match_id"], r["market"], r["cbook"]))
            if m is None:
                continue
            mc.append((1.0 + r["clv"]) / (1.0 + m) - 1.0)
            mcc.append(r["match_id"])
        mcr = clustered(mc, mcc)
        ds = sorted(r["d"] for r in rs)
        print(f"{bot}  {'RETIRED' if rs[0]['retired'] else 'ACTIVE'}  "
              f"n={len(rs)}  {ds[0]}..{ds[-1]}")
        print(f"    priced at: " + ", ".join(f"{b} {n}" for b, n in
                                             sorted(books.items(), key=lambda x: -x[1])))
        if roi:
            print(f"    ROI                     n={roi['n']:5d} {roi['mean']*100:+7.2f}% "
                  f"CI[{roi['lo']*100:+7.2f},{roi['hi']*100:+7.2f}]")
        if raw:
            print(f"    own-book CLV, RAW       n={raw['n']:5d} {raw['mean']*100:+7.2f}% "
                  f"CI[{raw['lo']*100:+7.2f},{raw['hi']*100:+7.2f}]   "
                  f"(break-even is the book's own margin, NOT 0)")
        else:
            print("    own-book CLV, RAW       none — no own-book close on any pick")
        if mcr:
            print(f"    own-book CLV, MARGIN-CORRECTED  n={mcr['n']:5d} "
                  f"{mcr['mean']*100:+7.2f}% CI[{mcr['lo']*100:+7.2f},{mcr['hi']*100:+7.2f}] "
                  f"t={mcr['t']:+.2f}")
        else:
            miss = complement(rs[0]["market"]) is None
            print("    own-book CLV, MARGIN-CORRECTED  NOT COMPUTABLE — "
                  + ("no complement defined for this market family (settlement."
                     "_market_complement_selections)" if miss
                     else "no own-book closing complement found for any pick"))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
