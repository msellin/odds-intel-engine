"""
OddsIntel — Backfill clv_pinnacle on settled bets (PIN-5 backfill)

Computes clv_pinnacle = (odds_at_pick / pinnacle_closing_odds) - 1 for all
settled simulated_bets that currently have clv_pinnacle = NULL.

Pinnacle closing odds come from odds_snapshots WHERE bookmaker = 'Pinnacle',
preferring is_closing = TRUE snapshots, falling back to the latest snapshot.

Safe to re-run: only updates rows where clv_pinnacle IS NULL.

Run:
    python3 scripts/backfill_clv_pinnacle.py
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query, execute_write


def _normalize_market(market: str) -> str:
    m = market.strip().lower()
    if m in ("1x2", "1×2"):
        return "1x2"
    if m in ("o/u", "ou", "over/under"):
        return "over_under_25"
    return m


def _normalize_selection(selection: str) -> str:
    s = selection.strip().lower()
    if s in ("home", "h"):
        return "home"
    if s in ("away", "a"):
        return "away"
    if s in ("draw", "d", "x"):
        return "draw"
    if s.startswith("over"):
        return "over"
    if s.startswith("under"):
        return "under"
    return s


def run():
    print("\n=== Backfill clv_pinnacle on settled bets ===\n")

    # All settled bets missing clv_pinnacle
    bets = execute_query(
        """SELECT id, match_id, market, selection, odds_at_pick
           FROM simulated_bets
           WHERE result != 'pending'
             AND clv_pinnacle IS NULL
           ORDER BY created_at""",
        [],
    )
    if not bets:
        print("Nothing to backfill — all settled bets already have clv_pinnacle.")
        return

    print(f"Found {len(bets)} settled bets without clv_pinnacle.")

    # Bulk-load all Pinnacle closing snapshots for these matches in one query
    match_ids = list({b["match_id"] for b in bets})

    # Prefer is_closing=TRUE, fall back to latest per (match_id, market, selection)
    snap_rows = execute_query(
        """SELECT DISTINCT ON (match_id, market, selection)
               match_id, market, selection, odds
           FROM odds_snapshots
           WHERE match_id = ANY(%s::uuid[])
             AND bookmaker = 'Pinnacle'
             AND odds > 1.0
           ORDER BY match_id, market, selection,
                    is_closing DESC, timestamp DESC""",
        (match_ids,),
    )

    # Index by (match_id, market, selection)
    snap_idx: dict[tuple, float] = {}
    for r in snap_rows:
        key = (str(r["match_id"]), r["market"], r["selection"])
        snap_idx[key] = float(r["odds"])

    updated = 0
    missing = 0

    for bet in bets:
        mkt = _normalize_market(bet["market"])
        sel = _normalize_selection(bet["selection"])
        key = (str(bet["match_id"]), mkt, sel)
        pin_close = snap_idx.get(key)

        if pin_close is None or pin_close <= 1.0:
            missing += 1
            continue

        odds_at_pick = float(bet["odds_at_pick"])
        clv_pin = round((odds_at_pick / pin_close) - 1, 4)

        execute_write(
            "UPDATE simulated_bets SET clv_pinnacle = %s WHERE id = %s",
            [clv_pin, bet["id"]],
        )
        updated += 1

    print(f"  Updated:       {updated} bets with clv_pinnacle")
    print(f"  No Pinnacle:   {missing} bets (no Pinnacle snapshot in odds_snapshots)")
    print("\n=== Done ===\n")


if __name__ == "__main__":
    run()
