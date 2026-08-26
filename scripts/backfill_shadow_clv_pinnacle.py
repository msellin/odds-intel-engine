"""SHADOW-CLV-BOOKMAKER-FIX-2026-08-26 — backfill shadow_bets.clv_pinnacle.

Bulk, not row-by-row: 104k settled shadow rows sit on only 4.6k matches (the
30-min refresh writes ~48 rows per pick per day), so the Pinnacle closing prices
are loaded once per match/market and reused across every row that shares them.

Usage:
    python3 scripts/backfill_shadow_clv_pinnacle.py --dry-run
    python3 scripts/backfill_shadow_clv_pinnacle.py
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _market_complement_selections,
    _normalize_bet_market,
    _normalize_bet_selection,
)
from workers.model.devig import devig  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=2000)
    args = ap.parse_args()

    bets = execute_query(
        """
        SELECT id, match_id, market, selection, odds_at_pick
          FROM shadow_bets
         WHERE result IN ('won','lost') AND clv_pinnacle IS NULL
        """,
        [],
    )
    print(f"{len(bets)} settled shadow rows without clv_pinnacle")
    if not bets:
        return 0

    match_ids = sorted({str(b["match_id"]) for b in bets})
    print(f"spanning {len(match_ids)} matches — loading Pinnacle closes")

    closes: dict = {}
    for i in range(0, len(match_ids), 500):
        chunk = match_ids[i : i + 500]
        rows = execute_query(
            """
            SELECT DISTINCT ON (o.match_id, o.market, o.selection)
                   o.match_id, o.market, o.selection, o.odds
              FROM odds_snapshots o
              JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = 'Pinnacle'
               AND o.timestamp <= m.date
             ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
            """,
            [chunk],
        )
        for r in rows:
            closes[(str(r["match_id"]), r["market"], r["selection"])] = float(r["odds"])
    print(f"loaded {len(closes)} Pinnacle closing prices")

    # Cache the de-vig per (match, market) — one solve serves every row on it.
    prob_cache: dict = {}
    updates: list[tuple] = []
    skipped = defaultdict(int)

    for b in bets:
        mid = str(b["match_id"])
        mkt = _normalize_bet_market(b["market"], b["selection"])
        sel = _normalize_bet_selection(b["selection"])
        sides = _market_complement_selections(mkt, sel)
        if not sides or sel not in sides:
            skipped["market_not_deviggable"] += 1
            continue
        key = (mid, mkt)
        if key not in prob_cache:
            odds = [closes.get((mid, mkt, s)) for s in sides]
            if any(o is None or o <= 1.0 for o in odds):
                prob_cache[key] = None
            else:
                prob_cache[key] = devig(odds)
        probs = prob_cache[key]
        if probs is None:
            skipped["no_full_pinnacle_close"] += 1
            continue
        p = probs[sides.index(sel)]
        if not (0.0 < p < 1.0):
            skipped["bad_prob"] += 1
            continue
        updates.append((round(float(b["odds_at_pick"]) * p - 1.0, 4), b["id"]))

    print(f"computable: {len(updates)}   skipped: {dict(skipped)}")
    if args.dry_run:
        for v, i in updates[:10]:
            print(f"  {i} -> clv_pinnacle {v:+.4f}")
        return 0

    done = 0
    for i in range(0, len(updates), args.batch):
        chunk = updates[i : i + args.batch]
        execute_write(
            """
            UPDATE shadow_bets AS s SET clv_pinnacle = v.clv
              FROM (SELECT unnest(%s::double precision[]) AS clv,
                           unnest(%s::uuid[]) AS id) AS v
             WHERE s.id = v.id
            """,
            [[c for c, _ in chunk], [str(i2) for _, i2 in chunk]],
        )
        done += len(chunk)
        print(f"  updated {done}/{len(updates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
