"""READ-ONLY. Can BTTS be priced from Pinnacle 1x2 + O/U 2.5 (Poisson / Dixon-Coles)?
Compare derived-Pinnacle BTTS vs soft-book consensus vs Coolbet on realised outcomes.
Expectation (stated before running): derived LL within ~0.002 nats of consensus;
independence Poisson slightly miscalibrated; Coolbet-vs-derived edge strategy CLV-unknowable,
ROI noise-dominated."""
import os, math, sys, json
import numpy as np, psycopg2
from scipy.optimize import least_squares, minimize_scalar
from scipy.stats import poisson

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 120
conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("set statement_timeout='900s'")

def latest_complete(market, legs, book_filter):
    q = f"""
    with s as (
      select o.match_id, o.bookmaker, o.timestamp, o.selection, o.odds
      from odds_snapshots o join matches m on m.id=o.match_id
      where o.market=%s and {book_filter}
        and m.status='finished' and m.score_home is not null
        and m.date > now()-interval '{DAYS} days'
        and o.timestamp < m.date and coalesce(o.is_live,false)=false
    ), g as (
      select match_id, bookmaker, timestamp, count(distinct selection) k,
             json_object_agg(selection, odds) j
      from s group by 1,2,3)
    select distinct on (match_id, bookmaker) match_id, bookmaker, timestamp, j
    from g where k=%s order by match_id, bookmaker, timestamp desc"""
    cur.execute(q, (market, legs))
    return cur.fetchall()

def shin(odds):
    q = np.array([1/o for o in odds]); B = q.sum()
    if len(q) == 2:
        # Shin for 2-way ~ equal-margin-by-odds; use closed form iterative
        pass
    z = 0.0
    for _ in range(50):
        p = (np.sqrt(z*z + 4*(1-z)*q*q/B) - z) / (2*(1-z))
        z = (p.sum() - 1) / (len(q) - 1) + z if False else z
        break
    # iterate z properly
    lo, hi = 0.0, 0.3
    for _ in range(60):
        z = (lo+hi)/2
        p = (np.sqrt(z*z + 4*(1-z)*q*q/B) - z) / (2*(1-z))
        if p.sum() > 1: lo = z
        else: hi = z
    return p / p.sum()

print("loading pinnacle 1x2...", flush=True)
p1 = {r[0]: (r[2], r[3]) for r in latest_complete('1x2', 3, "o.bookmaker='Pinnacle'")}
print("loading pinnacle ou25...", flush=True)
pou = {r[0]: (r[2], r[3]) for r in latest_complete('over_under_25', 2, "o.bookmaker='Pinnacle'")}
print("loading btts all books...", flush=True)
bt = {}
for mid, bk, ts, j in latest_complete('btts', 2, "o.bookmaker not in ('api-football-live','Unibet-Kambi')"):
    bt.setdefault(mid, {})[bk] = (ts, j)
cur.execute(f"""select id, date, score_home, score_away from matches
  where status='finished' and score_home is not null and date > now()-interval '{DAYS} days'""")
res = {r[0]: r[1:] for r in cur.fetchall()}
print(len(p1), len(pou), len(bt), len(res))

N = 11
def grid(lh, la, rho=0.0):
    ph = poisson.pmf(np.arange(N), lh); pa = poisson.pmf(np.arange(N), la)
    M = np.outer(ph, pa)
    if rho:
        M[0,0] *= 1 - lh*la*rho; M[0,1] *= 1 + lh*rho
        M[1,0] *= 1 + la*rho;    M[1,1] *= 1 - rho
    return M / M.sum()

I, J = np.meshgrid(np.arange(N), np.arange(N), indexing='ij')
def probs(M):
    return (M[I>J].sum(), M[I==J].sum(), M[I<J].sum(), M[(I+J)>2].sum(), M[1:,1:].sum())

def fit(pH, pD, pA, pO, rho):
    def r(x):
        lh, la = np.exp(x); h, d, a, o, _ = probs(grid(lh, la, rho))
        return [h-pH, a-pA, o-pO]
    s = least_squares(r, x0=[math.log(1.4), math.log(1.1)])
    lh, la = np.exp(s.x)
    return lh, la, s.fun

rows = []
for mid in set(p1) & set(pou) & set(bt) & set(res):
    j1 = p1[mid][1]; jo = pou[mid][1]
    try:
        pH, pD, pA = shin([float(j1['home']), float(j1['draw']), float(j1['away'])])
        pO, pU = shin([float(jo['over']), float(jo['under'])])
    except Exception:
        continue
    kick, sh, sa = res[mid]
    y = int(sh > 0 and sa > 0)
    books = {}
    for bk, (ts, jb) in bt[mid].items():
        try:
            oy, on = float(jb['yes']), float(jb['no'])
        except Exception:
            continue
        m = 1/oy + 1/on
        if not (0.98 < m < 1.20):
            continue
        books[bk] = (shin([oy, on])[0], oy, on, (kick - ts).total_seconds()/60)
    if len(books) < 3:
        continue
    rows.append(dict(mid=mid, kick=kick, y=y, pH=pH, pD=pD, pA=pA, pO=pO,
                     pin_age=(kick - p1[mid][0]).total_seconds()/60, books=books))
print("fixtures with pinnacle 1x2+ou and >=3 btts books:", len(rows), flush=True)

# fit lambdas for rho grid
RHOS = [0.0, -0.05, -0.10, -0.15]
for r in rows:
    r['d'] = {}
    for rho in RHOS:
        lh, la, fun = fit(r['pH'], r['pD'], r['pA'], r['pO'], rho)
        r['d'][rho] = probs(grid(lh, la, rho))[4]
        if rho == 0.0:
            r['lh'], r['la'], r['resid'] = lh, la, float(np.abs(fun).max())

