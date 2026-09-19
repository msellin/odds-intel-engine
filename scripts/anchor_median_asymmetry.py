#!/usr/bin/env python3
"""ANCHOR-MEDIAN-ASYMMETRY — is "beats Pinnacle on 58% of fixtures" real edge,
or overconfidence?

Read-only. Writes nothing.
    python3 scripts/anchor_median_asymmetry.py --days 90

THE QUESTION
------------
`anchor_book_sharpness_research.py` section C found, after the §9 outlier guard:

    book          mean dLL     median dLL   book win%    t
    Marathonbet    -0.0004       -0.0036      58.3%    -1.08  (tie)
    1xBet          -0.0002       -0.0029      55.8%    -0.44  (tie)
    BetVictor      -0.0001       -0.0029      54.3%    -0.22  (tie)
    Betfair        +0.0005       -0.0059      56.1%    +0.91  (tie)

Four independent books beat Pinnacle on a clear MAJORITY of fixtures while tying
or losing on the MEAN. At n=15,000 a 58.3% win rate is not noise (sign-test
z ~ +15). So the ASYMMETRY is certainly real; what it MEANS is the open question,
and there are two readings with opposite consequences:

  (a) GENUINE — the book prices typical fixtures better than Pinnacle and only
      loses on a thin tail. Then it is a better anchor for the fixtures we
      actually bet, and sweeping it directly is worth the work.
  (b) OVERCONFIDENCE — the book's de-vigged probabilities are pushed too far
      toward the favourite. It then wins slightly on the ~55% of fixtures where
      the favourite wins and loses heavily on upsets. Mean log-loss, a PROPER
      score, correctly reports no improvement; the median is just counting the
      frequent small wins and ignoring the rare large losses. Under this reading
      the book is a WORSE anchor, and the win% is a trap.

A de-vigged wide book is a natural suspect for (b): Marathonbet carries ~11.1%
overround against Pinnacle's ~9.2% on the shared slate, and removing more margin
pushes probabilities further apart.

⚠️ ONE CONFOUND IS ALREADY RULED OUT, which is why these four are testable at
all: they are ALL API-Football-fed, so they share Pinnacle's sweep and its 5.0
min median quote age. The 15-140 min staleness handicap that makes Coolbet /
Epicbet / Unibet-Site uninterpretable does not apply here.

HOW THIS ANSWERS IT — three tests, and (b) predicts all three
------------------------------------------------------------
  1. CALIBRATION (ECE + the curve). Overconfidence is visible directly: high
     predicted-probability bins under-deliver and low bins over-deliver. Pooled
     over all three selections, so each fixture contributes 3 (p, outcome) pairs.
  2. FAVOURITE vs UPSET. Split mean dLL by whether the market favourite won. (b)
     predicts the book wins the favourite subset and loses the upset subset; (a)
     predicts it wins, or at least does not collapse, on both.
  3. CONFIDENCE GAP. Mean top-selection probability minus the actual hit-rate of
     that selection. Positive = overconfident. This is the same quantity as (1)
     reduced to one number, reported because it is the one a reader can check
     against intuition.

  4. STRATIFY BY THE REFERENCE'S OWN OVERROUND, and re-run under BOTH de-vig
     methods. This is the test that actually answered it, and neither (a) nor
     (b) predicted the result — see below.

WHAT IT FOUND (2026-09-19, 90d) — AFTER AN ADVERSARIAL REVIEW THAT BROKE THE
FIRST ANSWER. Read the retraction before the result; the first version of this
header stated the opposite conclusion and rested it on the wrong de-vig.

(b) OVERCONFIDENCE IS REFUTED, and this part survived review. Nothing is
overconfident: every book's confidence gap is NEGATIVE (under-confident) and
Pinnacle's is the most negative of all. Point ECE favours all four books
(0.34-0.58% vs 0.54-0.69%) — but a paired bootstrap 95% CI on that difference
STRADDLES ZERO for all four, and Brier actually favours Pinnacle for BetVictor
and Betfair. So "the books are better calibrated" is DIRECTIONAL AND UNDER-
POWERED, not established. Report it that way.

❌ RETRACTED — "where Pinnacle is sharp, Pinnacle wins". The first version of
this script stratified by Pinnacle's overround under PROPORTIONAL de-vig and
concluded the books only beat Pinnacle on wide quotes, so "the lever is
Pinnacle's own quote quality". Three things killed it:

  1. WRONG DE-VIG. Production is Shin (`devig()` -> `shin_devig()`), and
     devig.py lines 17-26 document proportional as "wrong in a known direction"
     for 3-way markets. Under SHIN there is NO band where Pinnacle wins:
         band     Marathonbet  1xBet  BetVictor  Betfair
         <4%         51.6%     46.0%    58.0%     52.5%
         4-6%        56.7%     49.7%    51.0%     55.2%
         6-9%        61.4%     59.2%    54.2%     57.2%
         >=9%        59.3%     58.8%    54.8%     56.9%
     BetVictor's gradient INVERTS — its best band is <4%.
  2. THE STRATIFIER WAS A PROXY FOR (pin_overround - book_overround). Splitting
     on whether the two arms carry MATCHED margin is decisive:
         matched (|pin-book| < 1.5pp):  48.9 / 51.8 / 56.9 / 58.3  — Shin
                                        48.9 / 51.7 / 55.8 / 58.3  — proportional
     IDENTICAL. When both arms carry the same margin the de-vig choice makes no
     difference at all, and the narrow bands are a TIE (48.9%, z=-0.71, p=0.48),
     not a Pinnacle win. The entire Shin-vs-proportional discrepancy was
     proportional's longshot bias penalising whichever arm was wider — the exact
     mechanism this file's own header describes for the BOOK and never applied
     to the STRATIFIER.
  3. "MONOTONE IN THREE OF FOUR" WAS FALSE. Only Betfair is monotone. The other
     three dip at 4-6%, which is precisely the band where (pin - book) is most
     negative — the artifact's fingerprint.

AND IT IS NOT ABOUT OUR FEED. corr(Pinnacle overround, Bet365 overround) = 0.615:
overround is largely a FIXTURE property. Stratifying by a THIRD book's overround,
which never touches Pinnacle's quote, reproduces the gradient (Marathonbet
50.6 -> 57.4 -> 59.1 under Shin). Residualising Pinnacle's overround on Bet365's
leaves the Shin gradient FLAT (57.7/57.1/58.2/60.4, all above 50%). Median quote
age is 5.0 min in every band for both arms, so there is no staleness mechanism
either.

WHAT ACTUALLY HOLDS
  * Books beat Pinnacle far more often on WIDE-overround fixtures than on narrow
    ones — real, de-vig-independent (matched: 48.9% -> 58.3%), and Bonferroni-safe
    at the top band. This is a statement about FIXTURE TYPE.
  * On narrow fixtures it is a TIE. Pinnacle does not win them.
  * Nothing here identifies OUR Pinnacle feed as the degraded party; the wideness
    is in every book's quote on those fixtures.
  * SWEEPING MARATHONBET OR BETVICTOR STILL BUYS NOTHING as a sharper anchor —
    but for the ORIGINAL reason, that mean dLL is a tie (t=-1.08), NOT for the
    retracted quote-quality reason. Under Shin, Marathonbet wins 58.3% overall
    and >=51.6% in every band, so the win-rate argument for it is if anything
    stronger than the mean suggests. The mean is the proper score; it is a tie;
    that is the answer.

⚠️ DOES NOT LICENSE AN OVERROUND VALIDITY GATE. PICKS-BOARD-WATCHLIST already
found overround banding NON-MONOTONE on ROI (>=9% +13.07% vs 4-6% -16.18%) and
FLAT on calibration. That is a different question from this one, and the
reconciliation is not "both can be true" — it is that NEITHER result supports the
gate, because overround band does not index Pinnacle-specific quality at all.

⚠️ EFFECT SIZES ARE TINY EITHER WAY: dLL ~0.001 against a base of ~0.98.

METHOD NOTE FOR WHOEVER EXTENDS THIS: report Shin first because it is production,
always show the matched-overround control next to any cross-book split, and
attach a binomial test to every win-rate. 32 simultaneous comparisons here
(4 books x 4 bands x 2 de-vigs); Bonferroni alpha = 0.00156.
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
from workers.model.devig import devig, shin_devig, proportional_devig  # noqa: E402

SIDES = ("home", "draw", "away")
REFERENCE = "Pinnacle"
# The four books that showed the asymmetry, all AF-fed (same sweep as Pinnacle).
CANDIDATES = ("Marathonbet", "1xBet", "BetVictor", "Betfair")
# Same guard and same meaning as anchor_book_sharpness_research.OUTLIER_MAX_RATIO.
OUTLIER_MAX_RATIO = 1.25
EXCLUDED = ("Unibet", "Unibet-Kambi", "Max", "Avg", "Betfair Exchange", "BetWin", "Betfred")
IDX = {"home": 0, "draw": 1, "away": 2}
NBINS = 10


def load(days: int) -> tuple[dict, dict]:
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.bookmaker, o.match_id, o.selection)
               o.bookmaker AS bk, o.match_id::text AS mid,
               lower(o.selection) AS sel, o.odds::float AS odds, m.result AS res
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.market = '1x2' AND o.is_live IS NOT TRUE AND o.odds > 1.01
           AND o.timestamp <= m.date
           AND m.date >= NOW() - (%s * INTERVAL '1 day')
           AND m.status = 'finished' AND m.result IS NOT NULL
           AND lower(o.selection) = ANY(%s)
           AND o.bookmaker = ANY(%s)
         ORDER BY o.bookmaker, o.match_id, o.selection, o.timestamp DESC
        """,
        [days, list(SIDES), list((REFERENCE,) + CANDIDATES)],
    )
    triples: dict = defaultdict(dict)
    results: dict = {}
    for r in rows:
        triples[(r["bk"], r["mid"])][r["sel"]] = r["odds"]
        results[r["mid"]] = r["res"]
    return {k: v for k, v in triples.items() if len(v) == 3}, results


