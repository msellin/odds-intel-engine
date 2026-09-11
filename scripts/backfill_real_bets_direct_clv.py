"""DIRECT-BOOK-CLV-2026-09-11 — recompute closing-line fields on settled real_bets.

Rewrites clv / closing_odds / closing_bookmaker / closing_minutes_before_ko /
clv_pinnacle on every settled real bet using `settlement.real_bet_closing()`,
the exact helper the live settle loop calls — so backfilled and freshly-settled
rows cannot disagree.

Why it overwrites `clv` rather than filling NULLs: the old value was measured
against an arbitrary API-Football book's close (see migration 332), so it is
not comparable with the new definition and must not survive alongside it.

Idempotent — safe to re-run (e.g. after NEAR-KICKOFF-CAPTURE starts writing
fresher direct-book closes).

    python3 scripts/backfill_real_bets_direct_clv.py --dry-run
    python3 scripts/backfill_real_bets_direct_clv.py
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.jobs.settlement import real_bet_closing  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    rows = execute_query(
        """SELECT rb.id, rb.match_id, rb.market, rb.selection, rb.bookmaker,
                  rb.actual_odds AS odds_at_pick, rb.clv AS old_clv
             FROM real_bets rb
            WHERE rb.combo_legs IS NULL
              AND rb.result IN ('won','lost','void','half_won','half_lost')
              AND rb.actual_odds IS NOT NULL
            ORDER BY rb.placed_at""",
        [],
    ) or []
    n_own = n_pin = changed = 0
    for r in rows:
        c = real_bet_closing(r)
        n_own += c["clv"] is not None
        n_pin += c["clv_pinnacle"] is not None
        old = float(r["old_clv"]) if r["old_clv"] is not None else None
        changed += old != c["clv"]
        if args.dry_run:
            continue
        execute_write(
            """UPDATE real_bets SET clv=%s, closing_odds=%s, closing_bookmaker=%s,
                                    closing_minutes_before_ko=%s, clv_pinnacle=%s
                WHERE id=%s""",
            [c["clv"], c["closing_odds"], c["closing_bookmaker"],
             c["closing_minutes_before_ko"], c["clv_pinnacle"], r["id"]],
        )
    mode = "DRY-RUN" if args.dry_run else "written"
    print(f"{mode}: {len(rows)} settled real bets · own-book close {n_own} · "
          f"pinnacle {n_pin} · clv changed {changed}")


if __name__ == "__main__":
    main()
