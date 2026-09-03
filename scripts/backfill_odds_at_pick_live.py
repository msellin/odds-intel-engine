#!/usr/bin/env python3
"""STALE-ODDS-HISTORY-RESTATE — fill `odds_at_pick_live` from snapshot history.

`odds_at_pick` is a high-water mark, not a price that was on offer:
STALE-BEST-ODDS-2026-09-02 showed the pipeline aggregating a fixture's entire
snapshot history and taking max(), so it recorded the best price ANY book
showed at ANY time. The fix is not retroactive and `pnl` settles from
`odds_at_pick`, so the historical record — the one we publish — is inflated.

This computes, per bet, the price a person could actually have taken:

    latest quote PER BOOK at or before pick_time, then MAX across books

Restricted to ACCESSIBLE_BOOKMAKERS, because a price at a book the operator
cannot reach is not an execution price either.

WHY MAX-ACROSS-BOOKS AND NOT THE NAMED BOOK
-------------------------------------------
This is deliberately the most generous *defensible* assumption: an operator
line-shopping across every account they hold. Pricing at the single
`recommended_bookmaker` would be more punishing (that book had often moved),
but the bet could genuinely have been placed elsewhere at the better price, so
max-across-books is the honest ceiling. Same rule used for the competitor
repricing in `_competitor_reprice.py`, so our numbers and theirs stay
comparable — using a stricter rule on ourselves than on Forebet would be its
own kind of dishonesty.

MARKET VOCABULARY
-----------------
The bet tables and `odds_snapshots` do not spell markets the same way, so the
join normalises both sides (see the CTE). O/U is the case that bit:
`market='o/u', selection='under 2.5'` on one side, `market='over_under_25',
selection='under'` on the other. Before OU-LIVE-PRICE-BLIND-2026-09-03 the join
compared them raw, matched nothing, and reported success — 0 of 1,111 settled
O/U bets priced, with no error anywhere. ANALYSIS_GOTCHAS #3.

WHAT THIS DOES NOT DO
---------------------
It never writes `odds_at_pick`, `pnl` or `bankroll_after`. Those stay as the
historical record. See migration 291 for why.

    python3 scripts/backfill_odds_at_pick_live.py --dry-run
    python3 scripts/backfill_odds_at_pick_live.py --apply
    python3 scripts/backfill_odds_at_pick_live.py --apply --table shadow_bets
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

# Mirrors daily_pipeline_v2.ACCESSIBLE_BOOKMAKERS. Imported rather than copied
# so the two cannot drift — a book added there but missed here would silently
# under-price every future bet.
from workers.jobs.daily_pipeline_v2 import ACCESSIBLE_BOOKMAKERS  # noqa: E402

TABLES = ("simulated_bets", "shadow_bets")

# One statement. Doing this per-bet in Python was the first shape and it was
# ~40 minutes of round-trips; this runs server-side in one pass.
_SQL = """
WITH b AS (
    -- OU-LIVE-PRICE-BLIND-2026-09-03: the bet tables and odds_snapshots do not
    -- share a market vocabulary, so the join below has to normalise both sides
    -- onto the snapshot's spelling. Getting this wrong is silent: an unmatched
    -- market simply contributes no rows and the script reports success. That
    -- is exactly how O/U sat at 0 of 1,111 settled bets while 1x2 worked.
    --
    --   bets       market 'o/u'          selection 'under 2.5'
    --   snapshots  market 'over_under_25' selection 'under'
    --
    -- 1x2 already agreed on both sides; LOWER() additionally folds in
    -- shadow_bets' uppercase '1X2' and 'O/U' variants, which had the same
    -- problem for the same reason.
    SELECT t.id, t.match_id, t.pick_time,
           CASE
             -- 'o/u' + 'under 2.5' -> 'over_under_25'. Only when the selection
             -- actually carries a line: a bare 'over' cannot be resolved to one,
             -- and inventing a default would silently price it off the wrong
             -- ladder rung. Those rows stay NULL, which is the honest answer.
             WHEN LOWER(t.market) IN ('o/u', 'ou') AND t.selection ~ '[0-9]'
               THEN 'over_under_' || REPLACE(
                      REGEXP_REPLACE(t.selection, '^[^0-9]*', ''), '.', '')
             ELSE LOWER(t.market)
           END AS market,
           -- Strip any trailing line off the selection ('under 2.5' -> 'under').
           -- Applies to markets already spelled 'over_under_NN' too, where the
           -- market is right but the selection still carries the line.
           CASE
             WHEN LOWER(t.market) IN ('o/u', 'ou')
               OR LOWER(t.market) LIKE 'over_under%%'
               THEN LOWER(REGEXP_REPLACE(t.selection, '[[:space:]]*[0-9.]+[[:space:]]*$', ''))
             ELSE LOWER(t.selection)
           END AS selection
      FROM {table} t
     WHERE t.pick_time IS NOT NULL
       AND t.odds_at_pick_live IS NULL
       {only_settled}
),
live AS (
    SELECT DISTINCT ON (b.id, o.bookmaker) b.id, o.odds
      FROM b
      JOIN odds_snapshots o
        ON  o.match_id  = b.match_id
       AND  LOWER(o.market)    = b.market
       AND  LOWER(o.selection) = b.selection
       AND  o.is_closing = false
       AND  o.odds > 1
       AND  o.bookmaker = ANY(%(books)s)
       -- the quote each book was showing when the bet was raised
       AND  o.timestamp <= b.pick_time
     ORDER BY b.id, o.bookmaker, o.timestamp DESC
),
best AS (
    SELECT id, MAX(odds) AS live_best FROM live GROUP BY id
)
UPDATE {table} t
   SET odds_at_pick_live = best.live_best
  FROM best
 WHERE t.id = best.id