def ece(pairs: list[tuple[float, int]]) -> tuple[float, list]:
    """Expected calibration error over (predicted p, hit 0/1), plus the curve."""
    bins: dict = defaultdict(list)
    for p, y in pairs:
        bins[min(int(p * NBINS), NBINS - 1)].append((p, y))
    n = len(pairs)
    e, curve = 0.0, []
    for b in range(NBINS):
        v = bins.get(b, [])
        if not v:
            continue
        pm = sum(p for p, _ in v) / len(v)
        am = sum(y for _, y in v) / len(v)
        e += (len(v) / n) * abs(pm - am)
        curve.append((b / NBINS, (b + 1) / NBINS, len(v), pm, am))
    return e, curve


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    a = ap.parse_args()

    triples, results = load(a.days)
    ref_q = {mid: q for (bk, mid), q in triples.items() if bk == REFERENCE}
    print(f"ANCHOR-MEDIAN-ASYMMETRY — read-only. window {a.days}d, "
          f"{len(ref_q)} fixtures with a {REFERENCE} triple\n")

    for bk in CANDIDATES:
        own = {mid: q for (b, mid), q in triples.items() if b == bk}
        shared, dropped = [], 0
        for mid, q in own.items():
            rq = ref_q.get(mid)
            k = IDX.get(str(results.get(mid)))
            if not rq or k is None:
                continue
            if max(max(q[s] / rq[s], rq[s] / q[s]) for s in SIDES) > OUTLIER_MAX_RATIO ** 2:
                dropped += 1
                continue
            pb, pr = devig([q[s] for s in SIDES]), devig([rq[s] for s in SIDES])
            if not pb or not pr or min(pb) <= 0 or min(pr) <= 0:
                continue
            shared.append((mid, pb, pr, k))
        if len(shared) < 500:
            print(f"{bk}: only {len(shared)} paired fixtures — skipped\n")
            continue

        d = [-math.log(pb[k]) + math.log(pr[k]) for _, pb, pr, k in shared]
        n = len(d)
        mean = sum(d) / n
        var = sum((x - mean) ** 2 for x in d) / (n - 1)
        t = mean / math.sqrt(var / n)
        win = sum(1 for x in d if x < 0) / n

        # 1 · calibration, pooled over all three selections
        pb_pairs = [(pb[i], 1 if i == k else 0) for _, pb, _, k in shared for i in range(3)]
        pr_pairs = [(pr[i], 1 if i == k else 0) for _, _, pr, k in shared for i in range(3)]
        eb, curve_b = ece(pb_pairs)
        er, curve_r = ece(pr_pairs)

        # 2 · favourite vs upset, favourite defined by the REFERENCE (one
        #     definition for both arms, or the split is not a fair comparison)
        fav = [x for x, (_, _, pr, k) in zip(d, shared) if max(range(3), key=lambda i: pr[i]) == k]
        ups = [x for x, (_, _, pr, k) in zip(d, shared) if max(range(3), key=lambda i: pr[i]) != k]

        # 3 · confidence gap on each book's OWN top selection
        def gap(which):
            tops = [(max(p), 1 if max(range(3), key=lambda i: p[i]) == k else 0)
                    for _, pb, pr, k in shared for p in ((pb if which == "b" else pr),)]
            return sum(p for p, _ in tops) / len(tops) - sum(y for _, y in tops) / len(tops)

        print("=" * 76)
        print(f"{bk}  vs  {REFERENCE}   (n={n}, dropped by §9 guard={dropped})")
        print("=" * 76)
        print(f"  mean dLL {mean:+.5f}  median dLL {median(d):+.5f}  "
              f"{bk} wins {100*win:.1f}%  t={t:+.2f}")
        print(f"\n  1 · CALIBRATION   ECE {bk} {100*eb:.3f}%   vs   {REFERENCE} {100*er:.3f}%"
              f"   -> {'BOOK better' if eb < er else REFERENCE + ' better'}")
        print(f"     {'bin':>12s} {'n':>7s} {bk[:10]+' pred':>16s} {'actual':>8s} {'gap':>8s}"
              f" | {REFERENCE+' pred':>14s} {'actual':>8s} {'gap':>8s}")
        for (lo, hi, nb, pmb, amb), (_, _, nr, pmr, amr) in zip(curve_b, curve_r):
            print(f"     {lo:.1f}-{hi:.1f}{'':>4s} {nb:7d} {pmb:16.3f} {amb:8.3f} "
                  f"{pmb-amb:+8.3f} | {pmr:14.3f} {amr:8.3f} {pmr-amr:+8.3f}")
        fm = sum(fav) / len(fav) if fav else float("nan")
        um = sum(ups) / len(ups) if ups else float("nan")
        print(f"\n  2 · FAVOURITE vs UPSET (favourite = {REFERENCE}'s top pick, both arms)")
        print(f"     favourite won : n={len(fav):6d}  mean dLL {fm:+.5f}"
              f"   ({bk} {'better' if fm < 0 else 'worse'})")
        print(f"     upset         : n={len(ups):6d}  mean dLL {um:+.5f}"
              f"   ({bk} {'better' if um < 0 else 'worse'})")
        print(f"\n  3 · CONFIDENCE GAP (mean top-pick prob − its actual hit rate; + = overconfident)")
        print(f"     {bk}: {gap('b'):+.4f}      {REFERENCE}: {gap('r'):+.4f}")
        print()
    section_4(triples, results, ref_q)