rows.sort(key=lambda r: r['kick'])
def ll(p, y): p = min(max(p, 1e-6), 1-1e-6); return -(y*math.log(p) + (1-y)*math.log(1-p))
Y = np.array([r['y'] for r in rows])
out = {"n": len(rows), "btts_rate": float(Y.mean())}
cons = np.array([np.median([b[0] for b in r['books'].values()]) for r in rows])
out['LL_base'] = float(np.mean([ll(Y.mean(), y) for y in Y]))
out['LL_consensus'] = float(np.mean([ll(p, y) for p, y in zip(cons, Y)]))
for rho in RHOS:
    d = np.array([r['d'][rho] for r in rows])
    out[f'LL_derived_rho{rho}'] = float(np.mean([ll(p, y) for p, y in zip(d, Y)]))
    out[f'mean_derived_rho{rho}'] = float(d.mean())
    out[f'corr_derived_cons_rho{rho}'] = float(np.corrcoef(d, cons)[0, 1])
    out[f'mad_derived_cons_rho{rho}'] = float(np.mean(np.abs(d - cons)))
out['mean_consensus'] = float(cons.mean())
# Coolbet
cb = [(r, r['books']['Coolbet']) for r in rows if 'Coolbet' in r['books']]
out['n_coolbet'] = len(cb)
out['LL_coolbet_devig'] = float(np.mean([ll(b[0], r['y']) for r, b in cb]))
out['LL_cons_on_coolbet_rows'] = float(np.mean([ll(np.median([x[0] for x in r['books'].values()]), r['y']) for r, b in cb]))
out['LL_derived_rho-0.1_on_coolbet_rows'] = float(np.mean([ll(r['d'][-0.10], r['y']) for r, b in cb]))
# blend test: logistic y ~ logit(cons) + logit(derived) on second half fitted on first half
from math import log
def logit(p): p = min(max(p, 1e-4), 1-1e-4); return log(p/(1-p))
h = len(rows)//2
import numpy.linalg as la_
def fit_logit(X, y):
    w = np.zeros(X.shape[1])
    for _ in range(50):
        p = 1/(1+np.exp(-X@w)); W = p*(1-p)
        H = X.T @ (X*W[:,None]) + 1e-6*np.eye(X.shape[1]); g = X.T @ (y-p)
        w += la_.solve(H, g)
    return w
best_rho = min(RHOS, key=lambda rr: out[f'LL_derived_rho{rr}'])
Xc = np.array([[1, logit(c)] for c in cons]); Xb = np.array([[1, logit(c), logit(r['d'][best_rho])] for c, r in zip(cons, rows)])
wc = fit_logit(Xc[:h], Y[:h]); wb = fit_logit(Xb[:h], Y[:h])
pc = 1/(1+np.exp(-Xc[h:]@wc)); pb = 1/(1+np.exp(-Xb[h:]@wb))
lc = np.array([ll(p, y) for p, y in zip(pc, Y[h:])]); lb = np.array([ll(p, y) for p, y in zip(pb, Y[h:])])
dlt = lc - lb
out['blend'] = dict(best_rho=best_rho, w_cons_only=wc.tolist(), w_blend=wb.tolist(),
                    dLL_heldout=float(dlt.mean()), t=float(dlt.mean()/(dlt.std()/math.sqrt(len(dlt)))), n_test=len(dlt))
# strategy: Coolbet side where coolbet_odds * p_anchor - 1 > thr, anchor in {derived, consensus_ex_coolbet}
strat = {}
for name in ['derived', 'cons_ex_cb']:
    for thr in [0.0, 0.03, 0.05, 0.08]:
        pnl = []; n = 0
        for r, b in cb:
            if name == 'derived': pa = r['d'][best_rho]
            else:
                others = [x[0] for k, x in r['books'].items() if k != 'Coolbet']
                if len(others) < 2: continue
                pa = float(np.median(others))
            for side, p, o in (('yes', pa, b[1]), ('no', 1-pa, b[2])):
                if o*p - 1 > thr:
                    won = (r['y'] == 1) if side == 'yes' else (r['y'] == 0)
                    pnl.append(o-1 if won else -1)
        a = np.array(pnl)
        strat[f'{name}_thr{thr}'] = dict(n=len(a), roi=float(a.mean()) if len(a) else None,
                                        t=float(a.mean()/(a.std()/math.sqrt(len(a)))) if len(a) > 2 else None)
out['coolbet_strat_close'] = strat
out['pin_age_min_median'] = float(np.median([r['pin_age'] for r in rows]))
out['cb_age_min_median'] = float(np.median([b[3] for r, b in cb]))
out['fit_resid_max_median'] = float(np.median([r['resid'] for r in rows]))
# calibration of derived by decile
d = np.array([r['d'][best_rho] for r in rows]); qs = np.quantile(d, np.linspace(0, 1, 6))
out['calib_derived'] = [(float(d[(d>=qs[i])&(d<=qs[i+1])].mean()), float(Y[(d>=qs[i])&(d<=qs[i+1])].mean())) for i in range(5)]
qs = np.quantile(cons, np.linspace(0, 1, 6))
out['calib_cons'] = [(float(cons[(cons>=qs[i])&(cons<=qs[i+1])].mean()), float(Y[(cons>=qs[i])&(cons<=qs[i+1])].mean())) for i in range(5)]
print(json.dumps(out, indent=1, default=str))
