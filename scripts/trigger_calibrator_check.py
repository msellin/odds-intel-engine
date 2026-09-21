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
CLV converges far faster than ROI — tens to hundreds of settled bets against
~9,300 for +/-2% on ROI. So this reports CLV as the primary signal and ROI only
as context, and it will not offer a verdict below --min-n.

That bar is DERIVED, not fixed (see required_clv_n below). It used to be a flat
334, which the measured spread says is both too small and far too large
depending on the effect you care about.
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402

# MODEL-TRAINING-DEBT (d), 2026-09-21 — a FIXED n answers no particular question.
#
# This was `CLV_USEFUL_N = 334`, carried as "the repo's own figure" for
# "~+/-2% on CLV". Measured against the actual data it is neither: over 60 days
# and 28,047 rows the margin-corrected CLV sd is 0.1103, so at t=2 the required
# sample is
#
#     to detect  1.0pp   n = 487      <- 334 is NOT enough
#     to detect  2.0pp   n = 122
#     to detect  5.0pp   n = 19       <- 334 is 17x more than needed
#
# One constant cannot be right for both ends of that. What matters is the effect
# size the DECISION turns on, so the threshold is now derived from it.
#
# The decision here is whether the per-selection calibrator fix recovered CLV
# from its -6.6% baseline. A 3pp move is the smallest that would change what we
# do, so that is the default — and the sd is measured from the data rather than
# assumed, so the bar tracks reality instead of drifting away from it.
CLV_EFFECT_PP = 0.03      # smallest CLV improvement that changes the decision
CLV_T_TARGET = 2.0        # ~95% two-sided
CLV_SD_FALLBACK = 0.1103  # measured 2026-09-21, n=28,047; used only if the query fails
CLV_N_FLOOR = 30          # never call a verdict on a handful, whatever the maths says


def required_clv_n(effect: float = CLV_EFFECT_PP, t: float = CLV_T_TARGET) -> int:
    """Sample needed to resolve `effect` at `t`, from the OBSERVED CLV spread.

    n = (t * sd / effect)^2 — the standard two-sided power expression for a mean.
    sd is read from the live ledger so the bar moves if the strategy's dispersion
    does; a fixed constant silently stops being the right number the moment it
    changes.
    """
    import math
    try:
        r = execute_query(
            """SELECT stddev(clv_margin_corrected) AS sd
                 FROM shadow_bets
                WHERE clv_margin_corrected IS NOT NULL
                  AND created_at > now() - interval '60 days'""")
        sd = float(r[0]["sd"]) if r and r[0]["sd"] is not None else CLV_SD_FALLBACK
    except Exception:
        sd = CLV_SD_FALLBACK
    return max(CLV_N_FLOOR, math.ceil((t * sd / effect) ** 2))


CLV_USEFUL_N = required_clv_n()
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


def verdict(min_n: int = CLV_USEFUL_N) -> dict:
    """Machine-readable form of the report, for the scheduled watcher.

    Returns {ready, post_clv_n, post_clv, post_roi, pre_clv, pre_clv_n, verdict}.
    `ready` is False until the post-fix era has enough settled CLV rows — the
    watcher stays silent until then rather than paging with a number built on
    four bets.
    """
    rows = _rows()
    eras = {"pre-fix": [], "post-fix": []}
    for r in rows:
        eras[_era(r["mv"])].append(r)
    post, pre = _agg(eras["post-fix"]), _agg(eras["pre-fix"])
    ready = post["clv_n"] >= min_n
    v = None
    if ready:
        v = "mechanism_sound" if (post["clv"] or 0) > 0 else "mechanism_suspect"
    return {"ready": ready, "post_clv_n": post["clv_n"], "post_clv": post["clv"],
            "post_roi": post["roi"], "post_settled": post["settled"],
            "pre_clv": pre["clv"], "pre_clv_n": pre["clv_n"], "verdict": v}


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
