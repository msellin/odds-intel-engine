#!/usr/bin/env python3
"""HALF-TIME RATINGS ([[#084]] step 1) — derive HT/2H scoring rates per team.

WHY THIS EXISTS
---------------
`matches.ht_score_home/away` and `h2_score_home/away` are filled on **172,439 of
175,450 finished matches (98.28%)** — the highest-coverage non-trivial columns in
the database — and **nothing derives a single feature from them**. `h2_score_*`
has literally zero SELECTs anywhere in the codebase. Meanwhile we store 2.0M
`1x2_1h` odds rows across 9,226 matches from 16 bookmakers and produce no 1H
prediction at all.

WHAT IT PRODUCES, AND WHY IN THIS SHAPE
---------------------------------------
Per team, opponent-adjusted and time-decayed: attack/defence multipliers for
goals scored in the FIRST half and in the SECOND half, separately by venue.

The outputs are deliberately built in BOTH shapes, because
`dev/active/per-market-feature-sets-design.md` establishes that 1x2 lives on the
DIFFERENCE of scoring rates and totals live on the SUM — and our current feature
vector has an explicit `elo_diff` and **no sum term at all**:

    SUM-shaped   (for the O/U and goals heads)
      ht_expected_total   = lam_ht_home + lam_ht_away
      h2_expected_total   = lam_h2_home + lam_h2_away
      ht_share_expected   = ht_expected_total / (ht + h2)   <- WHEN goals arrive

    DIFFERENCE-shaped  (for the 1x2 head)
      ht_expected_diff    = lam_ht_home - lam_ht_away
      h2_expected_diff    = lam_h2_home - lam_h2_away

HALF-LIFE. Defaults to 300 days, not the 30-90 used for match outcome. The source
is Wheatcroft & Sienkiewicz (arXiv:2101.02104), but note (corrected 2026-09-24): their
~300 days is the decay of a shot-CONVERSION model with odds as a regressor (90 days
without odds), not a measured memory for goal ratings. Treat 300 as a choice, not a
published optimum for this rating.

LEAK-FREE BY CONSTRUCTION. Ratings are fitted on matches strictly BEFORE the
`--asof` date; nothing reads a fixture's own result.

Usage:
    python3 scripts/build_half_time_ratings.py --asof 2026-08-20
    python3 scripts/build_half_time_ratings.py --asof 2026-08-20 --half-life 90
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402

IPF_ITERS = 6


class HalfRatings:
    """Opponent-adjusted attack/defence multipliers for ONE half's goals.

    value(home, away) = base_home * att[home] * dfn[away]

    Fitted by iterative proportional fitting on exponentially-decayed history —
    the same construction as scripts/ou_shots_corners_rating.py, which is
    deliberate: a second implementation of a rating is a second definition of it.
    """

    def __init__(self, label: str):
        self.label = label
        self.att = defaultdict(lambda: 1.0)
        self.dfn = defaultdict(lambda: 1.0)
        self.base_h = 1.0
        self.base_a = 1.0

    def fit(self, rows, kh, ka, half_life):
        w = {}
        last = max(r["d"] for r in rows)
        for r in rows:
            age = (last - r["d"]).days
            w[r["id"]] = 0.5 ** (max(age, 0) / half_life)
        tw = sum(w.values()) or 1.0
        self.base_h = sum(w[r["id"]] * r[kh] for r in rows) / tw
        self.base_a = sum(w[r["id"]] * r[ka] for r in rows) / tw
        if self.base_h <= 0 or self.base_a <= 0:
            return
        for _ in range(IPF_ITERS):
            nf, df = defaultdict(float), defaultdict(float)
            na, da = defaultdict(float), defaultdict(float)
            for r in rows:
                ww, h, a = w[r["id"]], r["h"], r["a"]
                nf[h] += ww * r[kh]; df[h] += ww * self.base_h * self.dfn[a]
                nf[a] += ww * r[ka]; df[a] += ww * self.base_a * self.dfn[h]
                na[a] += ww * r[kh]; da[a] += ww * self.base_h * self.att[h]
                na[h] += ww * r[ka]; da[h] += ww * self.base_a * self.att[a]
            for t in list(nf):
                if df[t] > 0:
                    self.att[t] = min(4.0, max(0.25, nf[t] / df[t]))
            for t in list(na):
                if da[t] > 0:
                    self.dfn[t] = min(4.0, max(0.25, na[t] / da[t]))

    def predict(self, h, a):
        return (self.base_h * self.att[h] * self.dfn[a],
                self.base_a * self.att[a] * self.dfn[h])

    def update(self, h, a, obs_h, obs_a, lr=0.06):
        """Online step, applied only AFTER a match has been predicted.

        This is what makes a historical population leak-free WITHOUT refitting
        from scratch at every date. Walking the matches in date order and
        updating after each prediction means a fixture is always scored by
        ratings built strictly from matches that preceded it — the same
        construction as scripts/ou_shots_corners_rating.py, and the reason that
        script could claim leak-freedom by inspection rather than by assertion.
        """
        exp_h, exp_a = self.predict(h, a)
        if exp_h > 0:
            adj = 1 + lr * (obs_h - exp_h) / exp_h
            self.att[h] = min(4.0, max(0.25, self.att[h] * adj))
            self.dfn[a] = min(4.0, max(0.25, self.dfn[a] * adj))
        if exp_a > 0:
            adj = 1 + lr * (obs_a - exp_a) / exp_a
            self.att[a] = min(4.0, max(0.25, self.att[a] * adj))
            self.dfn[h] = min(4.0, max(0.25, self.dfn[h] * adj))


def load(asof):
    """Finished matches with a half-time score, strictly before `asof`.

    `h2_*` is derived rather than read: the stored h2_score_* columns exist at
    the same 98.28% coverage, but deriving FT - HT here means the two can never
    disagree, and it is one less column to trust.
    """
    return execute_query("""
        SELECT m.id, m.date::date AS d, m.home_team_id h, m.away_team_id a,
               m.ht_score_home::float  AS hth,
               m.ht_score_away::float  AS hta,
               (m.score_home - m.ht_score_home)::float AS h2h,
               (m.score_away - m.ht_score_away)::float AS h2a
          FROM matches m
         WHERE m.status = 'finished'
           AND m.ht_score_home IS NOT NULL AND m.ht_score_away IS NOT NULL
           AND m.score_home   IS NOT NULL AND m.score_away   IS NOT NULL
           AND m.score_home >= m.ht_score_home
           AND m.score_away >= m.ht_score_away   -- guard: FT cannot be below HT
           AND m.date < %s
         ORDER BY m.date""", (asof,))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2026-08-20",
                    help="fit on matches strictly BEFORE this date (the residual "
                         "harness cutoff, so the ratings are leak-free for it)")
    ap.add_argument("--half-life", type=float, default=300.0,
                    help="exponential decay in days. 300 for totals, 30-90 for "
                         "match outcome — Wheatcroft & Sienkiewicz. We currently "
                         "share ONE decay across every head, which this exposes.")
    ap.add_argument("--min-matches", type=int, default=5)
    a = ap.parse_args()

    rows = load(a.asof)
    print(f"HALF-TIME RATINGS ([[#084]]) — fitted on matches before {a.asof}")
    print(f"  {len(rows):,} matches with a half-time score  (half-life {a.half_life:g}d)")
    if len(rows) < 5000:
        print("  too little history — aborting")
        return 1

    seen = defaultdict(int)
    for r in rows:
        seen[r["h"]] += 1
        seen[r["a"]] += 1
    rateable = {t for t, n in seen.items() if n >= a.min_matches}
    print(f"  {len(rateable):,} teams with >= {a.min_matches} matches\n")

    ht = HalfRatings("1H"); ht.fit(rows, "hth", "hta", a.half_life)
    h2 = HalfRatings("2H"); h2.fit(rows, "h2h", "h2a", a.half_life)

    print(f"  base 1H goals: home {ht.base_h:.3f} / away {ht.base_a:.3f}")
    print(f"  base 2H goals: home {h2.base_h:.3f} / away {h2.base_a:.3f}")
    tot_h, tot_a = ht.base_h + h2.base_h, ht.base_a + h2.base_a
    print(f"  => league-average match: {tot_h + tot_a:.3f} goals, "
          f"{100*(ht.base_h + ht.base_a)/(tot_h + tot_a):.1f}% of them in the FIRST half")

    # ── the features, in both shapes ──────────────────────────────────────────
    print(f"\n  {'':4}{'SUM-shaped (O/U head)':<44}{'DIFFERENCE-shaped (1x2 head)'}")
    ok = 0
    vals = {"ht_sum": [], "h2_sum": [], "ht_share": [], "ht_diff": [], "h2_diff": []}
    for r in rows[-4000:]:
        if r["h"] not in rateable or r["a"] not in rateable:
            continue
        lh_ht, la_ht = ht.predict(r["h"], r["a"])
        lh_h2, la_h2 = h2.predict(r["h"], r["a"])
        ht_sum, h2_sum = lh_ht + la_ht, lh_h2 + la_h2
        vals["ht_sum"].append(ht_sum)
        vals["h2_sum"].append(h2_sum)
        vals["ht_share"].append(ht_sum / (ht_sum + h2_sum) if (ht_sum + h2_sum) else 0)
        vals["ht_diff"].append(lh_ht - la_ht)
        vals["h2_diff"].append(lh_h2 - la_h2)
        ok += 1
    import statistics as st
    print(f"  computed on {ok:,} recent matches (sanity sample)\n")
    for k, v in vals.items():
        if v:
            print(f"     {k:12s} mean {st.mean(v):+.3f}  sd {st.pstdev(v):.3f}  "
                  f"range [{min(v):+.2f}, {max(v):+.2f}]")

    # ── does it carry signal at all? a cheap dipstick, NOT a verdict ──────────
    # Correlation of the predicted 1H total against the REALISED 1H total on the
    # held-back tail. This is not the alpha test and must never be quoted as one;
    # it only answers "is the rating doing anything, or is it noise?" before the
    # expensive MFV + retrain work is built on top of it.
    tail = execute_query("""
        SELECT m.home_team_id h, m.away_team_id a,
               (m.ht_score_home + m.ht_score_away)::float AS ht_tot,
               (m.score_home + m.score_away)::float       AS ft_tot
          FROM matches m
         WHERE m.status='finished' AND m.ht_score_home IS NOT NULL
           AND m.score_home IS NOT NULL AND m.date >= %s
         ORDER BY m.date LIMIT 20000""", (a.asof,))
    pred, act, pred_ft, act_ft = [], [], [], []
    for r in tail:
        if r["h"] not in rateable or r["a"] not in rateable:
            continue
        lh, la = ht.predict(r["h"], r["a"])
        p2h, p2a = h2.predict(r["h"], r["a"])
        pred.append(lh + la); act.append(r["ht_tot"])
        pred_ft.append(lh + la + p2h + p2a); act_ft.append(r["ft_tot"])

    def corr(x, y):
        n = len(x)
        if n < 30: return None
        mx, my = st.mean(x), st.mean(y)
        num = sum((xi-mx)*(yi-my) for xi, yi in zip(x, y))
        dx = sum((xi-mx)**2 for xi in x) ** .5
        dy = sum((yi-my)**2 for yi in y) ** .5
        return num/(dx*dy) if dx and dy else None

    print(f"\n  OUT-OF-SAMPLE DIPSTICK on {len(pred):,} matches on/after {a.asof}")
    print(f"     predicted 1H total vs realised 1H total : r = {corr(pred, act):+.4f}")
    print(f"     predicted FT total vs realised FT total : r = {corr(pred_ft, act_ft):+.4f}")
    print("     (a dipstick, NOT the alpha test — it only says whether the rating")
    print("      is doing anything before we build MFV columns and a retrain on it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
