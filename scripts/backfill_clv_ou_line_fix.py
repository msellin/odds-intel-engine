"""Recompute clv for sim_bets / shadow_bets affected by the OU-line bug.

SIM-BETS-CLV-OU-LINE-BACKFILL (2026-05-24): the previous
`_normalize_bet_market` hardcoded "over_under_25" for any "o/u" bet — so
sim_bets / shadow_bets on OU 1.5 / 3.5 lines were pulling closing odds
from the OU 2.5 line, producing +60-76% bogus CLV values. This script
recomputes clv (and closing_odds) for every affected row using the now
line-aware normalizer.

Run:
    venv/bin/python scripts/backfill_clv_ou_line_fix.py [--dry-run] [--table simulated_bets|shadow_bets|both]
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


def backfill(table: str, dry_run: bool) -> tuple[int, int]:
    """Recompute clv for OU bets on non-2.5 lines in the given table.
    Returns (candidates, updated)."""
    rows = execute_query(f"""
        SELECT id, match_id, market, selection, odds_at_pick, clv AS old_clv
        FROM {table}
        WHERE market = 'o/u'
          AND result != 'pending'
          AND selection NOT IN ('over 2.5', 'under 2.5')
    """)
    print(f"\n[{table}] candidates: {len(rows)}")

    updates = []
    for r in rows:
        market = _normalize_bet_market(r["market"], r["selection"])
        selection = _normalize_bet_selection(r["selection"])
        closing = get_closing_odds(str(r["match_id"]), market, selection)
        if closing and closing > 1.0 and r["odds_at_pick"]:
            new_clv = round(float(r["odds_at_pick"]) / float(closing) - 1, 4)
            old_clv = float(r["old_clv"]) if r["old_clv"] is not None else None
            if old_clv is None or abs(new_clv - old_clv) > 1e-4:
                updates.append((r["id"], r["selection"], old_clv, new_clv, float(closing)))
        else:
            # New formula finds no matching snapshot (e.g. line doesn't exist on this match).
            # Clear the (wrong) old clv so we don't keep showing a bogus value.
            if r["old_clv"] is not None:
                updates.append((r["id"], r["selection"], float(r["old_clv"]), None, None))

    print(f"[{table}] changing: {len(updates)}")
    for rid, sel, old, new, closing in updates[:10]:
        old_str = f"{old:+.4f}" if old is not None else "NULL"
        new_str = f"{new:+.4f}" if new is not None else "NULL"
        print(f"  {sel:>12} {old_str} → {new_str} (closing={closing})")

    if dry_run:
        return len(rows), 0

    n = 0
    for rid, _sel, _old, new_clv, _closing in updates:
        execute_write(
            f"UPDATE {table} SET clv = %s WHERE id = %s",
            [new_clv, rid],
        )
        n += 1
    return len(rows), n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--table", default="both",
                        choices=["simulated_bets", "shadow_bets", "both"])
    args = parser.parse_args()

    tables = (["simulated_bets", "shadow_bets"]
              if args.table == "both" else [args.table])
    grand = 0
    for t in tables:
        _, n = backfill(t, args.dry_run)
        grand += n
    print(f"\nTotal updated: {grand}" + (" (dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
