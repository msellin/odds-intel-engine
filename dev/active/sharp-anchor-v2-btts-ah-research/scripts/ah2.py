import pandas as pd, numpy as np
p=pd.read_pickle('ah_pairs.pkl')
d=pd.read_csv('ah_close.csv',parse_dates=['timestamp','date'])
m=d.groupby('match_id').agg(sh=('score_home','first'),sa=('score_away','first'),status=('status','first'),date=('date','first'),league=('league_id','first'))
ts=d.pivot_table(index=['match_id','bookmaker','handicap_line'],columns='selection',values='timestamp',aggfunc='first')
p=p.join(ts.max(axis=1).rename('ts'),on=['match_id','bookmaker','handicap_line'])
p=p.join(m,on='match_id')
print(m.status.value_counts().head())
p=p[p.sh.notna()]
for b in ['Pinnacle','Coolbet','Epicbet','Tonybet']:
    s=p[p.bookmaker==b].mins
    print(b,'mins_before quantiles',s.quantile([.1,.25,.5,.75,.9]).round(1).tolist())
def ret(margin,L,side,odds):
    # returns per unit stake for home/away at home-perspective line L
    def one(Lh):
        x=margin+Lh if side=='home' else -(margin+Lh)
        return np.where(x>0,odds-1,np.where(x==0,0.0,-1.0))
    q=((L*2)%1)!=0
    return np.where(q,0.5*one(L-0.25)+0.5*one(L+0.25),one(L))
p.to_pickle('ah_pairs2.pkl')
pin=p[p.bookmaker=='Pinnacle'].set_index(['match_id','handicap_line'])
rows=[]
for b in ['Coolbet','Epicbet','Tonybet']:
    s=p[p.bookmaker==b].join(pin[['home','away','ovr','ts','mins']],on=['match_id','handicap_line'],rsuffix='_pin').dropna(subset=['home_pin'])
    s['dt']=(s.ts-s.ts_pin).dt.total_seconds().abs()/60
    for side in ['home','away']:
        q=s.copy(); q['side']=side
        ph=(1/q.home_pin)/(1/q.home_pin+1/q.away_pin)
        q['p']=ph if side=='home' else 1-ph
        q['o']=q[side]
        q['ev']=q.p*q.o-1
        q['r']=ret((q.sh-q.sa).values,q.handicap_line.values,side,q.o.values)
        rows.append(q)
A=pd.concat(rows)
A['ltype']=np.where(A.handicap_line%1==0,'whole',np.where((A.handicap_line*2)%1==0,'half','quarter'))
A['absL']=A.handicap_line.abs()
A.to_pickle('ah_eval.pkl')
def clus(g):
    # match-clustered se of mean r
    mm=g.groupby('match_id').r.agg(['sum','count'])
    n=len(g); mu=g.r.mean()
    se=np.sqrt(((mm['sum']-mm['count']*mu)**2).sum())/n
    return pd.Series({'n':n,'fx':g.match_id.nunique(),'ev':g.ev.mean()*100,'roi':mu*100,'lo':(mu-1.96*se)*100,'hi':(mu+1.96*se)*100})
print('\n== ALL legs (flat), latest pre-KO, any alignment')
print(A.groupby('bookmaker').apply(clus).round(2))
al=A[(A.dt<=20)&(A.mins<=30)&(A.mins_pin<=30)]
print('\n== aligned |dt|<=20m & both <=30m pre-KO')
print(al.groupby('bookmaker').apply(clus).round(2))
for thr in [0.0,0.02,0.03,0.05]:
    print(f'\n== aligned, EV>={thr}')
    print(al[al.ev>=thr].groupby(['bookmaker']).apply(clus).round(2))
    print(al[al.ev>=thr].groupby(['bookmaker','ltype']).apply(clus).round(2))
