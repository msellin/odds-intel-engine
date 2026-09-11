"""DRIFT-FEATURE backfill — populate
match_feature_vectors.pinnacle_drift_{home,draw,away} from the
is_opening + is_closing Pinnacle 1X2 rows landed by CSV-FULL-EXTRACT.

The signal: per match, for each of (home, draw, away):

    drift = (1 / pinnacle_close_odds) − (1 / pinnacle_open_odds)

Positive → sharp money pushed Pinnacle's implied probability up for that
selection (i.e., market moved toward that selection).

The backtest (`scripts/backtest_csv_full_extract.py`) showed an 8.76 pp
home win-rate spread between top and bottom quintile of `drift_home` on
~8,850 matches. This script materialises the feature so the next
Sunday weekly retrain can pick it up.

Run:
  python3 scripts/backfill_pinnacle_drift.py --dry-run            # count only
  python3 scripts/backfill_pinnacle_drift.py                       # actually write
  python3 scripts/backfill_pinnacle_drift.py --since 2024-01-01    # subset

Requires migration 179 applied first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, get_pool  # noqa: E402


def compute_drifts(since: str | None) -> list[tuple[str, float, float, float]]:
    """Return list of (match_id, drift_home, drift_draw, drift_away) for every
    match where Pinnacle has both is_opening and is_closing rows on each of the
    three 1X2 selections. Drift = close_implied − open_implied."""
    where = "AND m.date >= %s::timestamptz" if since else ""
    params: list = [since] if since else []
    rows = execute_query(
        f"""
        -- DRIFT-FEATURE-FANOUT-2026-09-11. This used to select ALL is_opening
        -- and ALL is_closing rows and join them, which is wrong on two counts
        -- because a price series routinely carries more than one of each: over
        -- all time there are 186,257 open/close pairs across only ~22,300
        -- matches, i.e. ~2.8 pairs per (match, selection) rather than 1.
        --
        --   * the join FANS OUT (n_open x n_close rows per selection), so
        --     `HAVING COUNT(*) ... = 3` -- which counted joined ROWS, not
        --     distinct selections -- was satisfied by a single selection with
        --     three pairs, leaving draw/away NULL and the row discarded by the
        --     Python None-guard below;
        --   * even when it survived, `MAX(drift)` picked the most POSITIVE of
        --     the fanned-out combinations rather than the real open-to-close
        --     move.
        --
        -- Measured effect: the old query returned 1,861 matches since
        -- 2026-06-01 where 11,716 have all three selections anchored at both
        -- ends -- it was silently dropping 84 per cent of what it could
        -- compute. DISTINCT ON picks the EARLIEST opening and the LATEST
        -- closing per (match, selection), which is what "open to close"
        -- means, and the HAVING now counts distinct selections.
        WITH open_rows AS (
          SELECT DISTINCT ON (match_id, selection)
                 match_id, selection, odds
          FROM odds_snapshots
          WHERE bookmaker = 'Pinnacle' AND market = '1x2'
            AND is_opening = true AND NOT COALESCE(is_live, false)
          ORDER BY match_id, selection, timestamp ASC
        ),
        close_rows AS (
          SELECT DISTINCT ON (match_id, selection)
                 match_id, selection, odds
          FROM odds_snapshots
          WHERE bookmaker = 'Pinnacle' AND market = '1x2'
            AND is_closing = true AND NOT COALESCE(is_live, false)
          ORDER BY match_id, selection, timestamp DESC
        ),
        joined AS (
          SELECT o.match_id, o.selection,
                 (1.0/c.odds) - (1.0/o.odds) AS drift
          FROM open_rows o
          JOIN close_rows c USING (match_id, selection)
          WHERE o.odds > 1.0 AND c.odds > 1.0
        )
        SELECT j.match_id::text AS match_id,
               MAX(CASE WHEN j.selection = 'home' THEN j.drift END) AS drift_home,
               MAX(CASE WHEN j.selection = 'draw' THEN j.drift END) AS drift_draw,
               MAX(CASE WHEN j.selection = 'away' THEN j.drift END) AS drift_away
        FROM joined j
        JOIN matches m ON m.id = j.match_id
        WHERE 1=1 {where}
        GROUP BY j.match_id
        HAVING COUNT(DISTINCT j.selection) FILTER (
                   WHERE j.selection IN ('home','draw','away')) = 3
        """,
        params,
    )
    return [
        (r["match_id"], float(r["drift_home"]), float(r["drift_draw"]), float(r["drift_away"]))
        for r in rows
        if r["drift_home"] is not None and r["drift_draw"] is not None and r["drift_away"] is not None
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="YYYY-MM-DD lower bound on match.date")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Computing Pinnacle drift (since={args.since or 'all-time'})...")
    drifts = compute_drifts(args.since)
    print(f"  {len(drifts):,} matches with paired open+close Pinnacle 1X2")
    if not drifts:
        return

    print(f"  Sample: {drifts[:3]}")

    if args.dry_run:
        print("  (dry-run — no writes)")
        return

    # Bulk UPSERT via UPDATE FROM VALUES — single round-trip.
    from psycopg2.extras import execute_values
    p = get_pool()
    conn = p.getconn()
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                UPDATE match_feature_vectors
                   SET pinnacle_drift_home = data.dh,
                       pinnacle_drift_draw = data.dd,
                       pinnacle_drift_away = data.da
                FROM (VALUES %s) AS data(mid, dh, dd, da)
                WHERE match_feature_vectors.match_id = data.mid::uuid
                """,
                drifts,
                page_size=2000,
            )
            updated = cur.rowcount
            conn.commit()
        print(f"  UPDATEd {updated:,} match_feature_vectors rows")
        skipped = len(drifts) - updated
        if skipped > 0:
            print(f"  ({skipped:,} matches in odds_snapshots have no MFV row — skipped)")
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


if __name__ == "__main__":
    main()
