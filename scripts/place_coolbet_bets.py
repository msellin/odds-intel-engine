"""
Trigger the Coolbet automated bet placer.

Usage:
    # Dry run — see what would be placed, no DB writes, no Coolbet API:
    venv/bin/python scripts/place_coolbet_bets.py

    # Record mode — write to real_bets table (replaces daily manual admin work):
    venv/bin/python scripts/place_coolbet_bets.py --record

    # Record pre-match AND inplay in one run (inplay auto-runs from inplay_bot,
    # this is for manual catch-up):
    venv/bin/python scripts/place_coolbet_bets.py --record --include-inplay

    # Inplay only:
    venv/bin/python scripts/place_coolbet_bets.py --record --inplay-only

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

from workers.automation.coolbet_placer import place_all_bets, place_all_inplay_bets

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
    p.add_argument("--include-inplay", action="store_true",
                   help="Also run the inplay placer (kicked-off matches). "
                        "Default is pre-match only. Inplay normally runs auto "
                        "from inplay_bot.py — use this for manual catch-up.")
    p.add_argument("--inplay-only", action="store_true",
                   help="Skip pre-match; run only the inplay placer.")
    p.add_argument("--inplay-window", type=int, default=30,
                   help="Minutes since pick_time to consider for inplay catch-up "
                        "(default 30, vs the 5-min default in the auto loop).")
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

    results: list[dict] = []
    if not args.inplay_only:
        results.extend(place_all_bets(
            record=args.record,
            execute=args.execute,
            min_edge=args.min_edge,
        ))
    if args.include_inplay or args.inplay_only:
        print(f"\n--- Inplay pass (window={args.inplay_window}min) ---")
        results.extend(place_all_inplay_bets(
            record=args.record,
            execute=args.execute,
            window_minutes=args.inplay_window,
        ))

    if not results:
        print("No qualifying bets found for today.")
        return

    placed   = [r for r in results if r["outcome"] == "placed"]
    dry_run  = [r for r in results if r["outcome"] == "dry_run"]
    skipped  = [r for r in results if r["outcome"] not in ("placed", "dry_run")]

    print(f"\n{'=' * 60}")
    print(f"Mode: {mode_label}")

    # COMBO-PRINT-SAFE (2026-05-23): combo result dicts don't carry
    # home_team / away_team / market / selection — they have combo_legs +
    # system_type + bot_name. Helper formats either shape without KeyError.
    def _label(r: dict) -> str:
        legs = r.get("combo_legs")
        if legs:
            n_legs = len(legs) if isinstance(legs, list) else "?"
            sys = r.get("system_type") or "straight"
            bot = r.get("bot_name") or "?"
            return f"COMBO[{sys}] {bot} ({n_legs} legs)"
        home = r.get("home_team", "?")
        away = r.get("away_team", "?")
        return f"{home} vs {away} | {r.get('market', '?')} {r.get('selection', '?')}"

    if args.execute:
        print(f"Results: {len(placed)} placed, {len(skipped)} skipped")
        for r in placed:
            ticket = r.get("ticket_id") or "record-only"
            odds = r.get("live_odds") or r.get("live_combined_odds") or 0
            print(f"  ✓  {_label(r)} @ {odds:.3f}  ticket={ticket}  real_bet={r.get('real_bet_id', '?')}")
    elif args.record:
        print(f"Recorded: {len(placed)} bets written to real_bets, {len(skipped)} skipped")
        for r in placed:
            odds = r.get("live_odds") or r.get("live_combined_odds") or 0
            print(f"  ✓  {_label(r)} @ {odds:.3f}  real_bet={r.get('real_bet_id', '?')}")
    else:
        print(f"Dry-run: {len(dry_run)} bets would be placed")
        for r in dry_run:
            odds = r.get("live_combined_odds") or r.get("model_odds") or 0
            print(f"  →  {_label(r)} @ model={odds:.3f}  coolbet={r.get('ev_odds') or '?'}")

    blocked = [r for r in skipped if r["outcome"] == "search_blocked"]
    if blocked:
        print()
        print(f"⚠  Coolbet search refused {len(blocked)} bet(s) — session/JWT "
              "appears dead (Incapsula or expired cbauth).")
        print("    Fix: Smart-ID in browser → copy fresh `cbauth` cookie → "
              "set COOLBET_MANUAL_JWT → re-run.")

    for r in skipped:
        reason = r.get("reason")
        suffix = f"{r['outcome']}" + (f" ({reason})" if reason else "")
        print(f"  ✗  {_label(r)} — {suffix}")

    print("=" * 60)

    sys.exit(0 if (placed or dry_run) else 1)


if __name__ == "__main__":
    main()
