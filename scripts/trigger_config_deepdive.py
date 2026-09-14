#!/usr/bin/env python3
"""TRIGGER-CONFIG-DEEPDIVE — is there REALLY no profitable configuration of the
model-anchored 1x2 trigger bots?

    python3 scripts/trigger_config_deepdive.py
    python3 scripts/trigger_config_deepdive.py --days 120 --min-n 40

WHY THIS EXISTS
---------------
Owner, 2026-09-14: *"it's difficult to believe that trigger bots don't have a
fold robust config, as they should be like all games when model ones are a
subset, so if we shrunk the trigger selection down to some profitable set...
can you do another validation round, just to be sure."*

A fair challenge, and the first search deserved it: it tested edge floors, odds
floors and selections SEPARATELY — about 11 cells — and reported "none
fold-robust" as though the space had been covered. It had not.

This searches the full product: selection x book x edge BAND x odds BAND
(bands, not just floors, so a ceiling can be found as well as a floor), ~9,000
combinations. It also answers the owner's structural premise, which turns out to
be the real finding.

THE PREMISE IS BACKWARDS, AND THAT IS THE ANSWER
------------------------------------------------
The trigger bots are NOT a superset of the model bots. They are a DISJOINT,
longshot-only population, because `FAVLONG-CUTS-2026-09-09` gave them an odds
FLOOR of 2.80:

    odds band    trigger bots    bot_v10_all
    < 2.0                   0             28
    2.0-2.8                 0             48
    2.8-4.0               301             90
    4.0-6.0               366              6
    6.0+                  183              0

Zero picks below 2.80. Median odds 4.35 against 2.94. So "shrinking the trigger
selection down" cannot reach the profitable region — the bots are CONFIGURED out
of it and hold no data there at all.

Measured on all placeable-book 1x2 picks, CLV falls as odds rise, and the
gradient holds WITHIN individual bots (which removes the "different bots supply
different bands" confound): bot_v10_all +5.6% below 2.8 against +2.4% at 2.8-4.0;
bot_pin_1x2_home_v1 +3.2 / +2.6 / +2.0. Seven of nine bots spanning two or more
bands fall monotonically.

⚠️ READ THE ROI COLUMN BEFORE ACTING. CLV and ROI agree that odds 2.0-2.8 is the
good band (+3.7% CLV, +12% ROI at t=+2.6) and that 2.8-3.6 is bad (-2.0%, -11% at
t=-2.4). They DISAGREE at the extreme: `edge>=13% x odds 1.0-2.0` is +16.7% CLV
but -25% ROI on a thin cell. Do not quote that one in either direction.

⚠️ AND THE TIME SPLIT MATTERS. The long-odds damage is concentrated in the second
half of the window (2.8-3.6 goes +0.9% -> -5.0%), while the short-odds cells are
stable across both halves (+5.4 -> +4.2, +3.8 -> +3.5). The stable claim is the
short-odds positive, not the long-odds collapse.

WHAT THE SEARCH RETURNS
-----------------------
Zero fold-robust positive configurations out of ~9,000. Dropping the
fold-robustness rule AND the volume gate entirely, the best cell anywhere in the
space is +1.81% on n=26 (one book, one narrow odds slice) — which is what a wide
scan produces by chance, not a finding. Without the book split nothing is
positive at all.

So the retirement stands, and now for a reason that survives the owner's
challenge: not "the search found nothing" but "every band these bots occupy is
negative, and the band that works is one they are configured out of and hold
zero data in".
"""
from __future__ import annotations

import argparse
import itertools
import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.api_clients.db import execute_query  # noqa: E402

PLACEABLE = ("Coolbet", "Unibet-Site", "Epicbet", "Betano", "Unibet")
TRIGGER_BOTS = ("bot_coolbet_trigger_1x2_v1", "bot_unibet_trigger_1x2_v1",
                "bot_trigger_1x2_model_v1")
EDGE_FLOORS = [0.0, 0.10, 0.13, 0.15, 0.18, 0.22, 0.26]
EDGE_CEILS = [0.18, 0.22, 0.26, 0.35, 9.0]
ODDS_FLOORS = [0.0, 2.8, 3.2, 3.6, 4.0, 4.5]
ODDS_CEILS = [3.2, 3.6, 4.0, 4.5, 6.0, 99.0]
ODDS_BANDS = [("<2.0", 0, 2.0), ("2.0-2.8", 2.0, 2.8), ("2.8-3.6", 2.8, 3.6),
              ("3.6-4.5", 3.6, 4.5), ("4.5-6.0", 4.5, 6.0), ("6.0+", 6.0, 99)]


def _picks(days: int, bots: tuple[str, ...] | None) -> list[dict]:
    out: list[dict] = []
    for tbl in ("shadow_bets_unique", "simulated_bets"):
        where = "b.name IN %s AND" if bots else ""
        params: list = [bots] if bots else []
        params.append(PLACEABLE)
        out += execute_query(f"""
            SELECT b.name AS bot, lower(s.selection) AS sel,
                   s.recommended_bookmaker AS bk,
                   COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float AS odds,
                   s.edge_percent::float AS edge, s.clv_pinnacle::float AS clv,
                   (s.result = 'won') AS won, s.pick_time
              FROM {tbl} s JOIN bots b ON b.id = s.bot_id
             WHERE {where} s.result IN ('won','lost') AND s.market = '1x2'
               AND s.recommended_bookmaker IN %s
               AND s.clv_pinnacle IS NOT NULL AND s.edge_percent IS NOT NULL
               AND COALESCE(s.odds_at_pick_live, s.odds_at_pick) > 1.0
               AND s.pick_time > now() - interval '{int(days)} days'
        """, params) or []
    out.sort(key=lambda r: r["pick_time"])
    return out


