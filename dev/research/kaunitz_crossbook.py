import os, psycopg2, math
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv('/Users/margussellin/www/odds-intel-engine/.env')
c = psycopg2.connect(os.getenv('DATABASE_URL'), connect_timeout=900)
c.set_session(readonly=True, autocommit=True)
cur = c.cursor()

EXCL = ('Max','Avg','betexplorer','api-football','api-football-live','ub')
SEL = {'1x2': ('home','draw','away'), 'over_under_25': ('over','under')}

cur.execute("""
select distinct on (o.match_id, o.market, o.selection, o.bookmaker)
       o.match_id, o.market, o.selection, o.bookmaker, o.odds::float,
       m.score_home, m.score_away
  from odds_snapshots o
  join matches m on m.id = o.match_id
 where o.market in ('1x2','over_under_25')
   and o.odds > 1.01
   and o.timestamp <= m.date
   and o.bookmaker <> ALL(%s)
   and m.score_home is not null and m.score_away is not null
   and m.date between now() - interval '12 months' and now()
 order by o.match_id, o.market, o.selection, o.bookmaker, o.timestamp desc
""", (list(EXCL),))

book = defaultdict(dict)   # (match, market) -> {(bk, sel): odds}
score = {}
for mid, mkt, sel, bk, odds, sh, sa in cur.fetchall():
    book[(mid, mkt)][(bk, sel)] = odds
    score[mid] = (sh, sa)

def won(mkt, sel, sh, sa):
    if mkt == '1x2':
        return (sel=='home' and sh>sa) or (sel=='draw' and sh==sa) or (sel=='away' and sh<sa)
    tot = sh + sa
    return (sel=='over' and tot > 2.5) or (sel=='under' and tot < 2.5)

MIN_BOOKS = 5
rows = []   # (bookmaker, ev, ret, market)
for (mid, mkt), d in book.items():
    sels = SEL[mkt]
    # mean odds per selection across books that price ALL selections
    bks = {b for b, _ in d}
    full = [b for b in bks if all((b, s) in d for s in sels)]
    if len(full) < MIN_BOOKS:
        continue
    mean_odds = {s: sum(d[(b, s)] for b in full)/len(full) for s in sels}
    raw = {s: 1.0/mean_odds[s] for s in sels}
    tot = sum(raw.values())
    p = {s: raw[s]/tot for s in sels}          # vig-free consensus
    sh, sa = score[mid]
    for b in full:
        for s in sels:
            o = d[(b, s)]
            # OUTLIER GUARD — same 1.25 ceiling production uses. A price more
            # than 25% above the vig-free consensus is not a value bet, it is a
            # data error (mislabelled line, stale quote, wrong market). Without
            # this the top EV band reads +75% ROI on pure contamination.
            if o > (1.0/p[s]) * 1.25:
                continue
            ev = p[s]*o - 1.0
            ret = (o - 1.0) if won(mkt, s, sh, sa) else -1.0
            rows.append((b, ev, ret, mkt))

print(f"universe: {len(rows):,} book-selection quotes across {len(book):,} match-markets\n")
for lo, hi in [(0.0,0.02),(0.02,0.04),(0.04,0.07),(0.07,0.12),(0.12,0.25)]:
    sub = [r for r in rows if lo <= r[1] < hi]
    if len(sub) < 50: continue
    roi = 100*sum(r[2] for r in sub)/len(sub)
    sd = math.sqrt(sum((r[2]-roi/100)**2 for r in sub)/(len(sub)-1))
    se = 100*sd/math.sqrt(len(sub))
    print(f"  EV {lo:+.0%}..{hi if hi<9 else 9:.0%}  n={len(sub):>7,}  ROI {roi:+6.2f}%  se {se:4.2f}  t {roi/se:+5.2f}")
print()
print("BY BOOK, EV +2%..+25%, outlier-guarded (the Kaunitz selection):")
byb = defaultdict(list)
for b, ev, ret, mkt in rows:
    if 0.02 <= ev <= 0.25: byb[b].append(ret)
print(f"  {'book':<16} {'n':>7} {'ROI':>9} {'t':>7}")
for b, rs in sorted(byb.items(), key=lambda kv: -len(kv[1])):
    if len(rs) < 100: continue
    roi = 100*sum(rs)/len(rs)
    sd = math.sqrt(sum((x-roi/100)**2 for x in rs)/(len(rs)-1))
    se = 100*sd/math.sqrt(len(rs))
    star = '   <<< EXECUTABLE' if b in ('Coolbet','Unibet-Site','Epicbet') else ''
    print(f"  {b:<16} {len(rs):>7,} {roi:+8.2f}% {roi/se:+6.2f}{star}")

