#!/usr/bin/env python3
"""OU-SHOTS-AND-CORNERS-RATING ([[#077]]) — does feeding the goals model SHOTS
instead of GOALS move alpha off zero?

THE HYPOTHESIS, AND WHOSE IT IS
-------------------------------
Wheatcroft (2020, *IJF* 36(3)), n = 68,672 bets: GAP ratings fed **shots +
corners** returned **+535 units**; the SAME ratings fed **goals** returned
**-631 units**, Bonferroni-corrected p < 0.001 for every input except goals.
Past goals are a WORSE input for a goals model than past shots.

Our O/U model is fed goals. This tests the one axis we have never moved.

WHY THIS IS THE FOURTH ATTEMPT AND WHAT IS DIFFERENT
----------------------------------------------------
    XGBoost O/U head (52 shared features, 9 real)          alpha = 0.0000
    "Dixon-Coles" -- actually DOUBLE-POISSON, because the
      tau correction is provably a no-op for O/U 2.5
      ([[#014]]; Petretta et al. publish the same)         alpha = 0.0000
    residual_test_ou, both de-vig arms, three lines        alpha = 0.0000

Every one of those varied the MODEL over the same input: goals and Elo. This
varies the INPUT VARIABLE. That is the axis the published evidence says matters.

METHOD
------
* GAP-style online ratings. Each team carries attack/defence multipliers for
  shots and corners, separately by venue, initialised from history before the
  cutoff and then updated AFTER each test match is predicted -- so they are both
  leak-free and never stale. That is Wheatcroft's actual construction, not an
  approximation of it.
* Opponent adjustment by iterative proportional fitting on the history window.
* Predicted statistics -> goal rates through a Poisson GLM fitted on history,
  then P(total > 2.5) from INDEPENDENT Poisson. Independence is safe here
  specifically: the Dixon-Coles correlation term provably cannot move O/U 2.5.
* Scored through `residual_test_ou`'s OWN functions, imported rather than
  copied, so alpha is comparable to the three zeros above rather than merely
  similar. Platt for LEVEL on the first half, alpha fitted on the first half and
  evaluated on the second, de-vigged Pinnacle as the market.

PRE-REGISTERED PASS CONDITION (identical to the other three):
    blend log-loss < market log-loss AND alpha > 0.02, on the realistic arm.

STOPPING RULE, committed before any result was seen:
    PASS -> the input matters; #025 and a re-derived edge floor become worth
            doing, against THIS model.
    FAIL -> the fourth zero, on the one axis the literature says should work.
            O/U modelling closes on evidence.

CONTROL: the shipped bundle is re-scored on the IDENTICAL restricted population,
so "alpha is still zero" cannot be an artefact of testing on a different 2,886
matches than the original 7,273.

Usage:
    python3 scripts/ou_shots_corners_rating.py
    python3 scripts/ou_shots_corners_rating.py --cutoff 2026-08-20 --half-life 180
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

import numpy as np
import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Imported, never copied — a second implementation of the scoring rule is a
# second definition of "alpha", and this whole task is a comparison of alphas.
from scripts.residual_test_ou import (  # noqa: E402
    ll, auc, fit_platt, fit_alpha, sig, devig_two_way, shin2,
)

CUTOFF = "2026-08-20"
MIN_HIST = 5          # matches with stats a team needs before we rate it
DECAY_HL = 180.0      # days; exponential half-life on historical weight
IPF_ITERS = 6         # iterative proportional fitting passes
GAP_LR = 0.08         # online rating learning rate


def _conn():
    return psycopg2.connect(os.getenv("DATABASE_URL")).cursor(
        cursor_factory=psycopg2.extras.RealDictCursor)


def load_history(c, cutoff):
    """Finished matches BEFORE the cutoff that carry shots and corners."""
    c.execute("""
        SELECT m.id, m.date, m.home_team_id h, m.away_team_id a,
               m.score_home::float gh, m.score_away::float ga,
               s.shots_home::float sh, s.shots_away::float sa,
               s.corners_home::float ch, s.corners_away::float ca
          FROM matches m JOIN match_stats s ON s.match_id = m.id
         WHERE m.status='finished' AND m.score_home IS NOT NULL
           AND s.shots_home IS NOT NULL AND s.corners_home IS NOT NULL
           AND m.date < %s
         ORDER BY m.date""", (cutoff,))
    return c.fetchall()


class StatRatings:
    """Opponent-adjusted attack/defence multipliers for ONE match statistic.

    value(home, away) = base_home * att[home] * dfn[away]
    value(away, home) = base_away * att[away] * dfn[home]

    Fitted by iterative proportional fitting on the decayed history, then kept
    current by an online GAP update as each new result lands. The online step is
    what makes this Wheatcroft's construction rather than a static rating: a
    team's form in the statistic moves within the test window, and the update
    happens strictly AFTER that match has been predicted.
    """

    def __init__(self, name):
        self.name = name
        self.att = defaultdict(lambda: 1.0)
        self.dfn = defaultdict(lambda: 1.0)
        self.base_h = 1.0
        self.base_a = 1.0

    def fit(self, rows, key_h, key_a, asof):
        w = {}
        for r in rows:
            age = (asof - r["date"]).days if r["date"] else 0
            w[r["id"]] = 0.5 ** (max(age, 0) / DECAY_HL)
        tw = sum(w.values()) or 1.0
        self.base_h = sum(w[r["id"]] * r[key_h] for r in rows) / tw
        self.base_a = sum(w[r["id"]] * r[key_a] for r in rows) / tw
        if self.base_h <= 0 or self.base_a <= 0:
            return

        for _ in range(IPF_ITERS):
            # attack: observed for / expected for
            num_f, den_f = defaultdict(float), defaultdict(float)
            num_a, den_a = defaultdict(float), defaultdict(float)
            for r in rows:
                ww = w[r["id"]]
                h, a = r["h"], r["a"]
                num_f[h] += ww * r[key_h]
                den_f[h] += ww * self.base_h * self.dfn[a]
                num_f[a] += ww * r[key_a]
                den_f[a] += ww * self.base_a * self.dfn[h]
                num_a[a] += ww * r[key_h]
                den_a[a] += ww * self.base_h * self.att[h]
                num_a[h] += ww * r[key_a]
                den_a[h] += ww * self.base_a * self.att[a]
            for t in list(num_f):
                if den_f[t] > 0:
                    self.att[t] = min(3.0, max(0.33, num_f[t] / den_f[t]))
            for t in list(num_a):
                if den_a[t] > 0:
                    self.dfn[t] = min(3.0, max(0.33, num_a[t] / den_a[t]))

    def predict(self, h, a):
        return (self.base_h * self.att[h] * self.dfn[a],
                self.base_a * self.att[a] * self.dfn[h])

    def update(self, h, a, obs_h, obs_a):
        """Online GAP step, applied only AFTER the match has been predicted."""
        exp_h, exp_a = self.predict(h, a)
        if exp_h > 0:
            self.att[h] = min(3.0, max(0.33, self.att[h] * (1 + GAP_LR * (obs_h - exp_h) / exp_h)))
            self.dfn[a] = min(3.0, max(0.33, self.dfn[a] * (1 + GAP_LR * (obs_h - exp_h) / exp_h)))
        if exp_a > 0:
            self.att[a] = min(3.0, max(0.33, self.att[a] * (1 + GAP_LR * (obs_a - exp_a) / exp_a)))
            self.dfn[h] = min(3.0, max(0.33, self.dfn[h] * (1 + GAP_LR * (obs_a - exp_a) / exp_a)))


def fit_poisson_glm(X, y, iters=400, lr=0.05):
    """log(lambda) = X @ beta, fitted by gradient ascent on the Poisson
    log-likelihood. Hand-rolled to avoid adding a dependency for 15 lines, and
    because the gradient of the Poisson LL is exactly X'(y - lambda)."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    beta = np.zeros(X.shape[1])
    beta[0] = math.log(max(y.mean(), 1e-6))
    n = len(y)
    for _ in range(iters):
        lam = np.exp(np.clip(X @ beta, -6, 3))
        beta += lr * (X.T @ (y - lam)) / n
    return beta


