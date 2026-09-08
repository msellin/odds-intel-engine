#!/usr/bin/env python3
"""Corners/cards (match_stats) backfill from AF — MARKET-DATA-AF-AUDIT #2.

The enrichment gate is already correct (skips AF-false-coverage leagues); the gap
is that finished matches in AF-coverage=TRUE leagues that fell outside the daily
enrichment window never got their stats. This backfills them.

Uses `/fixtures?ids=` (20 fixtures per call) whose payload carries `statistics`
inline — so corners AND cards AND every other stat come from ONE batched call,
parsed by the same `parse_fixture_stats` the live path uses and upserted via
`store_match_stats`. Only touches cov=TRUE finished matches with no corners row.

    python3 scripts/backfill_match_stats_af.py --limit 5000
    python3 scripts/backfill_match_stats_af.py --all
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402
from workers.api_clients.api_football import get_fixtures_batch, parse_fixture_stats  # noqa: E402
from workers.api_clients.supabase_client import store_match_stats  # noqa: E402


def _targets(limit: int | None) -> list[dict]:
    lim = f"LIMIT {int(limit)}" if limit else ""
    return execute_query(
        f"""
        SELECT m.id::text AS id, m.api_football_id AS af_id
          FROM matches m JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL
           AND m.api_football_id IS NOT NULL
           AND l.coverage_statistics_fixtures = TRUE
           AND NOT EXISTS (
               SELECT 1 FROM match_stats s
                WHERE s.match_id = m.id AND s.corners_home IS NOT NULL)
         ORDER BY m.date DESC
         {lim}
        """
    )


def apply_backfill(limit: int | None = 2000, sleep_s: float = 0.4,
                   verbose: bool = False) -> dict:
    rows = _targets(limit)
    by_af = {int(r["af_id"]): r["id"] for r in rows}
    af_ids = list(by_af.keys())
    stats = {"scanned": len(af_ids), "stored": 0, "no_stats": 0, "api_calls": 0}
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
            mid = by_af.get(int(af_id))
            if mid is None or not fx:
                continue
            parsed = parse_fixture_stats(fx.get("statistics") or [])
            if parsed.get("corners_home") is None and parsed.get("corners_away") is None:
                stats["no_stats"] += 1
                continue
            store_match_stats(mid, parsed)   # upsert: corners + cards + shots + …
            stats["stored"] += 1
        if sleep_s:
            time.sleep(sleep_s)
        if verbose and (i // 20) % 25 == 0:
            print(f"  ...{i+len(chunk)}/{len(af_ids)} scanned, {stats['stored']} stored")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    limit = None if a.all else a.limit
    print(f"match_stats (corners/cards) backfill — limit={limit}")
    s = apply_backfill(limit=limit, verbose=True)
    print(f"\nscanned={s['scanned']} stored={s['stored']} no_stats={s['no_stats']} api_calls={s['api_calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
