import pandas as pd, numpy as np
from scipy.optimize import brentq
d=pd.read_csv('ah_pit.csv',parse_dates=['timestamp'])
d=d[d.odds>1]
d['timestamp']=d.timestamp.dt.floor('min')
# pair home/away at the same write (same book, match, line, timestamp)
w=d.pivot_table(index=['match_id','bookmaker','handicap_line','timestamp'],columns='selection',values='odds',aggfunc='first').dropna().reset_index()
mb=d.groupby(['match_id','bookmaker','handicap_line','timestamp']).mins_before.first()
w=w.join(mb,on=['match_id','bookmaker','handicap_line','timestamp'])
sc=d.groupby('match_id')[['score_home','score_away']].first()
print(w.groupby('bookmaker').size())
def powdv(oh,oa):
    f=lambda k:(1/oh)**k+(1/oa)**k-1
    try: k=brentq(f,0.5,3); return (1/oh)**k
    except: return np.nan
pin=w[w.bookmaker=='Pinnacle'].copy()
pin['ph']=[powdv(a,b) for a,b in zip(pin.home,pin.away)]
pin=pin.sort_values('timestamp')
close=pin.sort_values('timestamp').groupby(['match_id','handicap_line']).tail(1).set_index(['match_id','handicap_line'])
close=close[close.mins_before<=15]
out=[]
for b in ['Coolbet','Epicbet']:
    s=w[(w.bookmaker==b)&(w.mins_before>=30)&(w.mins_before<=360)].sort_values('timestamp')
    # dedupe: one decision per (match,line) per 30-min bucket -> use earliest quote in each bucket
    s['bk']=(s.mins_before//30)
    s=s.groupby(['match_id','handicap_line','bk']).head(1)
    m=pd.merge_asof(s,pin[['match_id','handicap_line','timestamp','ph']].rename(columns={'timestamp':'pts'}),left_on='timestamp',right_on='pts',by=['match_id','handicap_line'],direction='backward',tolerance=pd.Timedelta('15min'))
    m=m.dropna(subset=['ph']).join(close[['ph']].rename(columns={'ph':'ph_close'}),on=['match_id','handicap_line']).dropna(subset=['ph_close'])
    for side in ['home','away']:
        q=m.copy(); q['side']=side; q['o']=q[side]
        q['p_now']=q.ph if side=='home' else 1-q.ph
        q['p_cl']=q.ph_close if side=='home' else 1-q.ph_close
        q['psoft']=(1/q[side])/(1/q.home+1/q.away)
        out.append(q)
Q=pd.concat(out); Q=Q[(Q.psoft-Q.p_now).abs()<=0.10]
Q['ev_now']=Q.p_now*Q.o-1; Q['clv']=Q.p_cl*Q.o-1
Q['absL']=Q.handicap_line.abs()
Q=Q.join(sc,on='match_id')
x=(Q.score_home-Q.score_away)+Q.handicap_line; x=np.where(Q.side=='home',x,-x)
Q['r']=np.where(x>0,Q.o-1,np.where(x==0,0,-1.0)); Q.loc[(Q.handicap_line*2)%1!=0,'r']=np.nan  # quarter lines: skip r
def summ(g):
    # one bet per (match,line,side): take first qualifying decision
    g=g.sort_values('timestamp').groupby(['match_id','handicap_line','side']).head(1)
    mm=g.groupby('match_id').clv.agg(['sum','count']); n=len(g); mu=g.clv.mean()
    se=np.sqrt(((mm['sum']-mm['count']*mu)**2).sum())/n
    return pd.Series({'n':n,'fx':g.match_id.nunique(),'ev_now':g.ev_now.mean()*100,'clv_vs_pin_close':mu*100,'lo':(mu-1.96*se)*100,'hi':(mu+1.96*se)*100,'roi':g.r.mean()*100})
Q=Q.reset_index(drop=True)
print('days covered', Q.timestamp.min(), Q.timestamp.max())
for thr in [-1, 0.0, 0.02, 0.04]:
    pass
for thr in [-1, 0.0, 0.02, 0.04]:
    print(f'\n== decision EV (power devig, PIT) >= {thr}')
    Z=Q[Q.ev_now>=thr].reset_index(drop=True); Z['wide']=Z.absL>=1
    print(Z.groupby(['bookmaker','wide']).apply(summ).round(2))