def section_4(triples: dict, results: dict, ref_q: dict) -> None:
    """Book win-rate against the reference, stratified by the REFERENCE's own
    overround, under both de-vig methods. See the header — this is the test that
    separated "the book is sharper" from "our anchor is degraded here"."""
    def band(o: float) -> str:
        return "<4%" if o < 0.04 else ("4-6%" if o < 0.06 else ("6-9%" if o < 0.09 else ">=9%"))

    for name, dv in (("SHIN (production)", shin_devig), ("PROPORTIONAL", proportional_devig)):
        print("=" * 82)
        print(f"4 · DE-VIG = {name}   —   dLL < 0 means the BOOK beats {REFERENCE}")
        print("=" * 82)
        print(f"  {'book':13s} {'pin overround':>14s} {'n':>6s} {'mean dLL':>10s} "
              f"{'med dLL':>10s} {'win%':>7s} {'z':>7s}")
        for bk in CANDIDATES:
            acc: dict = defaultdict(list)
            for (b, mid), q in triples.items():
                if b != bk:
                    continue
                rq, k = ref_q.get(mid), IDX.get(str(results.get(mid)))
                if not rq or k is None:
                    continue
                if max(max(q[s] / rq[s], rq[s] / q[s]) for s in SIDES) > OUTLIER_MAX_RATIO ** 2:
                    continue
                pb, pr = dv([q[s] for s in SIDES]), dv([rq[s] for s in SIDES])
                if not pb or not pr or min(pb) <= 0 or min(pr) <= 0:
                    continue
                acc[band(sum(1.0 / rq[s] for s in SIDES) - 1.0)].append(
                    -math.log(pb[k]) + math.log(pr[k]))
            for bd in ("<4%", "4-6%", "6-9%", ">=9%"):
                d = acc.get(bd, [])
                if len(d) < 300:
                    print(f"  {bk:13s} {bd:>14s} {len(d):6d}   (too few)")
                    continue
                n = len(d)
                w = sum(1 for x in d if x < 0)
                z = (w - n / 2) / math.sqrt(n / 4)          # binomial vs 50%
                star = "" if abs(z) < 3.17 else " *"        # Bonferroni a=.00156 over 32 cells
                print(f"  {bk:13s} {bd:>14s} {n:6d} {sum(d)/n:+10.5f} {median(d):+10.5f} "
                      f"{100*w/n:6.1f}% {z:+7.2f}{star}")
        print()
    section_5(triples, results, ref_q)


