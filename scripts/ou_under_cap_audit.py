"""OU-UNDER-CAP investigation (VAL-POST-MORTEM follow-up, 2026-05-24).

Hypothesis from the post-mortem: high-conviction OU-under picks (calibrated_prob > 0.75)
underperform their predicted hit rate because Poisson variance is too tight at low total
expected goals — real football has a fatter right tail than independent Poisson goals.

Method:
  1. Pull all v14 OU-under bets settled since 2026-05-06 with calibrated_prob set.
  2. Bucket by calibrated_prob and compare predicted hit rate to actual hit rate.
  3. If actual < predicted in the high-conviction buckets, the pattern is real and
     a min_prob cap is justified.
  4. Surface specific blowout cases for review.
"""
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("SET statement_timeout='120s'")

print("=== OU-UNDER-CAP audit ===\n")
print("Source: simulated_bets settled (won/lost), v14, market=o/u, selection LIKE 'under%',")
print("        created_at >= 2026-05-06\n")

# 1. Total settled OU-under bets
cur.execute("""
    SELECT COUNT(*) FROM simulated_bets
    WHERE market='o/u' AND selection LIKE 'under%'
      AND result IN ('won','lost') AND model_version='v14'
      AND created_at >= '2026-05-06'
""")
total = cur.fetchone()[0]
print(f"Total settled OU-under v14 bets since 5/6: {total}\n")

# 2. Calibration table: predicted vs actual hit rate per bucket
print("=== Predicted vs actual hit rate by calibrated_prob bucket ===\n")
cur.execute("""
    WITH bets AS (
        SELECT calibrated_prob::float AS cal_p,
               (CASE WHEN result='won' THEN 1 ELSE 0 END) AS won,
               pnl, stake, odds_at_pick, selection
        FROM simulated_bets
        WHERE market='o/u' AND selection LIKE 'under%'
          AND result IN ('won','lost') AND model_version='v14'
          AND created_at >= '2026-05-06'
          AND calibrated_prob IS NOT NULL
    )
    SELECT
        CASE
            WHEN cal_p < 0.55 THEN '50-55%'
            WHEN cal_p < 0.60 THEN '55-60%'
            WHEN cal_p < 0.65 THEN '60-65%'
            WHEN cal_p < 0.70 THEN '65-70%'
            WHEN cal_p < 0.75 THEN '70-75%'
            WHEN cal_p < 0.80 THEN '75-80%'
            WHEN cal_p < 0.85 THEN '80-85%'
            WHEN cal_p < 0.90 THEN '85-90%'
            ELSE                     '90%+'
        END AS bucket,
        COUNT(*) AS n,
        ROUND(AVG(cal_p)::numeric * 100, 1) AS predicted_pct,
        ROUND(100.0 * SUM(won) / COUNT(*), 1) AS actual_pct,
        ROUND(100.0 * SUM(won) / COUNT(*) - AVG(cal_p)::numeric * 100, 1) AS delta,
        ROUND(100.0 * SUM(pnl) / NULLIF(SUM(stake),0), 1) AS roi_pct
    FROM bets GROUP BY bucket ORDER BY bucket
""")
print(f"  {'bucket':<10}{'n':>4}{'predicted%':>12}{'actual%':>10}{'Δ':>8}{'ROI%':>8}")
rows = cur.fetchall()
totals = {"n":0,"sum_pred":0,"sum_won":0,"sum_pnl":0,"sum_stake":0}
for r in rows:
    delta = f"{float(r[4]):+.1f}" if r[4] is not None else "—"
    print(f"  {r[0]:<10}{r[1]:>4}{str(r[2]):>12}{str(r[3]):>10}{delta:>8}{str(r[5]):>8}")

# 3. Split by OU line (1.5, 2.5, 3.5)
print("\n=== Split by OU line (2.5 is by far the biggest) ===\n")
cur.execute("""
    SELECT selection, COUNT(*) AS n,
           ROUND(AVG(calibrated_prob)::numeric * 100, 1) AS predicted_pct,
           ROUND(100.0 * COUNT(*) FILTER (WHERE result='won') / COUNT(*), 1) AS actual_pct,
           ROUND(100.0 * SUM(pnl) / NULLIF(SUM(stake),0), 1) AS roi_pct
    FROM simulated_bets
    WHERE market='o/u' AND selection LIKE 'under%'
      AND result IN ('won','lost') AND model_version='v14'
      AND created_at >= '2026-05-06'
      AND calibrated_prob IS NOT NULL
    GROUP BY selection ORDER BY n DESC
""")
for r in cur.fetchall():
    print(f"  {r[0]:<12}  n={r[1]:>4}  predicted={r[2]}%  actual={r[3]}%  Δ={float(r[3]) - float(r[2]):+.1f}pp  ROI={r[4]}%")

