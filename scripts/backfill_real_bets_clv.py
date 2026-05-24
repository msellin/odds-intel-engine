"""Backfill clv for settled real_bets that have NULL clv.

REAL-BETS-CLV-NORMALIZE (2026-05-24): settlement called `get_closing_odds`
with raw real_bets market/selection labels ('O/U', '1X2', 'o/u' + 'over 2.5')
that don't match the canonical labels in odds_snapshots ('over_under_25' +
'over'), so 279 of 359 settled real_bets had NULL clv. Also fixes the
CLV-OU-LINE bug: OU 3.5 / 1.5 bets were pulling closing odds from OU 2.5.

Run:
    venv/bin/python scripts/backfill_real_bets_clv.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write
from workers.jobs.settlement import (
    _normalize_bet_market,
    _normalize_bet_selection,
    get_closing_odds,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = execute_query("""
        SELECT id, match_id, market, selection, actual_odds, combo_legs IS NOT NULL AS is_combo
        FROM real_bets
        WHERE result != 'pending' AND clv IS NULL AND combo_legs IS NULL
    """)
    print(f"Candidates: {len(rows)}")

    updates = []
    for r in rows:
        market = _normalize_bet_market(r["market"], r["selection"])
        selection = _normalize_bet_selection(r["selection"])
        closing = get_closing_odds(str(r["match_id"]), market, selection)
        if closing and closing > 1.0 and r["actual_odds"]:
            clv = round(float(r["actual_odds"]) / float(closing) - 1, 4)
            updates.append((r["id"], clv, closing))

    print(f"Will set clv on {len(updates)} rows")
    if updates[:10]:
        print("Sample:")
        for rid, clv, closing in updates[:10]:
            print(f"  {rid} → clv={clv:+.4f} (closing={closing})")

    if args.dry_run:
        print("Dry run — no writes.")
        return

    for rid, clv, _ in updates:
        execute_write("UPDATE real_bets SET clv = %s WHERE id = %s", [clv, rid])
    print(f"Updated {len(updates)} rows.")


if __name__ == "__main__":
    main()
