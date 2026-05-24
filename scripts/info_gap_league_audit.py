"""INFO-GAP-LEAGUE-AUDIT — per-league CLV distribution audit.

VAL-POST-MORTEM flagged that INFORMATION_GAP losses cluster in Scottish Championship,
USL / MLS NextPro, and Latvian higher tier — leagues where pre-KO CLV is consistently
-80% to -98%. This audit:

  1. Computes per-league CLV distribution from settled simulated_bets (clean window
     post 2026-05-06).
  2. Flags leagues with consistently negative CLV (median CLV < -2% on n >= 20).
  3. Reports per-league ROI to see if negative CLV translates to actual losses.

Outputs guide a possible LEAGUE-COHORT-FILTER (deprioritise sharp markets) or
LINEUP-WAIT-GATE (require confirmed lineups before placing in flagged leagues).
"""
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("SET statement_timeout='180s'")

print("=" * 80)
print("INFO-GAP-LEAGUE-AUDIT — per-league CLV + ROI distribution")
print("=" * 80)
print()
print("Source: simulated_bets settled (won/lost/void) since 2026-05-06, CLV computed.")
print()

# Per-league summary
cur.execute("""
    SELECT l.name, l.country, l.tier,
           COUNT(*) AS n,
           ROUND((AVG(sb.clv) * 100)::numeric, 1) AS avg_clv_pct,
           ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY sb.clv) * 100)::numeric, 1) AS median_clv,
           ROUND(100.0 * SUM(sb.pnl) / NULLIF(SUM(sb.stake), 0), 1) AS roi_pct,
           COUNT(*) FILTER (WHERE sb.clv < -0.30) AS huge_neg_clv,
           ROUND(100.0 * COUNT(*) FILTER (WHERE sb.result='won') / NULLIF(COUNT(*) FILTER (WHERE sb.result IN ('won','lost')),0), 1) AS hit_rate
    FROM simulated_bets sb
    JOIN matches m ON m.id = sb.match_id
    LEFT JOIN leagues l ON l.id = m.league_id
    WHERE sb.result IN ('won','lost','void')
      AND sb.created_at >= '2026-05-06'
      AND sb.clv IS NOT NULL
    GROUP BY l.name, l.country, l.tier
    HAVING COUNT(*) >= 10
    ORDER BY median_clv NULLS LAST
""")

rows = cur.fetchall()
print(f"  Leagues with >= 10 settled CLV-having bets since 5/6 ({len(rows)} leagues):\n")
print(f"  {'league':<32}{'country':<14}{'tier':>5}{'n':>6}{'avg_clv':>10}{'med_clv':>10}{'roi%':>8}{'huge−clv':>10}{'hit%':>7}")
print("  " + "─" * 102)

flagged = []
for r in rows:
    name, country, tier, n, avg_clv, med_clv, roi, huge_neg, hit = r
    line = f"  {str(name)[:30]:<32}{str(country)[:12]:<14}{str(tier):>5}{n:>6}{str(avg_clv):>10}{str(med_clv):>10}{str(roi):>8}{huge_neg:>10}{str(hit):>7}"
    print(line)
    # Flag rule: median CLV < -2% AND n >= 20
    if med_clv is not None and float(med_clv) < -2.0 and n >= 20:
        flagged.append((name, country, tier, n, med_clv, roi))

print()
print("=" * 80)
print(f"FLAGGED LEAGUES — median CLV < −2% on n ≥ 20 bets ({len(flagged)} flagged)")
print("=" * 80)
print()
print(f"  {'league':<32}{'country':<14}{'tier':>5}{'n':>6}{'med_clv':>10}{'roi%':>8}")
print("  " + "─" * 85)
for name, country, tier, n, med_clv, roi in flagged:
    print(f"  {str(name)[:30]:<32}{str(country)[:12]:<14}{str(tier):>5}{n:>6}{str(med_clv):>10}{str(roi):>8}")

# Aggregate flagged-league impact
if flagged:
    flagged_names = tuple(name for name, _, _, _, _, _ in flagged)
    cur.execute("""
        SELECT COUNT(*) AS n,
               ROUND(100.0 * SUM(sb.pnl) / NULLIF(SUM(sb.stake), 0), 1) AS roi_pct,
               ROUND((AVG(sb.clv) * 100)::numeric, 1) AS avg_clv
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE sb.result IN ('won','lost','void')
          AND sb.created_at >= '2026-05-06'
          AND l.name = ANY(%s)
    """, (list(flagged_names),))
    r = cur.fetchone()
    print(f"\n  Aggregate impact of flagged-league bets: n={r[0]}, ROI={r[1]}%, avg_clv={r[2]}%")

    # And the non-flagged?
    cur.execute("""
        SELECT COUNT(*) AS n,
               ROUND(100.0 * SUM(sb.pnl) / NULLIF(SUM(sb.stake), 0), 1) AS roi_pct,
               ROUND((AVG(sb.clv) * 100)::numeric, 1) AS avg_clv
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE sb.result IN ('won','lost','void')
          AND sb.created_at >= '2026-05-06'
          AND (l.name IS NULL OR l.name <> ALL(%s))
    """, (list(flagged_names),))
    r = cur.fetchone()
    print(f"  Aggregate impact of all OTHER bets:        n={r[0]}, ROI={r[1]}%, avg_clv={r[2]}%")

# Show top 5 LARGEST-VOLUME leagues regardless of CLV (sanity context)
print("\n=== Top-5 highest-volume leagues (for context) ===\n")
cur.execute("""
    SELECT l.name, l.country, COUNT(*) AS n,
           ROUND((AVG(sb.clv) * 100)::numeric, 1) AS avg_clv,
           ROUND(100.0 * SUM(sb.pnl) / NULLIF(SUM(sb.stake),0), 1) AS roi
    FROM simulated_bets sb
    JOIN matches m ON m.id = sb.match_id
    LEFT JOIN leagues l ON l.id = m.league_id
    WHERE sb.result IN ('won','lost','void')
      AND sb.created_at >= '2026-05-06'
      AND sb.clv IS NOT NULL
    GROUP BY l.name, l.country
    ORDER BY n DESC LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {str(r[0])[:32]:<34}{str(r[1])[:12]:<14}  n={r[2]:>4}  avg_clv={str(r[3]):>7}%  roi={str(r[4]):>7}%")

cur.close(); conn.close()
