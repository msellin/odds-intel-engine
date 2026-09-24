"""Gated cells (publisher-equivalent gates) + Holm over the pre-registered family."""
import math
import numpy as np, pandas as pd
from scipy import stats
pd.set_option('display.width', 220); pd.set_option('display.max_rows', 300)
D = '/private/tmp/claude-501/-Users-margussellin-www-odds-intel-engine/327c52bc-b4f7-45d5-86b9-d0d5d8f1cab7/scratchpad/data/'
F = pd.read_pickle(D + 'flagged.pkl')   # edge>2pct, outlier-guarded, dedup per fixture/book/market, DQ flags
A = pd.read_pickle(D + 'legs.pkl')
INTACT = pd.Timestamp('2026-09-17', tz='UTC')
A['era'] = np.where(A.ko >= INTACT, 'intact', 'pruned')

def gate(X):
    g = (X.odds <= 4.0)
    g &= ~((X.anchor == 'pinnacle') & (X.ovr > 0.04))
    return X[g]

def days_cov(mkt, reg, anc, bk):
    x = A[(A.market == mkt) & (A.regime == reg) & (A.anchor == anc) & (A.bk == bk)]
    if reg != 'close':
        x = x[x.era == 'intact']
    return max(x.ko.dt.date.nunique(), 1), x.match_id.nunique()

G = gate(F)
rows = []
BOOKS = {'btts': ['Coolbet', 'Epicbet', 'Unibet-Site', 'Tonybet'], 'asian_handicap': ['Coolbet', 'Epicbet', 'Tonybet'],
         '1x2': ['Coolbet', 'Epicbet', 'Unibet-Site', 'Tonybet']}
ANCH = {'btts': ['consensus', 'exchange'], 'asian_handicap': ['pinnacle', 'consensus', 'exchange'], '1x2': ['pinnacle', 'consensus']}
for reg in ['decision', 'close']:
    for mkt in ['1x2', 'btts', 'asian_handicap']:
        for anc in ANCH[mkt]:
            for bk in BOOKS[mkt]:
                for t in [0.02, 0.03, 0.05]:
                    o = dict(regime=reg, market=mkt, anchor=anc, bk=bk, thr=t)
                    if anc == 'exchange':
                        o.update(n=0, p1=1.0, note='no exchange history (exchange_quotes starts 2026-09-24)'); rows.append(o); continue
                    days, fx = days_cov(mkt, reg, anc, bk)
                    S = G[(G.regime == reg) & (G.market == mkt) & (G.anchor == anc) & (G.bk == bk) & (G.edge > t)]
                    C = S[~S.dq_any]
                    c = C.clv.dropna()
                    o.update(days=days, fixtures_cov=fx, n=len(S), per_day=len(S) / days, dq=S.dq_any.mean() if len(S) else np.nan,
                             n_clean=len(C), clv=c.mean() if len(c) else np.nan, clv_sd=c.std() if len(c) > 1 else np.nan)
                    if len(c) >= 10 and c.std() > 0:
                        tt = c.mean() / (c.std() / math.sqrt(len(c)))
                        o['t'] = tt; o['p1'] = stats.t.sf(tt, len(c) - 1)
                    else:
                        o['t'] = np.nan; o['p1'] = 1.0
                    p = C.pnl.dropna()
                    o['roi_n'] = len(p); o['roi'] = p.mean() if len(p) else np.nan
                    o['roi_se'] = p.std() / math.sqrt(len(p)) if len(p) > 1 else np.nan
                    o['mean_edge'] = C.edge.mean() if len(C) else np.nan
                    rows.append(o)
R = pd.DataFrame(rows)

def holm(p):
    p = np.asarray(p); m = len(p); order = np.argsort(p); adj = np.empty(m); run = 0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (m - k) * p[i])); adj[i] = run
    return adj

fam = R[(R.regime == 'decision') & (R.market != '1x2')].copy()
fam['p_holm'] = holm(fam.p1.fillna(1.0).values)
ben = R[(R.regime == 'decision') & (R.market == '1x2')].copy()
ben['p_holm'] = holm(ben.p1.fillna(1.0).values)
print('FAMILY F size', len(fam), ' benchmark family size', len(ben))
cols = ['market', 'anchor', 'bk', 'thr', 'days', 'fixtures_cov', 'n', 'per_day', 'dq', 'n_clean', 'mean_edge', 'clv', 'clv_sd', 't', 'p_holm', 'roi_n', 'roi', 'roi_se']
print('\n=== DECISION (T-2h, intact 8 days) — family F (BTTS/AH) ===')
print(fam[cols].round(3).to_string())
print('\n=== DECISION — 1x2 benchmark (publisher gates: odds<=4, Pinnacle ovr<=4pct) ===')
print(ben[cols].round(3).to_string())
cl = R[(R.regime == 'close') & (R.anchor != 'exchange')]
print('\n=== CLOSE (60 d; edge==CLV by construction, ROI is the only check) ===')
print(cl[['market', 'anchor', 'bk', 'thr', 'days', 'fixtures_cov', 'n', 'per_day', 'dq', 'n_clean', 'mean_edge', 'roi_n', 'roi', 'roi_se']].round(3).to_string())
R.to_pickle(D + 'gated_cells.pkl'); fam.to_pickle(D + 'family.pkl')
# power: n for +2pct CLV at observed sd
sd = pd.concat([fam.clv_sd, ben.clv_sd]).median()
print('\nmedian per-leg CLV sd in decision cells:', round(sd, 4), ' n for +2pct CLV at 80pct power one-sample:', math.ceil(((1.645 + 0.84) * sd / 0.02) ** 2))
