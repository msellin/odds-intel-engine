"""SHADOW-OU-EDGE-AUDIT-2026-08-26 — is the OU line-shop "edge" a mislabelled line?

The OU line-shopping bots select on a de-vigged edge of 11-13% against Pinnacle,
and the Pinnacle close does not move toward them at all by kickoff. An edge that
survives to the close is not a mispricing — a real one gets arbitraged away. The
alternatives are that the price is unobtainable, or that the two books are not
quoting the same thing.

The test: for each pick, take the soft book's quote on its stated line and ask
which of PINNACLE'S lines it actually matches best. If a book's "over 2.5" price
sits closest to Pinnacle's over 3.0, the line is mislabelled and the edge is an
accounting artefact, not value.

Usage:
    python3 scripts/ou_line_integrity_audit.py
    python3 scripts/ou_line_integrity_audit.py --book Coolbet
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402

# Every OU line Pinnacle quotes often enough to compare against.
CANDIDATE_LINES = [
    "over_under_15", "over_under_175", "over_under_20", "over_under_225",
    "over_under_25", "over_under_275", "over_under_30", "over_under_325",
    "over_under_35", "over_under_375", "over_under_40", "over_under_45",
]


def line_value(market: str) -> float:
    return float(market.replace("over_under_", "")) / 10.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", default=None)
    ap.add_argument("--start", default="2026-08-01")
    args = ap.parse_args()

    picks = execute_query(
        """
        SELECT s.id, s.match_id, s.market, s.selection, s.odds_at_pick,
               s.recommended_bookmaker AS bk, b.name AS bot, s.result
          FROM shadow_bets_unique s
          JOIN bots b ON b.id = s.bot_id
         WHERE b.name IN ('bot_sweep_ou25_v1','bot_sweep_ou35_v1')
           AND s.result IN ('won','lost')
           AND s.recommended_bookmaker IS NOT NULL
           AND s.pick_time >= %s
        """,
        [args.start],
    )
    if args.book:
        picks = [p for p in picks if p["bk"] == args.book]
    print(f"{len(picks)} settled OU line-shop picks since {args.start}")
    if not picks:
        return 0

    mids = sorted({str(p["match_id"]) for p in picks})
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.selection)
               o.match_id, o.market, o.selection, o.odds
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = 'Pinnacle'
           AND o.market = ANY(%s) AND o.timestamp <= m.date
         ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
        """,
        [mids, CANDIDATE_LINES],
    )
    pin: dict = {}
    for r in rows:
        pin[(str(r["match_id"]), r["market"], r["selection"])] = float(r["odds"])
    print(f"loaded {len(pin)} Pinnacle OU prices across {len(CANDIDATE_LINES)} lines\n")

    by_book: dict = defaultdict(lambda: defaultdict(int))
    totals: dict = defaultdict(int)
    no_alt = 0

    for p in picks:
        mid, sel = str(p["match_id"]), p["selection"]
        stated = p["market"]
        ours = float(p["odds_at_pick"])
        # Which Pinnacle line is our quote closest to, in probability terms?
        best_line, best_gap = None, None
        for cand in CANDIDATE_LINES:
            o = pin.get((mid, cand, sel))
            if not o or o <= 1.0:
                continue
            gap = abs(1.0 / ours - 1.0 / o)
            if best_gap is None or gap < best_gap:
                best_gap, best_line = gap, cand
        if best_line is None:
            no_alt += 1
            continue
        by_book[p["bk"]][best_line == stated] += 1
        totals[p["bk"]] += 1
        if best_line != stated:
            by_book[p["bk"]][f"->{best_line}"] += 1

    print("Which Pinnacle line does each book's quote actually match?")
    print(f"{'bookmaker':14s} {'picks':>6s} {'matches stated line':>21s} {'drifts elsewhere':>18s}")
    print("-" * 66)
    for bk in sorted(totals, key=lambda b: -totals[b]):
        n = totals[bk]
        ok = by_book[bk][True]
        print(f"{bk:14s} {n:6d} {ok:14d} ({100.0*ok/n:4.0f}%) {n-ok:12d} ({100.0*(n-ok)/n:4.0f}%)")
        drift = {k: v for k, v in by_book[bk].items() if isinstance(k, str) and k.startswith("->")}
        for k, v in sorted(drift.items(), key=lambda kv: -kv[1])[:4]:
            print(f"{'':16s} nearest Pinnacle line {k[2:]:16s} {v:4d} pick(s)")
    if no_alt:
        print(f"\n{no_alt} picks had no comparable Pinnacle OU price at all")

    print("\nReading this: a book whose quotes mostly match the STATED line is "
          "pricing\nthe same thing we are, and its gap to Pinnacle is real "
          "disagreement. A book that\nconsistently lands on a DIFFERENT Pinnacle "
          "line is mislabelled, and the 'edge'\nagainst it is an accounting "
          "artefact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
