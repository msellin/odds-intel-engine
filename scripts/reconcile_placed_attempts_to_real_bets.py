#!/usr/bin/env python3
"""REAL-BETS-ATTEMPTS-RECONCILED (2026-09-15, OWN Phase 0.D).

Every CONFIRMED real placement must have a `real_bets` row. The UI placer writes
the row only AFTER the balance delta confirms the stake; if that write raises,
the code logs "placed but could not write real_bets" and records the attempt as
`outcome='placed'` with `real_bet_id IS NULL`. Money left the account; the
ledger does not know. Found once (2026-09-13 16:22, bot_coolbet_ou_model_v1,
U2.5, EUR 10) by the quant review of the OWN audit.

This script is the repair and the guard's counterpart: for every
`coolbet_placement_attempts` row with `outcome='placed' AND execute_mode` and no
linked `real_bets` row, insert the ledger row FROM THE ATTEMPT (the attempt is
the evidence — odds read from the slip, stake applied, bot, pick id) with
`placed_real=TRUE`, stamp `placed_at` to the attempt time so the ledger's
history is right, and link the attempt back. Idempotent: re-running finds
nothing to do. Smoke `REAL-BETS-ATTEMPTS-RECONCILED` asserts the count is 0.

    python3 scripts/reconcile_placed_attempts_to_real_bets.py            # dry-run
    python3 scripts/reconcile_placed_attempts_to_real_bets.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query, execute_write  # noqa: E402


ORPHANS_SQL = """
SELECT a.id, a.attempted_at, a.bot_id, a.bot_name, a.shadow_bet_id,
       a.simulated_bet_id, a.match_id, a.market, a.selection,
       a.captured_odds, a.coolbet_odds, a.stake_applied, a.stake_requested,
       a.home_team, a.away_team, a.kickoff
  FROM coolbet_placement_attempts a
 WHERE a.outcome = 'placed'
   AND a.execute_mode = TRUE
   AND a.real_bet_id IS NULL
   AND NOT EXISTS (
         SELECT 1 FROM real_bets rb
          WHERE rb.match_id = a.match_id
            AND lower(rb.market) = lower(a.market)
            AND lower(rb.selection) = lower(a.selection)
            AND rb.bookmaker = 'Coolbet'
            AND rb.placed_at BETWEEN a.attempted_at - interval '10 minutes'
                                 AND a.attempted_at + interval '10 minutes')
 ORDER BY a.attempted_at
"""


def find_orphans() -> list[dict]:
    return execute_query(ORPHANS_SQL) or []


def repair(orphan: dict) -> str | None:
    from workers.api_clients.supabase_client import store_real_bet
    odds = float(orphan["coolbet_odds"] or orphan["captured_odds"])
    stake = float(orphan["stake_applied"] or orphan["stake_requested"] or 0)
    if stake <= 0:
        raise ValueError(f"attempt {orphan['id']} has no stake — refusing to fabricate one")
    rb_id = store_real_bet(
        match_id=str(orphan["match_id"]), market=orphan["market"],
        selection=orphan["selection"], bookmaker="Coolbet",
        captured_odds=float(orphan["captured_odds"]) if orphan.get("captured_odds") else odds,
        actual_odds=odds, stake=stake,
        bot_id=str(orphan["bot_id"]) if orphan.get("bot_id") else None,
        simulated_bet_id=(str(orphan["shadow_bet_id"]) if orphan.get("shadow_bet_id")
                          else (str(orphan["simulated_bet_id"]) if orphan.get("simulated_bet_id") else None)),
        notes=f"reconciled-from-attempt {orphan['id']} (REAL-BETS-ATTEMPTS-RECONCILED 2026-09-15)",
        placed_real=True,
    )
    if not rb_id:
        raise RuntimeError(f"store_real_bet returned no id for attempt {orphan['id']}")
    # The ledger's history must show WHEN the money moved, not when the repair ran.
    execute_write("UPDATE real_bets SET placed_at = %s WHERE id = %s",
                  (orphan["attempted_at"], rb_id))
    execute_write("UPDATE coolbet_placement_attempts SET real_bet_id = %s WHERE id = %s",
                  (rb_id, orphan["id"]))
    return rb_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the missing rows")
    a = ap.parse_args()
    orphans = find_orphans()
    print(f"confirmed placements without a real_bets row: {len(orphans)}")
    for o in orphans:
        print(f"  {o['attempted_at']:%Y-%m-%d %H:%M}  {o['bot_name']}  "
              f"{o['home_team']} v {o['away_team']}  {o['market']}/{o['selection']} "
              f"@ {o['coolbet_odds'] or o['captured_odds']}  EUR {o['stake_applied']}")
    if not orphans:
        return 0
    if not a.apply:
        print("dry-run — pass --apply to insert")
        return 1
    for o in orphans:
        rb = repair(o)
        print(f"  -> real_bets {rb}")
    remaining = find_orphans()
    print(f"remaining orphans: {len(remaining)}")
    return 0 if not remaining else 1


if __name__ == "__main__":
    sys.exit(main())
