"""COOLBET-SEARCH-SPORT-FILTER-FOLLOWUP audit (2026-06-15).

Diagnostic for "why does the early cohort skew to AH / DC?" — operator
observed that many early-KO matches have only AH or DC paper bets in
the queue.

Three hypotheses, tested in this script:
  (a) Cohort timing — fresh-odds refresh hits 1X2/OU later; AH/DC bets
      are written by an earlier run before the 1X2/OU bots get fresh prices
  (b) Bot universe — fewer 1X2/OU bots fire at the early cohort
  (c) Data-side gaps — missing Pinnacle on these books at the time of run

First-run finding (2026-06-15, 14d window): hypothesis (a) is wrong (1x2 IS
present at the morning 04 UTC cohort), hypothesis (c) is wrong (Pinnacle is
on 1517 of 1522 matches in the window). Hypothesis (b) is correct — see
the per-bot KO-bin firing table in `coolbet_early_ko_drilldown.py`.

Re-run any time with: `python3 scripts/coolbet_market_mix_audit.py`.
Window is hardcoded to 14 days; edit the INTERVAL strings to widen.
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

# ── (1) Market mix per hour-of-day created_at ────────────────────────────
print("=" * 80)
print("(1) Market mix per UTC hour (pick creation time) — last 14d simulated_bets")
print("=" * 80)
cur.execute("""
    SELECT EXTRACT(hour FROM created_at)::int AS h,
           market,
           COUNT(*)                                AS n,
           ROUND(AVG(edge_percent)::numeric, 2)    AS avg_edge_pct
    FROM simulated_bets
    WHERE created_at >= NOW() - INTERVAL '14 days'
      AND match_minute_at_pick IS NULL  -- prematch only (exclude in-play noise)
    GROUP BY h, market
    ORDER BY h, n DESC
""")
rows = cur.fetchall()
by_hour = defaultdict(lambda: defaultdict(int))
hour_totals = defaultdict(int)
for h, m, n, e in rows:
    by_hour[h][m] = n
    hour_totals[h] += n

# Find all markets across the window
all_markets = sorted({m for h_dict in by_hour.values() for m in h_dict})
print(f"\n  hour | total | " + " | ".join(f"{m:>14}" for m in all_markets))
print("  " + "-" * (10 + 16 * len(all_markets)))
for h in sorted(by_hour):
    tot = hour_totals[h]
    cells = []
    for m in all_markets:
        n = by_hour[h].get(m, 0)
        pct = (n / tot * 100) if tot else 0
        cells.append(f"{n:>5} ({pct:>4.0f}%)" if n else f"{'·':>14}")
    print(f"  {h:>4} | {tot:>5} | " + " | ".join(cells))

# ── (2) Bot universe — which bots fire at which markets ──────────────────
print("\n" + "=" * 80)
print("(2) Bot universe per market — which bots produce each market type")
print("=" * 80)
cur.execute("""
    SELECT b.name, sb.market, COUNT(*) AS n
    FROM simulated_bets sb
    JOIN bots b ON b.id = sb.bot_id
    WHERE sb.created_at >= NOW() - INTERVAL '14 days'
      AND sb.match_minute_at_pick IS NULL
    GROUP BY b.name, sb.market
    ORDER BY b.name, n DESC
""")
bot_markets = defaultdict(dict)
for name, m, n in cur.fetchall():
    bot_markets[name][m] = n

print(f"\n  {'bot':<30} | {'top markets (n)':<60}")
print("  " + "-" * 92)
for bot in sorted(bot_markets):
    parts = sorted(bot_markets[bot].items(), key=lambda x: -x[1])
    s = ", ".join(f"{m}={n}" for m, n in parts[:4])
    print(f"  {bot:<30} | {s:<60}")

# ── (3) Cohort timing — when does each market peak ──────────────────────
print("\n" + "=" * 80)
print("(3) Market peak hour — when does each market produce most picks")
print("=" * 80)
cur.execute("""
    SELECT market,
           EXTRACT(hour FROM created_at)::int AS h,
           COUNT(*) AS n
    FROM simulated_bets
    WHERE created_at >= NOW() - INTERVAL '14 days'
      AND match_minute_at_pick IS NULL
    GROUP BY market, h
