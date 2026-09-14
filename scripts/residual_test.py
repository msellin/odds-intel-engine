#!/usr/bin/env python3
"""RESIDUAL-TEST — does the model predict anything the market price does not?

Master task #4. Method locked in `dev/active/residual-test-preregistration.md`
BEFORE this ran, including two corrections found by auditing the design:

  * `p_model` excludes market shrinkage. Production's `cal_prob` is ~99 pct
    Pinnacle at the live alpha, so blending it against Pinnacle would test
    Pinnacle against itself.
  * The primary runs twice — OPTIMISTIC on stored features, REALISTIC with the
    eight post-hoc columns forced to NULL. The REALISTIC arm decides.

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
    c = psycopg2.connect(os.getenv("DATABASE_URL")).cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cols = joblib.load(f"{BUNDLE}/feature_cols.pkl")
    model = joblib.load(f"{BUNDLE}/result_1x2.pkl")
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
                      ORDER BY o.match_id, o.selection, o.timestamp DESC)
        SELECT {", ".join(f'mfv."{x}"' for x in real)},
               (m.score_home > m.score_away) hw, m.date,
               ph.od ph, pd.od pd, pa.od pa
          FROM match_feature_vectors mfv
          JOIN matches m ON m.id = mfv.match_id
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
    print(f"RESIDUAL TEST — universe: matches on/after {CUTOFF} with a Pinnacle triple")
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
