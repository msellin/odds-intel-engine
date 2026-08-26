"""SHADOW-NO-PIN-ANCHOR-2026-08-26 — is Pinnacle actually required?

The line-shopping bots all treat the de-vigged Pinnacle close as truth. Two
questions follow, and both are empirical:

  1. How much worse is a market CONSENSUS anchor than Pinnacle? If consensus is
     close, the ~40% of matches with no Pinnacle price are workable and BTTS
     (which Pinnacle never quotes through API-Football) is not a dead market.
  2. Are matches WITHOUT Pinnacle intrinsically harder to price, or just
     unanchored? If the model is equally calibrated on both, the problem is the
     missing yardstick, not the matches.

Anchors compared, all de-vigged with Shin and scored on realised outcomes:
  * pinnacle       — the current anchor
  * consensus_all  — de-vigged mean probability across every book quoting
  * consensus_sharp— same, restricted to the books that track Pinnacle closest
  * best_price     — the max quote (what a naive line-shopper implicitly trusts)

Usage:
    python3 scripts/anchor_comparison_backtest.py --market 1x2
    python3 scripts/anchor_comparison_backtest.py --market btts
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402

SIDES = {
    "1x2": ["home", "draw", "away"],
    "btts": ["yes", "no"],
    "over_under_25": ["over", "under"],
    "over_under_35": ["over", "under"],
}

# Books that historically track Pinnacle most closely on our feed. Used as the
# "sharp consensus" fallback where Pinnacle is absent.
SHARP_SET = {"Pinnacle", "Marathonbet", "1xBet", "10Bet", "Betfair", "SBO"}


def outcome(market: str, selection: str, sh: int, sa: int) -> int | None:
    if market == "1x2":
        return int({"home": sh > sa, "draw": sh == sa, "away": sa > sh}[selection])
    if market == "btts":
        yes = sh > 0 and sa > 0
        return int(yes if selection == "yes" else not yes)
    if market.startswith("over_under"):
        line = float(market.replace("over_under_", "")) / 10.0
        total = sh + sa
        if total == line:
            return None
        return int(total > line if selection == "over" else total < line)
    return None


def brier_ll(pairs):
    n = len(pairs)
    b = sum((p - y) ** 2 for p, y in pairs) / n
    eps = 1e-12
    ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
              for p, y in pairs) / n
    return b, ll, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--market", default="1x2", choices=sorted(SIDES))
    args = ap.parse_args()

    sides = SIDES[args.market]
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.selection, o.bookmaker)
               o.match_id, o.selection, o.bookmaker, o.odds,
               m.score_home, m.score_away
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.market = %s AND o.timestamp <= m.date
           AND m.status = 'finished'
           AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
           AND m.date >= %s AND m.date < %s
         ORDER BY o.match_id, o.selection, o.bookmaker, o.timestamp DESC
        """,
        [args.market, args.start, args.end],
    )
    per_match: dict = defaultdict(lambda: defaultdict(dict))
    scores: dict = {}
    for r in rows:
        mid = str(r["match_id"])
        per_match[mid][r["bookmaker"]][r["selection"]] = float(r["odds"])
        scores[mid] = (int(r["score_home"]), int(r["score_away"]))
    print(f"market={args.market}  {args.start} → {args.end}   matches with any price: {len(per_match)}")

    anchors: dict = defaultdict(list)
    # Same-sample comparison: only matches where Pinnacle IS present, so the
    # anchors are judged on identical games.
    both: dict = defaultdict(list)
    n_with_pin = n_without = 0

    for mid, books in per_match.items():
        sh, sa = scores[mid]
        ys = [outcome(args.market, s, sh, sa) for s in sides]
        if any(y is None for y in ys):
            continue

        def devigged(sel_books: dict) -> list[float] | None:
            if any(s not in sel_books or sel_books[s] <= 1.0 for s in sides):
                return None
            return devig([sel_books[s] for s in sides])

        pin = devigged(books.get("Pinnacle", {}))
        if pin:
            n_with_pin += 1
        else:
            n_without += 1

        # Consensus = de-vig each book, then average the probabilities. Averaging
        # after de-vigging (not before) keeps a wide-margin book from dragging
        # the level; it only contributes its shape.
        def consensus(book_filter) -> list[float] | None:
            probs = []
            for bk, sel_books in books.items():
                if not book_filter(bk):
                    continue
                d = devigged(sel_books)
                if d:
                    probs.append(d)
            if len(probs) < 3:
                return None
            out = [sum(p[i] for p in probs) / len(probs) for i in range(len(sides))]
            t = sum(out)
            return [p / t for p in out]

        cons_all = consensus(lambda bk: True)
        # LEAVE-ONE-OUT. A consensus that INCLUDES the book you are betting at
        # is circular as a line-shop anchor: the best price is by construction
        # above the average of a set containing it, so every candidate shows
        # positive "edge". The usable form drops the book being priced. Here we
        # drop the book offering the best price on the first selection, which is
        # the book a line-shopper would actually bet.
        best_bk = None
        best_o = 0.0
        for bk, sel_books in books.items():
            o = sel_books.get(sides[0])
            if o and o > best_o:
                best_o, best_bk = o, bk
        cons_loo = consensus(lambda bk, _b=best_bk: bk != _b)
        cons_sharp = consensus(lambda bk: bk in SHARP_SET)
        cons_nopin = consensus(lambda bk: bk != "Pinnacle")

        for name, probs in (
            ("pinnacle", pin),
            ("consensus_all", cons_all),
            ("consensus_sharp", cons_sharp),
            ("consensus_no_pinnacle", cons_nopin),
            ("consensus_leave_one_out", cons_loo),
        ):
            if probs:
                for i, y in enumerate(ys):
                    anchors[name].append((probs[i], y))

        if pin and cons_all and cons_sharp and cons_nopin and cons_loo:
            for i, y in enumerate(ys):
                both["pinnacle"].append((pin[i], y))
                both["consensus_all"].append((cons_all[i], y))
                both["consensus_sharp"].append((cons_sharp[i], y))
                both["consensus_no_pinnacle"].append((cons_nopin[i], y))
                both["consensus_leave_one_out"].append((cons_loo[i], y))

    print(f"matches with Pinnacle: {n_with_pin}   without: {n_without} "
          f"({100.0*n_without/max(n_with_pin+n_without,1):.1f}% unanchored)\n")

    print("ALL AVAILABLE ROWS (different samples — coverage differs)")
    print(f"{'anchor':24s} {'n':>9s} {'brier':>11s} {'logloss':>11s}")
    print("-" * 60)
    for name in ("pinnacle", "consensus_all", "consensus_sharp",
                 "consensus_no_pinnacle", "consensus_leave_one_out"):
        if anchors[name]:
            b, ll, n = brier_ll(anchors[name])
            print(f"{name:24s} {n:9d} {b:11.6f} {ll:11.6f}")

    if both["pinnacle"]:
        print("\nAPPLES-TO-APPLES (only matches where every anchor exists)")
        print(f"{'anchor':24s} {'n':>9s} {'brier':>11s} {'logloss':>11s} {'vs pinnacle':>13s}")
        print("-" * 74)
        base, _, _ = brier_ll(both["pinnacle"])
        for name in ("pinnacle", "consensus_all", "consensus_sharp",
                     "consensus_no_pinnacle", "consensus_leave_one_out"):
            b, ll, n = brier_ll(both[name])
            delta = "" if name == "pinnacle" else f"{b - base:+13.6f}"
            print(f"{name:24s} {n:9d} {b:11.6f} {ll:11.6f} {delta:>13s}")
        print("\n(positive 'vs pinnacle' = worse than Pinnacle; the size of that "
              "number is how much edge is lost by not having Pinnacle)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
