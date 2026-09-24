import pandas as pd, numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson
q=pd.read_csv('pin_1x2_ou.csv')
q['market']=q.market.str.lower()
q=q[(q.mins_before<=60)]
X=q[q.market=='1x2'].pivot_table(index='match_id',columns='selection',values='odds').dropna()
X=X.div(1,axis=0); inv=1/X; X=inv.div(inv.sum(axis=1),axis=0)  # proportional devig
ou={}
for L,mk in [(1.5,'over_under_15'),(2.5,'over_under_25'),(3.5,'over_under_35')]:
    o=q[q.market==mk].pivot_table(index='match_id',columns='selection',values='odds').dropna()
    ou[L]=(1/o.over)/(1/o.over+1/o.under)
OU=pd.DataFrame(ou)
M=X.join(OU,how='inner').dropna(subset=[2.5])
print('matches with 1x2+OU2.5 within 60m:',len(M))
G=np.arange(11)
def mat(lh,la,rho):
    P=np.outer(poisson.pmf(G,lh),poisson.pmf(G,la))
    if rho:
        P[0,0]*=1-lh*la*rho; P[0,1]*=1+lh*rho; P[1,0]*=1+la*rho; P[1,1]*=1-rho
    return P/P.sum()
D=np.subtract.outer(G,G); T=np.add.outer(G,G)
def fit(r,dc):
    tgt=[(D>0,r.home),(D==0,r.draw)]+[(T>L,r[L]) for L in (1.5,2.5,3.5) if not np.isnan(r[L])]
    def f(x):
        lh,la=np.exp(x[:2]); rho=x[2] if dc else 0
        P=mat(lh,la,rho); return sum((P[m].sum()-t)**2 for m,t in tgt)
    x0=[np.log(1.4),np.log(1.1)]+([0.0] if dc else [])
    b=[(-3,2),(-3,2)]+([(-0.3,0.3)] if dc else [])
    s=minimize(f,x0,bounds=b,method='L-BFGS-B'); lh,la=np.exp(s.x[:2])
    return lh,la,(s.x[2] if dc else 0),s.fun
res=[]
for mid,r in M.iterrows():
    a=fit(r,False); b=fit(r,True)
    res.append((mid,*a,*b))
R=pd.DataFrame(res,columns=['match_id','lh','la','r0','sse','lh2','la2','rho','sse2']).set_index('match_id')
print(R[['sse','sse2','rho']].describe().round(5))
R.to_pickle('lambdas.pkl')
