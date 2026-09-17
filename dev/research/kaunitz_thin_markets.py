import os, psycopg2, math
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv('/Users/margussellin/www/odds-intel-engine/.env')
c=psycopg2.connect(os.getenv('DATABASE_URL'), connect_timeout=900); c.set_session(readonly=True, autocommit=True)
cur=c.cursor()
EXCL=('Max','Avg','betexplorer','api-football','api-football-live','ub')
SEL={'1x2':('home','draw','away'),'over_under_25':('over','under')}
cur.execute("""
select distinct on (o.match_id,o.market,o.selection,o.bookmaker)
       o.match_id,o.market,o.selection,o.bookmaker,o.odds::float,m.score_home,m.score_away
  from odds_snapshots o join matches m on m.id=o.match_id
 where o.market in ('1x2','over_under_25') and o.odds>1.01
   and o.timestamp <= m.date and o.bookmaker <> ALL(%s)
   and m.score_home is not null and m.date between now()-interval '12 months' and now()
 order by o.match_id,o.market,o.selection,o.bookmaker,o.timestamp desc
""",(list(EXCL),))
book=defaultdict(dict); score={}
for mid,mkt,sel,bk,odds,sh,sa in cur.fetchall():
    book[(mid,mkt)][(bk,sel)]=odds; score[mid]=(sh,sa)
def won(mkt,sel,sh,sa):
    if mkt=='1x2': return (sel=='home' and sh>sa) or (sel=='draw' and sh==sa) or (sel=='away' and sh<sa)
    return (sel=='over' and sh+sa>2.5) or (sel=='under' and sh+sa<2.5)

rows=[]
for (mid,mkt),d in book.items():
    sels=SEL[mkt]
    if not all(('Pinnacle',s) in d for s in sels): continue      # Pinnacle anchors
    raw={s:1.0/d[('Pinnacle',s)] for s in sels}; tot=sum(raw.values())
    p={s:raw[s]/tot for s in sels}                                # de-vigged Pinnacle
    others={b for b,_ in d if b!='Pinnacle'}
    nb=len(others)
    sh,sa=score[mid]
    for b in others:
        for s in sels:
            if (b,s) not in d: continue
            o=d[(b,s)]
            if o > (1.0/p[s])*1.25: continue                      # outlier guard
            rows.append((b,nb,p[s]*o-1.0,(o-1.0) if won(mkt,s,sh,sa) else -1.0))
print(f"anchored on de-vigged Pinnacle: {len(rows):,} quotes\n")
def show(title, pred):
    sub=[r for r in rows if pred(r) and r[2]>=0.02]
    if len(sub)<200: print(f'  {title:<34} n={len(sub)} (too few)'); return
    roi=100*sum(r[3] for r in sub)/len(sub)
    sd=math.sqrt(sum((r[3]-roi/100)**2 for r in sub)/(len(sub)-1)); se=100*sd/math.sqrt(len(sub))
    print(f'  {title:<34} n={len(sub):>7,}  ROI {roi:+6.2f}%  t {roi/se:+5.2f}')
print("EV >= +2% vs de-vigged Pinnacle, by MARKET THINNESS (other books pricing):")
show('THIN  1-3 other books', lambda r: r[1]<=3)
show('MID   4-8 other books', lambda r: 4<=r[1]<=8)
show('THICK 9+ other books',  lambda r: r[1]>=9)
print()
print("Our executable books only:")
for bk in ('Coolbet','Epicbet','Unibet-Site'):
    show(f'{bk} (all)', lambda r,b=bk: r[0]==b)
    show(f'{bk} thin (<=3 others)', lambda r,b=bk: r[0]==b and r[1]<=3)
