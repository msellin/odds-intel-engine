import sys, math
import numpy as np, pandas as pd
from scipy import stats
sys.path.insert(0, '/private/tmp/claude-501/-Users-margussellin-www-odds-intel-engine/327c52bc-b4f7-45d5-86b9-d0d5d8f1cab7/scratchpad')
import scan
from scan import SIDES, SOFT, D, anchors, load, WIN

pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40); pd.set_option('display.max_rows', 400)
A = pd.read_pickle(D + 'legs.pkl')
INTACT = pd.Timestamp('2026-09-17', tz='UTC')
A['era'] = np.where(A.ko >= INTACT, 'intact', 'pruned')
rng = np.random.default_rng(7)

# ---------- 0. AH sign convention check (close regime, Pinnacle-joined legs) ----------
ah = A[(A.market == 'asian_handicap') & (A.regime == 'close') & (A.anchor == 'pinnacle')]
print('AH sign check: median edge / share |edge|>25pct by book & side')
print(ah.groupby(['bk', 'sel']).edge.agg(['count', 'median', lambda s: (s.abs() > .25).mean()]).round(4))
# strong-home-favourite fixtures: the soft book's most balanced line should be negative (home gives goals)
x1 = A[(A.market == '1x2') & (A.regime == 'close') & (A.anchor == 'pinnacle') & (A.sel == 'home')][['match_id', 'P']].drop_duplicates('match_id')
fav = set(x1[x1.P > .65].match_id)
d = pd.read_csv(D + 'asian_handicap.csv'); d = d[d.match_id.isin(fav)]
d['ts'] = pd.to_datetime(d.ts, utc=True, format='ISO8601')
last = d.sort_values('ts').groupby(['match_id', 'bk', 'line', 'sel']).tail(1).pivot_table(index=['match_id', 'bk', 'line'], columns='sel', values='odds').dropna().reset_index()
last['gap'] = (last.home - last.away).abs()
bal = last.sort_values('gap').groupby(['match_id', 'bk']).head(1)
print('balanced-line sign on home favourites (P_home>0.65): share line<0 by book')
print(bal.groupby('bk').line.agg(['count', lambda s: (s < 0).mean(), 'median']).round(3))
del d

# ---------- 1. data-quality flags ----------
# (d) board mismatch: soft book 1x2 (latest pre-KO) vs >=5-book consensus 1x2, normalised, any leg off by >0.12
d1 = load('1x2')
soft1 = d1[d1.bk.isin(SOFT)].sort_values('ts').groupby(['match_id', 'bk', 'sel']).tail(1)
sp = soft1.pivot_table(index=['match_id', 'bk'], columns='sel', values='odds').dropna()
sp = sp.div(1, axis=0).rdiv(1); sp = sp.div(sp.sum(axis=1), axis=0)
cons1 = d1[d1.bk.isin(scan.CONS)].sort_values('ts').groupby(['match_id', 'bk', 'sel']).tail(1)
cp = cons1.pivot_table(index=['match_id', 'bk'], columns='sel', values='odds').dropna().rdiv(1)
cp = cp.div(cp.sum(axis=1), axis=0).groupby('match_id').agg(['median', 'count'])
cm = pd.DataFrame({s: cp[(s, 'median')] for s in ['home', 'draw', 'away']})[cp[('home', 'count')] >= 4]
spj = sp.join(cm, on='match_id', rsuffix='_c').dropna()
spj['dev'] = np.max([(spj[s] - spj[s + '_c']).abs() for s in ['home', 'draw', 'away']], axis=0)
board_bad = set(spj[spj.dev > 0.12].index)
print('board check: (fixture,soft book) pairs tested', len(spj), 'off-board', len(board_bad))
print(pd.Series([b for _, b in board_bad]).value_counts())
del d1

# (c)/(e) soft two-way pairs for ordering and overround
def soft_pair_info(X):
    X = X.copy()
    oth = {'yes': 'no', 'no': 'yes', 'home': 'away', 'away': 'home'}
    key = ['regime', 'anchor', 'match_id', 'bk', 'line']
    tab = X.set_index(key + ['sel'])['odds']
    Ptab = X.set_index(key + ['sel'])['P']
    other = [tab.get(tuple(r[k] for k in key) + (oth.get(r['sel'], ''),), np.nan) for r in X[key + ['sel']].to_dict('records')]
    oP = [Ptab.get(tuple(r[k] for k in key) + (oth.get(r['sel'], ''),), np.nan) for r in X[key + ['sel']].to_dict('records')]
    X['odds_other'] = other; X['P_other'] = oP
    X['soft_ovr'] = 1 / X.odds + 1 / X.odds_other - 1
    X['invert'] = ((X.P - X.P_other).abs() >= 0.10) & (np.sign(X.P - X.P_other) != np.sign(1 / X.odds - 1 / X.odds_other))
    X['bad_ovr'] = (X.soft_ovr < 0) | (X.soft_ovr > 0.15)
    return X

two = A[A.market != '1x2']
two = soft_pair_info(two)
A = pd.concat([A[A.market == '1x2'].assign(invert=False, bad_ovr=False, soft_ovr=np.nan), two], ignore_index=True)
A['board'] = [(m, b) in board_bad for m, b in zip(A.match_id, A.bk)]
A['outlier'] = A.edge > 0.25
A['collision_era'] = A.market.isin(['btts', 'asian_handicap']) & (A.bk == 'Coolbet') & (A.ts < pd.Timestamp('2026-09-18', tz='UTC'))
A['not_finished'] = A.win.isna()

