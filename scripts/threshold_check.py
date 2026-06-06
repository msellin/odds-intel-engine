"""Ad-hoc: report current values for the 'Key Thresholds to Watch' section of PRIORITY_QUEUE.md.

Schema notes (verified 2026-05-24, refreshed 2026-06-06):
- Fixtures table is `matches`, key is `match_id` (and `matches.id`), status values: scheduled/live/finished/postponed
- match_feature_vectors uses `opening_implied_home/draw/away` (no single `opening_implied` column)
- lineups has match_id but NO `fetched_at` / `captured_at`; date queries must use `matches.lineups_fetched_at`
- **2026-06-06 audit fixes**:
  - `simulated_bets.market` is **lowercase** in the DB (`o/u`, `1x2`, `btts`, `asian_handicap`).
    Earlier queries used uppercase ('O/U', '1X2', 'AH', 'BTTS') and silently returned 0 rows.
  - `news_events` table exists but is empty — news signals now live in `match_signals` with
    `signal_name ILIKE '%news%'`. Same for lineup signals.
  - `simulated_bets` has a single `model_probability` column (not separate poisson/xgb cols);
    P3.2 stacked meta gate must be measured via joins to the `predictions` table where
    `source IN ('poisson', 'xgboost')`.
"""
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

def safe(query, fmt=lambda r: r[0][0]):
    try:
        cur.execute(query)
        return fmt(cur.fetchall())
    except Exception as e:
        conn.rollback()
        return f"ERR: {e.__class__.__name__}: {str(e)[:140]}"

out = []

out.append(("Platt scaling — predictions w/ finished match outcomes",
    safe("""SELECT COUNT(*) FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE m.status = 'finished'""")))

out.append(("In-play — distinct matches in live_match_snapshots w/ xG",
    safe("""SELECT COUNT(DISTINCT match_id) FROM live_match_snapshots
            WHERE xg_home IS NOT NULL OR xg_away IS NOT NULL""")))

out.append(("Meta-model Phase 1 — MFV rows since 2026-05-06",
    safe("""SELECT COUNT(*) FROM match_feature_vectors WHERE match_date >= '2026-05-06'""")))
out.append(("  ... w/ opening_implied_home set (proxy for any opening implied)",
    safe("""SELECT COUNT(*) FROM match_feature_vectors
            WHERE match_date >= '2026-05-06' AND opening_implied_home IS NOT NULL""")))

out.append(("Post-mortem patterns — model_evaluations w/ market='post_mortem'",
    safe("""SELECT COUNT(*) FROM model_evaluations WHERE market='post_mortem'""")))

out.append(("BOT-QUAL-FILTER — settled (won/lost/void) bets since 2026-05-06",
    safe("""SELECT COUNT(*) FROM simulated_bets
            WHERE result != 'pending' AND created_at >= '2026-05-06'""")))

out.append(("ALN-1 — won/lost/void bets since 2026-05-06",
    safe("""SELECT COUNT(*) FROM simulated_bets
            WHERE result IN ('won','lost','void') AND created_at >= '2026-05-06'""")))

# 2026-06-06 audit: news_events table is empty. The signal pipeline writes news +
# lineup signals to match_signals (signal_name ILIKE '%news%' / '%lineup%') with
# captured_at timestamps. Use match_signals for both.
out.append(("News signals — distinct matches in match_signals since 5/6",
    safe("""SELECT COUNT(DISTINCT match_id) FROM match_signals
            WHERE signal_name ILIKE '%news%' AND captured_at >= '2026-05-06'""")))
out.append(("Lineup signals — distinct matches in match_signals since 5/6",
    safe("""SELECT COUNT(DISTINCT match_id) FROM match_signals
            WHERE signal_name ILIKE '%lineup%' AND captured_at >= '2026-05-06'""")))
out.append(("Lineup signals — distinct matches via matches.lineups_fetched_at since 5/6",
    safe("""SELECT COUNT(*) FROM matches WHERE lineups_fetched_at >= '2026-05-06'""")))

def fmt_coverage(r):
    finished, w_stats = r[0]
    pct = (w_stats / finished * 100) if finished else 0
    return f"{w_stats:,} / {finished:,} = {pct:.1f}%"
out.append(("ML-RETRAIN-1 — match_stats coverage (raw, all finished matches)",
    safe("""SELECT (SELECT COUNT(*) FROM matches WHERE status='finished'),
                   (SELECT COUNT(DISTINCT match_id) FROM match_stats)""",
         fmt_coverage)))

# Stats-supplying leagues only: API-Football doesn't return match_stats for many
# lower-tier / cup / women's / regional leagues. The raw metric above is inflated
# by 387 new leagues added in May 2026 that simply don't produce stats. The
# meaningful figure for ML-RETRAIN-1 readiness is the coverage among leagues that
# ever produced any stats — that's what the retrain actually has to work with.
out.append(("ML-RETRAIN-1 — coverage among stats-supplying leagues (TRUE metric)",
    safe("""WITH lcov AS (
                SELECT l.id,
                       COUNT(*) FILTER (WHERE m.status='finished') AS finished,
                       COUNT(ms.match_id) AS w_stats
                FROM leagues l
                LEFT JOIN matches m ON m.league_id = l.id
                LEFT JOIN match_stats ms ON ms.match_id = m.id
                GROUP BY l.id
            )
            SELECT SUM(finished), SUM(w_stats)
            FROM lcov WHERE w_stats > 0""",
         fmt_coverage)))

