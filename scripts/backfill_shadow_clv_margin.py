#!/usr/bin/env python3
"""SHADOW-CLV-MARGIN-BACKFILL (2026-09-15, OWN Phase 1a / Phase 6 engine side).

Fill `shadow_bets.closing_margin` and `shadow_bets.clv_margin_corrected` for
settled rows that already carry an own-book close (`closing_bookmaker IS NOT
NULL`, `clv IS NOT NULL`) but were settled before migration 355. Uses exactly
the settlement helper (`settlement.closing_book_margin`) so the backfilled number
cannot differ from the number settlement writes going forward.

NULL stays NULL: when the closing book's full complement of selections is not
available the margin is undefined and the row keeps clv_margin_corrected NULL,
never an average (the doctrine `real_bets` and `picks_forward_test` already
follow).

    python3 scripts/backfill_shadow_clv_margin.py --days 90          # dry-run
    python3 scripts/backfill_shadow_clv_margin.py --days 90 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402


def candidates(days: int) -> list[dict]:
    return execute_query(
        """SELECT id, match_id, market, selection, closing_bookmaker, clv
             FROM shadow_bets
            WHERE result IN ('won', 'lost')
              AND closing_bookmaker IS NOT NULL
              AND clv IS NOT NULL
              AND clv_margin_corrected IS NULL
              AND closing_margin IS NULL
              AND pick_time > now() - make_interval(days => %s)
            ORDER BY pick_time""",
        [days],
    ) or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    from workers.jobs.settlement import (closing_book_margin, _normalize_bet_market)

    rows = candidates(a.days)
    print(f"settled own-book rows missing margin correction ({a.days}d): {len(rows)}")
    done = undefined = 0
    for r in rows:
        market = _normalize_bet_market(r["market"], r["selection"])
        m = closing_book_margin(str(r["match_id"]), market, r["closing_bookmaker"])
        if m is None:
            undefined += 1
            continue
        mc = (1.0 + float(r["clv"])) / (1.0 + m) - 1.0
        if a.apply:
            execute_write(
                "UPDATE shadow_bets SET closing_margin = %s, clv_margin_corrected = %s WHERE id = %s",
                (round(m, 5), round(mc, 5), r["id"]),
            )
        done += 1
    print(f"{'wrote' if a.apply else 'would write'} {done}; margin undefined (left NULL) {undefined}")
    if not a.apply and rows:
        print("dry-run — pass --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
