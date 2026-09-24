import pandas as pd, numpy as np
pd.set_option('display.width',200)
D='data/'
X=pd.read_csv(D+'exchange.csv'); X['ts']=pd.to_datetime(X.captured_at,utc=True,format='ISO8601'); X['ko']=pd.to_datetime(X.ko,utc=True,format='ISO8601')
print(X.groupby('market').agg(rows=('id','size'),fx=('match_id','nunique'),first=('ts','min'),last=('ts','max')).to_string())
X=X[X.market.isin(['btts','asian_handicap'])].copy()
X['line']=X.handicap_line if True else None
X.loc[X.market=='btts','line']=0.0
X=X[(X.market=='btts')|((X.line*2)==np.floor(X.line*2))]
X['spread']=X.lay/X.back-1
X['liq']=(X.spread<=0.05)&(X.market_matched>=1000)&X.back.notna()&X.lay.notna()
print('liquid share by market:', X.groupby('market').liq.mean().round(3).to_dict())
print('market_matched quantiles (EUR):'); print(X.groupby('market').market_matched.describe(percentiles=[.1,.5,.9]).round(0))
# latest snapshot per (match, market, line, sel); both sides liquid & same capture
X=X.sort_values('ts'); L=X.groupby(['match_id','market','line','selection']).tail(1)
P=L.pivot_table(index=['match_id','market','line','ts','ko'],columns='selection',values=['back','lay','liq'],aggfunc='last').reset_index()
out=[]
for mkt,(a,b) in {'btts':('yes','no'),'asian_handicap':('home','away')}.items():
    q=P[P.market==mkt].dropna(subset=[('back',a),('back',b),('lay',a),('lay',b)])
    q=q[(q[('liq',a)]==True)&(q[('liq',b)]==True)]
    ma=(q[('back',a)]+q[('lay',a)])/2; mb=(q[('back',b)]+q[('lay',b)])/2
    pa=(1/ma)/(1/ma+1/mb)
    for s,p in ((a,pa),(b,1-pa)):
        out.append(pd.DataFrame({'match_id':q.match_id.values,'market':mkt,'line':q.line.values,'sel':s,'P':p.values,'xts':q.ts.values,'ko':q.ko.values,
                                 'mid_ovr':(1/ma+1/mb-1).values}))
E_=pd.concat(out)
print('liquid two-sided exchange markets (latest):', E_.groupby('market').match_id.nunique().to_dict(), ' mid overround median', E_.mid_ovr.median().round(4))
S=pd.read_csv(D+'soft_now.csv'); S['ts']=pd.to_datetime(S.ts,utc=True,format='ISO8601'); S.loc[S.market=='btts','line']=0.0
S=S.sort_values('ts').groupby(['match_id','bk','market','line','sel']).tail(1)
J=S.merge(E_,on=['match_id','market','line','sel'])
J['gap_min']=(J.ts-pd.to_datetime(J.xts,utc=True)).dt.total_seconds()/60
J=J[J.gap_min.abs()<=60]
J['edge']=J.odds*J.P-1
print(J.groupby(['market','bk']).agg(fx=('match_id','nunique'),legs=('edge','size'),med=('edge','median'),gt2=('edge',lambda s:(s>.02).mean()),gt3=('edge',lambda s:(s>.03).mean()),gt5=('edge',lambda s:(s>.05).mean())).round(3).to_string())
print(J[J.edge>0.03][['market','bk','line','sel','odds','P','edge','gap_min']].round(3).to_string())
