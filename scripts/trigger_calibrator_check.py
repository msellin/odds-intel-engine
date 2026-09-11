#!/usr/bin/env python3
"""TRIGGER-CALIBRATOR-CHECK — did fixing the calibrator fix the trigger bots?

Run this. It answers one question and tells you whether it can answer it yet.

    python3 scripts/trigger_calibrator_check.py

WHAT IT IS FOR
--------------
On 2026-09-11 `pick_triggers._fit_calibrator('1x2')` was changed from ONE
isotonic curve pooled over home/draw/away to PER-SELECTION fits. The pooled fit
under-estimated HOME by 10-15pp (home wins 44.4% of the time, draws 24.5%, and
a single monotone curve cannot say both), which pushed every home window to a
much higher price and made the bots fire only on longshots — the recorded
"selects longshots, do not promote" symptom, and the leading suspect for their
negative CLV of -8% to -11%.

So the question is: **with the bias removed, does CLV recover?** That answer
gates whether the real-money mirror bots should be moved onto the same
window-firing mechanism (EPIC: MIRROR-AND-TRIGGER-CONVERGENCE, phase 2). If CLV
goes positive the mechanism is sound and worth migrating onto. If it stays
negative the mechanism itself is suspect and phase 2 should NOT proceed.

WHY A DEDICATED SCRIPT AND NOT `floor_grid_sweep --group-by model_version`
--------------------------------------------------------------------------
That was suggested first and it does not work here, for two reasons worth
knowing because they apply to any similar check:

  1. The cube groups EVERY shadow bot, so double_chance (5,371 rows), BTTS and
     asian_handicap swamp the handful of trigger rows and the answer is not
     visible in the output at all.
  2. The cube only loads SETTLED picks. Immediately after the fix the new-era
     picks exist but have not settled, so they cannot appear — which reads as
     "no data" rather than "not yet".

This script filters to trigger bots, splits the two calibration eras, and — the
important part — says plainly when there is not yet enough settled volume,
instead of printing a confident-looking table built on four bets.

HOW THE ERAS ARE TOLD APART
---------------------------
`pick_triggers` stamps `_CALIBRATOR_REV` into the window's `model_version`, and
the matcher carries it onto the pick. So `model_version LIKE '%+selcal1'` is
post-fix and NULL is pre-fix. Never pool them: the pre-fix sample came from a
measurably different (and wrong) model.

WHAT COUNTS AS ENOUGH
---------------------
CLV converges far faster than ROI — roughly 334 settled bets for a useful CLV
read against ~9,300 for +/-2% on ROI. So this reports CLV as the primary signal
and ROI only as context, and it will not offer a verdict below --min-n.
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402

CLV_USEFUL_N = 334      # ~+/-2% on CLV; the repo's own figure
ERA_TAG = "selcal1"


def _rows():
    return execute_query(
        """
        SELECT b.name AS bot,
               s.model_version AS mv,
               s.result,
               s.clv_pinnacle::float AS clv,
               s.odds_at_pick::float AS odds,
               (CASE WHEN s.result = 'won'
                     THEN (COALESCE(s.odds_at_pick_live, s.odds_at_pick) - 1)
                     ELSE -1 END)::float AS ret
          FROM shadow_bets_unique s
          JOIN bots b ON b.id = s.bot_id
         WHERE b.name LIKE '%%trigger%%'
        """
    )


def _era(mv: str | None) -> str:
    return "post-fix" if (mv or "").endswith(f"+{ERA_TAG}") else "pre-fix"


def _agg(rows):
    settled = [r for r in rows if r["result"] in ("won", "lost")]
    clv = [r["clv"] for r in settled if r["clv"] is not None]
    out = {"picks": len(rows), "settled": len(settled),
           "roi": None, "clv": None, "clv_n": len(clv)}
    if settled:
        out["roi"] = 100 * st.mean([r["ret"] for r in settled])
    if clv:
        out["clv"] = 100 * st.mean(clv)
    return out


def run(min_n: int, per_bot: bool) -> int:
    rows = _rows()
    if not rows:
        print("no trigger-bot picks found at all — is pick_trigger_matcher running?")
        return 1

    eras = {"pre-fix": [], "post-fix": []}
    for r in rows:
        eras[_era(r["mv"])].append(r)

    print(f"\n{'='*78}")
    print("TRIGGER CALIBRATOR CHECK — did per-selection calibration fix the bots?")
    print(f"{'='*78}")
    print(f"  {'era':12}{'picks':>8}{'settled':>9}{'ROI%':>9}{'CLVpin%':>10}  (CLV n)")
    for era in ("pre-fix", "post-fix"):
        a = _agg(eras[era])
        roi = f"{a['roi']:+.1f}" if a["roi"] is not None else "—"
        clv = f"{a['clv']:+.1f}" if a["clv"] is not None else "—"
        print(f"  {era:12}{a['picks']:>8}{a['settled']:>9}{roi:>9}{clv:>10}"
              f"  ({a['clv_n']})")

    post = _agg(eras["post-fix"])
    pre = _agg(eras["pre-fix"])

    print(f"\n{'-'*78}\nVERDICT\n{'-'*78}")
    if post["clv_n"] < min_n:
        need = min_n - post["clv_n"]
        rate = None
        # crude but honest: how fast did the pre-fix era accumulate CLV rows?
        if pre["clv_n"] > 0:
            days = execute_query(
                """SELECT GREATEST(1, EXTRACT(EPOCH FROM
                          (MAX(s.pick_time) - MIN(s.pick_time)))/86400.0) AS d
                     FROM shadow_bets_unique s JOIN bots b ON b.id = s.bot_id
                    WHERE b.name LIKE '%%trigger%%'
                      AND s.result IN ('won','lost')
                      AND s.clv_pinnacle IS NOT NULL"""
            )
            d = float(days[0]["d"]) if days and days[0]["d"] else None
            if d:
                rate = pre["clv_n"] / d
        eta = f" (~{need / rate:.0f} days at the observed {rate:.0f}/day)" if rate else ""
        print(f"  NOT YET ANSWERABLE. Post-fix era has {post['clv_n']} settled picks with a")
        print(f"  Pinnacle CLV; a useful read needs ~{min_n}{eta}.")
        print(f"  Come back when the 'settled' column above reaches ~{min_n}.")
        print()
        print("  Do NOT read the post-fix ROI/CLV above as a result yet, and do NOT")
        print("  pool the two eras to get a bigger number — the pre-fix sample came")
        print("  from a model that under-estimated HOME by 10-15pp, so averaging")
        print("  them describes neither.")
    else:
        pc, qc = post["clv"], pre["clv"]
        print(f"  Post-fix CLV {pc:+.1f}% on n={post['clv_n']} "
              f"(pre-fix was {qc:+.1f}% on n={pre['clv_n']}).")
        if pc is not None and pc > 0:
            print("  CLV is POSITIVE → the window-firing mechanism looks sound. Phase 2")
            print("  of the convergence epic (give the mirror bots trigger firing) is")
            print("  worth building. Promotion to real money still needs ROI volume.")
        else:
            print("  CLV is still NEGATIVE → the calibrator was not the whole story, so")
            print("  the firing mechanism itself is suspect. Do NOT move the real-money")
            print("  mirror bots onto it; investigate what else selects the wrong side.")

    if per_bot:
        print(f"\n{'-'*78}\nPER BOT (post-fix era only)\n{'-'*78}")
        by = {}
        for r in eras["post-fix"]:
            by.setdefault(r["bot"], []).append(r)
        print(f"  {'bot':38}{'picks':>7}{'settled':>9}{'CLVpin%':>10}")
        for bot, sub in sorted(by.items(), key=lambda kv: -len(kv[1])):
            a = _agg(sub)
            clv = f"{a['clv']:+.1f}" if a["clv"] is not None else "—"
            print(f"  {bot[:37]:38}{a['picks']:>7}{a['settled']:>9}{clv:>10}")
        if not by:
            print("  (no post-fix picks yet)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-n", type=int, default=CLV_USEFUL_N,
                    help=f"settled picks with a CLV needed before a verdict "
                         f"(default {CLV_USEFUL_N})")
    ap.add_argument("--per-bot", action="store_true",
                    help="also break the post-fix era down per bot")
    a = ap.parse_args()
    return run(a.min_n, a.per_bot)


if __name__ == "__main__":
    raise SystemExit(main())
