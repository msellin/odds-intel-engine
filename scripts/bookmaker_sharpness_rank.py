"""SHADOW-NO-PIN-ANCHOR-2026-08-26 — which books in the AF feed are actually sharp?

Pinnacle is treated as the reference anchor everywhere in this codebase, but that
is an inherited assumption, not something measured on our own data. And it has a
hard limit: API-Football's Pinnacle feed carries only 8 bet types, so BTTS has no
Pinnacle price at all and never will through this provider.

So: rank every bookmaker by how well its de-vigged closing prices predict real
outcomes. Brier and log loss on identical matches. A book that scores close to
Pinnacle is usable as a reference where Pinnacle is missing.

Usage:
    python3 scripts/bookmaker_sharpness_rank.py --market 1x2
    python3 scripts/bookmaker_sharpness_rank.py --market btts
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


def outcome(market, selection, sh, sa):
    if market == "1x2":
        return int({"home": sh > sa, "draw": sh == sa, "away": sa > sh}[selection])
    if market == "btts":
        yes = sh > 0 and sa > 0
        return int(yes if selection == "yes" else not yes)
    if market.startswith("over_under"):
        line = float(market.replace("over_under_", "")) / 10.0
        t = sh + sa
        if t == line:
            return None
        return int(t > line if selection == "over" else t < line)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--market", default="1x2", choices=sorted(SIDES))
    ap.add_argument("--min-n", type=int, default=3000)
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
    per: dict = defaultdict(lambda: defaultdict(dict))
    scores: dict = {}
    for r in rows:
        mid = str(r["match_id"])
        per[mid][r["bookmaker"]][r["selection"]] = float(r["odds"])
        scores[mid] = (int(r["score_home"]), int(r["score_away"]))

    # Overround is reported too: a tight margin is the usual proxy for sharpness,
    # and it is worth seeing whether it actually tracks calibration here.
    acc: dict = defaultdict(list)
    acc_by_match: dict = defaultdict(dict)
    vig: dict = defaultdict(list)
    for mid, books in per.items():
        sh, sa = scores[mid]
        ys = [outcome(args.market, s, sh, sa) for s in sides]
        if any(y is None for y in ys):
            continue
        for bk, sel in books.items():
            if any(s not in sel or sel[s] <= 1.0 for s in sides):
                continue
            probs = devig([sel[s] for s in sides])
            if not probs:
                continue
            vig[bk].append(sum(1.0 / sel[s] for s in sides) - 1.0)
            rows_here = [(probs[i], y) for i, y in enumerate(ys)]
            acc[bk].extend(rows_here)
            acc_by_match[bk][mid] = rows_here

    # PAIRWISE vs PINNACLE, on matches BOTH books price.
    #
    # Ranking books by raw Brier is invalid: Brier depends on how hard the games
    # are, and each book quotes a different slate. A book that only prices top
    # leagues gets a better Brier for free. The first version of this script had
    # that bug and put Pinnacle 14th of 15 — an artefact of 888Sport quoting
    # 14,679 rows against Pinnacle's 51,921, not a real ranking.
    pin_by_match = acc_by_match.get("Pinnacle", {})
    pairwise = []
    for bk, per_match_pairs in acc_by_match.items():
        if bk == "Pinnacle":
            continue
        shared = set(per_match_pairs) & set(pin_by_match)
        if len(shared) < 300:
            continue
        mine, theirs = [], []
        for mid in shared:
            mine.extend(per_match_pairs[mid])
            theirs.extend(pin_by_match[mid])
        n = len(mine)
        bm = sum((p - y) ** 2 for p, y in mine) / n
        bp = sum((p - y) ** 2 for p, y in theirs) / n
        # Paired per-match differences give a t-stat: the same games, so the
        # match-difficulty term cancels instead of contaminating the comparison.
        diffs = []
        for mid in shared:
            a = sum((p - y) ** 2 for p, y in per_match_pairs[mid]) / len(per_match_pairs[mid])
            b = sum((p - y) ** 2 for p, y in pin_by_match[mid]) / len(pin_by_match[mid])
            diffs.append(a - b)
        k = len(diffs)
        md = sum(diffs) / k
        vd = sum((x - md) ** 2 for x in diffs) / (k - 1)
        se = math.sqrt(vd / k)
        pairwise.append((md, bk, len(shared), n, bm, bp, md / se if se else 0.0))
    pairwise.sort()

    print("PAIRWISE vs PINNACLE — only matches BOTH books price (this is the "
          "valid comparison)\n")
    print(f"{'bookmaker':16s} {'matches':>8s} {'book brier':>11s} {'pin brier':>11s}"
          f" {'diff':>10s} {'t':>7s}   verdict")
    print("-" * 80)
    for md, bk, nm, n, bm, bp, t in pairwise:
        verdict = ("sharper than Pinnacle" if t <= -1.96
                   else "worse than Pinnacle" if t >= 1.96
                   else "indistinguishable")
        print(f"{bk:16s} {nm:8d} {bm:11.6f} {bp:11.6f} {md:+10.6f} {t:+7.2f}   {verdict}")
    print("\n(negative diff = book beats Pinnacle on the shared slate; "
          "|t| >= 1.96 is significant)\n")

    out = []
    for bk, pairs in acc.items():
        n = len(pairs)
        if n < args.min_n:
            continue
        b = sum((p - y) ** 2 for p, y in pairs) / n
        eps = 1e-12
        ll = -sum(y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps))
                  for p, y in pairs) / n
        out.append((b, ll, n, bk, 100.0 * sum(vig[bk]) / len(vig[bk])))
    out.sort()

    print(f"market={args.market}   {args.start} → {args.end}\n")
    print("RAW BRIER — NOT a valid ranking, shown only to make the confound "
          "visible.\nEach book prices a different slate, so this partly measures "
          "how hard its games are.\n")
    print(f"{'rank':>4s} {'bookmaker':16s} {'n':>8s} {'brier':>10s} {'logloss':>10s}"
          f" {'overround':>10s} {'vs Pinnacle':>12s}")
    print("-" * 76)
    pin = next((o for o in out if o[3] == "Pinnacle"), None)
    for i, (b, ll, n, bk, ov) in enumerate(out, 1):
        delta = f"{b - pin[0]:+12.6f}" if pin and bk != "Pinnacle" else ("reference" if pin else "")
        star = "  <-- sharpest" if i == 1 else ""
        print(f"{i:4d} {bk:16s} {n:8d} {b:10.6f} {ll:10.6f} {ov:9.2f}% {delta:>12s}{star}")
    if not pin:
        print("\nNOTE: Pinnacle does not quote this market in the API-Football feed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
