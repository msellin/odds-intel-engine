#!/usr/bin/env python3
"""COMPARE-BUNDLES-HOLDOUT — score two model bundles on the SAME held-out features.

Every offline evaluator in this repo scores a model against the feature table as
it stands, which meant a leak-trained model was scored on leaked features and
looked strong. That is how ELO-FORM-LEAK survived May to September, and how ten
consecutive versions were promoted while every one of them was, in live serving,
worse than predicting the base rate.

This runs the comparison that answers the question: both bundles get IDENTICAL
inputs, on matches AFTER both cutoffs, with `*_missing` indicators reconstructed
the way `_build_row_from_mfv` does at inference rather than read from a table
that does not hold them.

The bar is the BASE RATE, not the other model. "Better than the previous
version" is how a family of models drifts for months without anyone noticing
that none of them beats a constant.

    python3 scripts/compare_bundles_holdout.py
"""
import os, sys, math, joblib
sys.path.insert(0, "/Users/margussellin/www/odds-intel-engine")
from dotenv import load_dotenv
load_dotenv("/Users/margussellin/www/odds-intel-engine/.env")
import numpy as np, psycopg2, psycopg2.extras
from statistics import mean

BASE = "/Users/margussellin/www/odds-intel-engine/data/models/soccer"
OLD, NEW = "v20260903_cut0820", "v20260914_clean_cut0820"
c = psycopg2.connect(os.getenv("DATABASE_URL")).cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def auc(sc, ys):
    p = sorted(zip(sc, ys)); n = len(p); rk = {}; i = 0
    while i < n:
        j = i
        while j+1 < n and p[j+1][0] == p[i][0]: j += 1
        r = (i+j)/2+1
        for k in range(i, j+1): rk[k] = r
        i = j+1
    pos = sum(y for _, y in p); neg = n-pos
    return (sum(rk[k] for k,(_,y) in enumerate(p) if y==1)-pos*(pos+1)/2)/(pos*neg)
def ll(ps, ys):
    e = 1e-9
    return -mean(y*math.log(max(min(p,1-e),e))+(1-y)*math.log(max(min(1-p,1-e),e)) for p,y in zip(ps,ys))

cols_old = joblib.load(f"{BASE}/{OLD}/feature_cols.pkl")
cols_new = joblib.load(f"{BASE}/{NEW}/feature_cols.pkl")
m_old = joblib.load(f"{BASE}/{OLD}/result_1x2.pkl")
m_new = joblib.load(f"{BASE}/{NEW}/result_1x2.pkl")
allcols = sorted(set(cols_old) | set(cols_new))
# `*_missing` indicators are DERIVED at inference (raw.get(col) is None -> 1),
# not stored. Select only real MFV columns and reconstruct the flags below,
# exactly as _build_row_from_mfv does.
real = [x for x in allcols if not x.endswith("_missing") and x != "match_id"]
c.execute("""SELECT column_name FROM information_schema.columns
             WHERE table_name='match_feature_vectors'""")
have = {r["column_name"] for r in c.fetchall()}
real = [x for x in real if x in have]
sel = ", ".join(f'mfv."{x}"' for x in real)
c.execute(f"""SELECT {sel}, (m.score_home>m.score_away) hw
FROM match_feature_vectors mfv JOIN matches m ON m.id=mfv.match_id
WHERE m.status='finished' AND m.score_home IS NOT NULL
  AND m.date > '2026-08-20' AND m.date < '2026-09-14'""")
rows = c.fetchall()
print(f"HELD-OUT period 2026-08-20 -> 2026-09-14 (after BOTH cutoffs): n={len(rows)}\n")
ys = [1 if r["hw"] else 0 for r in rows]
base = mean(ys)

def score(model, cols, label):
    def val(r, cl):
        if cl.endswith("_missing"):
            base_col = cl[:-len("_missing")]
            return 1.0 if r.get(base_col) is None else 0.0
        v = r.get(cl)
        return float(v) if v is not None else 0.0
    X = np.array([[val(r, cl) for cl in cols] for r in rows])
    P = model.predict_proba(X)
    classes = list(model.classes_)
    idx = classes.index(0) if 0 in classes else classes.index("H")
    ps = [float(p[idx]) for p in P]
    print(f"  {label:34s} AUC={auc(ps,ys):.4f}  log-loss={ll(ps,ys):.4f}")
    return ll(ps, ys)

print("  1X2 HOME, both models scored on the SAME clean held-out features:")
l_old = score(m_old, cols_old, f"OLD  {OLD} (leak-trained)")
l_new = score(m_new, cols_new, f"NEW  {NEW} (clean)")
lb = ll([base]*len(ys), ys)
print(f"  {'BASE RATE (the bar to beat)':34s} AUC=0.5000  log-loss={lb:.4f}")
print(f"\n  base rate on this period = {base:.4f}")
print(f"  NEW vs OLD   : {100*(l_old-l_new)/l_old:+.2f}% log-loss")
print(f"  NEW vs BASE  : {100*(lb-l_new)/lb:+.2f}% log-loss   -> {'BEATS BASE RATE' if l_new < lb else 'still worse than base'}")
print(f"  OLD vs BASE  : {100*(lb-l_old)/lb:+.2f}% log-loss   -> {'beats base' if l_old < lb else 'worse than base'}")
