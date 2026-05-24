"""MFV-LINEUP-BACKFILL — populate match_feature_vectors.lineup_confirmed retroactively.

NEWS-LINEUP-VALIDATE (2026-05-24) found lineup_confirmed was 100% NULL because
nothing wrote a 'lineup_confirmed' signal to match_signals. The MFV builder fix
(MFV-LINEUP-WIRE) now derives it from matches.lineups_fetched_at, but only for
NEWLY built rows. This script backfills the 54k+ historical MFV rows where the
column is still NULL.

Idempotent — only updates rows where lineup_confirmed IS NULL.
"""
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("SET statement_timeout='300s'")

print("=== MFV-LINEUP-BACKFILL ===\n")

# Pre-state
cur.execute("""
    SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE lineup_confirmed IS NULL) AS null_now
    FROM match_feature_vectors
""")
total, null_pre = cur.fetchone()
print(f"  Pre:  {total:,} MFV rows total, {null_pre:,} with lineup_confirmed IS NULL\n")

# Update
print(f"  Running UPDATE ...")
cur.execute("""
    UPDATE match_feature_vectors mfv
    SET lineup_confirmed = (m.lineups_fetched_at IS NOT NULL)
    FROM matches m
    WHERE m.id = mfv.match_id
      AND mfv.lineup_confirmed IS NULL
""")
n_updated = cur.rowcount
conn.commit()
print(f"  Updated {n_updated:,} rows.\n")

# Post-state
cur.execute("""
    SELECT COUNT(*) AS total,
           COUNT(*) FILTER (WHERE lineup_confirmed = TRUE) AS w_lineups,
           COUNT(*) FILTER (WHERE lineup_confirmed = FALSE) AS no_lineups,
           COUNT(*) FILTER (WHERE lineup_confirmed IS NULL) AS still_null
    FROM match_feature_vectors
""")
r = cur.fetchone()
print(f"  Post: total={r[0]:,}, lineup_confirmed=TRUE={r[1]:,}, FALSE={r[2]:,}, NULL={r[3]:,}")

# Post-cutoff (B-ML3 training window)
cur.execute("""
    SELECT COUNT(*) AS total,
           COUNT(*) FILTER (WHERE lineup_confirmed = TRUE) AS w_lineups,
           COUNT(*) FILTER (WHERE lineup_confirmed IS NULL) AS still_null
    FROM match_feature_vectors
    WHERE match_date >= '2026-05-06'
""")
r = cur.fetchone()
print(f"\n  B-ML3 training window (>= 2026-05-06):")
print(f"    total={r[0]:,}, lineup_confirmed=TRUE={r[1]:,} ({100*r[1]/max(r[0],1):.1f}%), NULL={r[2]:,}")

cur.close(); conn.close()
print("\n  Done.")
