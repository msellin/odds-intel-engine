#!/usr/bin/env python3
"""[[#179]] Fill shadow_bets.odds_own_pick_time for picks made before #162 W2.1 (2026-09-25 15:22:24 UTC).

The stored odds_at_pick / odds_at_pick_live of those rows were rewritten by later sweeps (DO UPDATE), so the
OWN price is re-derived from odds_snapshots: the leg's own book (recommended_bookmaker), same match / market /
selection, pre-match, latest at or before pick_time and no older than 180 min. Never overwrites odds_at_pick*.
Idempotent: only rows with own_price_checked_at IS NULL; every visited row gets a check time, so a NULL price
afterwards means "no snapshot survived" (7-day retention), not "not processed".

    python3 scripts/backfill_own_pick_time_price.py [--batch 2000] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CUTOFF = "2026-09-25 15:22:24+00"
FRESH_MIN = 180

_BATCH = """
WITH todo AS (
  SELECT id FROM shadow_bets
   WHERE pick_time < %(cutoff)s AND inplay_minute IS NULL AND own_price_checked_at IS NULL
   ORDER BY id LIMIT %(batch)s)
UPDATE shadow_bets x
   SET odds_own_pick_time = (
         SELECT o.odds FROM odds_snapshots o
          WHERE o.match_id = x.match_id AND o.market = x.market AND o.selection = x.selection
            AND o.bookmaker = x.recommended_bookmaker AND o.is_live IS NOT TRUE
            AND o."timestamp" <= x.pick_time
            AND o."timestamp" >= x.pick_time - make_interval(mins => %(fresh)s)
          ORDER BY o."timestamp" DESC LIMIT 1),
       own_price_checked_at = now()
  FROM todo WHERE x.id = todo.id"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from workers.api_clients.db import execute_query, execute_write
    left = execute_query("SELECT count(*) AS n FROM shadow_bets WHERE pick_time < %s AND inplay_minute IS NULL "
                         "AND own_price_checked_at IS NULL", (CUTOFF,))[0]["n"]
    print(f"{left} pre-W2.1 shadow legs to check")
    if a.dry_run:
        return
    done = 0
    while True:
        n = execute_write(_BATCH, {"cutoff": CUTOFF, "batch": a.batch, "fresh": FRESH_MIN}) or 0
        if not n:
            break
        done += n
        print(f"  checked {done}/{left}")
    got = execute_query("SELECT count(*) FILTER (WHERE odds_own_pick_time > 1) AS priced, count(*) AS checked "
                        "FROM shadow_bets WHERE own_price_checked_at IS NOT NULL", [])[0]
    print(f"done: {got['priced']} of {got['checked']} checked legs have a pick-time own price")


if __name__ == "__main__":
    main()
