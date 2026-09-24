import pandas as pd, numpy as np
from scipy.optimize import minimize, brentq
A=pd.read_pickle('ah_derived.pkl')
pin=A[(A.bookmaker=='Pinnacle')&(A.mins<=60)].copy()
def powdv(oh,oa):
    f=lambda k:(1/oh)**k+(1/oa)**k-1
    try: k=brentq(f,0.5,3); return (1/oh)**k
    except: return np.nan
pin['p_pow']=[powdv(a,b) for a,b in zip(pin.home,pin.away)]
pin['absL']=pin.handicap_line.abs()
pin['Lb']=pd.cut(pin.absL,[-0.1,0.3,0.8,1.3,1.8,3.1],labels=['0/.25','.5/.75','1/1.25','1.5/1.75','2+'])
m=pin.sh-pin.sa; x=m+pin.handicap_line
half=(pin.handicap_line%1==0.5)|(pin.handicap_line%1==-0.5)|((pin.handicap_line*2)%1==0)&(pin.handicap_line%1!=0)
whole=(pin.handicap_line%1==0)&(x!=0)
S=pin[half|whole].copy(); S['y']=(x[half|whole]>0).astype(int)
S=S.sort_values('date'); cut=S.date.quantile(0.6); tr=S[S.date<cut]; te=S[S.date>=cut]
print('train until',cut,'n',len(tr),len(te))
lg=lambda p: np.log(np.clip(p,1e-4,1-1e-4)/(1-np.clip(p,1e-4,1-1e-4)))
def ll(p,y): p=np.clip(p,1e-4,1-1e-4); return -(y*np.log(p)+(1-y)*np.log(1-p)).mean()
def fitlog(X,y):
    X=np.column_stack([np.ones(len(y)),X]); f=lambda b: np.sum(np.logaddexp(0,X@b)-y*(X@b))
    return minimize(f,np.zeros(X.shape[1]),method='BFGS').x
def pred(b,X): X=np.column_stack([np.ones(len(X)),X]); return 1/(1+np.exp(-X@b))
for lb in ['0/.25','.5/.75','1/1.25','1.5/1.75','2+']:
    a=tr[tr.Lb==lb]; t=te[te.Lb==lb]
    b1=fitlog(lg(a.p_dir.values)[:,None],a.y.values)
    b2=fitlog(np.column_stack([lg(a.p_dir),lg(a.p_dc)-lg(a.p_dir)]),a.y.values)
    print(lb,'test n',len(t),'raw dir %.4f pow %.4f dc %.4f | recal dir %.4f | blend %.4f'%(ll(t.p_dir,t.y),ll(t.p_pow,t.y),ll(t.p_dc,t.y),
      ll(pred(b1,lg(t.p_dir.values)[:,None]),t.y),ll(pred(b2,np.column_stack([lg(t.p_dir),lg(t.p_dc)-lg(t.p_dir)])),t.y)), 'b2',b2.round(3))
# calibration of direct by line: mean y vs mean p
print(S.groupby('Lb').agg(n=('y','size'),y=('y','mean'),p_dir=('p_dir','mean'),p_pow=('p_pow','mean'),p_dc=('p_dc','mean')).round(4))
# split by favourite side: is the direct error on home-fav lines (L<0) vs dog lines (L>0)?
S['sgn']=np.sign(S.handicap_line)
print(S[S.absL>=1].groupby('sgn').agg(n=('y','size'),y=('y','mean'),p_dir=('p_dir','mean'),p_pow=('p_pow','mean'),p_dc=('p_dc','mean')).round(4))
S.to_pickle('pin_scored.pkl')