# 2026-06-06 audit: simulated_bets.market is LOWERCASE in the DB.
# Lowercase values: 'o/u', '1x2', 'btts', 'asian_handicap', 'double_chance', 'draw_no_bet'.
# Prior uppercase queries silently returned 0 rows for ~2 weeks.
#
# 2026-06-06 SUNDAY-RETRAIN-RECON: the queries below use the EXACT same
# filters as `scripts/fit_platt.py` so the count IS the gate count. The
# script needs:
#   * O/U: result IN ('won','lost') AND calibrated_prob IS NOT NULL
#          AND odds_at_pick IS NOT NULL  (gate: 300+ per selection)
#   * BTTS: result IN ('won','lost') AND calibrated_prob IS NOT NULL
#          (gate: 100+ per selection — MIN_SAMPLES_DEFAULT, NOT 300)
#   * 1X2:  from `predictions` table, source='ensemble', match.status='finished'
#          (gate: 100+ per selection)
#   * AH:   NOT IMPLEMENTED in fit_platt.py. The 332 count below is data-
#          readiness only; no Platt fit happens until AH-PLATT-WIRE task ships.
out.append(("CAL-PLATT O/U v14 (300+ gate) — fit_platt's exact filter",
    safe("""SELECT m, COUNT(*) FROM (
              SELECT CASE WHEN selection ILIKE 'over%%' THEN 'over_25_over'
                          WHEN selection ILIKE 'under%%' THEN 'over_25_under'
                     END AS m
              FROM simulated_bets
              WHERE market='o/u' AND result IN ('won','lost')
                AND model_version='v14'
                AND calibrated_prob IS NOT NULL
                AND odds_at_pick IS NOT NULL
            ) s GROUP BY m ORDER BY m""",
         lambda r: ", ".join(f"{m}={n}" for m, n in r) or "no rows")))

out.append(("CAL-PLATT 1X2 v14 — per selection (settled)",
    safe("""SELECT selection, COUNT(*) FROM simulated_bets
            WHERE market='1x2' AND result IN ('won','lost') AND model_version='v14'
            GROUP BY selection ORDER BY selection""",
         lambda r: ", ".join(f"{s}={n}" for s, n in r) or "no rows")))

out.append(("CAL-PLATT BTTS (100+ gate) — fit_platt's exact filter, v14",
    safe("""SELECT m, COUNT(*) FROM (
              SELECT CASE WHEN LOWER(selection)='yes' THEN 'btts_yes' ELSE 'btts_no' END AS m
              FROM simulated_bets
              WHERE market='btts' AND result IN ('won','lost')
                AND model_version='v14'
                AND calibrated_prob IS NOT NULL
            ) s GROUP BY m ORDER BY m""",
         lambda r: ", ".join(f"{m}={n}" for m, n in r) or "no rows")))

out.append(("CAL-PLATT AH — data readiness ONLY (no fit_platt branch yet — see AH-PLATT-WIRE)",
    safe("""SELECT COUNT(*) FROM simulated_bets
            WHERE market='asian_handicap' AND result IN ('won','lost')
              AND calibrated_prob IS NOT NULL""")))

out.append(("CLV rows for bot_meta_v1 — sim_bets w/ clv since 5/6",
    safe("""SELECT COUNT(*) FROM simulated_bets WHERE clv IS NOT NULL AND created_at >= '2026-05-06'""")))

out.append(("Meta-model Phase 2 — settled + CLV + dimension_scores",
    safe("""SELECT COUNT(*) FROM simulated_bets
            WHERE result IN ('won','lost','void') AND created_at >= '2026-05-06'
              AND clv IS NOT NULL AND dimension_scores IS NOT NULL""")))

# 2026-06-06 audit: P3.2 stacked-meta gate. Original gate as written was
# unmeasurable — simulated_bets has a single `model_probability` column (the
# ensemble output), not separate poisson/xgb columns. Per-source predictions
# live in the `predictions` table. Measure as: settled bets where BOTH
# source='poisson' AND source='xgboost' rows exist for the bet's match.
# Caveat: xgboost only predicts 1x2 in current pipeline so most non-1x2
# bets fail this filter. P3.2's stacked-meta target is naturally 1x2-only.
out.append(("P3.2 — settled bets w/ poisson+xgb predictions on the match",
    safe("""SELECT COUNT(*) FROM simulated_bets sb
            WHERE sb.result IN ('won','lost')
              AND EXISTS (SELECT 1 FROM predictions p
                          WHERE p.match_id=sb.match_id AND p.source='poisson')
              AND EXISTS (SELECT 1 FROM predictions p
                          WHERE p.match_id=sb.match_id AND p.source='xgboost')""")))

# bonus context — distinct selections in CAL-PLATT-UPGRADE for v14 to spot if it's `over25`/`under25`
# (selection values are 'over 2.5' / 'under 2.5' / 'over 3.5' / 'under 3.5' etc.)
out.append(("DBG — distinct O/U v14 selection values (settled, any prob)",
    safe("""SELECT selection, COUNT(*) FROM simulated_bets
            WHERE market='o/u' AND result IN ('won','lost') AND model_version='v14'
            GROUP BY selection ORDER BY 2 DESC""",
         lambda r: ", ".join(f"{s}={n}" for s, n in r) or "no rows")))

width = max(len(k) for k, _ in out) + 2
for k, v in out:
    print(f"{k.ljust(width)} {v}")

cur.close()
conn.close()