def section_5(triples: dict, results: dict, ref_q: dict) -> None:
    """THE CONTROL THAT DECIDES IT — all four books pooled, split by whether the
    two arms carry MATCHED margin.

    Section 4's Shin-vs-proportional disagreement was not information about the
    books. Pinnacle's overround band is near-collinear with
    (pin_overround − book_overround), and proportional de-vig is biased against
    whichever arm is wider (devig.py lines 17-26). Hold the margins level and the
    two methods agree exactly — which is how we know the disagreement was the
    method, not the market. Any future cross-book split must print this."""
    def band(o):
        return "<4%" if o < 0.04 else ("4-6%" if o < 0.06 else ("6-9%" if o < 0.09 else ">=9%"))

    print("=" * 82)
    print("5 · MATCHED-OVERROUND CONTROL (all four books pooled)")
    print("=" * 82)
    for name, dv in (("SHIN (production)", shin_devig), ("PROPORTIONAL", proportional_devig)):
        acc: dict = defaultdict(list)
        for (b, mid), q in triples.items():
            if b not in CANDIDATES:
                continue
            rq, k = ref_q.get(mid), IDX.get(str(results.get(mid)))
            if not rq or k is None:
                continue
            if max(max(q[s] / rq[s], rq[s] / q[s]) for s in SIDES) > OUTLIER_MAX_RATIO ** 2:
                continue
            pb, pr = dv([q[s] for s in SIDES]), dv([rq[s] for s in SIDES])
            if not pb or not pr or min(pb) <= 0 or min(pr) <= 0:
                continue
            po = sum(1.0 / rq[s] for s in SIDES) - 1.0
            bo = sum(1.0 / q[s] for s in SIDES) - 1.0
            key = ("matched" if abs(po - bo) < 0.015 else "unmatched", band(po))
            acc[key].append(-math.log(pb[k]) + math.log(pr[k]))
        print(f"  {name}")
        for grp in ("matched", "unmatched"):
            cells = []
            for bd in ("<4%", "4-6%", "6-9%", ">=9%"):
                d = acc.get((grp, bd), [])
                cells.append(f"{100*sum(1 for x in d if x<0)/len(d):5.1f}% (n={len(d)})"
                             if len(d) >= 300 else "   — (n<300)")
            lbl = "matched |pin−book|<1.5pp" if grp == "matched" else "unmatched"
            print(f"    {lbl:26s} " + "  ".join(cells))
    print("\n  Read the MATCHED rows: if the two de-vigs agree there and disagree")
    print("  elsewhere, the disagreement is the de-vig, not the books. They do agree.")
    print()


if __name__ == "__main__":
    main()
