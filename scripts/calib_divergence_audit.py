"""CALIB-DIVERGENCE-LOG audit (revised 2026-05-24).

VAL-POST-MORTEM flagged that calibration sometimes flips raw model prob direction
by 20+pp (e.g. raw 10% -> calibrated 39%). Initial assumption was that we'd need
to add a `raw_model_probability` column to audit this retroactively. But the
column ALREADY exists: `simulated_bets.model_probability` is raw, `calibrated_prob`
is post-calibration. So the audit is just an aggregation.

Buckets bets by |cal - raw| divergence magnitude, then reports hit rate and ROI
per bucket. If high-divergence bets underperform, that's a calibration over-correction
worth gating in the bot funnel.
"""
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("SET statement_timeout='120s'")

print("=== Calibration divergence audit ===\n")
print("Source: simulated_bets, settled (won/lost/void) since 2026-05-06, v14 only.\n")

# 1. Overall divergence distribution
cur.execute("""
    WITH bets AS (
        SELECT model_probability::float AS raw_p,
               calibrated_prob::float   AS cal_p,
               ABS(calibrated_prob - model_probability)::float AS divergence,
               (calibrated_prob > model_probability) AS cal_pushed_up,
               result, pnl, stake, market, edge_percent, kelly_fraction
        FROM simulated_bets
        WHERE result IN ('won','lost','void')
          AND created_at >= '2026-05-06'
          AND model_version = 'v14'
          AND calibrated_prob IS NOT NULL
          AND model_probability IS NOT NULL
    )
    SELECT
        CASE
            WHEN divergence < 0.02 THEN '0_<=2pp'
            WHEN divergence < 0.05 THEN '1_2-5pp'
            WHEN divergence < 0.10 THEN '2_5-10pp'
            WHEN divergence < 0.15 THEN '3_10-15pp'
            WHEN divergence < 0.20 THEN '4_15-20pp'
            ELSE                         '5_>20pp'
        END AS bucket,
        COUNT(*) AS n,
        ROUND(100.0 * COUNT(*) FILTER (WHERE result='won') / NULLIF(COUNT(*) FILTER (WHERE result IN ('won','lost')),0), 1) AS hit_rate,
        ROUND(100.0 * SUM(pnl) / NULLIF(SUM(stake), 0), 1) AS roi_pct,
        ROUND(AVG(divergence)::numeric, 4) AS avg_div,
        COUNT(*) FILTER (WHERE cal_pushed_up) AS cal_up,
        COUNT(*) FILTER (WHERE NOT cal_pushed_up) AS cal_down
    FROM bets GROUP BY bucket ORDER BY bucket
""")
print(f"  {'bucket':<14}{'n':>5}{'hit%':>7}{'ROI%':>8}{'avg_div':>10}{'cal↑':>6}{'cal↓':>6}")
for r in cur.fetchall():
    print(f"  {r[0]:<14}{r[1]:>5}{str(r[2]):>7}{str(r[3]):>8}{str(r[4]):>10}{r[5]:>6}{r[6]:>6}")

# 2. Direction-flips: did calibration cross the implied-prob threshold?
# A "sign flip" happens when raw < ip but cal > ip, or vice versa.
# We're effectively betting the market when calibration shifts the call.
print("\n=== Direction flips: raw vs cal on opposite sides of implied prob ===\n")
cur.execute("""
    WITH bets AS (
        SELECT model_probability::float AS raw_p,
               calibrated_prob::float   AS cal_p,
               (1.0 / odds_at_pick)::float AS ip,
               result, pnl, stake
        FROM simulated_bets
        WHERE result IN ('won','lost','void')
          AND created_at >= '2026-05-06'
          AND model_version = 'v14'
          AND calibrated_prob IS NOT NULL
          AND model_probability IS NOT NULL
          AND odds_at_pick > 0
    )
    SELECT
        CASE
            WHEN raw_p < ip AND cal_p > ip THEN 'flipped_up (raw_neg → cal_pos)'
            WHEN raw_p > ip AND cal_p < ip THEN 'flipped_down (raw_pos → cal_neg)'
            ELSE                                'no_flip'
        END AS flip_state,
        COUNT(*) AS n,
        ROUND(100.0 * COUNT(*) FILTER (WHERE result='won') / NULLIF(COUNT(*) FILTER (WHERE result IN ('won','lost')),0), 1) AS hit_rate,
        ROUND(100.0 * SUM(pnl) / NULLIF(SUM(stake), 0), 1) AS roi_pct
    FROM bets GROUP BY flip_state ORDER BY flip_state
""")
for r in cur.fetchall():
    print(f"  {r[0]:<40}  n={r[1]:>5}  hit%={str(r[2]):>5}  ROI%={str(r[3]):>7}")

# 3. Per-tier divergence — VAL-POST-MORTEM specifically called out tier 3-4
print("\n=== Divergence by league tier ===\n")
cur.execute("""
    WITH bets AS (
        SELECT sb.model_probability::float AS raw_p,
               sb.calibrated_prob::float   AS cal_p,
               ABS(sb.calibrated_prob - sb.model_probability)::float AS divergence,
               sb.result, sb.pnl, sb.stake,
               l.tier
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE sb.result IN ('won','lost','void')
          AND sb.created_at >= '2026-05-06'
          AND sb.model_version = 'v14'
          AND sb.calibrated_prob IS NOT NULL
          AND sb.model_probability IS NOT NULL
    )
    SELECT tier,
           COUNT(*) AS n,
           ROUND(AVG(divergence)::numeric, 4) AS avg_div,
           ROUND(STDDEV(divergence)::numeric, 4) AS std_div,
           ROUND(100.0 * SUM(pnl) / NULLIF(SUM(stake), 0), 1) AS roi_pct,
           COUNT(*) FILTER (WHERE divergence >= 0.15) AS high_div
    FROM bets GROUP BY tier ORDER BY tier NULLS LAST
""")
print(f"  {'tier':<6}{'n':>6}{'avg_div':>10}{'std':>8}{'ROI%':>8}{'high_div(>=15pp)':>20}")
for r in cur.fetchall():
    print(f"  {str(r[0]):<6}{r[1]:>6}{str(r[2]):>10}{str(r[3]):>8}{str(r[4]):>8}{r[5]:>20}")

# 4. Top examples of high-divergence settled bets — sanity check
print("\n=== Top 10 highest-divergence settled bets (for manual eyeball) ===\n")
cur.execute("""
    SELECT sb.created_at::date AS d,
           b.name AS bot,
           sb.market, sb.selection,
           sb.model_probability AS raw_p,
           sb.calibrated_prob AS cal_p,
           sb.odds_at_pick AS odds,
           sb.result, sb.pnl,
           l.tier
    FROM simulated_bets sb
    JOIN bots b ON b.id = sb.bot_id
    JOIN matches m ON m.id = sb.match_id
    LEFT JOIN leagues l ON l.id = m.league_id
    WHERE sb.result IN ('won','lost')
      AND sb.created_at >= '2026-05-06'
      AND sb.model_version = 'v14'
      AND sb.calibrated_prob IS NOT NULL
      AND sb.model_probability IS NOT NULL
    ORDER BY ABS(sb.calibrated_prob - sb.model_probability) DESC
    LIMIT 10
""")
for r in cur.fetchall():
    print(f"  {r[0]} {r[1]:<24} {r[2]:<6} {r[3]:<10} raw={float(r[4]):.2f} cal={float(r[5]):.2f} "
          f"@{float(r[6]):.2f} T{r[9]} -> {r[7]:<5} pnl={float(r[8]):+.2f}")

cur.close(); conn.close()
