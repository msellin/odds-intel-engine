import os, sys, math, joblib
sys.path.insert(0, "/Users/margussellin/www/odds-intel-engine")
from dotenv import load_dotenv
load_dotenv("/Users/margussellin/www/odds-intel-engine/.env")
import numpy as np, psycopg2, psycopg2.extras
from statistics import mean
BASE="/Users/margussellin/www/odds-intel-engine/data/models/soccer"
c=psycopg2.connect(os.getenv("DATABASE_URL")).cursor(cursor_factory=psycopg2.extras.RealDictCursor)
def ll(ps,ys):
    e=1e-9; return -mean(y*math.log(max(min(p,1-e),e))+(1-y)*math.log(max(min(1-p,1-e),e)) for p,y in zip(ps,ys))
def auc(sc,ys):
    p=sorted(zip(sc,ys)); n=len(p); rk={}; i=0
    while i<n:
        j=i
        while j+1<n and p[j+1][0]==p[i][0]: j+=1
        r=(i+j)/2+1
        for k in range(i,j+1): rk[k]=r
        i=j+1
    pos=sum(y for _,y in p); neg=n-pos
    return (sum(rk[k] for k,(_,y) in enumerate(p) if y==1)-pos*(pos+1)/2)/(pos*neg)
def sig(z): return 1/(1+math.exp(-max(-60,min(60,z))))
def fit_platt(pairs, iters=2000, lr=2.0):
    a,b=1.0,0.0; n=len(pairs)
    for _ in range(iters):
        ga=gb=0.0
        for p,y in pairs:
            e=sig(a*p+b)-y; ga+=e*p; gb+=e
        a-=lr*ga/n; b-=lr*gb/n
    return a,b

NEW="v20260914_clean_cut0820"
cols=joblib.load(f"{BASE}/{NEW}/feature_cols.pkl"); m=joblib.load(f"{BASE}/{NEW}/result_1x2.pkl")
real=[x for x in cols if not x.endswith("_missing")]
c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='match_feature_vectors'")
have={r["column_name"] for r in c.fetchall()}; real=[x for x in real if x in have]
c.execute(f"""SELECT {", ".join(f'mfv."{x}"' for x in real)}, (m.score_home>m.score_away) hw, m.date
FROM match_feature_vectors mfv JOIN matches m ON m.id=mfv.match_id
WHERE m.status='finished' AND m.score_home IS NOT NULL
  AND m.date > '2026-08-20' AND m.date < '2026-09-14' ORDER BY m.date""")
rows=c.fetchall()
def val(r,cl):
    if cl.endswith("_missing"): return 1.0 if r.get(cl[:-8]) is None else 0.0
    v=r.get(cl); return float(v) if v is not None else 0.0
X=np.array([[val(r,cl) for cl in cols] for r in rows])
P=m.predict_proba(X); classes=list(m.classes_); idx=classes.index(0) if 0 in classes else classes.index("H")
ps=[float(p[idx]) for p in P]; ys=[1 if r["hw"] else 0 for r in rows]

cut=len(rows)//2
tr=list(zip(ps[:cut],[float(y) for y in ys[:cut]])); te_p, te_y = ps[cut:], ys[cut:]
a,b=fit_platt(tr)
cal=[sig(a*p+b) for p in te_p]
base=mean([float(y) for y in ys[:cut]])
print(f"TIME-ORDERED: calibrate on first half (n={cut}), test on second (n={len(te_y)})")
print(f"  Platt fitted on the CLEAN model: a={a:.3f} b={b:+.3f}\n")
print(f"  {'':28s} {'AUC':>7s} {'log-loss':>9s}")
print(f"  {'raw model':28s} {auc(te_p,te_y):7.4f} {ll(te_p,te_y):9.4f}")
print(f"  {'RECALIBRATED':28s} {auc(cal,te_y):7.4f} {ll(cal,te_y):9.4f}")
print(f"  {'base rate (the gate)':28s} {0.5:7.4f} {ll([base]*len(te_y),te_y):9.4f}")
lb=ll([base]*len(te_y),te_y); lc=ll(cal,te_y)
print(f"\n  RECALIBRATED vs base rate: {100*(lb-lc)/lb:+.2f}% log-loss -> {'*** BEATS THE BASE RATE ***' if lc<lb else 'still worse'}")
