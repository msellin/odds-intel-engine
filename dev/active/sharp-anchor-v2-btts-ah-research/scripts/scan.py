"""Sharp-anchor feasibility scan: BTTS + AH (full/half) vs the 1x2 benchmark. READ-ONLY, local CSVs.
See prereg.md (written before outcomes)."""
import sys, math, json
import numpy as np, pandas as pd
sys.path.insert(0, '/Users/margussellin/www/odds-intel-engine')
from workers.model.devig import devig  # Shin

D = '/private/tmp/claude-501/-Users-margussellin-www-odds-intel-engine/327c52bc-b4f7-45d5-86b9-d0d5d8f1cab7/scratchpad/data/'
SOFT = ['Coolbet', 'Epicbet', 'Unibet-Site', 'Tonybet']
CONS = ['Marathonbet', '1xBet', 'William Hill', 'BetVictor', 'Betano', 'Betfair', 'Bet365',
        'Superbet', '10Bet', '888Sport', 'SBO', 'Dafabet']
SIDES = {'1x2': ['home', 'draw', 'away'], 'btts': ['yes', 'no'], 'asian_handicap': ['home', 'away']}
H = pd.Timedelta(hours=1)
TAU = {'decision': pd.Timedelta(hours=2), 'decision4h': pd.Timedelta(hours=4), 'close': pd.Timedelta(0)}
WIN = pd.Timedelta(minutes=60)

M = pd.read_csv(D + 'matches.csv', parse_dates=['ko'])
M = M.set_index('match_id')


def load(mkt):
    d = pd.read_csv(D + mkt + '.csv')
    d['ts'] = pd.to_datetime(d['ts'], utc=True, format='ISO8601')
    d['line'] = d['line'].fillna(0.0) if mkt != 'asian_handicap' else d['line']
    d = d.join(M['ko'], on='match_id')
    d['rel'] = d['ko'] - d['ts']            # time before KO (>=0)
    return d


def complete_latest(d, sides, lo, hi):
    """Per (match,bk,line): latest COMPLETE market (all sides at one ts) with rel in [lo,hi]."""
    w = d[(d.rel >= lo) & (d.rel <= hi)]
    w = w.copy()
    w['n'] = w.groupby(['match_id', 'bk', 'line', 'ts'])['sel'].transform('nunique')
    w = w[w.n == len(sides)]
    last = w.groupby(['match_id', 'bk', 'line'])['ts'].transform('max')
    w = w[w.ts == last]
    p = w.pivot_table(index=['match_id', 'bk', 'line', 'ts'], columns='sel', values='odds', aggfunc='last').reset_index()
    return p


def shin_rows(p, sides):
    out = []
    for r in p[sides].itertuples(index=False):
        pr = devig(list(r))
        out.append(pr if pr else [np.nan] * len(sides))
    a = np.array(out)
    for i, s in enumerate(sides):
        p['P_' + s] = a[:, i]
    p['ovr'] = sum(1 / p[s] for s in sides) - 1
    return p


def anchors(d, sides, lo, hi):
    """Pinnacle and consensus fair probs per (match,line) for window rel in [lo,hi]."""
    p = complete_latest(d[d.bk.isin(CONS + ['Pinnacle'])], sides, lo, hi)
    if p.empty or not all(s in p.columns for s in sides):
        e = pd.DataFrame(columns=['P_' + s for s in sides] + ['ts', 'ovr', 'nbooks'],
                         index=pd.MultiIndex.from_tuples([], names=['match_id', 'line']))
        return e, e
    p = shin_rows(p, sides)
    pin = p[p.bk == 'Pinnacle'].set_index(['match_id', 'line'])
    c = p[p.bk.isin(CONS)]
    agg = {('P_' + s): 'mean' for s in sides}
    agg['bk'] = 'count'; agg['ts'] = 'max'
    cons = c.groupby(['match_id', 'line']).agg(agg).rename(columns={'bk': 'nbooks'})
    cons = cons[cons.nbooks >= 5]
    return pin, cons


