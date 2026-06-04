"""
Coolbet match-probe — diagnose why a specific bet was tagged "no match"
or "no market" on /admin/place.

Mirrors the placer's match-finding flow without placing anything:
  1. search_coolbet_event(home, away) — the multi-pass search
  2. fuzzy_match_event over the full fo-category tree if search misses
  3. fetch_match_markets / fetch_odds_for_markets — what Coolbet actually
     offers for the matched event
  4. resolve_placement_target(market, selection) — would the placer have
     found the (market, selection) pair?

Use this to feed real fixes back into search_coolbet_event /
fuzzy_match_event when the UI shows "⚠ no match" for a match you can
verify exists at Coolbet.

Usage:
    venv/bin/python3 scripts/coolbet_match_probe.py "Gagra" "Dila"
    venv/bin/python3 scripts/coolbet_match_probe.py "Jezero" "Mornar" \
        --market double_chance --selection x2
    venv/bin/python3 scripts/coolbet_match_probe.py "Gagra" "Dila" \
        --market asian_handicap --selection "home 0.5"
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv()

from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import (
    search_coolbet_event,
    fuzzy_match_event,
    fetch_coolbet_events,
)
from workers.automation.coolbet_explorer import (
    fetch_match_markets,
    fetch_odds_for_markets,
    resolve_placement_target,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("home", help="Home team name as stored in our DB")
    ap.add_argument("away", help="Away team name as stored in our DB")
    ap.add_argument("--market", help="Optional: paper-bet market label "
                                     "(1X2 / O/U / BTTS / double_chance / asian_handicap)")
    ap.add_argument("--selection", help="Optional: paper-bet selection "
                                        "(home/away/draw, over 2.5, yes, 1x, home 0.5, ...)")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Show placer's own log lines (search passes, fuzzy scores)")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Probe is a diagnostic — Coolbet's search / fo-category / fo-match /
    # sidebets endpoints all work with Imperva cookies only, no JWT.
    # Anon-read keeps the probe usable even when COOLBET_MANUAL_JWT is stale.
    session = CoolbetSession(require_auth=False)

    # ── Step 1: search ─────────────────────────────────────────────────────
    print(f"\n[1/4] search_coolbet_event({args.home!r}, {args.away!r})")
    ev = search_coolbet_event(session, args.home, args.away)
    if ev is None:
        # ── Step 2: fall back to full fo-category tree + fuzzy match ───────
        print("      → search miss. Loading full fo-category tree…")
        try:
            tree = fetch_coolbet_events(session)
        except Exception as e:
            print(f"      ! fo-category fetch failed: {e}")
            return 2
        print(f"      → tree has {len(tree)} events. Fuzzy matching…")
        ev = fuzzy_match_event(args.home, args.away, tree)
        if ev is None:
            print(f"\nVerdict: NO_EVENT — Coolbet has no match for "
                  f"'{args.home} vs {args.away}'.")
            print("Either the event truly isn't on Coolbet, or the fuzzy "
                  "match needs a tweak. Re-run with -v to see search "
                  "passes and the best-but-too-low fuzzy score.")
            return 1

    cb_id = int(ev["id"])
    print(f"      ✓ matched Coolbet event {cb_id}: "
          f"'{ev.get('home')}' vs '{ev.get('away')}'")

    # ── Step 3: markets ────────────────────────────────────────────────────
    print(f"\n[2/4] fetch_match_markets(matchId={cb_id})")
    markets = fetch_match_markets(session, cb_id)
    print(f"      → {len(markets)} markets offered. First 12:")
    for m in markets[:12]:
        line = m.get("line")
        line_str = f" line={line}" if line is not None else ""
        print(f"        - {m.get('name')!r}{line_str}")

    print(f"\n[3/4] fetch_odds_for_markets({len(markets)} markets)")
    odds_map = fetch_odds_for_markets(session, markets)
    print(f"      → got odds for {len(odds_map)} markets")

    if not args.market or not args.selection:
        print("\n(No --market / --selection given — skipping resolve check.)")
        return 0

    # ── Step 4: resolve target ─────────────────────────────────────────────
    print(f"\n[4/4] resolve_placement_target({args.market!r}, {args.selection!r})")
    target = resolve_placement_target(markets, odds_map, args.market, args.selection)
    if target is None:
        print(f"\nVerdict: NO_MARKET — Coolbet has the event but does not "
              f"offer ({args.market}, {args.selection}).")
        print("Sample of what IS offered (first 12 above). Compare with "
              "your selection — could be a quarter-line vs half-line "
              "mismatch, missing DC outcome, etc.")
        return 1

    bo_id, oc_id, odds_uuid, odds = target
    print(f"      ✓ resolved: betOfferId={bo_id} outcomeId={oc_id} "
          f"oddsId={odds_uuid} odds={odds:.3f}")
    print(f"\nVerdict: READY — placer would place this bet at {odds:.3f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
