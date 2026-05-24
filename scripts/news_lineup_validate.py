"""NEWS-LINEUP-VALIDATE — gate test for B-ML3 feature selection.

Two questions from PRIORITY_QUEUE:
  (1) AUC of `news_impact_score` vs actual outcome divergence — gate: >0.52
  (2) `lineup_confidence` accuracy check

Both signals are stored in match_signals (raw) and in match_feature_vectors (the MFV
schema that B-ML3 will read). We validate against MFV since that's what B-ML3 sees.

For (1) we compute AUC against three targets:
  - home_win    (1 = home won, 0 = otherwise) — does positive news help home?
  - away_win    (1 = away won, 0 = otherwise) — does negative news help away?
  - pseudo_clv  (1 = pseudo_clv > 0, 0 = otherwise) — does news predict CLV+ picks?

For (2) we partition by lineup_confirmed and compare prediction calibration.

AUC computed via Mann-Whitney U / rank-based formula (no sklearn dependency).
"""
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2
from statistics import mean, stdev

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute("SET statement_timeout='180s'")


def auc_rank(scores, labels):
    """Mann-Whitney U-based AUC. scores: list of floats; labels: list of 0/1.

    Returns (auc, n_pos, n_neg). None if either class is empty.
    """
    pairs = sorted(zip(scores, labels), key=lambda x: x[0])
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None, n_pos, n_neg
    # Rank with average ties
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j < len(pairs) - 1 and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1  # ranks are 1-based
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    sum_ranks_pos = sum(r for r, (s, lab) in zip(ranks, pairs) if lab == 1)
    u_pos = sum_ranks_pos - n_pos * (n_pos + 1) / 2
    auc = u_pos / (n_pos * n_neg)
    return auc, n_pos, n_neg


def auc_ci(auc, n_pos, n_neg, z=1.96):
    """Hanley-McNeil approximate 95% CI for AUC. Returns (low, high)."""
    if auc is None:
        return None, None
    q1 = auc / (2 - auc)
    q2 = (2 * auc * auc) / (1 + auc)
    var = (auc * (1 - auc) + (n_pos - 1) * (q1 - auc * auc) + (n_neg - 1) * (q2 - auc * auc)) / (n_pos * n_neg)
    if var <= 0:
        return auc, auc
    se = var ** 0.5
    return max(0.0, auc - z * se), min(1.0, auc + z * se)


print("=" * 70)
print("NEWS-LINEUP-VALIDATE")
print("=" * 70)

# --------------------------------------------------------------------
# Part 1: news_impact_score AUC
# --------------------------------------------------------------------
print("\nPart 1: news_impact_score predictive validity\n")

# Pull MFV rows that have settled outcomes + non-null news signal
cur.execute("""
    SELECT news_impact_score::float AS news,
           match_outcome,
           pseudo_clv_home::float AS pclv_h,
           pseudo_clv_draw::float AS pclv_d,
           pseudo_clv_away::float AS pclv_a,
           ensemble_prob_home::float AS ph,
           ensemble_prob_away::float AS pa
    FROM match_feature_vectors
    WHERE match_date >= '2026-05-06'
      AND news_impact_score IS NOT NULL
      AND match_outcome IS NOT NULL
""")
rows = cur.fetchall()
print(f"  total settled MFV rows with news signal: {len(rows)}")
non_zero = [r for r in rows if r[0] != 0.0]
print(f"  rows where news_impact_score is non-zero: {len(non_zero)} ({100*len(non_zero)/max(len(rows),1):.0f}%)")
print()

if non_zero:
    avg = mean(r[0] for r in non_zero)
    print(f"  avg news_impact_score (non-zero): {avg:+.4f}")
    print(f"  range: [{min(r[0] for r in non_zero):+.3f}, {max(r[0] for r in non_zero):+.3f}]")
    print()

def home_win_label(outcome):  # match_outcome values: 'home', 'draw', 'away'
    return 1 if outcome == "home" else 0
def away_win_label(outcome):
    return 1 if outcome == "away" else 0

# Drop rows where news_impact_score is exactly 0 (it's a "no news" signal, not predictive)
nz = non_zero
scores = [r[0] for r in nz]

for tgt_name, label_fn in [
    ("home_win (positive news_impact -> home should win)", home_win_label),
    ("away_win (negative news_impact -> away should win)", away_win_label),
]:
    labels = [label_fn(r[1]) for r in nz]
    auc, n_pos, n_neg = auc_rank(scores, labels)
    if auc is None:
        print(f"  {tgt_name}: insufficient class balance")
        continue
    lo, hi = auc_ci(auc, n_pos, n_neg)
    gate = ">= 0.52 (gate)" if auc >= 0.52 else "< 0.52 (FAILS gate)"
    print(f"  AUC vs {tgt_name}")
    print(f"    AUC = {auc:.4f}   95% CI [{lo:.4f}, {hi:.4f}]   n_pos={n_pos} n_neg={n_neg}   {gate}")

# Pseudo-CLV target: did the home pseudo_clv exceed zero? (i.e., the bet would have been +CLV)
# pseudo_clv_{home,draw,away} are computed against opening odds. We use home as the canonical target.
print()
labels_pclv_h = [1 if (r[2] is not None and r[2] > 0) else 0 for r in nz if r[2] is not None]
scores_pclv_h = [r[0] for r in nz if r[2] is not None]
if labels_pclv_h:
    auc, n_pos, n_neg = auc_rank(scores_pclv_h, labels_pclv_h)
    if auc is not None:
        lo, hi = auc_ci(auc, n_pos, n_neg)
        gate = ">= 0.52 (gate)" if auc >= 0.52 else "< 0.52 (FAILS gate)"
        print(f"  AUC vs pseudo_clv_home > 0 (B-ML3 target)")
        print(f"    AUC = {auc:.4f}   95% CI [{lo:.4f}, {hi:.4f}]   n_pos={n_pos} n_neg={n_neg}   {gate}")