""")
market_hours = defaultdict(dict)
for m, h, n in cur.fetchall():
    market_hours[m][h] = n

print(f"\n  {'market':<20} | {'top 3 hours (n)':<40} | {'spread':>8}")
print("  " + "-" * 72)
for m in sorted(market_hours):
    h_dict = market_hours[m]
    total = sum(h_dict.values())
    top = sorted(h_dict.items(), key=lambda x: -x[1])[:3]
    top_s = ", ".join(f"{h:02d}h={n}" for h, n in top)
    # spread = top 3 share of total — high = peaked, low = spread out
    top3_share = sum(n for _, n in top) / total * 100 if total else 0
    print(f"  {m:<20} | {top_s:<40} | {top3_share:>6.0f}%")

# ── (4) Pinnacle availability by hour — for the early-cohort matches ────
print("\n" + "=" * 80)
print("(4) Pinnacle odds availability per market — for matches in last 14d")
print("=" * 80)
cur.execute("""
    SELECT os.market,
           COUNT(DISTINCT os.match_id) AS matches_with_pin
    FROM odds_snapshots os
    JOIN matches m ON m.id = os.match_id
    WHERE os.bookmaker = 'Pinnacle'
      AND os.is_live = false
      AND m.date >= NOW() - INTERVAL '14 days'
      AND m.date < NOW() + INTERVAL '1 day'
    GROUP BY os.market
    ORDER BY matches_with_pin DESC
""")
print(f"\n  {'market':<20} | {'matches with Pinnacle (last 14d)':>32}")
print("  " + "-" * 54)
for m, n in cur.fetchall():
    print(f"  {m:<20} | {n:>32}")

# ── (5) Early-cohort (hour 5-10 UTC) picks — explicit breakdown ──────────
print("\n" + "=" * 80)
print("(5) Early cohort (UTC hour 5-10) deep-dive — last 14d")
print("=" * 80)
cur.execute("""
    SELECT market,
           COUNT(*) AS n,
           ROUND(AVG(edge_percent)::numeric, 2) AS avg_edge,
           COUNT(*) FILTER (WHERE bot_id IN (
               SELECT id FROM bots WHERE maturity_label='calibrated' AND is_active=true
           )) AS n_calibrated
    FROM simulated_bets
    WHERE created_at >= NOW() - INTERVAL '14 days'
      AND match_minute_at_pick IS NULL
      AND EXTRACT(hour FROM created_at) BETWEEN 5 AND 10
    GROUP BY market
    ORDER BY n DESC
""")
print(f"\n  {'market':<20} | {'n':>6} | {'avg edge':>10} | {'n calibrated':>14}")
print("  " + "-" * 60)
for m, n, e, nc in cur.fetchall():
    print(f"  {m:<20} | {n:>6} | {str(e):>10} | {nc:>14}")

# ── (6) Bot kickoff-cohort timing — which bots fire pre-KO vs at-KO ─────
print("\n" + "=" * 80)
print("(6) Match-KO-vs-pick-time delta per market (last 14d settled)")
print("=" * 80)
cur.execute("""
    SELECT sb.market,
           ROUND(AVG(EXTRACT(EPOCH FROM (m.date - sb.created_at)) / 3600)::numeric, 1) AS hrs_before_ko_avg,
           ROUND(MIN(EXTRACT(EPOCH FROM (m.date - sb.created_at)) / 3600)::numeric, 1) AS hrs_before_ko_min,
           ROUND(MAX(EXTRACT(EPOCH FROM (m.date - sb.created_at)) / 3600)::numeric, 1) AS hrs_before_ko_max,
           COUNT(*) AS n
    FROM simulated_bets sb
    JOIN matches m ON m.id = sb.match_id
    WHERE sb.created_at >= NOW() - INTERVAL '14 days'
      AND sb.match_minute_at_pick IS NULL
    GROUP BY sb.market
    HAVING COUNT(*) >= 5
    ORDER BY hrs_before_ko_avg DESC
""")
print(f"\n  {'market':<20} | {'avg hrs pre-KO':>14} | {'min':>6} | {'max':>6} | {'n':>5}")
print("  " + "-" * 65)
for m, avg, mn, mx, n in cur.fetchall():
    print(f"  {m:<20} | {str(avg):>14} | {str(mn):>6} | {str(mx):>6} | {n:>5}")

cur.close()
conn.close()
print("\nDone.")
