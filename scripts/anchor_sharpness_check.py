import os
from dotenv import load_dotenv
import psycopg2, psycopg2.extras
load_dotenv("/Users/margussellin/www/odds-intel-engine/.env")
c = psycopg2.connect(os.getenv("DATABASE_URL")).cursor(cursor_factory=psycopg2.extras.RealDictCursor)
print("=== CLAIM: Pinnacle is not the sharpest book in this feed ===")
print("    1X2 overround by book (latest pre-KO triple, same timestamp, since 2026-06-01)\n")
c.execute("""
WITH q AS (
  SELECT DISTINCT ON (o.match_id, o.bookmaker, o.selection)
         o.match_id, o.bookmaker, o.selection, o.odds::float od
    FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
   WHERE o.market='1x2' AND o.is_live IS NOT TRUE AND o.odds > 1.01
     AND m.date >= '2026-06-01' AND m.status='finished'
   ORDER BY o.match_id, o.bookmaker, o.selection, o.timestamp DESC),
t AS (SELECT match_id, bookmaker,
             sum(1.0/od) AS ovr, count(*) n
        FROM q GROUP BY 1,2 HAVING count(*)=3)
SELECT bookmaker, count(*) fixtures,
       round((100*(avg(ovr)-1))::numeric,2) mean_ovr_pct,
       round((100*(percentile_cont(0.5) WITHIN GROUP (ORDER BY ovr)-1))::numeric,2) median_ovr_pct
  FROM t GROUP BY 1 HAVING count(*) > 300 ORDER BY 4""")
for r in c.fetchall():
    star = "  <<< our reference" if r["bookmaker"] == "Pinnacle" else ("  <- we bet here" if r["bookmaker"] in ("Coolbet","Epicbet","Unibet-Site","Betano") else "")
    print(f"  {r['bookmaker']:16s} n={r['fixtures']:6d}  mean {r['mean_ovr_pct']:6}%  median {r['median_ovr_pct']:6}%{star}")

print("\n=== how many fixtures have a GENUINELY sharp Pinnacle price? ===")
c.execute("""
WITH q AS (SELECT DISTINCT ON (o.match_id, o.selection) o.match_id, o.selection, o.odds::float od
    FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
   WHERE o.market='1x2' AND o.bookmaker='Pinnacle' AND o.is_live IS NOT TRUE AND o.odds>1.01
     AND m.date >= '2026-06-01' AND m.status='finished'
   ORDER BY o.match_id, o.selection, o.timestamp DESC),
t AS (SELECT match_id, sum(1.0/od) ovr FROM q GROUP BY 1 HAVING count(*)=3)
SELECT count(*) n,
  count(*) FILTER (WHERE ovr < 1.04) under_4pct,
  count(*) FILTER (WHERE ovr >= 1.09) over_9pct FROM t""")
r = c.fetchone()
print(f"  fixtures with a Pinnacle triple: {r['n']}")
print(f"    overround < 4% (plausibly sharp): {r['under_4pct']} ({100*r['under_4pct']/r['n']:.1f}%)")
print(f"    overround >= 9% (a goodwill quote): {r['over_9pct']} ({100*r['over_9pct']/r['n']:.1f}%)")
