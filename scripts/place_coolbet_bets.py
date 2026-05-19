"""
Trigger the Coolbet automated bet placer.

Usage:
    # Dry run — see what would be placed, no DB writes, no Coolbet API:
    venv/bin/python scripts/place_coolbet_bets.py

    # Record mode — write to real_bets table (replaces daily manual admin work):
    venv/bin/python scripts/place_coolbet_bets.py --record

    # Execute mode — record + place actual bet at Coolbet:
    venv/bin/python scripts/place_coolbet_bets.py --execute

    # Lower the edge threshold for this run (default 3%):
    venv/bin/python scripts/place_coolbet_bets.py --record --min-edge 0.02

    All modes are idempotent — running multiple times only creates records that
    don't already exist in real_bets.

Setup (one-time):
    1. Add COOLBET_USER, COOLBET_PASS to .env
    2. Log in to Coolbet in Chrome → DevTools → Application → Cookies
       Copy cookies into .env as individual vars:
           COOLBET_COOKIE_REESE84=...
           COOLBET_COOKIE_VISID_INCAP=...
           COOLBET_COOKIE_NLBI=...
           COOLBET_COOKIE_NLBI2=...
           COOLBET_COOKIE_INCAP_SES=...
    3. Optionally set COOLBET_STAKE (default 10.0) and COOLBET_MIN_EDGE (default 0.03)
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.automation.coolbet_placer import place_all_bets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    p = argparse.ArgumentParser(description="Place qualifying Coolbet bets")
    p.add_argument("--record", action="store_true",
                   help="Write to real_bets table (replaces manual admin workflow)")
    p.add_argument("--execute", action="store_true",
                   help="Record + place bets at Coolbet API (implies --record)")
    p.add_argument("--min-edge", type=float, default=None,
                   help="Min edge fraction, e.g. 0.03 = 3%% (overrides .env)")
    args = p.parse_args()

    if args.execute:
        mode_label = "EXECUTE (record + Coolbet API)"
    elif args.record:
        mode_label = "RECORD (write real_bets, no Coolbet API)"
    else:
        mode_label = "DRY RUN (no DB writes, no Coolbet API)"
        print(f"=== DRY RUN — no DB writes, no bets placed ===")
        print("    --record  →  write to real_bets table")
        print("    --execute →  record + place at Coolbet\n")

    results = place_all_bets(
        record=args.record,
        execute=args.execute,
        min_edge=args.min_edge,
    )

    if not results:
        print("No qualifying bets found for today.")
        return

    placed   = [r for r in results if r["outcome"] == "placed"]
    dry_run  = [r for r in results if r["outcome"] == "dry_run"]
    skipped  = [r for r in results if r["outcome"] not in ("placed", "dry_run")]

    print(f"\n{'=' * 60}")
    print(f"Mode: {mode_label}")

    if args.execute:
        print(f"Results: {len(placed)} placed, {len(skipped)} skipped")
        for r in placed:
            ticket = r.get("ticket_id") or "record-only"
            print(f"  ✓  {r['home_team']} vs {r['away_team']} | {r['market']} {r['selection']} "
                  f"@ {r.get('live_odds', 0):.3f}  ticket={ticket}  real_bet={r.get('real_bet_id', '?')}")
    elif args.record:
        print(f"Recorded: {len(placed)} bets written to real_bets, {len(skipped)} skipped")
        for r in placed:
            print(f"  ✓  {r['home_team']} vs {r['away_team']} | {r['market']} {r['selection']} "
                  f"@ {r.get('live_odds', 0):.3f}  real_bet={r.get('real_bet_id', '?')}")
    else:
        print(f"Dry-run: {len(dry_run)} bets would be placed")
        for r in dry_run:
            print(f"  →  {r['home_team']} vs {r['away_team']} | {r['market']} {r['selection']} "
                  f"@ model={r.get('model_odds', 0):.3f}  coolbet={r.get('ev_odds') or '?'}")

    for r in skipped:
        print(f"  ✗  {r['home_team']} vs {r['away_team']} | {r['market']} {r['selection']} "
              f"— {r['outcome']}")

    print("=" * 60)

    sys.exit(0 if (placed or dry_run) else 1)


if __name__ == "__main__":
    main()
