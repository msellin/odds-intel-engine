#!/usr/bin/env python3
"""Emit the engine's edge/odds floors into a GENERATED TypeScript file.

FLOORS-ONE-SOURCE-CROSS-LANGUAGE (2026-09-11). The Python side was consolidated
onto one predicate (`clears_edge_floor`), but the frontend still carried its own
copies: `upcoming-picks.ts` hardcoded 0.1/2.8 and 0.08/1.8 plus its own
TypeScript implementation of the FAVLONG-CUTS home-underdog rule, with NO import
path to Python. So a floor change in the engine silently never reached the
published "place >= X.XX" hint that readers act on.

This generator makes Python authoritative and the TS file derived. It also emits
a PARITY FIXTURE — (market, selection, odds) -> floor, computed by the real
Python predicate — so a test can prove the TS reimplementation still agrees,
not merely that the constants match. Constants agreeing while the RULE drifts is
exactly how the two paths diverged before.

Run:  python3 scripts/gen_frontend_floors.py [--check]
`--check` exits 1 if the committed file is stale (used by the smoke test).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from workers.automation.coolbet_placer import (  # noqa: E402
    _MIN_EDGE_BY_MARKET, _MIN_ODDS_BY_MARKET, _MODEL_1X2_HOME_FLOOR,
    min_edge_for_pick,
)

OUT = (pathlib.Path(__file__).resolve().parents[2]
       / "odds-intel-web" / "src" / "lib" / "generated" / "engine-floors.ts")

# Inputs the fixture must cover. Each is a case that has actually mattered:
# home-underdog at/above the odds floor (FAVLONG-CUTS 10%), home BELOW the odds
# floor (falls back to pooled 13%), away and draw (pooled — not fold-robust at
# 10%), and both O/U spellings.
_FIXTURE_INPUTS = [
    ("1x2", "home", 3.30), ("1x2", "home", 2.80), ("1x2", "home", 2.79),
    ("1x2", "away", 3.30), ("1x2", "draw", 3.30),
    ("over_under_25", "under", 2.93), ("o/u", "over", 1.85),
]


def render() -> str:
    fixture = [
        {"market": m, "selection": s, "odds": o,
         "floor": round(min_edge_for_pick(m, s, o), 6)}
        for m, s, o in _FIXTURE_INPUTS
    ]
    edge = {k: v for k, v in sorted(_MIN_EDGE_BY_MARKET.items())}
    odds = {k: v for k, v in sorted(_MIN_ODDS_BY_MARKET.items())}
    return f'''// GENERATED FILE — DO NOT EDIT BY HAND.
// Source of truth: workers/automation/coolbet_placer.py
// Regenerate:      python3 scripts/gen_frontend_floors.py
// Drift guard:     smoke test FLOORS-ONE-SOURCE-CROSS-LANGUAGE
//
// Why this file exists (FLOORS-ONE-SOURCE-CROSS-LANGUAGE, 2026-09-11):
// upcoming-picks.ts hardcoded 0.1/2.8 and 0.08/1.8 and its own copy of the
// FAVLONG-CUTS home-underdog rule, with no import path to Python. A floor
// change in the engine never reached the published "place >= X.XX" hint that
// readers act on. Six copies of the edge floors existed across the stack; this
// closes the cross-language ones.

export const ENGINE_MIN_EDGE_BY_MARKET: Record<string, number | null> =
  {json.dumps(edge, indent=2)};

export const ENGINE_MIN_ODDS_BY_MARKET: Record<string, number> =
  {json.dumps(odds, indent=2)};

/** FAVLONG-CUTS: 1x2 HOME underdogs (odds >= the 1x2 odds floor) clear at this
 *  edge. Home-favs and aways stay on the pooled floor — they are not
 *  fold-robust at 10%. */
export const ENGINE_MODEL_1X2_HOME_FLOOR = {_MODEL_1X2_HOME_FLOOR};

/** Parity fixture, computed by Python's real min_edge_for_pick(). A test
 *  asserts the TS rule reproduces every row. Constants agreeing while the RULE
 *  drifts is exactly how these paths diverged before, so pinning the numbers
 *  alone is not enough. */
export const ENGINE_FLOOR_FIXTURE: ReadonlyArray<{{
  market: string; selection: string; odds: number; floor: number;
}}> = {json.dumps(fixture, indent=2)};
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed file is stale")
    args = ap.parse_args()
    want = render()
    if args.check:
        have = OUT.read_text() if OUT.exists() else ""
        if have != want:
            print(f"STALE: {OUT} does not match the engine floors.\n"
                  f"Run: python3 scripts/gen_frontend_floors.py")
            return 1
        print(f"OK: {OUT.name} matches the engine floors")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(want)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
