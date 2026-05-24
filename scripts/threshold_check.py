"""Ad-hoc: report current values for the 'Key Thresholds to Watch' section of PRIORITY_QUEUE.md.

Schema notes (verified 2026-05-24):
- Fixtures table is `matches`, key is `match_id` (and `matches.id`), status values: scheduled/live/finished/postponed
- News uses `news_events` (not news_analysis)
- match_feature_vectors uses `opening_implied_home/draw/away` (no single `opening_implied` column)
- lineups uses `match_id` (no `fixture_id` / `captured_at`)
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

out.append(("News signals — distinct matches in news_events since 5/6",
    safe("""SELECT COUNT(DISTINCT match_id) FROM news_events WHERE detected_at >= '2026-05-06'""")))

out.append(("Lineup signals — distinct matches in lineups (no date col; total)",
    safe("""SELECT COUNT(DISTINCT match_id) FROM lineups""")))
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

out.append(("CAL-PLATT O/U v14 — per selection (calibrated_prob & odds set)",
    safe("""SELECT selection, COUNT(*) FROM simulated_bets
            WHERE market='O/U' AND result IN ('won','lost')
              AND model_version='v14'
              AND calibrated_prob IS NOT NULL
              AND odds_at_pick IS NOT NULL
            GROUP BY selection ORDER BY selection""",
         lambda r: ", ".join(f"{s}={n}" for s, n in r) or "no rows")))

out.append(("CAL-PLATT 1X2 v14 — per selection (settled)",
    safe("""SELECT selection, COUNT(*) FROM simulated_bets
            WHERE market='1X2' AND result IN ('won','lost') AND model_version='v14'
            GROUP BY selection ORDER BY selection""",
         lambda r: ", ".join(f"{s}={n}" for s, n in r) or "no rows")))

out.append(("CAL-PLATT AH/BTTS — settled since 5/6",
    safe("""SELECT market, COUNT(*) FROM simulated_bets
            WHERE market IN ('AH','BTTS') AND result IN ('won','lost','void')
              AND created_at >= '2026-05-06'
            GROUP BY market ORDER BY market""",
         lambda r: ", ".join(f"{m}={n}" for m, n in r) or "no rows")))

out.append(("CLV rows for bot_meta_v1 — sim_bets w/ clv since 5/6",
    safe("""SELECT COUNT(*) FROM simulated_bets WHERE clv IS NOT NULL AND created_at >= '2026-05-06'""")))

out.append(("Meta-model Phase 2 — settled + CLV + dimension_scores",
    safe("""SELECT COUNT(*) FROM simulated_bets
            WHERE result IN ('won','lost','void') AND created_at >= '2026-05-06'
              AND clv IS NOT NULL AND dimension_scores IS NOT NULL""")))

# bonus context — distinct selections in CAL-PLATT-UPGRADE for v14 to spot if it's `over25`/`under25`
out.append(("DBG — distinct O/U v14 selection values (settled, any prob)",
    safe("""SELECT selection, COUNT(*) FROM simulated_bets
            WHERE market='O/U' AND result IN ('won','lost') AND model_version='v14'
            GROUP BY selection ORDER BY 2 DESC""",
         lambda r: ", ".join(f"{s}={n}" for s, n in r) or "no rows")))

width = max(len(k) for k, _ in out) + 2
for k, v in out:
    print(f"{k.ljust(width)} {v}")

cur.close()
conn.close()
