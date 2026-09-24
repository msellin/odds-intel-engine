import pandas as pd, numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize
R=pd.read_pickle('lambdas.pkl'); A=pd.read_pickle('ah_pairs2.pkl')
G=np.arange(11); D=np.subtract.outer(G,G)
def mat(lh,la,rho):
    P=np.outer(poisson.pmf(G,lh),poisson.pmf(G,la))
    if rho: P[0,0]*=1-lh*la*rho; P[0,1]*=1+lh*rho; P[1,0]*=1+la*rho; P[1,1]*=1-rho
    return P/P.sum()
def peff(P,L):
    # effective fair prob for home at home-perspective line L (handles quarter)
    halves=[L-0.25,L+0.25] if (L*2)%1!=0 else [L,L]
    w=sum(P[D+h>0].sum() for h in halves); l=sum(P[D+h<0].sum() for h in halves)
    return w/(w+l)
A=A[A.handicap_line.abs()<=3]
A=A[A.match_id.isin(R.index)]
cache={}
out=[]
for mid,g in A.groupby('match_id'):
    r=R.loc[mid]; P0=mat(r.lh,r.la,0); P1=mat(r.lh2,r.la2,r.rho)
    for L in g.handicap_line.unique():
        out.append((mid,L,peff(P0,L),peff(P1,L)))
Dv=pd.DataFrame(out,columns=['match_id','handicap_line','p_pois','p_dc'])
A=A.merge(Dv,on=['match_id','handicap_line'])
A['p_dir']=(1/A.home)/(1/A.home+1/A.away)
A.to_pickle('ah_derived.pkl')
pin=A[(A.bookmaker=='Pinnacle')&(A.mins<=60)].copy()
pin['absL']=pin.handicap_line.abs()
pin['Lb']=pd.cut(pin.absL,[-0.1,0.3,0.8,1.3,1.8,3.1],labels=['0/.25','.5/.75','1/1.25','1.5/1.75','2+'])
pin['e_pois']=pin.p_pois-pin.p_dir; pin['e_dc']=pin.p_dc-pin.p_dir
print('derived minus Pinnacle-AH direct, by |line|:')
print(pin.groupby('Lb').agg(n=('e_dc','size'),bias_pois=('e_pois','mean'),mad_pois=('e_pois',lambda s:s.abs().mean()),bias_dc=('e_dc','mean'),mad_dc=('e_dc',lambda s:s.abs().mean())).round(4))
# outcome scoring on binary-resolving legs: half lines, and whole lines excluding pushes
m=pin.sh-pin.sa; x=m+pin.handicap_line
half=((pin.handicap_line*2)%1==0)&(pin.handicap_line%1!=0)
whole=(pin.handicap_line%1==0)&(x!=0)
S=pin[half|whole].copy(); S['y']=(x[half|whole]>0).astype(int)
def ll(p,y): p=np.clip(p,1e-4,1-1e-4); return -(y*np.log(p)+(1-y)*np.log(1-p)).mean()
print('\nlog-loss (home side) on resolving legs:')
for lb,g in S.groupby('Lb'):
    print(lb, len(g), 'direct %.4f  pois %.4f  dc %.4f'%(ll(g.p_dir,g.y),ll(g.p_pois,g.y),ll(g.p_dc,g.y)))
# nested: does derived add info to direct? logit(y) ~ logit(direct) + (logit(dc)-logit(direct))
lg=lambda p: np.log(np.clip(p,1e-4,1-1e-4)/(1-np.clip(p,1e-4,1-1e-4)))
def fitlog(X,y):
    X=np.column_stack([np.ones(len(y)),X])
    f=lambda b: np.sum(np.logaddexp(0,X@b)-y*(X@b))
    return minimize(f,np.zeros(X.shape[1]),method='BFGS').x
rng=np.random.default_rng(0)
for lb in ['0/.25','.5/.75','1/1.25','1.5/1.75','2+']:
    g=S[S.Lb==lb].reset_index(drop=True)
    Xm=np.column_stack([lg(g.p_dir),lg(g.p_dc)-lg(g.p_dir)]); y=g.y.values
    b=fitlog(Xm,y)
    ids=pd.factorize(g.match_id)[0]; groups=[np.where(ids==k)[0] for k in range(ids.max()+1)]
    bs=[]
    for _ in range(150):
        pick=np.concatenate([groups[k] for k in rng.integers(0,len(groups),len(groups))])
        bs.append(fitlog(Xm[pick],y[pick])[2])
    se=np.std(bs)
    print(lb,'n',len(g),'coef direct %.3f  coef (dc-direct) %.3f  se %.3f z %.2f'%(b[1],b[2],se,b[2]/se))
