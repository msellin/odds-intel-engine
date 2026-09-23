"""Grade consensus_anchor picks that were published before grading existed.

[[#094]] (2026-09-23). The publisher grades at claim time; the 44 picks sent on
2026-09-22/23 predate that. This rebuilds, for each ungraded row, the quotes the
publisher would have seen at `published_at` (latest per book within 6 h, the
same window as load_candidates) and applies the SAME `grade_consensus_pick`,
so a backfilled grade and a live grade are one function, not two.

    python3 scripts/backfill_consensus_grades.py          # dry run
    python3 scripts/backfill_consensus_grades.py --write
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.publish_picks_forward_test import (
    CONSENSUS_ARM, GRADE_PANEL, MARKETS, _book_probs, grade_consensus_pick,
)
from workers.api_clients.db import execute_query, execute_write


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    rows = execute_query(
        """SELECT p.id, p.match_id, p.market, p.selection, p.odds::float AS odds,
                  p.bookmaker, p.edge::float AS edge, p.published_at, l.tier
             FROM picks_forward_test p
             JOIN matches m ON m.id = p.match_id
             JOIN leagues l ON l.id = m.league_id
            WHERE p.arm = %s AND p.grade IS NULL
            ORDER BY p.published_at""", (CONSENSUS_ARM,))
    for r in rows:
        sides = MARKETS[r["market"]]
        q = execute_query(
            """SELECT DISTINCT ON (bookmaker, selection) bookmaker, selection,
                      odds::float AS odds, timestamp
                 FROM odds_snapshots
                WHERE match_id = %s AND market = %s AND is_live IS NOT TRUE
                  AND bookmaker = ANY(%s)
                  AND timestamp <= %s AND timestamp > %s - interval '6 hours'
                ORDER BY bookmaker, selection, timestamp DESC""",
            (r["match_id"], r["market"], list(GRADE_PANEL),
             r["published_at"], r["published_at"]))
        side_q: dict = {}
        for x in q:
            side_q.setdefault(x["selection"], {})[x["bookmaker"]] = (x["odds"], x["timestamp"])
        idx = sides.index(r["selection"])
        panel = {}
        for b in GRADE_PANEL:
            bp = _book_probs(sides, side_q, b)
            if bp:
                panel[b] = bp[idx]
        grade, reasons = grade_consensus_pick(r["edge"], r["odds"], r["bookmaker"],
                                              r["tier"], panel)
        print(f"{r['published_at']:%m-%d %H:%M} {r['market']:14} {r['selection']:5} "
              f"{r['odds']:5.2f} {r['bookmaker']:12} edge={r['edge']:.3f} -> {grade} {reasons}")
        if a.write:
            execute_write(
                "UPDATE picks_forward_test SET grade=%s, grade_reasons=%s "
                "WHERE id=%s AND grade IS NULL", (grade, reasons, r["id"]))
    print(f"{len(rows)} ungraded rows{' written' if a.write else ' (dry run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
