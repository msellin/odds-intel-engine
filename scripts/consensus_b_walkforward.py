#!/usr/bin/env python3
"""Can grade B be made better? Walk-forward — docs/PUBLISHED_PICKS_GRADING_2026_09_23.md §4,
pre-registered before the run.

In each fold the config search sees ONLY earlier months and is scored on the next month, so
every scored pick is out of sample for the config that picked it. Grades come from the
publisher's own grade_consensus_pick() via consensus_grade_a_test.

    python3 scripts/consensus_b_walkforward.py /tmp/replay_may.csv
"""
from __future__ import annotations

import csv
import itertools
import random
import sys
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.consensus_grade_a_test import f, panel  # noqa: E402
from scripts.publish_picks_forward_test import grade_consensus_pick  # noqa: E402

FOLDS = [("2026-05", "2026-06"), ("2026-06", "2026-07"), ("2026-07", "2026-08"), ("2026-08", "2026-09")]
MIN_TRAIN_N = 40
GRID = list(itertools.product(
    (1, 2, 3),                 # n_books_pos_edge >=
    (None, 0.05, 0.03),        # gap_to_second <=
    ("both", "1x2", "over_under_25"),
    (4.0, 2.5),                # max odds
    (None, 1.8, 2.0),          # min odds (owner)
    (None, 6.0),               # lead_h <=
))


def keep(r, cfg) -> bool:
    conf, gap, mkt, mx, mn, lead = cfg
    if f(r["n_books_pos_edge"]) is None or f(r["n_books_pos_edge"]) < conf:
        return False
    g = f(r["gap_to_second"])
    if gap is not None and (g is None or g > gap):
        return False
    if mkt != "both" and r["market"] != mkt:
        return False
    o = f(r["odds"])
    if o > mx or (mn is not None and o < mn):
        return False
    if lead is not None and f(r["lead_h"]) > lead:
        return False
    return True


def roi(rows):
    return mean(f(r["pnl"]) for r in rows) if rows else float("-inf")


def main() -> int:
    rows = list(csv.DictReader(open(sys.argv[1])))
    for r in rows:
        tier = f(r.get("tier"))
        r["grade"], _ = grade_consensus_pick(f(r["edge"]), f(r["odds"]), r["bookmaker"],
                                             int(tier) if tier is not None else None, panel(r))
        # [[#098]] re-tier: the publisher now returns B/C/D. This analysis was
        # pre-registered on the OLD two grades, so map back (old B = new B+C,
        # old C = new D) and a re-run reproduces the recorded numbers.
        r["grade"] = "C" if r["grade"] == "D" else "B"
        r["mo"] = r["kickoff"][:7]
    B = [r for r in rows if r["grade"] == "B"]

    oos_sel, oos_base = [], []
    print(f"{'test':8}{'chosen config (conf, gap, market, max, min, lead)':52}{'train':>14}{'test sel':>16}{'test B':>16}")
    for last_train, test in FOLDS:
        train = [r for r in B if r["mo"] <= last_train]
        te = [r for r in B if r["mo"] == test]
        best, best_roi = None, float("-inf")
        for cfg in GRID:
            k = [r for r in train if keep(r, cfg)]
            if len(k) >= MIN_TRAIN_N and roi(k) > best_roi:
                best, best_roi = cfg, roi(k)
        sel = [r for r in te if keep(r, best)] if best else te
        oos_sel += sel
        oos_base += te
        n_tr = len([r for r in train if best and keep(r, best)])
        print(f"{test:8}{str(best):52}{f'{100*best_roi:+.1f}% n={n_tr}':>14}"
              f"{f'{100*roi(sel):+.1f}% n={len(sel)}':>16}{f'{100*roi(te):+.1f}% n={len(te)}':>16}")

    d = roi(oos_sel) - roi(oos_base)
    # bootstrap the difference over test picks (paired by resampling the base set,
    # selection membership travels with each pick)
    ids = {id(r) for r in oos_sel}
    rng, worse = random.Random(7), 0
    for _ in range(4000):
        smp = [rng.choice(oos_base) for _ in oos_base]
        s_sel = [r for r in smp if id(r) in ids]
        if not s_sel or roi(s_sel) - roi(smp) <= 0:
            worse += 1
    p = worse / 4000
    vol = len(oos_sel) / max(1, len(oos_base))
    ok = d > 0 and p < 0.05 and vol >= 0.40
    print(f"\nPOOLED OUT-OF-SAMPLE: refined {100*roi(oos_sel):+.1f}% (n={len(oos_sel)})  vs  "
          f"plain B {100*roi(oos_base):+.1f}% (n={len(oos_base)})")
    print(f"  difference {100*d:+.1f}pp   bootstrap p={p:.3f}   volume kept {vol:.0%}")
    print(f"  VERDICT: {'PASS' if ok else 'FAIL'}  (needs diff>0, p<0.05, volume>=40%)")

    print("\nDESCRIPTIVE ONLY (not a test) — single dimensions on ALL grade-B rows:")
    for lab, fn in (("confirm >=2 books", lambda r: f(r["n_books_pos_edge"]) >= 2),
                    ("confirm >=3 books", lambda r: f(r["n_books_pos_edge"]) >= 3),
                    ("odds >= 1.8", lambda r: f(r["odds"]) >= 1.8),
                    ("odds >= 2.0", lambda r: f(r["odds"]) >= 2.0),
                    ("odds < 1.8", lambda r: f(r["odds"]) < 1.8),
                    ("1x2 only", lambda r: r["market"] == "1x2"),
                    ("O/U only", lambda r: r["market"] == "over_under_25")):
        k = [r for r in B if fn(r)]
        print(f"  {lab:20s} n={len(k):4d}  ROI {100*roi(k):+6.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