def soft_latest(d, lo, hi):
    w = d[d.bk.isin(SOFT) & (d.rel >= lo) & (d.rel <= hi)]
    w = w.sort_values('ts')
    return w.groupby(['match_id', 'bk', 'line', 'sel']).tail(1)


def grade(mkt, sel, line, h, a):
    if mkt == '1x2':
        res = 'home' if h > a else ('away' if a > h else 'draw')
        return 1.0 if sel == res else 0.0
    if mkt == 'btts':
        y = (h > 0 and a > 0)
        return 1.0 if (sel == 'yes') == y else 0.0
    m = h - a + line if sel == 'home' else -(h - a + line)
    return 1.0 if m > 0 else (0.5 if m == 0 else 0.0)   # 0.5 marks push


def build(mkt, d, regime):
    sides = SIDES[mkt]
    tau = TAU[regime]
    lo, hi = tau, tau + WIN
    pin, cons = anchors(d, sides, lo, hi)
    soft = soft_latest(d, lo, hi)
    # closes (strictly later than decision anchor; last hour pre-KO)
    if regime != 'close':
        pinC, consC = anchors(d, sides, pd.Timedelta(0), WIN)
    rows = []
    for anc_name, A in (('pinnacle', pin), ('consensus', cons)):
        if mkt == 'btts' and anc_name == 'pinnacle':
            continue
        j = soft.join(A[[f'P_{s}' for s in sides] + ['ts'] + (['ovr'] if anc_name == 'pinnacle' else ['nbooks'])],
                      on=['match_id', 'line'], rsuffix='_a', how='inner')
        j = j[(j.ts - j.ts_a).abs() <= WIN]
        j['P'] = [getattr(r, 'P_' + r.sel) for r in j.itertuples()]
        j['edge'] = j.odds * j.P - 1
        j['ratio'] = j.odds * j.P - 1  # same as edge (odds/fair-1 == odds*P-1)
        if regime != 'close':
            C = pinC if anc_name == 'pinnacle' else consC
            jc = j.join(C[[f'P_{s}' for s in sides] + ['ts']], on=['match_id', 'line'], rsuffix='_c', how='left')
            jc['Pc'] = [getattr(r, 'P_' + r.sel + '_c') if (r.ts_c == r.ts_c) else np.nan for r in jc.itertuples()]
            jc.loc[~(jc.ts_c > jc.ts_a), 'Pc'] = np.nan     # close must be a LATER snapshot
            jc['clv'] = jc.odds * jc.Pc - 1
            j = jc
        else:
            j['clv'] = j['edge']
        j['anchor'] = anc_name
        rows.append(j)
    if not rows:
        return pd.DataFrame()
    X = pd.concat(rows, ignore_index=True)
    X['market'] = mkt; X['regime'] = regime
    X = X.join(M[['status', 'score_home', 'score_away', 'ko']], on='match_id', rsuffix='_m')
    fin = (X.status == 'finished') & X.score_home.notna()
    X['win'] = np.nan
    X.loc[fin, 'win'] = [grade(mkt, s, l, h, a) for s, l, h, a in
                         zip(X.loc[fin, 'sel'], X.loc[fin, 'line'], X.loc[fin, 'score_home'], X.loc[fin, 'score_away'])]
    X['pnl'] = np.where(X.win == 1, X.odds - 1, np.where(X.win == 0.5, 0.0, -1.0))
    X.loc[X.win.isna(), 'pnl'] = np.nan
    return X


if __name__ == '__main__':
    out = []
    for mkt in ['1x2', 'btts', 'asian_handicap']:
        d = load(mkt)
        for regime in ['close', 'decision', 'decision4h']:
            X = build(mkt, d, regime)
            print(mkt, regime, len(X), flush=True)
            out.append(X)
        del d
    A = pd.concat(out, ignore_index=True)
    A.to_pickle(D + 'legs.pkl')