def _folds_ok(sel: list[dict], k: int = 3) -> bool:
    """CLV positive in EVERY fold. `sel` must already be in pick_time order."""
    if len(sel) < 40:
        return False
    step = len(sel) // k
    return all(
        st.mean([r["clv"] for r in (sel[i * step:(i + 1) * step] if i < k - 1
                                    else sel[i * step:])]) > 0
        for i in range(k))


def _roi(c: list[dict]) -> float:
    return 100 * st.mean([(r["odds"] - 1) if r["won"] else -1.0 for r in c])


def run(days: int, min_n: int) -> int:
    trig = _picks(days, TRIGGER_BOTS)
    allp = _picks(days, None)
    print(f"\ntrigger 1x2 picks (placeable, CLV-bearing, {days}d): n={len(trig):,}")
    print(f"all 1x2 picks for the counterfactual:                 n={len(allp):,}\n")

    print("1. WHERE THE TRIGGER BOTS LIVE vs where the money is")
    print(f"   {'odds band':12}{'trigger n':>11}{'all-bots CLV%':>15}{'all-bots ROI%':>15}")
    for lbl, lo, hi in ODDS_BANDS:
        t = sum(1 for r in trig if lo <= r["odds"] < hi)
        c = [r for r in allp if lo <= r["odds"] < hi]
        cv = f"{100*st.mean([r['clv'] for r in c]):+.1f}" if len(c) >= 25 else "·"
        rv = f"{_roi(c):+.0f}" if len(c) >= 25 else "·"
        print(f"   {lbl:12}{t:>11}{cv:>15}{rv:>15}")

    print("\n2. EXHAUSTIVE search over the trigger bots "
          "(selection x book x edge band x odds band)")
    sels = [("all", None), ("home", {"home"}), ("away", {"away"}),
            ("home+away", {"home", "away"})]
    bks = [("all", None)] + [(b, {b}) for b in sorted({r["bk"] for r in trig})]
    hits, tested, best_any = [], 0, []
    for (sl, ss), (bl, bs), ef, ec, of_, oc in itertools.product(
            sels, bks, EDGE_FLOORS, EDGE_CEILS, ODDS_FLOORS, ODDS_CEILS):
        if ec <= ef or oc <= of_:
            continue
        tested += 1
        c = [r for r in trig
             if (ss is None or r["sel"] in ss) and (bs is None or r["bk"] in bs)
             and ef <= r["edge"] < ec and of_ <= r["odds"] < oc]
        if len(c) < 25:
            continue
        m = 100 * st.mean([r["clv"] for r in c])
        lbl = f"sel={sl} bk={bl} edge[{ef:.2f},{ec:.2f}) odds[{of_},{oc})"
        best_any.append((m, len(c), lbl))
        if m > 0 and len(c) >= min_n and _folds_ok(c):
            hits.append((m, len(c), _roi(c), lbl))
    print(f"   {tested:,} combinations tested, n>={min_n}, CLV>0 AND positive in all 3 folds")
    if hits:
        for m, n, roi, lbl in sorted(hits, reverse=True)[:15]:
            print(f"   CLV {m:+6.2f}%  n={n:<4} ROI {roi:+6.1f}%   {lbl}")
    else:
        print("   *** ZERO fold-robust positive configurations. ***")
    print("\n   Best cell of ANY size/shape, fold-robustness ignored:")
    for m, n, lbl in sorted(best_any, reverse=True)[:3]:
        print(f"   CLV {m:+6.2f}%  n={n:<4}  {lbl}")

    print("\n3. WITHIN-BOT odds gradient (removes the 'which bot supplied it' confound)")
    print("   cells are CLV% / ROI% (n); a bot must span >=2 bands to appear")
    within = [("<2.8", 0, 2.8), ("2.8-4.0", 2.8, 4.0), ("4.0+", 4.0, 99)]
    print(f"   {'bot':34}" + "".join(f"{lbl:>18}" for lbl, _, _ in within))
    for bot in sorted({r["bot"] for r in allp}):
        br = [r for r in allp if r["bot"] == bot]
        cells = [[r for r in br if lo <= r["odds"] < hi] for _, lo, hi in within]
        if sum(1 for c in cells if len(c) >= 25) < 2:
            continue
        line = f"   {bot[:32]:34}"
        for c in cells:
            if len(c) < 25:
                line += f"{'·':>18}"
            else:
                clv = 100 * st.mean([r["clv"] for r in c])
                line += f"{f'{clv:+.1f}/{_roi(c):+.0f}({len(c)})':>18}"
        print(line)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--min-n", type=int, default=40)
    a = ap.parse_args()
    return run(a.days, a.min_n)


if __name__ == "__main__":
    raise SystemExit(main())
