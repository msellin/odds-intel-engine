#!/usr/bin/env python3
"""SHADOW-CLV-MARGIN-BACKFILL (2026-09-15, OWN Phase 1a / Phase 6 engine side).

Fill `shadow_bets.closing_margin` and `shadow_bets.clv_margin_corrected` for
settled rows that already carry an own-book close (`closing_bookmaker IS NOT
NULL`, `clv IS NOT NULL`) but were settled before migration 355.

SET-BASED. The first version called `settlement.closing_book_margin()` per row
(2-3 lookups each) and managed ~4k of 53k raw rows in 15 minutes. This one
computes the closing book's margin ONCE per (fixture, book, market) from
`odds_snapshots` — each selection's latest pre-kickoff quote at that book,
`is_closing` rows preferred, the same within-book assembly the helper does —
and updates every matching shadow row in one statement. NULL stays NULL: a
market with a missing leg or a margin outside [0, 0.5] is left uncorrected,
never averaged.

Idempotent (only rows with clv_margin_corrected IS NULL are touched).

    python3 scripts/backfill_shadow_clv_margin.py --days 90          # dry-run (counts)
    python3 scripts/backfill_shadow_clv_margin.py --days 90 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402

# shadow_bets.market -> odds_snapshots.market + the full complement of sides
MKT_SQL = """
    CASE WHEN lower(sb.market) = '1x2' THEN '1x2'
         WHEN lower(sb.market) = 'btts' THEN 'btts'
         WHEN lower(sb.market) LIKE 'over_under_%%' THEN lower(sb.market)
         WHEN lower(sb.market) LIKE 'team_total_%%' THEN lower(sb.market)
         WHEN lower(sb.market) LIKE 'corners_ou_%%' THEN lower(sb.market)
    END
"""

CANDIDATES = f"""
    SELECT sb.id, sb.match_id, sb.closing_bookmaker AS bk, {MKT_SQL} AS mkt, sb.clv
      FROM shadow_bets sb
     WHERE sb.result IN ('won', 'lost')
       AND sb.closing_bookmaker IS NOT NULL
       AND sb.clv IS NOT NULL
       AND sb.clv_margin_corrected IS NULL
       AND sb.pick_time > now() - make_interval(days => %(days)s)
"""

UPDATE_SQL = f"""
WITH cand AS ({CANDIDATES}),
t AS (SELECT DISTINCT match_id, bk, mkt FROM cand WHERE mkt IS NOT NULL),
q AS (
    SELECT DISTINCT ON (o.match_id, o.bookmaker, o.market, o.selection)
           o.match_id, o.bookmaker, o.market, lower(o.selection) AS selection, o.odds::float AS odds
      FROM odds_snapshots o
      JOIN t ON t.match_id = o.match_id AND t.bk = o.bookmaker AND t.mkt = o.market
      JOIN matches m ON m.id = o.match_id
     WHERE o.timestamp <= m.date AND o.odds > 1 AND COALESCE(o.is_live, false) = false
     ORDER BY o.match_id, o.bookmaker, o.market, o.selection,
              COALESCE(o.is_closing, false) DESC, o.timestamp DESC
),
mg AS (
    SELECT match_id, bookmaker, market, SUM(1.0 / odds) - 1.0 AS m, count(*) AS n_sides
      FROM q GROUP BY 1, 2, 3
)
UPDATE shadow_bets sb
   SET closing_margin = round(mg.m::numeric, 5),
       clv_margin_corrected = round(((1.0 + sb.clv) / (1.0 + mg.m) - 1.0)::numeric, 5)
  FROM cand c JOIN mg ON mg.match_id = c.match_id AND mg.bookmaker = c.bk AND mg.market = c.mkt
 WHERE sb.id = c.id
   AND mg.m BETWEEN 0 AND 0.5
   AND mg.n_sides = CASE WHEN mg.market = '1x2' THEN 3 ELSE 2 END
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    n = execute_query(f"SELECT count(*) AS n FROM ({CANDIDATES}) c", {"days": a.days})[0]["n"]
    print(f"settled own-book shadow rows missing the margin correction ({a.days}d): {n}")
    if not a.apply:
        print("dry-run — pass --apply to write")
        return 0
    from workers.api_clients.db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '20min'")
            cur.execute(UPDATE_SQL, {"days": a.days})
            updated = cur.rowcount
            conn.commit()
    left = execute_query(f"SELECT count(*) AS n FROM ({CANDIDATES}) c", {"days": a.days})[0]["n"]
    print(f"updated {updated}; still NULL (margin undefined or leg missing): {left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
