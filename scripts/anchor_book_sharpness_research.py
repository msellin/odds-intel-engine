#!/usr/bin/env python3
"""ANCHOR-BOOK-SHARPNESS — which book should be our fair-value anchor?

Read-only. Writes nothing. Run:
    python3 scripts/anchor_book_sharpness_research.py            # today's slate + history
    python3 scripts/anchor_book_sharpness_research.py --days 120

WHY THIS EXISTS
---------------
`scripts/anchor_sharpness_check.py` (2026-09-14) produced the finding that our
"Pinnacle" feed carries a 9.18% median 1X2 overround — wider than Coolbet. That
result stands, but the script has TWO limits this one removes:

  1. IT IS NOT PAIRED. It groups by bookmaker over each book's OWN slate, which
     is ANALYSIS_GOTCHAS §10 — the exact trap that produced, and then retracted,
     the "Pinnacle is the widest book" claim. Every number here is reported on a
     stated, shared fixture set.
  2. IT MEASURES MARGIN, NOT SHARPNESS. Overround is what a book CHARGES. It is
     a necessary condition for sharpness and not the thing itself: a book can be
     cheap and wrong. The only test that establishes "sharper" is whether the
     de-vigged probability predicts the outcome better, which needs settled
     fixtures — section C.

THREE SECTIONS, and they answer different questions:

  A. TODAY, PAIRED       what each book charges on the SAME fixtures, right now.
                         Answers "who is cheap today", nothing more.
  B. TODAY, CONSENSUS    how far each book's de-vigged line sits from the
                         leave-one-out consensus of every other book. A book that
                         hugs consensus is priced off the market; an outlier is
                         either sharper than everyone or lazier than everyone,
                         and section A cannot tell those apart. Diagnostic only.
  C. HISTORY, OUTCOMES   Shin-de-vigged log-loss and Brier against the actual
                         result, paired on the fixtures both books priced.
                         ⚠️ THIS IS THE ONLY SECTION THAT ESTABLISHES SHARPNESS.

METHOD NOTES — each is a trap this repo has already paid for.

* PAIRED OR IT DID NOT HAPPEN (§10). Sections A and C report every book on its
  intersection with the reference, and print that n. A median over a book's own
  slate is comparable to nothing.
* LATEST PRE-KICKOFF, NEVER LIVE. `is_live IS NOT TRUE` and `timestamp <= date`.
  An in-play price in a pre-match comparison is a different quantity.
* PHANTOM BOOKS EXCLUDED. 'Unibet' (AF feed, 33.1% of quotes higher than the site
  actually offers), 'Unibet-Kambi' (38%), and the synthetic aggregates
  Max / Avg / Betfair Exchange / BetWin / Betfred.
* SHIN, NOT PROPORTIONAL (workers/model/devig.py). Proportional de-vig on a
  3-way market takes too little margin off the longshot, which manufactures
  apparent probability on draws and away dogs — the selections that lose.
* LOG-LOSS IS PAIRED AND SO IS ITS TEST. Reporting two books' log-loss over
  different fixtures compares slates, not books. The per-fixture DIFFERENCE is
  what carries the paired t.
* ⚠️ THE OUTLIER GUARD IS LOAD-BEARING (ANALYSIS_GOTCHAS §9, §58) — added
  2026-09-19 after an adversarial review of this script's FIRST result. Without
  it the script reported "Pinnacle beats Coolbet, dLL +0.0086, t=+4.26" on
  n=7,043. That number was **68 fixtures**: the top 50 carried 92% of the total,
  the median was NEGATIVE (Coolbet better) and Coolbet won 50.6% of fixtures.
  Applying the repo's own production guard collapses it to dLL -0.0001, t=-0.14
  — a dead tie, which is what docs/ANCHOR_IS_NOT_SHARP_2026_09_14.md already
  said. §58 states it verbatim: a mean and a rate that disagree "is never a
  subtle finding; it is outliers." A proper score does not excuse skipping it.
* AND THE OUTLIERS ARE A REAL BUG, NOT NOISE. They are HOME/AWAY INVERSIONS in
  the feeds we scrape OURSELVES (Coolbet 68 fixtures, Epicbet 39, Unibet-Site 5;
  ZERO on every API-Football-sourced book). `--show-outliers` prints them. See
  SELF-SCRAPED-1X2-ORIENTATION-FAULT in PRIORITY_QUEUE.md.
* ⚠️ A REMAINING CONFOUND THIS SCRIPT CANNOT REMOVE, stated rather than hidden:
  the books are not on a common clock. Median age of the "latest pre-kickoff"
  quote is ~5 min for Pinnacle and every AF-fed book (one sweep), but 14.8 min
  for Unibet-Site, 83.9 for Coolbet and 144.4 for Epicbet. Pinnacle is therefore
  handed 75-140 extra minutes of market movement on exactly the three rows the
  operator cares about. Re-aligning is not possible in this data (§59(a)
  retention has pruned the intermediate rows). So section C is INFORMATIVE for
  the AF-fed books and INCONCLUSIVE for the three we bet.
* ROWS ARE NOT COMPARABLE TO EACH OTHER. Each is a valid paired test of ONE book
  against Pinnacle on ITS OWN intersection — Pinnacle's own log-loss ranges
  0.9699 to 0.9887 across those slates. Read down the t column, never across
  the LL columns, and note 15 simultaneous tests carry ~0.75 expected false
  positives at |t| > 1.96 with no correction applied.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.supabase_client import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402

EXCLUDED_BOOKS = ("Unibet", "Unibet-Kambi", "Max", "Avg", "Betfair Exchange",
                  "BetWin", "Betfred")
SIDES = ("home", "draw", "away")
OUR_BOOKS = {"Coolbet", "Epicbet", "Unibet-Site", "Betano"}
REFERENCE = "Pinnacle"
MIN_PAIRED_N = 100          # below this a paired median is not worth printing
# ANALYSIS_GOTCHAS §9 — the guard the PRODUCTION line-shop path already applies
# to 1X2. A soft price above Pinnacle's by more than this factor is a data fault,
# not an opportunity. Symmetric here because we are comparing, not shopping.
OUTLIER_MAX_RATIO = 1.25


def _fetch(where_extra: str, params: list, need_finished: bool) -> dict:
    """Latest pre-kickoff 1X2 triple per (book, fixture). Returns {(book, mid): {sel: odds}}."""
    rows = execute_query(
        f"""
        SELECT DISTINCT ON (o.bookmaker, o.match_id, o.selection)
               o.bookmaker AS bk, o.match_id::text AS mid,
               lower(o.selection) AS sel, o.odds::float AS odds,
               m.result AS res
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.market = '1x2'
           AND o.is_live IS NOT TRUE
           AND o.odds > 1.01
           AND o.timestamp <= m.date
           AND lower(o.selection) = ANY(%s)
           AND NOT (o.bookmaker = ANY(%s))
           {'AND m.status = %s AND m.result IS NOT NULL' if need_finished else ''}
           {where_extra}
         ORDER BY o.bookmaker, o.match_id, o.selection, o.timestamp DESC
        """,
        [list(SIDES), list(EXCLUDED_BOOKS)] + (['finished'] if need_finished else []) + params,
    )
    out: dict = defaultdict(dict)
    results: dict = {}
    for r in rows:
        out[(r["bk"], r["mid"])][r["sel"]] = r["odds"]
        results[r["mid"]] = r["res"]
    # only complete triples survive — a partial complement cannot be de-vigged
    return ({k: v for k, v in out.items() if len(v) == 3}, results)


def _overround(q: dict) -> float:
    return sum(1.0 / q[s] for s in SIDES) - 1.0


def _probs(q: dict) -> list[float] | None:
    return devig([q[s] for s in SIDES])


def section_a(triples: dict) -> set:
    """What each book CHARGES, on its own slate and paired against the reference."""
    print("\n" + "=" * 78)
    print("A · TODAY — 1X2 overround, own slate vs the slate shared with " + REFERENCE)
    print("=" * 78)
    by_book: dict = defaultdict(dict)
    for (bk, mid), q in triples.items():
        by_book[bk][mid] = _overround(q)
    ref = by_book.get(REFERENCE, {})
    if not ref:
        print(f"  no {REFERENCE} triples today — cannot pair")
        return set()

    rows = []
    for bk, per in by_book.items():
        shared = set(per) & set(ref)
        if len(per) < 30:
            continue
        rows.append((
            bk, len(per), 100 * median(per.values()),
            len(shared),
            100 * median([per[m] for m in shared]) if shared else None,
            100 * median([ref[m] for m in shared]) if shared else None,
        ))
    rows.sort(key=lambda r: (r[4] if r[4] is not None else 999))
    print(f"  {'book':18s} {'own n':>6s} {'own med':>8s} | {'shared n':>8s} "
          f"{'book':>8s} {REFERENCE:>9s} {'book−ref':>9s}")
    print("  " + "-" * 74)
    for bk, n, own, sn, bo, ro in rows:
        tag = " ←we bet" if bk in OUR_BOOKS else ("  <<REF" if bk == REFERENCE else "")
        if bo is None or sn < MIN_PAIRED_N:
            print(f"  {bk:18s} {n:6d} {own:7.2f}% | {sn:8d} {'—':>8s} {'—':>9s} {'—':>9s}{tag}")
        else:
            print(f"  {bk:18s} {n:6d} {own:7.2f}% | {sn:8d} {bo:7.2f}% {ro:8.2f}% "
                  f"{bo-ro:+8.2f}pp{tag}")
    print(f"\n  Read the RIGHT half only. 'own med' is each book's own slate and is")
    print(f"  comparable to nothing (ANALYSIS_GOTCHAS §10) — it is printed so the")
    print(f"  difference between the two halves is visible.")
    return {r[0] for r in rows if r[3] >= MIN_PAIRED_N}


def section_b(triples: dict, books: set) -> None:
    """How far each book sits from the leave-one-out consensus of the others."""
    print("\n" + "=" * 78)
    print("B · TODAY — distance from the leave-one-out consensus (diagnostic only)")
    print("=" * 78)
    per_fixture: dict = defaultdict(dict)
    for (bk, mid), q in triples.items():
        if bk not in books:
            continue
        p = _probs(q)
        if p:
            per_fixture[mid][bk] = p

    dev: dict = defaultdict(list)
    for mid, byb in per_fixture.items():
        if len(byb) < 5:        # a consensus of <4 others is not a consensus
            continue
        for bk, p in byb.items():
            others = [v for k, v in byb.items() if k != bk]
            cons = [sum(o[i] for o in others) / len(others) for i in range(3)]
            dev[bk].append(sum(abs(p[i] - cons[i]) for i in range(3)) / 3)

    rows = sorted(((bk, len(v), 100 * sum(v) / len(v)) for bk, v in dev.items()
                   if len(v) >= MIN_PAIRED_N), key=lambda r: r[2])
    print(f"  {'book':18s} {'n':>6s} {'mean |p − LOO consensus|':>26s}")
    print("  " + "-" * 52)
    for bk, n, d in rows:
        tag = " ←we bet" if bk in OUR_BOOKS else ("  <<REF" if bk == REFERENCE else "")
        print(f"  {bk:18s} {n:6d} {d:24.3f}pp{tag}")
    print("\n  ⚠️ NOT a sharpness ranking. Hugging consensus means priced off the")
    print("  market; deviating means either sharper than everyone or lazier than")
    print("  everyone, and this statistic cannot tell those apart. Section C can.")


def section_c(triples: dict, results: dict) -> None:
    """The only section that establishes sharpness: does the price predict?"""
    print("\n" + "=" * 78)
    print("C · HISTORY — Shin-de-vigged accuracy vs outcome, PAIRED against " + REFERENCE)
    print("=" * 78)
    idx = {"HOME_WIN": 0, "DRAW": 1, "AWAY_WIN": 2, "home": 0, "draw": 1, "away": 2,
           "H": 0, "D": 1, "A": 2, "1": 0, "X": 1, "2": 2}
    ref_q = {mid: q for (bk, mid), q in triples.items() if bk == REFERENCE}
    scored: dict = defaultdict(dict)
    dropped: dict = defaultdict(int)
    outliers: list = []
    for (bk, mid), q in triples.items():
        k = idx.get(str(results.get(mid)))
        if k is None:
            continue
        # §9 GUARD. Drop the fixture when this book's price on ANY selection is
        # more than OUTLIER_MAX_RATIO away from the reference in either
        # direction. Both directions, because a home/away inversion makes one
        # leg far too long AND its partner far too short.
        rq = ref_q.get(mid)
        if bk != REFERENCE and rq:
            worst = max(max(q[s] / rq[s], rq[s] / q[s]) for s in SIDES)
            if worst > OUTLIER_MAX_RATIO ** 2:
                dropped[bk] += 1
                outliers.append((bk, mid, [q[s] for s in SIDES], [rq[s] for s in SIDES]))
                continue
        p = _probs(q)
        if not p or min(p) <= 0:
            continue
        scored[bk][mid] = (-math.log(max(p[k], 1e-9)),
                           sum((p[i] - (1.0 if i == k else 0.0)) ** 2 for i in range(3)))

    globals()["_LAST_DROPPED"] = dict(dropped)
    globals()["_LAST_OUTLIERS"] = outliers
    ref = scored.get(REFERENCE, {})
    if not ref:
        print(f"  no settled {REFERENCE} triples in window")
        return
    print(f"  {'book':18s} {'paired n':>9s} {'dropped':>8s} {'mean ΔLL':>9s} "
          f"{'med ΔLL':>9s} {'book win':>7s} {'t':>7s} {'sharper':>9s}")
    print("  " + "-" * 86)
    rows = []
    for bk, per in scored.items():
        if bk == REFERENCE:
            continue
        shared = sorted(set(per) & set(ref))
        if len(shared) < MIN_PAIRED_N:
            continue
        d = [per[m][0] - ref[m][0] for m in shared]       # book − ref; NEGATIVE = book better
        n = len(d)
        mean = sum(d) / n
        var = sum((x - mean) ** 2 for x in d) / (n - 1)
        t = mean / math.sqrt(var / n) if var > 0 else 0.0
        win = sum(1 for x in d if x < 0) / n               # fixtures the book wins
        rows.append((bk, n, mean, median(d), win, t, _LAST_DROPPED.get(bk, 0)))
    rows.sort(key=lambda r: r[2])
    for bk, n, d, med, win, t, drop in rows:
        verdict = "tie" if abs(t) < 1.96 else (bk[:9] if d < 0 else REFERENCE[:9])
        tag = " ←we bet" if bk in OUR_BOOKS else ""
        print(f"  {bk:18s} {n:9d} {drop:8d} {d:+9.4f} {med:+9.4f} {100*win:6.1f}% "
              f"{t:+7.2f} {verdict:>9s}{tag}")
    print(f"\n  'dropped' = fixtures excluded by the §9 outlier guard (ratio > "
          f"{OUTLIER_MAX_RATIO**2:.2f}x on some leg).")
    print("  ΔLL is the PER-FIXTURE difference (book − reference); negative = book predicts")
    print("  better. MEAN and MEDIAN are both shown on purpose: where they disagree in sign,")
    print("  the mean is a tail, not a finding (§58). 'book win' is the share of fixtures the")
    print("  book beats the reference on. |t| < 1.96 is a tie — the honest answer for most")
    print("  of this table. Rows are NOT comparable to each other (see the header).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90,
                    help="settled-history window for section C (default 90)")
    ap.add_argument("--skip-history", action="store_true")
    ap.add_argument("--show-outliers", action="store_true",
                    help="print the fixtures the §9 guard dropped — they are a DATA FAULT "
                         "worth looking at, not noise to discard quietly")
    a = ap.parse_args()

    print("ANCHOR-BOOK-SHARPNESS — read-only, writes nothing")
    today, _ = _fetch("AND m.date::date = CURRENT_DATE", [], need_finished=False)
    print(f"\ntoday's slate: {len({m for _, m in today})} fixtures with a complete "
          f"1X2 triple at {len({b for b, _ in today})} books "
          f"({len(today)} book-fixture triples)")
    books = section_a(today)
    section_b(today, books)
    if not a.skip_history:
        hist, res = _fetch("AND m.date >= NOW() - (%s * INTERVAL '1 day')",
                           [a.days], need_finished=True)
        print(f"\nsettled history: {len({m for _, m in hist})} fixtures, last {a.days} days")
        section_c(hist, res)
        if a.show_outliers:
            print("\n" + "=" * 78)
            print("OUTLIERS DROPPED BY THE §9 GUARD — inspect these, do not just discard them")
            print("=" * 78)
            print(f"  {'book':14s} {'book h/d/a':26s} {REFERENCE+' h/d/a':26s} {'swap fixes?':>11s}")
            for bk, mid, bq, rq in sorted(_LAST_OUTLIERS)[:60]:
                sw = max(max(bq[i] / rq[2 - i], rq[2 - i] / bq[i]) for i in (0, 2))
                print(f"  {bk:14s} {'/'.join(f'{x:.2f}' for x in bq):26s} "
                      f"{'/'.join(f'{x:.2f}' for x in rq):26s} "
                      f"{('YES' if sw < 1.5 else 'no'):>11s}")


if __name__ == "__main__":
    main()