"""


def _counts(cur, table: str) -> dict:
    cur.execute(f"""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE result IN ('won','lost')) AS settled,
               COUNT(odds_at_pick_live) AS filled,
               COUNT(*) FILTER (WHERE result IN ('won','lost')
                                 AND odds_at_pick_live IS NOT NULL) AS settled_filled
          FROM {table}
    """)
    # get_conn() hands back a plain tuple cursor, not a RealDictCursor —
    # dict(row) raises here rather than returning columns.
    total, settled, filled, settled_filled = cur.fetchone()
    return {"total": total, "settled": settled, "filled": filled,
            "settled_filled": settled_filled}


def _roi(cur, table: str) -> list[tuple]:
    """Recorded vs live-priced ROI on the rows where both exist."""
    cur.execute(f"""
        SELECT COUNT(*),
               ROUND((100.0 * SUM(CASE WHEN result='won'
                                       THEN (odds_at_pick - 1) * 10 ELSE -10 END)
                      / NULLIF(10 * COUNT(*), 0))::numeric, 2),
               ROUND((100.0 * SUM(CASE WHEN result='won'
                                       THEN (odds_at_pick_live - 1) * 10 ELSE -10 END)
                      / NULLIF(10 * COUNT(*), 0))::numeric, 2),
               COUNT(*) FILTER (WHERE odds_at_pick > odds_at_pick_live + 0.001)
          FROM {table}
         WHERE result IN ('won','lost') AND odds_at_pick_live IS NOT NULL
    """)
    return cur.fetchone()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="explicit no-op; the default already rolls back")
    ap.add_argument("--table", choices=TABLES, help="one table (default: both)")
    ap.add_argument("--all-rows", action="store_true",
                    help="include unsettled bets (default: settled only)")
    args = ap.parse_args()

    books = sorted(ACCESSIBLE_BOOKMAKERS)
    tables = [args.table] if args.table else list(TABLES)
    console.print(f"\n[bold]odds_at_pick_live backfill[/bold] — books: {', '.join(books)}")
    console.print(f"[dim]{'APPLY' if args.apply else 'DRY RUN'} · tables: "
                  f"{', '.join(tables)}[/dim]\n")

    from workers.api_clients.db import get_conn

    for table in tables:
        with get_conn() as conn, conn.cursor() as cur:
            before = _counts(cur, table)
            sql = _SQL.format(
                table=table,
                only_settled="" if args.all_rows else "AND t.result IN ('won','lost')",
            )
            cur.execute(sql, {"books": books})
            n = cur.rowcount
            if args.apply:
                conn.commit()
            else:
                conn.rollback()
            # Re-read on a fresh connection so a rolled-back dry run reports
            # the real state rather than its own uncommitted view.
            pass

        with get_conn() as conn2, conn2.cursor() as cur2:
            after = _counts(cur2, table)
            roi = _roi(cur2, table) if after["settled_filled"] else None

        t = Table(title=table, show_header=True, header_style="bold")
        for c in ("rows", "settled", "priced before", "priced after", "would update"):
            t.add_column(c, justify="right")
        t.add_row(str(before["total"]), str(before["settled"]),
                  str(before["filled"]), str(after["filled"]), str(n))
        console.print(t)

        if roi and roi[0]:
            cnt, rec, live, above = roi
            console.print(f"  settled + priced: [bold]{cnt}[/bold]   "
                          f"recorded ROI [bold]{rec:+.2f}%[/bold]   "
                          f"live-priced ROI [bold]{live:+.2f}%[/bold]   "
                          f"inflation {float(rec) - float(live):+.2f}pp")
            console.print(f"  [dim]{above} of {cnt} ({100.0 * above / cnt:.1f}%) "
                          f"record odds above anything live at pick time[/dim]")
        cov = (100.0 * after["settled_filled"] / after["settled"]) if after["settled"] else 0
        console.print(f"  [dim]coverage on settled rows: {cov:.1f}% — publish this "
                      f"with any restated figure (ANALYSIS_GOTCHAS #29)[/dim]\n")

    if not args.apply:
        console.print("[yellow]Dry run — nothing written. Re-run with --apply.[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