# (a) stale: soft price unchanged since run start while the anchor moved >=1.5pp (intact era, flagged legs only)
def stale_flags(L):
    res = {}
    for mkt in L.market.unique():
        d = load(mkt)
        d = d[d.match_id.isin(L[L.market == mkt].match_id)]
        for i, r in L[L.market == mkt].iterrows():
            s = d[(d.match_id == r.match_id) & (d.bk == r.bk) & (d.sel == r.sel) & (d.line == r.line) & (d.ts <= r.ts)].sort_values('ts')
            if s.empty:
                res[i] = (np.nan, np.nan); continue
            chg = s.odds.ne(s.odds.shift()).cumsum()
            start = s[chg == chg.iloc[-1]].ts.iloc[0]
            age_h = (r.ts - start).total_seconds() / 3600
            rel0 = r.ko - start
            dm = d[d.match_id == r.match_id]
            pin, cons = anchors(dm, SIDES[mkt], rel0, rel0 + WIN)
            T = pin if r.anchor == 'pinnacle' else cons
            try:
                p0 = T.loc[(r.match_id, r.line), 'P_' + r.sel]
                p0 = float(p0.iloc[0]) if hasattr(p0, 'iloc') else float(p0)
            except KeyError:
                p0 = np.nan
            res[i] = (age_h, r.P - p0)
    return res

# ---------- 2. cell statistics ----------
THR = [0.02, 0.03, 0.05]

def dedup(F):
    return F.sort_values('edge', ascending=False).drop_duplicates(['regime', 'anchor', 'match_id', 'bk', 'market'])

def cell(F, days):
    n = len(F)
    o = {'n': n, 'per_day': n / days if days else np.nan}
    c = F.clv.dropna()
    o['clv_n'] = len(c)
    o['clv_mean'] = c.mean() if len(c) else np.nan
    o['clv_t'] = c.mean() / (c.std(ddof=1) / math.sqrt(len(c))) if len(c) > 2 and c.std() > 0 else np.nan
    o['clv_p1'] = stats.t.sf(o['clv_t'], len(c) - 1) if o['clv_t'] == o['clv_t'] else 1.0
    p = F.pnl.dropna(); o['roi_n'] = len(p)
    o['roi'] = p.mean() if len(p) else np.nan
    if len(p) >= 5:
        bs = [rng.choice(p.values, len(p)).mean() for _ in range(2000)]
        o['roi_lo'], o['roi_hi'] = np.percentile(bs, [2.5, 97.5])
    return o

rows = []
for (mkt, reg, anc, bk), G in A.groupby(['market', 'regime', 'anchor', 'bk']):
    base = G[G.era == 'intact'] if reg != 'close' else G
    days = base.ko.dt.date.nunique()
    fx = base.match_id.nunique()
    guarded = base[~base.outlier]
    r0 = {'market': mkt, 'regime': reg, 'anchor': anc, 'bk': bk, 'days': days, 'fixtures': fx, 'legs': len(base),
          'med_edge': guarded.edge.median(), 'outlier_share': base.outlier.mean()}
    for t in THR:
        r0[f'leg>{int(t*100)}'] = (guarded.edge > t).mean()
    rows.append(r0)
cov = pd.DataFrame(rows)
print('\n=== COVERAGE + EDGE DISTRIBUTION (per leg, outlier-guarded) ===')
print(cov.round(4).to_string())

# flagged legs, deduped, with DQ
F = A[(A.edge > 0.02) & ~A.outlier & ((A.regime == 'close') | (A.era == 'intact'))].copy()
F = dedup(F)
need_stale = F[(F.era == 'intact') & (F.regime != 'close')]
sf = stale_flags(need_stale)
F['soft_age_h'] = [sf.get(i, (np.nan, np.nan))[0] for i in F.index]
F['anchor_move'] = [sf.get(i, (np.nan, np.nan))[1] for i in F.index]
F['stale'] = (F.soft_age_h >= 1.0) & (F.anchor_move.abs() >= 0.015)
F['dq_any'] = F.board | F.invert | F.bad_ovr | F.stale | F.collision_era
F.to_pickle(D + 'flagged.pkl')

res = []
for (mkt, reg, anc, bk), G in F.groupby(['market', 'regime', 'anchor', 'bk']):
    days = A[(A.market == mkt) & (A.regime == reg)]
    days = (days[days.era == 'intact'] if reg != 'close' else days).ko.dt.date.nunique()
    for t in THR:
        S = G[G.edge > t]
        o = {'market': mkt, 'regime': reg, 'anchor': anc, 'bk': bk, 'thr': t}
        o.update(cell(S, days))
        o['dq_share'] = S.dq_any.mean() if len(S) else np.nan
        o['board'] = S.board.sum(); o['invert'] = S.invert.sum(); o['bad_ovr'] = S.bad_ovr.sum()
        o['stale'] = S.stale.sum(); o['collision'] = S.collision_era.sum()
        C = S[~S.dq_any]
        oc = cell(C, days)
        o['clean_n'] = oc['n']; o['clean_clv'] = oc['clv_mean']; o['clean_clv_t'] = oc['clv_t']; o['clean_roi'] = oc['roi']
        res.append(o)
R = pd.DataFrame(res)
R.to_pickle(D + 'cells.pkl')
print('\n=== FLAGGED (dedup 1/fixture/book/market), CLV + ROI + DQ ===')
print(R.round(4).to_string())
