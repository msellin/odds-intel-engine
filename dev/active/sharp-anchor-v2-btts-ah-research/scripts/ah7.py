import pandas as pd, numpy as np
from scipy.optimize import brentq
from scipy.stats import norm
E=pd.read_pickle('ah_eval.pkl'); Dv=pd.read_pickle('ah_derived.pkl')
pin=Dv[Dv.bookmaker=='Pinnacle'][['match_id','handicap_line','p_dc','p_pois']].drop_duplicates(['match_id','handicap_line'])
E=E.merge(pin,on=['match_id','handicap_line'],how='inner')
def powdv(oh,oa):
    f=lambda k:(1/oh)**k+(1/oa)**k-1
    try: k=brentq(f,0.5,3); return (1/oh)**k
    except: return np.nan
E['ph_pow']=[powdv(a,b) for a,b in zip(E.home_pin,E.away_pin)]
E['ph_dir']=(1/E.home_pin)/(1/E.home_pin+1/E.away_pin)
lg=lambda p: np.log(p/(1-p)); il=lambda z:1/(1+np.exp(-z))
E['ph_blend']=il(lg(E.ph_dir)+0.7*(lg(E.p_dc)-lg(E.ph_dir)))   # coefficients ~ train fit (0.66-0.81); fixed a priori at 0.7
E['psoft']=np.where(E.side=='home',(1/E.home)/(1/E.home+1/E.away),(1/E.away)/(1/E.home+1/E.away))
for a in ['dir','pow','blend']:
    E['p_'+a]=np.where(E.side=='home',E['ph_'+a],1-E['ph_'+a]); E['ev_'+a]=E['p_'+a]*E.o-1
E=E[((E.psoft-E.p_dir).abs()<=0.10)&(E.dt<=20)&(E.mins<=30)&(E.mins_pin<=30)&(E.absL>=1)]
def clus(g,evc):
    mm=g.groupby('match_id').r.agg(['sum','count']); n=len(g); mu=g.r.mean()
    se=np.sqrt(((mm['sum']-mm['count']*mu)**2).sum())/n
    return dict(n=n,fx=g.match_id.nunique(),ev=round(g[evc].mean()*100,2),roi=round(mu*100,2),lo=round((mu-1.96*se)*100,1),hi=round((mu+1.96*se)*100,1),p1=round(1-norm.cdf(mu/se),3))
rows=[]
for b in ['Coolbet','Epicbet']:
    for a in ['dir','pow','blend']:
        g=E[(E.bookmaker==b)&(E['ev_'+a]>=0.02)]
        rows.append(dict(book=b,anchor=a,**clus(g,'ev_'+a)))
T=pd.DataFrame(rows)
# Holm
T=T.sort_values('p1'); m=len(T); T['holm']=[min(1,max((m-i)*p for i,p in enumerate(T.p1[:k+1]))) for k in range(m)]
print(T.to_string())
# side split for blend
for b in ['Coolbet','Epicbet']:
    g=E[(E.bookmaker==b)&(E.ev_blend>=0.02)]
    g['fav_covers']=((g.side=='home')&(g.handicap_line<0))|((g.side=='away')&(g.handicap_line>0))
    print(b, g.groupby('fav_covers').apply(lambda x: pd.Series(clus(x,'ev_blend'))).to_string())
    g=E[(E.bookmaker==b)&(E.ev_dir>=0.02)]
    g['fav_covers']=((g.side=='home')&(g.handicap_line<0))|((g.side=='away')&(g.handicap_line>0))
    print(b,'dir', g.groupby('fav_covers').apply(lambda x: pd.Series(clus(x,'ev_dir'))).to_string())
