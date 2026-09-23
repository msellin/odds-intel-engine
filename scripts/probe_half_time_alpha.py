#!/usr/bin/env python3
"""HALF-TIME ALPHA PROBE ([[#084]]) — cheap α test before the MFV build.

Scores the half-time ratings from `build_half_time_ratings.py` through
`residual_test_ou`'s OWN imported functions, so α is directly comparable to the
baseline in `dev/active/half-time-layer-baseline.md` (O/U 2.5: market log-loss
0.6737, AUC 0.6038, α 0.0000, residual AUC 0.4382).

WHY A PROBE RATHER THAN GOING STRAIGHT TO THE FEATURE BUILD. Wiring the ratings
into `match_feature_vectors` and retraining is ~a day. Scoring them standalone is
two hours and answers the same question the other way round: if a half-time goals
model cannot beat the market ALONE, it is unlikely to rescue a 52-feature vector
that already fails. Same reasoning as the shots+corners probe in [[#077]].

⚠️ WHAT THIS PROBE CAN AND CANNOT SAY.
  CAN:    does a HT/2H-derived goals model carry information the market lacks,
          on O/U 2.5?
  CANNOT: whether half-time features help the EXISTING model. A standalone probe
          failing does not prove the feature is worthless as one input among
          many — it proves the model alone is not competitive. That distinction
          killed a premature conclusion in [[#077]] and is why the MFV build
          still runs afterwards.
  ALSO CANNOT: say anything about the 1H MARKETS themselves (1x2_1h,
          team_total_1h_*), which is where the half-time layer's real novelty
          sits — we price nothing there today. That needs its own test.

Usage:
    python3 scripts/probe_half_time_alpha.py
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.residual_test_ou import (  # noqa: E402
    ll, auc, fit_platt, fit_alpha, sig, devig_two_way, shin2,
)
from scripts.build_half_time_ratings import HalfRatings, load  # noqa: E402
from workers.api_clients.db import execute_query  # noqa: E402

CUTOFF = "2026-08-20"


def p_over_25(lam: float) -> float:
    """P(total >= 3) for a single Poisson with rate `lam`.

    The two halves are summed into one rate before this, which is exact for
    independent Poissons: the sum of independent Poissons is Poisson with the
    summed rate. Modelling the halves separately and adding is therefore not an
    approximation of the match total — it IS the match total, with the halves'
    different scoring rates respected on the way."""
    p_under = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(3))
    return max(1e-6, min(1 - 1e-6, 1.0 - p_under))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default=CUTOFF)
    ap.add_argument("--half-life", type=float, default=300.0)
    a_ = ap.parse_args()

    hist = load(a_.cutoff)
    print(f"HALF-TIME ALPHA PROBE ([[#084]]) — cutoff {a_.cutoff}")
    print(f"  fitted on {len(hist):,} matches with a half-time score "
          f"(half-life {a_.half_life:g}d)")
    from collections import defaultdict
    seen = defaultdict(int)
    for r in hist:
        seen[r["h"]] += 1
        seen[r["a"]] += 1
    rateable = {t for t, n in seen.items() if n >= 5}

    ht = HalfRatings("1H"); ht.fit(hist, "hth", "hta", a_.half_life)
    h2 = HalfRatings("2H"); h2.fit(hist, "h2h", "h2a", a_.half_life)

    # ⚠️ WALK FORWARD, added after the first probe run gave a FAIL on a WEAKER
    # construction than the one actually shipped.
    #
    # The first version fitted once at the cutoff and predicted every later match
    # from that frozen rating. The populated MFV columns do not work that way:
    # `populate_half_time_features.py` updates the rating after each match, so by
    # the time it reaches a fixture it holds everything up to the day before.
    # Measured consequence — predicted-vs-realised FT total on the SAME
    # post-cutoff matches: frozen fit r = +0.1117, walk-forward r = +0.2378.
    # The probe was testing a materially worse feature than the one in the table,
    # and reporting its failure as the feature's failure.
    #
    # Still leak-free: the update happens strictly AFTER the prediction is taken.
    ht_w = HalfRatings("1H"); ht_w.att = ht.att.copy(); ht_w.dfn = ht.dfn.copy()
    ht_w.base_h, ht_w.base_a = ht.base_h, ht.base_a
    h2_w = HalfRatings("2H"); h2_w.att = h2.att.copy(); h2_w.dfn = h2.dfn.copy()
    h2_w.base_h, h2_w.base_a = h2.base_h, h2.base_a

    rows = execute_query("""
        WITH pin AS (SELECT DISTINCT ON (o.match_id, o.selection)
                            o.match_id, o.selection, o.odds::float od
                       FROM odds_snapshots o
                      WHERE o.market='over_under_25' AND o.bookmaker='Pinnacle'
                        AND o.is_live IS NOT TRUE AND o.is_closing = false AND o.odds > 1.01
                        AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
                      ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT m.home_team_id h, m.away_team_id a,
               (m.score_home + m.score_away)::float total, po.od po, pu.od pu,
               m.ht_score_home::float hth, m.ht_score_away::float hta,
               (m.score_home - m.ht_score_home)::float h2h,
               (m.score_away - m.ht_score_away)::float h2a
          FROM matches m
          JOIN pin po ON po.match_id = m.id AND po.selection='over'
          JOIN pin pu ON pu.match_id = m.id AND pu.selection='under'
         WHERE m.status='finished' AND m.score_home IS NOT NULL
           AND m.ht_score_home IS NOT NULL
           AND m.score_home >= m.ht_score_home AND m.score_away >= m.ht_score_away
           AND m.date >= %s
         ORDER BY m.date, m.id""", (a_.cutoff,))

    pm_raw, pk, pk_shin, ys = [], [], [], []
    for r in rows:
        if r["h"] in rateable and r["a"] in rateable:
            lh, la = ht_w.predict(r["h"], r["a"])
            p2h, p2a = h2_w.predict(r["h"], r["a"])
            pm_raw.append(p_over_25(min(6.0, lh + la + p2h + p2a)))
            pk.append(devig_two_way(r["po"], r["pu"]))
            pk_shin.append(shin2(r["po"], r["pu"]))
            ys.append(1 if r["total"] > 2.5 else 0)
        # UPDATE AFTER PREDICTING — never before.
        ht_w.update(r["h"], r["a"], r["hth"], r["hta"])
        h2_w.update(r["h"], r["a"], r["h2h"], r["h2a"])

    n = len(ys)
    print(f"  scored on {n:,} out-of-sample fixtures "
          f"(over-2.5 base rate {sum(ys)/n:.4f})\n")
    if n < 800:
        print("  UNDERPOWERED — fewer than 800 rows")
        return 1

    cut = n // 2
    print("=" * 74)
    print("SCORED THROUGH residual_test_ou's OWN FUNCTIONS (imported, not copied)")
    print("=" * 74)
    print("  BASELINE to beat (dev/active/half-time-layer-baseline.md):")
    print("     market log-loss 0.6737  AUC 0.6038  |  old model α 0.0000, resid AUC 0.4382\n")
    for arm, mkt in (("HALF-TIME MODEL vs de-vig Pinnacle — DECIDES", pk),
                     ("HALF-TIME MODEL vs SHIN de-vig (robustness)", pk_shin)):
        aa, bb = fit_platt(list(zip(pm_raw[:cut], [float(y) for y in ys[:cut]])))
        pm = [sig(aa * p + bb) for p in pm_raw]
        alpha = fit_alpha(pm[:cut], mkt[:cut], ys[:cut])
        te = slice(cut, None)
        l_mkt, l_mod = ll(mkt[te], ys[te]), ll(pm[te], ys[te])
        l_bl = ll([alpha*m + (1-alpha)*k for m, k in zip(pm[te], mkt[te])], ys[te])
        resid = auc([m - k for m, k in zip(pm[te], mkt[te])], ys[te])
        ok = (l_bl < l_mkt) and (alpha > 0.02)
        print(f"  -- {arm}")
        print(f"     Platt a={aa:.3f} b={bb:+.3f}   fitted alpha = {alpha:.4f}")
        print(f"     market alone   log-loss {l_mkt:.4f}   AUC {auc(mkt[te], ys[te]):.4f}")
        print(f"     model alone    log-loss {l_mod:.4f}   AUC {auc(pm[te], ys[te]):.4f}")
        print(f"     BLEND          log-loss {l_bl:.4f}   ({100*(l_mkt-l_bl)/l_mkt:+.3f}% vs market)")
        print(f"     residual AUC {resid:.4f}  (0.5 = no information)")
        print(f"     PRIMARY: {'PASS' if ok else 'FAIL'}  (needs blend<market AND alpha>0.02)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
