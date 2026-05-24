"""Recompute real_bets.edge_pct_taken using the additive-edge formula.

REAL-BETS-EDGE-FORMULA-FIX (2026-05-24):
The 2026-05-23 EFFECTIVE-PROB-FIX backfilled edge_pct_taken using a
multiplicative formula (`(1 + edge_at_pick) × actual_odds/odds_at_pick - 1`)
that disagrees with the bot's additive convention (`calibrated_prob - 1/odds`,
`daily_pipeline_v2.py:2384`). This script re-backfills using the additive
formula so the column matches what bots and the place modal compute.

Run:
    venv/bin/python scripts/backfill_real_bets_edge_formula.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview UPDATE without writing")
    args = parser.parse_args()

    # Singles only — combos don't have a single calibrated_prob.
    sql_preview = """
        SELECT rb.id, rb.actual_odds, rb.edge_pct_taken AS old_edge,
               COALESCE(sb.calibrated_prob, sb.model_probability) AS prob
        FROM real_bets rb
        JOIN simulated_bets sb ON sb.id = rb.simulated_bet_id
        WHERE rb.simulated_bet_id IS NOT NULL
          AND rb.combo_legs IS NULL
          AND rb.actual_odds > 1.0
          AND COALESCE(sb.calibrated_prob, sb.model_probability) IS NOT NULL
    """
    rows = execute_query(sql_preview)
    print(f"Rows to backfill: {len(rows)}")

    changed = []
    for r in rows:
        prob = float(r["prob"])
        odds = float(r["actual_odds"])
        new_edge = round(prob - 1.0 / odds, 5)
        old_edge = float(r["old_edge"]) if r["old_edge"] is not None else None
        if old_edge is None or abs(new_edge - old_edge) > 1e-5:
            changed.append((r["id"], old_edge, new_edge))

    print(f"Rows that will change: {len(changed)}")
    if changed[:10]:
        print("Sample (first 10):")
        for rid, old, new in changed[:10]:
            print(f"  {rid} : {old} → {new}")

    if args.dry_run:
        print("Dry run — no writes.")
        return

    sql_update = """
        UPDATE real_bets rb
        SET edge_pct_taken = ROUND(
              (COALESCE(sb.calibrated_prob, sb.model_probability)
               - 1.0 / rb.actual_odds)::numeric, 5
            )
        FROM simulated_bets sb
        WHERE sb.id = rb.simulated_bet_id
          AND rb.combo_legs IS NULL
          AND rb.actual_odds > 1.0
          AND COALESCE(sb.calibrated_prob, sb.model_probability) IS NOT NULL
    """
    n = execute_write(sql_update)
    print(f"Updated {n} rows.")


if __name__ == "__main__":
    main()
