import pandas as pd, numpy as np
d=pd.read_csv('ah_close.csv',parse_dates=['timestamp','date'])
d=d[d.odds>1.0]
p=d.pivot_table(index=['match_id','bookmaker','handicap_line'],columns='selection',values='odds',aggfunc='first')
t=d.pivot_table(index=['match_id','bookmaker','handicap_line'],columns='selection',values='mins_before',aggfunc='first')
p=p.dropna(); p['ovr']=1/p.home+1/p.away-1
p['mins']=t.loc[p.index].max(axis=1)
p=p.reset_index()
p['ltype']=np.where(p.handicap_line%1==0,'whole',np.where((p.handicap_line*2)%1==0,'half','quarter'))
print(p.groupby(['bookmaker','ltype']).ovr.describe(percentiles=[.5]).round(4))
# overround sanity: fraction outside [0,0.2]
print(p.groupby('bookmaker').ovr.apply(lambda s:((s<0)|(s>0.2)).mean()).round(4))
# monotonic check Pinnacle: home devig prob vs line
p['ph']=(1/p.home)/(1/p.home+1/p.away)
pin=p[p.bookmaker=='Pinnacle']
print(pin.groupby('handicap_line').ph.mean().loc[-2:2].round(3))
# sign check vs Pinnacle: for each soft book, compare ph at L with pinnacle ph at L and at -L
P=pin.set_index(['match_id','handicap_line']).ph
for b in ['Coolbet','Epicbet','Tonybet']:
    s=p[(p.bookmaker==b)&(p.handicap_line!=0)]
    same=P.reindex(list(zip(s.match_id,s.handicap_line))).values
    flip=P.reindex(list(zip(s.match_id,-s.handicap_line))).values
    ph=s.ph.values
    m1=~np.isnan(same); m2=~np.isnan(flip)
    print(b,'n_same',m1.sum(),'MAD same %.4f'%np.nanmean(np.abs(ph-same)),'corr %.3f'%np.corrcoef(ph[m1],same[m1])[0,1],'| n_flip',m2.sum(),'MAD flip %.4f'%np.nanmean(np.abs(ph-flip)))
p.to_pickle('ah_pairs.pkl')
