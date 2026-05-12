"""
ALN-1 retrospective analysis.

Answers three questions:
  1. How many past bets would ALN-1 have filtered?
  2. Was their ROI worse than retained bets? (does the filter help?)
  3. Per-bot impact — which bots lose the most volume?

Run any time to refresh. Uses quality bets only (created_at >= 2026-05-06).

Usage:
    python3 scripts/aln1_analysis.py
"""

import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import psycopg2
from decimal import Decimal

LOW_BUMP = 0.01   # extra edge fraction required for LOW-alignment bets
BASE_THRESHOLD = 0.05  # approximate base edge threshold

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

print("=" * 62)
print("ALN-1 RETROSPECTIVE ANALYSIS")
print("Quality bets only (created_at >= 2026-05-06)")
print(f"Rule: LOW alignment bets need edge >= base + {LOW_BUMP*100:.0f}%")
print("=" * 62)

# --- 1. Impact by alignment class and filter decision
threshold_low = BASE_THRESHOLD + LOW_BUMP
cur.execute(f"""
    SELECT
        alignment_class,
        CASE
            WHEN alignment_class = 'LOW' AND edge_percent < {threshold_low}
            THEN 'filtered' ELSE 'retained'
        END AS aln1,
        COUNT(*)                                           AS bets,
        ROUND(SUM(pnl)::numeric, 2)                        AS total_pnl,
        ROUND(SUM(pnl) / NULLIF(SUM(stake), 0) * 100, 1)  AS roi_pct,
        ROUND(AVG(edge_percent) * 100, 2)                  AS avg_edge_pct,
        ROUND(AVG(clv) * 100, 2)                           AS avg_clv_pct
    FROM simulated_bets
    WHERE result IN ('won','lost','void')
      AND created_at >= '2026-05-06'
      AND alignment_class IS NOT NULL
    GROUP BY alignment_class, aln1
    ORDER BY alignment_class, aln1
""")
rows = cur.fetchall()

print(f"\n{'Class':<10} {'Decision':<10} {'Bets':>6} {'PnL':>8} {'ROI%':>7} {'AvgEdge%':>10} {'AvgCLV%':>9}")
print("-" * 66)
for cls, dec, bets, pnl, roi, edge, clv in rows:
    marker = "  ← would drop" if dec == "filtered" else ""
    pnl_f = float(pnl or 0)
    roi_f = float(roi or 0)
    edge_f = float(edge or 0)
    clv_f = float(clv or 0)
    print(f"{cls:<10} {dec:<10} {bets:>6} {pnl_f:>8.2f} {roi_f:>7.1f}% {edge_f:>9.2f}% {clv_f:>8.2f}%{marker}")

# --- 2. Summary
cur.execute(f"""
    SELECT
        COUNT(*) FILTER (
            WHERE alignment_class = 'LOW' AND edge_percent < {threshold_low}
        )                                                   AS would_filter,
        COUNT(*)                                            AS total,
        SUM(pnl) FILTER (
            WHERE alignment_class = 'LOW' AND edge_percent < {threshold_low}
        )                                                   AS filtered_pnl,
        ROUND(
            SUM(pnl) FILTER (
                WHERE NOT (alignment_class = 'LOW' AND edge_percent < {threshold_low})
            ) / NULLIF(
                SUM(stake) FILTER (
                    WHERE NOT (alignment_class = 'LOW' AND edge_percent < {threshold_low})
                ), 0
            ) * 100, 1
        )                                                   AS roi_retained
    FROM simulated_bets
    WHERE result IN ('won','lost','void')
      AND created_at >= '2026-05-06'
      AND alignment_class IS NOT NULL
""")
would_filter, total, filtered_pnl, roi_retained = cur.fetchone()
filtered_pnl = float(filtered_pnl or 0)
roi_retained = float(roi_retained or 0)

print(f"\n--- Summary ---")
print(f"Bets filtered: {would_filter} / {total}  ({would_filter/total*100:.1f}%)")
print(f"PnL of filtered bets: €{filtered_pnl:.2f}  (negative = we avoid losers ✓, positive = we drop winners ✗)")
print(f"ROI of retained bets: {roi_retained:.1f}%")

# --- 3. Per-bot volume impact
cur.execute(f"""
    SELECT
        b.name,
        COUNT(*)                                            AS total_bets,
        COUNT(*) FILTER (
            WHERE sb.alignment_class = 'LOW' AND sb.edge_percent < {threshold_low}
        )                                                   AS would_filter,
        ROUND(SUM(sb.pnl) / NULLIF(SUM(sb.stake), 0) * 100, 1)  AS roi_all,
        ROUND(
            SUM(sb.pnl) FILTER (
                WHERE NOT (sb.alignment_class = 'LOW' AND sb.edge_percent < {threshold_low})
            ) / NULLIF(
                SUM(sb.stake) FILTER (
                    WHERE NOT (sb.alignment_class = 'LOW' AND sb.edge_percent < {threshold_low})
                ), 0
            ) * 100, 1
        )                                                   AS roi_retained
    FROM simulated_bets sb
    JOIN bots b ON sb.bot_id = b.id
    WHERE sb.result IN ('won','lost','void')
      AND sb.created_at >= '2026-05-06'
      AND sb.alignment_class IS NOT NULL
    GROUP BY b.name
    HAVING COUNT(*) >= 3
    ORDER BY would_filter DESC, total_bets DESC
""")
rows = cur.fetchall()

print(f"\n--- Per-bot impact ---")
print(f"{'Bot':<28} {'Total':>6} {'Filter':>7} {'ROI all':>8} {'ROI kept':>9}")
print("-" * 64)
for name, total, filt, roi_all, roi_ret in rows:
    filt = filt or 0
    roi_all = float(roi_all or 0)
    roi_ret = float(roi_ret or 0)
    arrow = " ↑" if roi_ret > roi_all + 0.5 else (" ↓" if roi_ret < roi_all - 0.5 else "")
    print(f"{name:<28} {total:>6} {filt:>7} {roi_all:>7.1f}% {roi_ret:>8.1f}%{arrow}")

print("\n↑/↓ = ROI direction change after applying ALN-1 filter")

conn.close()
