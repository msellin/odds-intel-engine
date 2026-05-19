"""
Trigger the Coolbet automated bet placer.

Usage:
    # Dry run — see what would be placed, no actual bets:
    venv/bin/python scripts/place_coolbet_bets.py

    # Live execution — places real bets:
    venv/bin/python scripts/place_coolbet_bets.py --execute

    # Lower the edge threshold for this run (default 3%):
    venv/bin/python scripts/place_coolbet_bets.py --execute --min-edge 0.02

Setup (one-time):
    1. Add COOLBET_USER, COOLBET_PASS to .env
    2. Log in to Coolbet in Chrome → DevTools → Application → Cookies
       Copy the following cookies into .env as one line:
           COOLBET_IMPERVA_COOKIES="reese84=...; visid_incap_723517=...; nlbi_723517=...; incap_ses_1099_723517=..."
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
    p.add_argument("--execute", action="store_true",
                   help="Actually place bets (default: dry-run)")
    p.add_argument("--min-edge", type=float, default=None,
                   help="Min edge fraction, e.g. 0.03 = 3%% (overrides .env)")
    args = p.parse_args()

    if not args.execute:
        print("=== DRY RUN — no bets will be placed ===")
        print("    Add --execute to place real bets.\n")

    results = place_all_bets(execute=args.execute, min_edge=args.min_edge)

    if not results:
        print("No qualifying bets found for today.")
        return

    placed   = [r for r in results if r["outcome"] == "placed"]
    dry_run  = [r for r in results if r["outcome"] == "dry_run"]
    skipped  = [r for r in results if r["outcome"] not in ("placed", "dry_run")]

    print(f"\n{'=' * 60}")
    if args.execute:
        print(f"Results: {len(placed)} placed, {len(skipped)} skipped")
        for r in placed:
            print(f"  ✓  {r['home_team']} vs {r['away_team']} | {r['market']} {r['selection']} "
                  f"@ {r.get('live_odds', '?'):.3f}  ticket={r.get('ticket_id', '?')}")
        for r in skipped:
            print(f"  ✗  {r['home_team']} vs {r['away_team']} | {r['market']} {r['selection']} "
                  f"— {r['outcome']}")
    else:
        print(f"Dry-run: {len(dry_run)} bets would be placed")
    print("=" * 60)

    sys.exit(0 if (placed or dry_run) else 1)


if __name__ == "__main__":
    main()
