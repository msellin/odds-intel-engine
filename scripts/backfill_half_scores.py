#!/usr/bin/env python3
"""1H-HT-GOALS backfill — populate matches.ht_score_* / h2_score_* from AF.

AF `/fixtures` carries `score.halftime` + `score.fulltime` for the full history
at NO extra quota beyond the fetch itself, and `get_fixtures_batch` pulls 20
fixtures per call. This backfills finished matches that are still missing HT
goals; 2nd-half goals are stored explicitly as FT - HT.

Scheduled sweep (small daily volume — only newly-finished matches) via
`apply_backfill()`; also runnable manually:

    python3 scripts/backfill_half_scores.py --with-1h-odds   # prioritise bettable-1H matches
    python3 scripts/backfill_half_scores.py --limit 5000
    python3 scripts/backfill_half_scores.py --all
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.api_clients.api_football import get_fixtures_batch, extract_half_scores  # noqa: E402


def _targets(limit: int | None, with_1h_odds: bool) -> list[dict]:
    """Finished matches with an AF id but no HT goals yet. When with_1h_odds is
    set, restrict to matches that actually have a 1H market (fastest path to a
    bettable-1H backtest)."""
    where_1h = (
        "AND EXISTS (SELECT 1 FROM odds_snapshots o WHERE o.match_id = m.id "
        "AND o.market IN ('1x2_1h') )"
        if with_1h_odds else ""
    )
    lim = f"LIMIT {int(limit)}" if limit else ""
    return execute_query(
        f"""
        SELECT m.id::text AS id, m.api_football_id AS af_id
          FROM matches m
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL
           AND m.api_football_id IS NOT NULL
           AND m.ht_score_home IS NULL
           {where_1h}
         ORDER BY m.date DESC
         {lim}
        """
    )


def apply_backfill(limit: int | None = 400, with_1h_odds: bool = False,
                   sleep_s: float = 0.3, verbose: bool = False) -> dict:
    """Backfill HT/2H goals. Returns {'scanned','updated','no_ht','api_calls'}.
    Default limit keeps the scheduled sweep light; pass limit=None for a full run."""
    rows = _targets(limit, with_1h_odds)
    by_af = {int(r["af_id"]): r["id"] for r in rows}
    af_ids = list(by_af.keys())
    stats = {"scanned": len(af_ids), "updated": 0, "no_ht": 0, "api_calls": 0}
    for i in range(0, len(af_ids), 20):
        chunk = af_ids[i:i + 20]
        stats["api_calls"] += 1
        try:
            fixtures = get_fixtures_batch(chunk)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  batch {i//20} failed: {e}")
            continue
        for af_id, fx in fixtures.items():
            if not fx:
                continue
            ht_h, ht_a, h2_h, h2_a = extract_half_scores(fx)
            mid = by_af.get(int(af_id))
            if mid is None:
                continue
            if ht_h is None:
                stats["no_ht"] += 1
                continue
            execute_write(
                """UPDATE matches
                      SET ht_score_home = %s, ht_score_away = %s,
                          h2_score_home = %s, h2_score_away = %s
                    WHERE id = %s""",
                (ht_h, ht_a, h2_h, h2_a, mid),
            )
            stats["updated"] += 1
        if sleep_s:
            time.sleep(sleep_s)
        if verbose and (i // 20) % 20 == 0:
            print(f"  ...{i+len(chunk)}/{len(af_ids)} scanned, {stats['updated']} updated")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max matches (default: all)")
    ap.add_argument("--with-1h-odds", action="store_true", help="only matches that have a 1H market")
    ap.add_argument("--all", action="store_true", help="no limit (same as omitting --limit)")
    a = ap.parse_args()
    limit = None if a.all else a.limit
    print(f"1H-HT-GOALS backfill — limit={limit} with_1h_odds={a.with_1h_odds}")
    s = apply_backfill(limit=limit, with_1h_odds=a.with_1h_odds, verbose=True)
    print(f"\nscanned={s['scanned']} updated={s['updated']} no_ht={s['no_ht']} api_calls={s['api_calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
