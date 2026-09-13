#!/usr/bin/env python3
"""CALIBRATION-TAIL-BACKTEST — does fixing the overconfident tail actually help?

    python3 scripts/calibration_tail_backtest.py
    python3 scripts/calibration_tail_backtest.py --folds 4

THE DEFECT THIS TESTS A FIX FOR (measured 2026-09-13)
-----------------------------------------------------
The model is calibrated ON AVERAGE — +0.0pp across 357,354 predictions — and
badly overconfident in the high band:

    predicted 59.2% -> actual 46.1%   (-13.2pp)
    predicted 69.1% -> actual 44.3%   (-24.8pp)
    predicted 80.7% -> actual 38.9%   (-41.8pp)

Only 3.3% of predictions live at >=55%, so the isotonic calibrator has almost
nothing to fit there — while the bots place 63% of their bets in exactly that
band. edge = cal_prob - 1/odds, so an inflated probability inflates every edge,
and inflates MOST where we bet MOST. That is why CLV gets worse as edge rises
instead of better.

WHY A BACKTEST AND NOT JUST "REFIT IT"
--------------------------------------
Because a recalibration that looks better in-sample is the easiest thing in the
world to produce and tells you nothing. The repo has been burned by exactly
that (`MODEL-FLIP-2026-07-06`: "retro was leakage-contaminated"). So:

  * WALK-FORWARD ONLY. Each fold fits the correction on picks that settled
    BEFORE the fold and applies it to picks after. No pick is ever scored by a
    curve that saw it.
  * THE SAME GATE EITHER WAY. Both arms apply the bot's real floors, so the
    comparison is "which picks does this select", not "a different strategy".
  * REPORTED AS DELTA WITH n. A recalibration that selects 4 bets and wins 3 is
    not evidence.

WHAT "BETTER" MEANS HERE, in priority order:
  1. CALIBRATION — does predicted match actual in the band we bet? This is the
     defect, so it is the primary measure.
  2. CLV — converges ~30x faster than ROI (~334 bets vs ~9,300), so it is the
     first honest read on whether selection improved.
  3. ROI — reported for completeness and NOT to be trusted at these volumes.
     It is here so nobody has to ask; the n is printed beside it precisely so
     the number cannot be quoted without its error bar.
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workers.api_clients.db import execute_query  # noqa: E402


# Fraction of the claimed edge treated as real. 0.5 = "half the edge is noise".
SHRINK_W = 0.5


def _picks() -> list[dict]:
    """Settled model-anchored picks with everything needed to re-select them."""
    return execute_query(
        """
        SELECT s.pick_time, s.market, s.selection,
               s.model_probability::float  AS praw,
               s.calibrated_prob::float    AS cal,
               COALESCE(s.odds_at_pick_live, s.odds_at_pick)::float AS odds,
               s.clv_pinnacle::float       AS clv,
               (s.result = 'won')          AS won
          FROM shadow_bets_unique s JOIN bots b ON b.id = s.bot_id
         WHERE s.result IN ('won','lost')
           AND s.model_probability IS NOT NULL
           AND s.calibrated_prob IS NOT NULL
           AND COALESCE(s.odds_at_pick_live, s.odds_at_pick) > 1.0
           AND b.name NOT LIKE '%%sharp%%' AND b.name NOT LIKE '%%pin_%%'
         ORDER BY s.pick_time
        """
    ) or []


def _fit(train: list[dict]):
    """Isotonic raw-probability -> observed frequency, fit on `train` only.

    Deliberately fit on the RAW model probability rather than on top of the
    existing calibrated one: stacking a correction on a curve that is already
    wrong in the tail inherits its shape, and the tail is the whole problem.
    """
    try:
        from sklearn.isotonic import IsotonicRegression
    except Exception as e:  # noqa: BLE001
        print(f"sklearn unavailable: {e}", file=sys.stderr)
        return None
    if len(train) < 400:
        return None
    xs = [r["praw"] for r in train]
    ys = [1.0 if r["won"] else 0.0 for r in train]
    return IsotonicRegression(out_of_bounds="clip").fit(xs, ys)


def _agg(sel: list[dict], probs: list[float]) -> dict:
    if not sel:
        return {"n": 0}
    won = sum(1 for r in sel if r["won"])
    pnl = sum((r["odds"] - 1) if r["won"] else -1 for r in sel)
    clvs = [r["clv"] for r in sel if r["clv"] is not None]
    return {
        "n": len(sel),
        "pred": 100 * st.mean(probs),
        "actual": 100 * won / len(sel),
        "roi": 100 * pnl / len(sel),
        "clv": 100 * st.mean(clvs) if clvs else None,
        "clv_n": len(clvs),
    }


def run(folds: int, floor: float) -> int:
    rows = _picks()
    if len(rows) < 800:
        print(f"only {len(rows)} settled picks — not enough to walk forward")
        return 1
    print(f"\n{'='*72}\nCALIBRATION-TAIL BACKTEST — walk-forward, {folds} folds\n{'='*72}")
    print(f"{len(rows)} settled model-anchored picks, edge floor {floor:.0%}\n")

    cur_sel, cur_p, new_sel, new_p = [], [], [], []
    shr_sel, shr_p = [], []
    step = len(rows) // (folds + 1)
    for k in range(1, folds + 1):
        train, test = rows[: step * k], rows[step * k: step * (k + 1)]
        iso = _fit(train)
        if iso is None:
            continue
        for r in test:
            # ARM A — what we do today: the stored calibrated probability.
            if r["cal"] - 1.0 / r["odds"] >= floor:
                cur_sel.append(r)
                cur_p.append(r["cal"])
            # ARM B — the same gate on a tail-corrected probability.
            corrected = float(iso.predict([r["praw"]])[0])
            if corrected - 1.0 / r["odds"] >= floor:
                new_sel.append(r)
                new_p.append(corrected)
            # ARM C — SHRINK THE EDGE toward the market instead of recalibrating.
            #
            # Added after arm B failed: recalibration left the gap at -18.4pp,
            # which says the defect is NOT a miscalibrated marginal curve. It is
            # the WINNER'S CURSE — we select picks precisely where the model's
            # noise runs high against the market, and the selection itself is
            # the bias. No amount of remapping the average fixes a conditional
            # error created by selecting on that average.
            #
            # The classic remedy is shrinkage: trust only a FRACTION of the
            # claimed edge, on the view that the rest is noise that will regress.
            # p_shrunk = market + w*(model - market), with 1/odds as the market
            # proxy (vig-inclusive, so conservative — it understates our edge).
            shrunk = 1.0 / r["odds"] + SHRINK_W * (r["cal"] - 1.0 / r["odds"])
            if shrunk - 1.0 / r["odds"] >= floor:
                shr_sel.append(r)
                shr_p.append(shrunk)

    a, b = _agg(cur_sel, cur_p), _agg(new_sel, new_p)
    c = _agg(shr_sel, shr_p)
    print(f"{'':22}{'n':>6}{'pred':>8}{'actual':>8}{'cal gap':>9}{'CLV%':>8}{'ROI%':>8}")
    for name, d in (("CURRENT (stored cal)", a), ("CORRECTED (refit)", b),
                    (f"SHRUNK (w={SHRINK_W})", c)):
        if not d["n"]:
            print(f"  {name:20}{0:>6}   — selects nothing at this floor")
            continue
        clv = f"{d['clv']:+.2f}" if d["clv"] is not None else "—"
        print(f"  {name:20}{d['n']:>6}{d['pred']:>7.1f}%{d['actual']:>7.1f}%"
              f"{d['actual']-d['pred']:>+9.1f}{clv:>8}{d['roi']:>+8.1f}")

    print(f"\n{'-'*72}\nVERDICT\n{'-'*72}")
    if not a["n"] or not b["n"]:
        print("  One arm selected nothing — no comparison possible.")
        return 1
    gap_a, gap_b = a["actual"] - a["pred"], b["actual"] - b["pred"]
    print(f"  Calibration gap in the band we bet: {gap_a:+.1f}pp -> {gap_b:+.1f}pp")
    print(f"  Selected volume: {a['n']} -> {b['n']} picks "
          f"({100*(b['n']-a['n'])/a['n']:+.0f}%)")
    if a["clv"] is not None and b["clv"] is not None:
        print(f"  CLV: {a['clv']:+.2f}% -> {b['clv']:+.2f}%  "
              f"({b['clv']-a['clv']:+.2f}pp)")
    print()
    if c["n"]:
        print(f"  SHRUNK arm: gap {c['actual']-c['pred']:+.1f}pp on n={c['n']}, "
              f"CLV {c['clv']:+.2f}%" if c.get("clv") is not None else
              f"  SHRUNK arm: gap {c['actual']-c['pred']:+.1f}pp on n={c['n']}")
    print()
    if abs(gap_b) < abs(gap_a):
        print("  The correction NARROWS the calibration gap out of sample — the")
        print("  defect is real and this addresses it. Judge promotion on CLV,")
        print("  not on the ROI column: at these volumes ROI is noise.")
    else:
        print("  The correction does NOT narrow the gap out of sample. Do not")
        print("  ship it — an in-sample improvement that fails walk-forward is")
        print("  exactly the trap that produced the leakage-contaminated retro.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--floor", type=float, default=0.10,
                    help="edge floor applied identically to both arms")
    a = ap.parse_args()
    return run(a.folds, a.floor)


if __name__ == "__main__":
    raise SystemExit(main())