# --------------------------------------------------------------------
# Part 1b: also check against match_signals.news_impact_score directly
# (in case MFV news_impact is downstream-computed and loses signal)
# --------------------------------------------------------------------
print("\nPart 1b: Direct match_signals validation (sanity check)\n")
cur.execute("""
    SELECT ms.signal_value::float AS news,
           m.result AS outcome
    FROM match_signals ms
    JOIN matches m ON m.id = ms.match_id
    WHERE ms.signal_name = 'news_impact_score'
      AND ms.created_at >= '2026-05-06'
      AND m.status = 'finished'
      AND m.result IS NOT NULL
      AND ms.signal_value != 0
""")
ms_rows = cur.fetchall()
print(f"  distinct rows (non-zero news, finished): {len(ms_rows)}")
if ms_rows:
    scores = [r[0] for r in ms_rows]
    labels = [1 if r[1] == "home" else 0 for r in ms_rows]
    auc, n_pos, n_neg = auc_rank(scores, labels)
    if auc:
        lo, hi = auc_ci(auc, n_pos, n_neg)
        gate = ">= 0.52" if auc >= 0.52 else "< 0.52"
        print(f"  AUC (match_signals) vs home_win: {auc:.4f}   CI [{lo:.4f}, {hi:.4f}]   n_pos={n_pos} n_neg={n_neg}   {gate}")

# --------------------------------------------------------------------
# Part 2: lineup_confidence
# --------------------------------------------------------------------
print("\nPart 2: lineup signal predictive validity\n")

# MFV stores lineup_confirmed (boolean). match_signals stores lineup_confidence (float).
cur.execute("""
    SELECT lineup_confirmed, COUNT(*) AS n,
           AVG(CASE WHEN match_outcome='home' THEN 1 ELSE 0 END)::float AS home_win_rate,
           AVG(ensemble_prob_home)::float AS avg_pred_home,
           COUNT(*) FILTER (WHERE pseudo_clv_home > 0)::float / NULLIF(COUNT(*),0) AS pclv_pos_rate
    FROM match_feature_vectors
    WHERE match_date >= '2026-05-06'
      AND match_outcome IS NOT NULL
    GROUP BY lineup_confirmed
""")
print(f"  {'lineup_confirmed':<18}{'n':>6}{'home_win_rate':>16}{'avg_pred_home':>16}{'pclv+_rate':>14}")
for r in cur.fetchall():
    n, hwr, aph, pcr = r[1], r[2] or 0, r[3] or 0, r[4] or 0
    print(f"  {str(r[0]):<18}{n:>6}{hwr:>16.4f}{aph:>16.4f}{pcr:>14.4f}")

# Calibration error per partition: |avg_pred_home - actual_home_win_rate|
print("\n  Calibration delta (|avg_pred_home - actual_home_win_rate|):")
cur.execute("""
    SELECT lineup_confirmed, COUNT(*) AS n,
           ABS(AVG(ensemble_prob_home) - AVG(CASE WHEN match_outcome='home' THEN 1.0 ELSE 0 END))::float AS calib_delta
    FROM match_feature_vectors
    WHERE match_date >= '2026-05-06'
      AND match_outcome IS NOT NULL
      AND ensemble_prob_home IS NOT NULL
    GROUP BY lineup_confirmed
""")
for r in cur.fetchall():
    print(f"    {str(r[0]):<18}n={r[1]:>5}   Δ={r[2]:.4f}")

# match_signals lineup_confidence — bucket by confidence and check hit rate
print("\n  match_signals.lineup_confidence vs home_win rate:")
cur.execute("""
    SELECT
        CASE
            WHEN ms.signal_value < 0.5 THEN '0_<0.5'
            WHEN ms.signal_value < 0.7 THEN '1_0.5-0.7'
            WHEN ms.signal_value < 0.9 THEN '2_0.7-0.9'
            ELSE                            '3_>=0.9'
        END AS bucket,
        COUNT(*) AS n,
        AVG(CASE WHEN m.result='home' THEN 1.0 ELSE 0 END)::float AS home_win_rate,
        AVG(ms.signal_value)::float AS avg_conf
    FROM match_signals ms
    JOIN matches m ON m.id = ms.match_id
    WHERE ms.signal_name = 'lineup_confidence'
      AND ms.created_at >= '2026-05-06'
      AND m.status = 'finished'
      AND m.result IS NOT NULL
    GROUP BY bucket ORDER BY bucket
""")
for r in cur.fetchall():
    print(f"    {r[0]:<12}n={r[1]:>5}   home_win={r[2]:.3f}   avg_conf={r[3]:.3f}")

# --------------------------------------------------------------------
# Verdict
# --------------------------------------------------------------------
print("\n" + "=" * 70)
print("VERDICT — feed into B-ML3 feature list?")
print("=" * 70)
print()
print("Read the AUC numbers above. Gate is 0.52.")
print("  news_impact_score: include if AUC >= 0.52 with CI lower bound > 0.50")
print("  lineup_confirmed:  include if it materially shifts calibration (Δ delta > 0.02)")
print("                     OR if higher-confidence-bucket hit rate is meaningfully different")
print()

cur.close(); conn.close()
