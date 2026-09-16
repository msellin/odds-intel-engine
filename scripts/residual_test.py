#!/usr/bin/env python3
"""RESIDUAL-TEST — does the model predict anything the market price does not?

Master task #4. Method locked in `dev/active/residual-test-preregistration.md`
BEFORE this ran, including two corrections found by auditing the design:

  * `p_model` excludes market shrinkage. Production's `cal_prob` is ~99 pct
    Pinnacle at the live alpha, so blending it against Pinnacle would test
    Pinnacle against itself.
  * The primary runs twice — OPTIMISTIC on stored features, REALISTIC with the
    eight post-hoc columns forced to NULL. The REALISTIC arm decides.

RE-RUN 2026-09-16 after RESIDUAL-TEST-ZEROED-MARKET-FEATURES
------------------------------------------------------------
Until this date the query selected only from `match_feature_vectors`, so the
three features holding the MARKET's own 1x2 price -- pinnacle_implied_home /
_draw / _away, which live in `match_signals`, not mfv -- were dropped by the
`real` filter and `build_X` fed the model 0.0 for all three. In a test whose
entire purpose is to compare the model against that market, the model never saw
it. Fixed with three LEFT JOIN LATERALs; same universe (n = 7,775 both runs,
identical base rate and overround), so the only thing that changed is what the
model was given.

    arm                          model AUC        residual AUC
                              before -> after    before -> after
    OPTIMISTIC                0.6635 -> 0.6718   0.4089 -> 0.4416
    REALISTIC (DECIDES)       0.6437 -> 0.6632   0.3791 -> 0.4189
    REALISTIC + SHIN          0.6437 -> 0.6632   0.3693 -> 0.4025

The defect was REAL and MATERIAL: supplying the three features lifted the
deciding arm's AUC by +0.0195. It did NOT change the verdict.

    alpha = 0.0000 on all three arms, before and after.
    market AUC 0.6997 vs model AUC 0.6632 -- the model is still well behind.
    residual AUC 0.4189 -- still BELOW 0.5, so where the model disagrees with
    Pinnacle, Pinnacle is still right more often.

So model-anchored 1x2 stays closed, and now it is closed on a fair test rather
than on a handicapped one. Two things worth carrying forward: (a) the model can
use market features when given them, which is evidence about the FEATURE
PIPELINE rather than about the model family; (b) an evaluation that silently
turns a missing column into 0.0 will always fail quietly -- see
docs/MODELLING_DATA_AUDIT_2026_09_16.md s1a.

    python3 scripts/residual_test.py
"""
from __future__ import annotations
import os, sys, math, joblib
sys.path.insert(0, str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dotenv import load_dotenv
load_dotenv()
import numpy as np, psycopg2, psycopg2.extras
from statistics import mean

BUNDLE = "data/models/soccer/v20260914_clean_cut0820"
CUTOFF = "2026-08-20"
POST_HOC = ["season_progress", "league_draw_rate_ytd", "league_clv_efficiency",
            "line_velocity", "goals_for_avg_home", "goals_against_avg_home",
            "goals_for_avg_away", "goals_against_avg_away"]

def ll(ps, ys):
    e = 1e-9
    return -mean(y*math.log(max(min(p,1-e),e))+(1-y)*math.log(max(min(1-p,1-e),e)) for p, y in zip(ps, ys))
def auc(sc, ys):
    p = sorted(zip(sc, ys)); n = len(p); rk = {}; i = 0
    while i < n:
        j = i
        while j+1 < n and p[j+1][0] == p[i][0]: j += 1
        r = (i+j)/2+1
        for k in range(i, j+1): rk[k] = r
        i = j+1
    pos = sum(y for _, y in p); neg = n-pos
    if not pos or not neg: return float("nan")
    return (sum(rk[k] for k,(_,y) in enumerate(p) if y==1)-pos*(pos+1)/2)/(pos*neg)
def sig(z): return 1/(1+math.exp(-max(-60, min(60, z))))
def fit_platt(pairs, iters=2500, lr=2.0):
    a, b = 1.0, 0.0; n = len(pairs)
    for _ in range(iters):
        ga = gb = 0.0
        for p, y in pairs:
            e = sig(a*p+b)-y; ga += e*p; gb += e
        a -= lr*ga/n; b -= lr*gb/n
    return a, b
def fit_alpha(pm, pk, ys):
    """alpha minimising log-loss of alpha*model + (1-alpha)*market. Grid + refine."""
    best = (1e9, 0.0)
    for a in [i/200 for i in range(201)]:
        v = ll([a*m+(1-a)*k for m, k in zip(pm, pk)], ys)
        if v < best[0]: best = (v, a)
    return best[1]

def main() -> int:
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--bundle", default=BUNDLE,
                     help="model bundle to score; lets an A/B train be compared "
                          "against the shipped one through the identical harness")
    _a = _ap.parse_args()
    BUNDLE_USED = _a.bundle
    c = psycopg2.connect(os.getenv("DATABASE_URL")).cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cols = joblib.load(f"{BUNDLE_USED}/feature_cols.pkl")
    model = joblib.load(f"{BUNDLE_USED}/result_1x2.pkl")
    real = [x for x in cols if not x.endswith("_missing")]
    c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='match_feature_vectors'")
    have = {r["column_name"] for r in c.fetchall()}
    real = [x for x in real if x in have]

    c.execute(f"""
        WITH pin AS (SELECT DISTINCT ON (o.match_id, o.selection)
                            o.match_id, o.selection, o.odds::float od
                       FROM odds_snapshots o
                      WHERE o.market='1x2' AND o.bookmaker='Pinnacle'
                        AND o.is_live IS NOT TRUE AND o.is_closing = false AND o.odds > 1.01
                        -- PRE-KO BOUND (added 2026-09-14 after audit). Without it,
                        -- 28 pct of selected "pre-kickoff" Pinnacle prices had
                        -- minutes_to_kickoff <= 0, i.e. collected AFTER kickoff --
                        -- defect B5's shape reproduced inside a decisive experiment.
                        -- Verified harmless here (61.7 pct were byte-identical to the
                        -- genuine pre-KO quote; market AUC 0.6983 vs 0.6980), but
                        -- the guard was absent and the next analysis would not be
                        -- so lucky. NOTE the sign convention: every WRITER computes
                        -- kickoff - now, so POSITIVE means before kickoff, despite
                        -- supabase_client.store_odds' docstring saying the opposite.
                        AND (o.minutes_to_kickoff IS NULL OR o.minutes_to_kickoff > 0)
                      ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT {", ".join(f'mfv."{x}"' for x in real)},
               (m.score_home > m.score_away) hw, m.date,
               ph.od ph, pd.od pd, pa.od pa,
               sh.v AS pinnacle_implied_home, sd.v AS pinnacle_implied_draw,
               sa.v AS pinnacle_implied_away
          FROM match_feature_vectors mfv
          JOIN matches m ON m.id = mfv.match_id
          -- RESIDUAL-TEST-ZEROED-MARKET-FEATURES (fixed 2026-09-16).
          -- pinnacle_implied_home/draw/away are in the model's feature_cols.pkl
          -- (train.py:581 PINNACLE_FEATURE_COLS builds them from odds_snapshots)
          -- but have NO COLUMN in match_feature_vectors -- daily_pipeline_v2
          -- writes them to match_signals instead. This query selected only from
          -- mfv, so the `real` filter above dropped all three and build_X's
          -- r.get(cl) returned None, which val() turns into 0.0. The model was
          -- therefore fed ZERO for the three features carrying the MARKET's own
          -- 1x2 price, inside a test whose whole purpose is to compare the model
          -- against that market. Signals exist for roughly half the universe; the
          -- *_missing indicators still fire correctly for rows without one.
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_home'
                              ORDER BY captured_at DESC LIMIT 1) sh ON true
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_draw'
                              ORDER BY captured_at DESC LIMIT 1) sd ON true
          LEFT JOIN LATERAL (SELECT signal_value v FROM match_signals
                              WHERE match_id=m.id AND signal_name='pinnacle_implied_away'
                              ORDER BY captured_at DESC LIMIT 1) sa ON true
          JOIN pin ph ON ph.match_id = m.id AND ph.selection='home'
          JOIN pin pd ON pd.match_id = m.id AND pd.selection='draw'
          JOIN pin pa ON pa.match_id = m.id AND pa.selection='away'
         WHERE m.status='finished' AND m.score_home IS NOT NULL AND m.date >= %s
         ORDER BY m.date""", (CUTOFF,))
    rows = c.fetchall()
    ys = [1 if r["hw"] else 0 for r in rows]
    inv = [1/r["ph"] + 1/r["pd"] + 1/r["pa"] for r in rows]
    pk = [(1/r["ph"])/s for r, s in zip(rows, inv)]           # proportional de-vig

    def shin(o_h, o_d, o_a):
        """Shin de-vig — removes proportionally MORE margin from longshots.
        Pre-registered robustness check: proportional overstates longshots, which
        is exactly where a spurious model edge would appear. Matters here because
        the measured overround is 8.49 pct, not the 2-3 pct Pinnacle shows on
        majors — this universe is mostly low-tier leagues."""
        q = [1/o_h, 1/o_d, 1/o_a]; t = sum(q)
        z = 0.0
        for _ in range(60):                      # bisection on the insider share
            zs = [( (z*z + 4*(1-z)*qi*qi/t) ** 0.5 - z) / (2*(1-z)) for qi in q]
            s_ = sum(zs)
            if s_ > 1: z += (s_-1)*0.5
            else: z -= (1-s_)*0.5
            z = max(0.0, min(0.5, z))
        zs = [((z*z + 4*(1-z)*qi*qi/t) ** 0.5 - z) / (2*(1-z)) for qi in q]
        tot = sum(zs)
        return zs[0]/tot
    pk_shin = [shin(r["ph"], r["pd"], r["pa"]) for r in rows]
    print(f"RESIDUAL TEST — bundle {BUNDLE_USED}, matches on/after {CUTOFF} with a Pinnacle triple")
    print(f"  n = {len(rows)}   home-win base rate = {mean(ys):.4f}")
    print(f"  Pinnacle overround = {mean(inv):.4f}  (vig ≈ {100*(mean(inv)-1):.2f}%)\n")

    def build_X(null_post_hoc: bool):
        def val(r, cl):
            if cl.endswith("_missing"):
                base = cl[:-8]
                if null_post_hoc and base in POST_HOC: return 1.0
                return 1.0 if r.get(base) is None else 0.0
            if null_post_hoc and cl in POST_HOC: return 0.0
            v = r.get(cl)
            return float(v) if v is not None else 0.0
        return np.array([[val(r, cl) for cl in cols] for r in rows])

    classes = list(model.classes_)
    idx = classes.index(0) if 0 in classes else classes.index("H")
    cut = len(rows)//2

    for arm, null_ph, mkt in (("OPTIMISTIC (stored features)", False, pk),
                              ("REALISTIC  (post-hoc NULLed) — DECIDES", True, pk),
                              ("REALISTIC + SHIN de-vig (robustness)", True, pk_shin)):
        pk_use = mkt
        P = model.predict_proba(build_X(null_ph))
        raw = [float(p[idx]) for p in P]
        a, b = fit_platt(list(zip(raw[:cut], [float(y) for y in ys[:cut]])))   # LEVEL only, no shrinkage
        pm = [sig(a*p+b) for p in raw]
        alpha = fit_alpha(pm[:cut], pk_use[:cut], ys[:cut])
        te = slice(cut, None)
        l_mkt = ll(pk_use[te], ys[te])
        l_bl = ll([alpha*m+(1-alpha)*k for m, k in zip(pm[te], pk_use[te])], ys[te])
        l_mod = ll(pm[te], ys[te])
        resid_auc = auc([m-k for m, k in zip(pm[te], pk_use[te])], ys[te])
        print(f"  ── {arm}")
        print(f"     Platt a={a:.3f} b={b:+.3f}   fitted alpha = {alpha:.4f}")
        print(f"     market alone   log-loss {l_mkt:.4f}   AUC {auc(pk_use[te], ys[te]):.4f}")
        print(f"     model alone    log-loss {l_mod:.4f}   AUC {auc(pm[te], ys[te]):.4f}")
        print(f"     BLEND          log-loss {l_bl:.4f}")
        print(f"     blend vs market: {100*(l_mkt-l_bl)/l_mkt:+.3f}%")
        print(f"     residual AUC (model−market predicts outcome): {resid_auc:.4f}  (0.5 = no info)")
        ok = (l_bl < l_mkt) and (alpha > 0.02)
        print(f"     PRIMARY: {'PASS' if ok else 'FAIL'}  (needs blend<market AND alpha>0.02)\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
