"""COOLBET-SEARCH-SPORT-FILTER-FOLLOWUP drill-down (2026-06-15).

Companion to `coolbet_market_mix_audit.py`. Buckets matches by KO hour
(not by pick-creation hour) to test the operator's "early-KO matches
only have AH/DC" observation directly.

First-run finding (2026-06-15, 14d window): 56% of early-KO matches
(KO 00-11 UTC) have ONLY AH/DC bets, dropping to 26% for evening-KO.
Per-bot KO-bin firing shows bot_v10_all (the calibrated 1x2/OU
workhorse) fires 4× less at early-KO than at late-aft; bot_conservative
and bot_proven_leagues_v2 produce ZERO early-KO picks. The skew is the
bot universe, not a placer bug.

Re-run any time with: `python3 scripts/coolbet_early_ko_drilldown.py`.
"""
import os, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

# Matches grouped by KO hour bin → market mix of their picks
print("=" * 80)
print("Market mix per KO-hour bin (last 14d settled prematch bets)")
print("=" * 80)
cur.execute("""
    SELECT
      CASE
        WHEN EXTRACT(hour FROM m.date) <  12 THEN 'A: early-KO (00-11 UTC)'
        WHEN EXTRACT(hour FROM m.date) < 16  THEN 'B: midday-KO (12-15 UTC)'
        WHEN EXTRACT(hour FROM m.date) < 19  THEN 'C: late-aft-KO (16-18 UTC)'
        ELSE                                     'D: evening-KO (19-23 UTC)'
      END AS ko_bin,
      sb.market,
      COUNT(*) AS n
    FROM simulated_bets sb
    JOIN matches m ON m.id = sb.match_id
    WHERE sb.created_at >= NOW() - INTERVAL '14 days'
      AND sb.match_minute_at_pick IS NULL
    GROUP BY ko_bin, sb.market
    ORDER BY ko_bin, n DESC
""")

bins = defaultdict(lambda: defaultdict(int))
bin_totals = defaultdict(int)
for ko_bin, m, n in cur.fetchall():
    bins[ko_bin][m] = n
    bin_totals[ko_bin] += n

all_markets = sorted({m for d in bins.values() for m in d})
print(f"\n  {'KO bin':<30} | {'tot':>5} | " + " | ".join(f"{m:>14}" for m in all_markets))
print("  " + "-" * (40 + 17 * len(all_markets)))
for ko_bin in sorted(bins):
    tot = bin_totals[ko_bin]
    cells = []
    for m in all_markets:
        n = bins[ko_bin].get(m, 0)
        pct = (n / tot * 100) if tot else 0
        cells.append(f"{n:>5} ({pct:>3.0f}%)" if n else f"{'·':>14}")
    print(f"  {ko_bin:<30} | {tot:>5} | " + " | ".join(cells))

# Per match — count distinct markets bet on for early-KO vs late-KO
print("\n" + "=" * 80)
print("Per-match diversity — how many DISTINCT markets fire on each match?")
print("=" * 80)
cur.execute("""
    SELECT
      CASE
        WHEN EXTRACT(hour FROM m.date) <  12 THEN 'A: early-KO (00-11 UTC)'
        WHEN EXTRACT(hour FROM m.date) < 16  THEN 'B: midday-KO (12-15 UTC)'
        WHEN EXTRACT(hour FROM m.date) < 19  THEN 'C: late-aft-KO (16-18 UTC)'
        ELSE                                     'D: evening-KO (19-23 UTC)'
      END AS ko_bin,
      COUNT(DISTINCT m.id) AS n_matches,
      AVG(market_count)   AS avg_markets_per_match
    FROM (
      SELECT match_id, COUNT(DISTINCT market) AS market_count
      FROM simulated_bets
      WHERE created_at >= NOW() - INTERVAL '14 days'
        AND match_minute_at_pick IS NULL
      GROUP BY match_id
    ) sb
    JOIN matches m ON m.id = sb.match_id
    GROUP BY ko_bin
    ORDER BY ko_bin
""")
print(f"\n  {'KO bin':<30} | {'n matches':>10} | {'avg distinct markets / match':>30}")
print("  " + "-" * 76)
for ko_bin, n, avg in cur.fetchall():
    print(f"  {ko_bin:<30} | {n:>10} | {avg:>30.2f}")

# Matches with ONLY AH/DC — the operator's complaint pattern
print("\n" + "=" * 80)
print("Match-level: how many early-KO matches have ONLY AH/DC picks?")
print("=" * 80)
cur.execute("""
    WITH per_match AS (
      SELECT sb.match_id,
             array_agg(DISTINCT sb.market) AS markets,
             m.date,
             EXTRACT(hour FROM m.date)::int AS ko_h
      FROM simulated_bets sb
      JOIN matches m ON m.id = sb.match_id
      WHERE sb.created_at >= NOW() - INTERVAL '14 days'
        AND sb.match_minute_at_pick IS NULL
      GROUP BY sb.match_id, m.date
    )
    SELECT
      CASE
        WHEN ko_h <  12 THEN 'A: early-KO'
        WHEN ko_h < 16  THEN 'B: midday-KO'
        WHEN ko_h < 19  THEN 'C: late-aft-KO'
        ELSE                'D: evening-KO'
      END AS ko_bin,
      COUNT(*) AS total_matches,
      COUNT(*) FILTER (WHERE markets <@ ARRAY['asian_handicap','double_chance']) AS only_ah_dc,
      COUNT(*) FILTER (WHERE NOT markets <@ ARRAY['asian_handicap','double_chance']) AS has_other
    FROM per_match
    GROUP BY ko_bin
    ORDER BY ko_bin
""")
print(f"\n  {'KO bin':<20} | {'total':>6} | {'only AH/DC':>11} | {'%':>5} | {'has other':>10}")
print("  " + "-" * 65)
for ko_bin, tot, only, has_other in cur.fetchall():
    pct = (only / tot * 100) if tot else 0
    print(f"  {ko_bin:<20} | {tot:>6} | {only:>11} | {pct:>4.0f}% | {has_other:>10}")

# Per-bot KO-bin firing
print("\n" + "=" * 80)
print("Per-bot KO-bin firing — which bots are quiet on early-KO matches?")
print("=" * 80)
cur.execute("""
    SELECT b.name,
           COUNT(*) FILTER (WHERE EXTRACT(hour FROM m.date) < 12)              AS early,
           COUNT(*) FILTER (WHERE EXTRACT(hour FROM m.date) BETWEEN 12 AND 15) AS midday,
           COUNT(*) FILTER (WHERE EXTRACT(hour FROM m.date) BETWEEN 16 AND 18) AS late_aft,
           COUNT(*) FILTER (WHERE EXTRACT(hour FROM m.date) >= 19)             AS evening,
           COUNT(*)                                                            AS total
    FROM simulated_bets sb
    JOIN bots b ON b.id = sb.bot_id
    JOIN matches m ON m.id = sb.match_id
    WHERE sb.created_at >= NOW() - INTERVAL '14 days'
      AND sb.match_minute_at_pick IS NULL
    GROUP BY b.name
    HAVING COUNT(*) >= 5
    ORDER BY total DESC
""")
print(f"\n  {'bot':<30} | {'early':>5} {'midday':>6} {'late':>5} {'eve':>5} | {'total':>6}")
print("  " + "-" * 70)
for name, e, mi, la, ev, tot in cur.fetchall():
    print(f"  {name:<30} | {e:>5} {mi:>6} {la:>5} {ev:>5} | {tot:>6}")

cur.close()
conn.close()