# 4. High-conviction subset (cal_p >= 0.75) — the post-mortem's specific concern
print("\n=== High-conviction subset: calibrated_prob >= 0.75 ===\n")
cur.execute("""
    WITH bets AS (
        SELECT calibrated_prob::float AS cal_p,
               (CASE WHEN result='won' THEN 1 ELSE 0 END) AS won,
               pnl, stake, odds_at_pick, selection
        FROM simulated_bets
        WHERE market='o/u' AND selection LIKE 'under%'
          AND result IN ('won','lost') AND model_version='v14'
          AND created_at >= '2026-05-06'
          AND calibrated_prob >= 0.75
    )
    SELECT
        COUNT(*) AS n,
        ROUND(AVG(cal_p)::numeric * 100, 1) AS predicted_pct,
        ROUND(100.0 * SUM(won) / NULLIF(COUNT(*),0), 1) AS actual_pct,
        ROUND(100.0 * SUM(pnl) / NULLIF(SUM(stake),0), 1) AS roi_pct,
        SUM(pnl) AS sum_pnl,
        SUM(stake) AS sum_stake
    FROM bets
""")
r = cur.fetchone()
print(f"  n={r[0]}  predicted={r[1]}%  actual={r[2]}%  Δ={float(r[2] or 0) - float(r[1] or 0):+.1f}pp  ROI={r[3]}%")
print(f"  total pnl={r[4]}  total stake={r[5]}")

# 5. Per-tier breakdown of high-conviction bucket
print("\n=== High-conviction (>=0.75) by tier ===\n")
cur.execute("""
    WITH bets AS (
        SELECT sb.calibrated_prob::float AS cal_p,
               (CASE WHEN sb.result='won' THEN 1 ELSE 0 END) AS won,
               sb.pnl, sb.stake, l.tier
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE sb.market='o/u' AND sb.selection LIKE 'under%'
          AND sb.result IN ('won','lost') AND sb.model_version='v14'
          AND sb.created_at >= '2026-05-06'
          AND sb.calibrated_prob >= 0.75
    )
    SELECT tier, COUNT(*) AS n,
           ROUND(AVG(cal_p)::numeric * 100, 1) AS predicted_pct,
           ROUND(100.0 * SUM(won) / NULLIF(COUNT(*),0), 1) AS actual_pct,
           ROUND(100.0 * SUM(pnl) / NULLIF(SUM(stake),0), 1) AS roi_pct
    FROM bets GROUP BY tier ORDER BY tier NULLS LAST
""")
for r in cur.fetchall():
    delta = float(r[3] or 0) - float(r[2] or 0)
    print(f"  tier {str(r[0]):<6}  n={r[1]:>3}  predicted={r[2]}%  actual={r[3]}%  Δ={delta:+.1f}pp  ROI={r[4]}%")

# 6. The blowout cases — actual scores when high-conviction under lost
print("\n=== Sample of high-conviction losses (the blowouts) ===\n")
cur.execute("""
    SELECT sb.created_at::date, ht.name AS home, at.name AS away,
           sb.selection, sb.calibrated_prob::float AS cal_p,
           sb.odds_at_pick, m.score_home, m.score_away,
           (m.score_home + m.score_away) AS total_goals
    FROM simulated_bets sb
    JOIN matches m ON m.id = sb.match_id
    JOIN teams ht ON ht.id = m.home_team_id
    JOIN teams at ON at.id = m.away_team_id
    WHERE sb.market='o/u' AND sb.selection LIKE 'under%'
      AND sb.result='lost' AND sb.model_version='v14'
      AND sb.created_at >= '2026-05-06'
      AND sb.calibrated_prob >= 0.75
    ORDER BY sb.calibrated_prob DESC LIMIT 10
""")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]} {r[2]:<22} vs {r[3]:<22}  cal={float(r[4]):.2f} @{float(r[5]):.2f}  → {r[6]}-{r[7]} (total {r[8]})")

cur.close(); conn.close()
