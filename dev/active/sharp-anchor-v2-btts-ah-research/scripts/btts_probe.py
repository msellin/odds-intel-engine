import os, psycopg2, numpy as np, math
conn=psycopg2.connect(os.environ["DATABASE_URL"]); cur=conn.cursor(); cur.execute("set statement_timeout='900s'")
BK=os.environ.get("BK","Coolbet")
cur.execute("""
with s as (select o.match_id,o.bookmaker,date_trunc('minute',o.timestamp) ts,o.selection,o.odds
 from odds_snapshots o join matches m on m.id=o.match_id
 where o.market='btts' and m.status='finished' and m.date>now()-interval '120 days' and o.timestamp<m.date and coalesce(o.is_live,false)=false
   and o.bookmaker not in ('api-football-live','Unibet-Kambi')),
g as (select match_id,bookmaker,ts,max(odds) filter(where selection='yes') y,max(odds) filter(where selection='no') n from s group by 1,2,3),
l as (select distinct on (match_id,bookmaker) * from g where y is not null and n is not null order by match_id,bookmaker,ts desc)
select l.match_id,l.bookmaker,l.ts,l.y,l.n,m.date,m.score_home,m.score_away from l join matches m on m.id=l.match_id""")
from collections import defaultdict
D=defaultdict(dict); info={}
for mid,bk,ts,y,n,kick,sh,sa in cur.fetchall():
    y=float(y);n=float(n); mg=1/y+1/n
    if not (0.98<mg<1.2): continue
    D[mid][bk]=((1/y)/mg,y,n,(kick-ts).total_seconds()/60); info[mid]=(kick,int(sh>0 and sa>0))
rows=[]
for mid,b in D.items():
    if BK not in b: continue
    oth=[v[0] for k,v in b.items() if k!=BK]
    if len(oth)<3: continue
    c=float(np.median(oth)); p,y,n,age=b[BK]
    rows.append((mid,c,p,y,n,age,info[mid][1],len(oth)))
print("n",len(rows))
gap=np.array([r[2]-r[1] for r in rows])
print("gap pct quantiles", np.round(np.quantile(gap,[.01,.05,.25,.5,.75,.95,.99]),3))
# bets: side with EV>0.05 vs consensus
def evsel(r,thr=0.05):
    mid,c,p,y,n,age,out,k=r; res=[]
    if y*c-1>thr: res.append(('yes',y,out==1))
    if n*(1-c)-1>thr: res.append(('no',n,out==0))
    return res
bets=[(r,s) for r in rows for s in evsel(r)]
print("bets",len(bets))
for lo,hi in [(0,0.05),(0.05,0.1),(0.1,0.2),(0.2,9)]:
    sel=[(r,s) for r,s in bets if lo<=abs(r[2]-r[1])<hi]
    if sel:
        pnl=np.array([s[1]-1 if s[2] else -1 for r,s in sel]); print(f"|gap| {lo}-{hi}: n={len(sel)} roi={pnl.mean():.3f}")
for lo,hi in [(0,30),(30,120),(120,600),(600,1e9)]:
    sel=[(r,s) for r,s in bets if lo<=r[5]<hi]
    if sel:
        pnl=np.array([s[1]-1 if s[2] else -1 for r,s in sel]); print(f"age {lo}-{hi}min: n={len(sel)} roi={pnl.mean():.3f}")
bets.sort(key=lambda x:-abs(x[0][2]-x[0][1]))
for r,s in bets[:12]: print(r[0][:8], "cons=%.2f book=%.2f y=%.2f n=%.2f age=%.0f out=%d side=%s"%(r[1],r[2],r[3],r[4],r[5],r[6],s[0]))
print("--- clean subset: |gap|<=0.08, age<=180")
for thr in [0.0,0.03,0.05]:
    bs=[(r,s) for r in rows for s in evsel(r,thr) if abs(r[2]-r[1])<=0.08 and r[5]<=180]
    pnl=np.array([s[1]-1 if s[2] else -1 for r,s in bs])
    if len(pnl)>2: print(thr, len(pnl), round(pnl.mean(),3), round(pnl.mean()/(pnl.std()/math.sqrt(len(pnl))),2), "yes share", round(np.mean([s[0]=='yes' for r,s in bs]),2), "avg odds", round(np.mean([s[1] for r,s in bs]),2))
# how many fixtures flagged as faults
print("fault-like |gap|>0.15:", int((np.abs(gap)>0.15).sum()), "of", len(gap))
