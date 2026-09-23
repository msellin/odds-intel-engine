#!/usr/bin/env python3
"""Grade A test + B/C re-test on the UNSEEN window — the implementation of
docs/PUBLISHED_PICKS_GRADING_2026_09_23.md §3, pre-registered before the run.

Reads a `consensus_arm_replay.py --days 146` CSV. Grades every row with the
publisher's OWN `grade_consensus_pick()` (same code that labels live picks),
then evaluates the four pre-registered A candidates on rows with kickoff before
2026-07-29 — the window the B/C rules were never chosen on.

    python3 scripts/consensus_grade_a_test.py /tmp/replay_may.csv
"""
from __future__ import annotations

import csv
import math
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.publish_picks_forward_test import GRADE_PANEL, grade_consensus_pick  # noqa: E402

SEEN_FROM = "2026-07-29"      # the §1 window starts here; everything before is UNSEEN
ESTONIAN = {"Epicbet", "Coolbet", "Unibet-Site", "Unibet"}
HOLM_M = 4


def f(x):
    return float(x) if x not in (None, "", "None") else None


def panel(r) -> dict:
    return {b: f(r.get(f"p_{b.lower()}")) for b in GRADE_PANEL
            if f(r.get(f"p_{b.lower()}")) is not None}


def a4(r) -> bool:
    odds, book = f(r["odds"]), r["bookmaker"]
    others = {b: p for b, p in panel(r).items() if b != book}
    return len(others) >= 4 and all(p * odds - 1 > 0 for p in others.values())


CANDIDATES = {
    "A1 non-Estonian best price": lambda r: r["bookmaker"] not in ESTONIAN,
    "A2 edge 4-6%":               lambda r: 0.04 <= f(r["edge"]) <= 0.06,
    "A3 odds <= 1.6":             lambda r: f(r["odds"]) <= 1.6,
    "A4 full panel agrees":       a4,
}


def stats(rows):
    x = [f(r["pnl"]) for r in rows]
    n = len(x)
    if n < 2:
        return dict(n=n, roi=mean(x) if x else 0.0, se=float("nan"), p=1.0)
    m, se = mean(x), stdev(x) / math.sqrt(n)
    z = m / se if se > 0 else 0.0
    return dict(n=n, roi=m, se=se, p=0.5 * math.erfc(z / math.sqrt(2)))


def fmt(s):
    return f"n={s['n']:5d}  ROI {100*s['roi']:+6.1f}% ± {100*s['se']:4.1f}  p={s['p']:.3f}"


def main() -> int:
    rows = list(csv.DictReader(open(sys.argv[1])))
    for r in rows:
        tier = f(r.get("tier"))
        r["grade"], _ = grade_consensus_pick(f(r["edge"]), f(r["odds"]), r["bookmaker"],
                                             int(tier) if tier is not None else None, panel(r))
        r["ko"] = r["kickoff"][:10]
    unseen = [r for r in rows if r["ko"] < SEEN_FROM]
    seen = [r for r in rows if r["ko"] >= SEEN_FROM]
    print(f"replayed picks: {len(rows):,}   UNSEEN (< {SEEN_FROM}): {len(unseen):,}   "
          f"seen: {len(seen):,}\n")

    print("FIDELITY — seen window should resemble §1 (C -25.6% n=285, B +10.6% n=392):")
    for g in ("B", "C"):
        print(f"  seen   {g}: {fmt(stats([r for r in seen if r['grade'] == g]))}")

    mid = median(r["ko"] for r in unseen)
    h1 = [r for r in unseen if r["ko"] < mid]
    h2 = [r for r in unseen if r["ko"] >= mid]
    print(f"\nB/C RE-TEST on UNSEEN (halves split at {mid}):")
    for g in ("B", "C"):
        g_all = [r for r in unseen if r["grade"] == g]
        print(f"  {g}: {fmt(stats(g_all))}   | h1 {100*stats([r for r in h1 if r['grade']==g])['roi']:+.1f}%"
              f"  h2 {100*stats([r for r in h2 if r['grade']==g])['roi']:+.1f}%")

    B = [r for r in unseen if r["grade"] == "B"]
    b_roi = stats(B)["roi"]
    res = {k: stats([r for r in B if fn(r)]) for k, fn in CANDIDATES.items()}
    order = sorted(res, key=lambda k: res[k]["p"])
    adj, run = {}, 0.0
    for i, k in enumerate(order):
        run = max(run, min(1.0, (HOLM_M - i) * res[k]["p"]))
        adj[k] = run
    print(f"\nGRADE A CANDIDATES on UNSEEN grade-B rows (B ROI {100*b_roi:+.1f}%), Holm m={HOLM_M}:")
    for k, fn in CANDIDATES.items():
        s = res[k]
        r1 = stats([r for r in h1 if r["grade"] == "B" and fn(r)])["roi"]
        r2 = stats([r for r in h2 if r["grade"] == "B" and fn(r)])["roi"]
        ok = adj[k] < 0.05 and s["roi"] > 0 and r1 > 0 and r2 > 0 and s["roi"] > b_roi
        print(f"  {k:28s} {fmt(s)}  Holm {adj[k]:.3f}  h1 {100*r1:+.1f}%  h2 {100*r2:+.1f}%"
              f"  -> {'PASS' if ok else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