def p_over_25(lh, la):
    """P(total goals >= 3) for independent Poisson.

    Independence is not a shortcut here: [[#014]] proved (and Petretta et al.
    published) that the Dixon-Coles correlation term has EXACTLY zero effect on
    O/U 2.5 for all lambda, mu, rho -- all four corrected cells sit under 2.5 and
    cancel. So a bivariate correction could not change this number."""
    p_under = 0.0
    for tot in range(3):
        s = 0.0
        for i in range(tot + 1):
            s += (math.exp(-lh) * lh ** i / math.factorial(i)) * \
                 (math.exp(-la) * la ** (tot - i) / math.factorial(tot - i))
        p_under += s
    return max(1e-6, min(1 - 1e-6, 1.0 - p_under))


def load_test(c, cutoff):
    """The harness population, restricted to matches where BOTH teams have
    enough prior stats history to be rated. Carries each match's own stats too,
    used ONLY for the post-prediction online update."""
    c.execute("""
        WITH pin AS (
          SELECT DISTINCT ON (o.match_id, o.selection) o.match_id, o.selection, o.odds::float od
            FROM odds_snapshots o
           WHERE o.market='over_under_25' AND o.bookmaker='Pinnacle'
             AND o.is_live IS NOT TRUE AND o.is_closing = false AND o.odds > 1.01
             AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
           ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT m.id, m.date, m.home_team_id h, m.away_team_id a,
               (m.score_home + m.score_away)::float total,
               m.score_home::float gh, m.score_away::float ga,
               po.od po, pu.od pu,
               s.shots_home::float sh, s.shots_away::float sa,
               s.corners_home::float ch, s.corners_away::float ca,
               l.country, l.name league
          FROM matches m
          JOIN pin po ON po.match_id = m.id AND po.selection='over'
          JOIN pin pu ON pu.match_id = m.id AND pu.selection='under'
          LEFT JOIN match_stats s ON s.match_id = m.id
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE m.status='finished' AND m.score_home IS NOT NULL
           AND m.score_away IS NOT NULL AND m.date >= %s
         ORDER BY m.date, m.id""", (cutoff,))
    return c.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", default=CUTOFF)
    ap.add_argument("--min-hist", type=int, default=MIN_HIST)
    ap.add_argument("--input", choices=("shots_corners", "goals"), default="shots_corners",
                    help="WHICH STATISTIC FEEDS THE RATINGS. This is the whole "
                         "experiment. `goals` is the CONTROL: identical rating "
                         "machinery, identical GLM, identical harness — only the "
                         "input variable changes. Without it a weak alpha is "
                         "unattributable, because 'shots do not help' and 'this "
                         "rating code is poor' produce the same number.")
    a_ = ap.parse_args()

    c = _conn()
    print(f"OU-SHOTS-AND-CORNERS-RATING ([[#077]]) — cutoff {a_.cutoff}\n")

    hist = load_history(c, a_.cutoff)
    print(f"history: {len(hist):,} finished matches with shots+corners before {a_.cutoff}")
    if len(hist) < 2000:
        print("too little history to rate teams — cannot answer on our data")
        return 1

    seen = defaultdict(int)
    for r in hist:
        seen[r["h"]] += 1
        seen[r["a"]] += 1
    rateable = {t for t, n in seen.items() if n >= a_.min_hist}
    print(f"teams with >= {a_.min_hist} rated matches: {len(rateable):,}")

    asof = hist[-1]["date"]
    # THE ONE LINE THE EXPERIMENT TURNS ON. Everything downstream — ratings,
    # GLM, harness — is identical between the two arms.
    if a_.input == "goals":
        k1h, k1a, k2h, k2a = "gh", "ga", "gh", "ga"   # goals in both slots
        # Two identical columns make the GLM's two log terms perfectly
        # collinear. Harmless HERE: undamped gradient descent moves both by the
        # same gradient, so b1+b2 acts as one coefficient and the PREDICTIONS
        # match a single-term model exactly. Only the printed split is
        # uninterpretable. Noted rather than special-cased, because keeping the
        # two arms structurally identical is the entire point of the control.
        n1, n2 = "goals", "goals(dup)"
    else:
        k1h, k1a, k2h, k2a = "sh", "sa", "ch", "ca"
        n1, n2 = "shots", "corners"
    stat1 = StatRatings(n1)
    stat2 = StatRatings(n2)
    stat1.fit(hist, k1h, k1a, asof)
    stat2.fit(hist, k2h, k2a, asof)
    shots, corners = stat1, stat2
    print(f"INPUT = {a_.input}")
    print(f"base {n1:8s} home {stat1.base_h:.2f} / away {stat1.base_a:.2f}")
    print(f"base {n2:8s} home {stat2.base_h:.2f} / away {stat2.base_a:.2f}")

    # ── map predicted STATISTICS -> goal rates, fitted on history only ────────
    Xh, yh, Xa, ya = [], [], [], []
    for r in hist:
        ps_h, ps_a = stat1.predict(r["h"], r["a"])
        pc_h, pc_a = stat2.predict(r["h"], r["a"])
        Xh.append([1.0, math.log(max(ps_h, .5)), math.log(max(pc_h, .5))])
        yh.append(r["gh"])
        Xa.append([1.0, math.log(max(ps_a, .5)), math.log(max(pc_a, .5))])
        ya.append(r["ga"])
    bh = fit_poisson_glm(Xh, yh)
    ba = fit_poisson_glm(Xa, ya)
    print(f"GLM home: log(lam) = {bh[0]:+.3f} {bh[1]:+.3f}*log(shots) {bh[2]:+.3f}*log(corners)")
    print(f"GLM away: log(lam) = {ba[0]:+.3f} {ba[1]:+.3f}*log(shots) {ba[2]:+.3f}*log(corners)")

    # ── walk the test window in date order ────────────────────────────────────
    test = load_test(c, a_.cutoff)
    pm_raw, pk, pk_shin, ys, leagues = [], [], [], [], []
    skipped_unrated = 0
    last_date = None
    for r in test:
        # LEAKAGE GUARD. Ratings only ever move forward in time, and the update
        # for a match happens after it is appended below — never before.
        if last_date is not None and r["date"] < last_date:
            raise AssertionError("test rows out of date order — leakage risk")
        last_date = r["date"]

        if r["h"] in rateable and r["a"] in rateable:
            ps_h, ps_a = stat1.predict(r["h"], r["a"])
            pc_h, pc_a = stat2.predict(r["h"], r["a"])
            lh = math.exp(min(3.0, bh[0] + bh[1]*math.log(max(ps_h, .5)) + bh[2]*math.log(max(pc_h, .5))))
            la = math.exp(min(3.0, ba[0] + ba[1]*math.log(max(ps_a, .5)) + ba[2]*math.log(max(pc_a, .5))))
            pm_raw.append(p_over_25(lh, la))
            pk.append(devig_two_way(r["po"], r["pu"]))
            pk_shin.append(shin2(r["po"], r["pu"]))
            ys.append(1 if r["total"] > 2.5 else 0)
            leagues.append(f"{r['country']} / {r['league']}")
        else:
            skipped_unrated += 1

        # ONLINE UPDATE — strictly after prediction, and only when this match
        # actually carries stats.
        if a_.input == "goals":
            stat1.update(r["h"], r["a"], r["gh"], r["ga"])
            stat2.update(r["h"], r["a"], r["gh"], r["ga"])
        elif r["sh"] is not None and r["ch"] is not None:
            stat1.update(r["h"], r["a"], r["sh"], r["sa"])
            stat2.update(r["h"], r["a"], r["ch"], r["ca"])

    n = len(ys)
    print(f"\ntest window: {len(test):,} Pinnacle-priced finished matches")
    print(f"  usable (both teams rateable): {n:,}   skipped: {skipped_unrated:,}")
    if n < 800:
        print("\nUNDERPOWERED — fewer than 800 usable rows. The honest output is "
              "'cannot be answered on our data', not a weak alpha.")
        return 1

    print(f"  over-2.5 base rate {np.mean(ys):.4f}")
    top = sorted(((leagues.count(l), l) for l in set(leagues)), reverse=True)[:5]
    print("  top leagues: " + ", ".join(f"{l} ({k})" for k, l in top))

    cut = n // 2
    print(f"\n{'='*78}\nSCORED THROUGH residual_test_ou's OWN FUNCTIONS "
          f"(imported, not reimplemented)\n{'='*78}")
    tag = a_.input.upper().replace("_", "+")
    for arm, mkt in ((f"{tag} vs de-vig Pinnacle — DECIDES", pk),
                     (f"{tag} vs SHIN de-vig (robustness)", pk_shin)):
        aa, bb = fit_platt(list(zip(pm_raw[:cut], [float(y) for y in ys[:cut]])))
        pm = [sig(aa * p + bb) for p in pm_raw]
        alpha = fit_alpha(pm[:cut], mkt[:cut], ys[:cut])
        te = slice(cut, None)
        l_mkt = ll(mkt[te], ys[te])
        l_mod = ll(pm[te], ys[te])
        l_bl = ll([alpha*m + (1-alpha)*k for m, k in zip(pm[te], mkt[te])], ys[te])
        resid = auc([m - k for m, k in zip(pm[te], mkt[te])], ys[te])
        ok = (l_bl < l_mkt) and (alpha > 0.02)
        print(f"\n  -- {arm}")
        print(f"     Platt a={aa:.3f} b={bb:+.3f}   fitted alpha = {alpha:.4f}")
        print(f"     market alone   log-loss {l_mkt:.4f}   AUC {auc(mkt[te], ys[te]):.4f}")
        print(f"     model alone    log-loss {l_mod:.4f}   AUC {auc(pm[te], ys[te]):.4f}")
        print(f"     BLEND          log-loss {l_bl:.4f}   ({100*(l_mkt-l_bl)/l_mkt:+.3f}% vs market)")
        print(f"     residual AUC {resid:.4f}  (0.5 = no information)")
        print(f"     PRIMARY: {'PASS' if ok else 'FAIL'}  (needs blend<market AND alpha>0.02)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
