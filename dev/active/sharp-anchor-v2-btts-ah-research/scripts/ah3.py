import pandas as pd, numpy as np
A=pd.read_pickle('ah_eval.pkl')
A['psoft']=np.where(A.side=='home',(1/A.home)/(1/A.home+1/A.away),(1/A.away)/(1/A.home+1/A.away))
A['dp']=(A.psoft-A.p).abs()
def clus(g):
    mm=g.groupby('match_id').r.agg(['sum','count']); n=len(g); mu=g.r.mean()
    se=np.sqrt(((mm['sum']-mm['count']*mu)**2).sum())/n
    return pd.Series({'n':n,'fx':g.match_id.nunique(),'ev':g.ev.mean()*100,'roi':mu*100,'lo':(mu-1.96*se)*100,'hi':(mu+1.96*se)*100})
G=A[(A.dp<=0.10)&(A.ovr>0.02)&(A.ovr_pin>0.0)]
print('guard drops', 1-len(G)/len(A), G.groupby('bookmaker').size().to_dict())
al=G[(G.dt<=20)&(G.mins<=30)&(G.mins_pin<=30)]
print('== guarded aligned, all legs'); print(al.groupby('bookmaker').apply(clus).round(2))
for thr in [0.0,0.02,0.03,0.05]:
    print(f'== guarded aligned EV>={thr}')
    print(al[al.ev>=thr].groupby(['bookmaker']).apply(clus).round(2))
print('== guarded aligned EV>=0.02 by |line| bucket')
al['Lb']=pd.cut(al.absL,[-0.1,0.3,0.8,1.3,1.8,5],labels=['0/.25','.5/.75','1/1.25','1.5/1.75','2+'])
print(al[al.ev>=0.02].groupby(['bookmaker','Lb']).apply(clus).round(2))
# calibration of Pinnacle AH devig vs outcome (effective prob, pushes excluded for whole lines)
pin=A[(A.bookmaker=='Epicbet')].drop_duplicates(['match_id','handicap_line','side'])
