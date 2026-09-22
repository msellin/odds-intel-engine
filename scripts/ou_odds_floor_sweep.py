#!/usr/bin/env python3
"""OU-ODDS-FLOOR-SWEEP — would a higher odds floor have saved the O/U bot?

Owner (2026-09-22): "is it possible to backtest OU model bot configuration
against historical data? i see that the long failing period came when we only
took higher odds picks for OU... test it backwards with different odds levels...
min threshold 1.8+, 2.0+, 2.2+, 2.4+ etc up until lets say 2.8+".

THE OBSERVATION IS RIGHT ABOUT *WHEN* AND CONFOUNDED ABOUT *WHY*
----------------------------------------------------------------
`bot_v10_ou` settled picks by era and price:

    era                        n     at 2.8+
    A pre-bug  (May 8-Sep 2)  157    25 (16%)
    B bug      (Sep 3-Sep 12)  80    59 (74%)     <- the drawdown on the chart
    C post-fix (Sep 13- )      15    12, 0 with CLV

The Sep 3 -> Sep 13 drawdown is OU-CALIBRATOR-DOMAIN-MISMATCH (migration 335):
a Platt curve fitted on raw ensemble probabilities and applied to Pinnacle-shrunk
ones, whose entire output range was [0.303, 0.666]. That made
`edge = cal_prob - 1/odds` degenerate into "how far is this price from ~0.45",
which is MAXIMISED BY THE LONGEST PRICE ON THE BOARD. The bug manufactured the
high-odds picks.

So "high-odds O/U" and "the bug window" are nearly the same rows. Sweeping the
floor over the whole period would re-measure the bug and report it as an
odds-band effect -- gotcha 47 (an odds-band effect is a BOT effect until you
split) arriving through gotcha 39 (never measure across a calibration change).
Era B is therefore reported SEPARATELY and never pooled into the headline.

WHAT THIS CAN AND CANNOT ANSWER
-------------------------------
CAN: whether RAISING the floor would have helped -- every such gate is a subset
of picks the bot actually made.

CANNOT: whether LOWERING it would help. Those picks are not in the ledger.
That needs a gate replay over all historical O/U odds (the "idealized" basis in
scripts/edge_floor_backtest.py) and carries best-of-books bias of its own.

LIKELY CANNOT ANSWER DEFINITIVELY AT ALL: n=157 clean, against this repo's own
bar of n>=334 for a useful CLV read (docs/BETA_PROMOTION_BAR.md). Expect a
direction and an interval, not a decision.

RULES THIS SCRIPT OBEYS
-----------------------
* PRIMARY METRIC IS CLV, NOT ROI (gotcha 8): CLV converges ~200x faster, and
  every ROI confidence interval in this system spans zero. ROI is shown beside
  it, with an interval, never alone.
* EXECUTABLE PRICE for both the threshold and the return:
  COALESCE(odds_at_pick_live, odds_at_pick). `odds_at_pick` alone is a MAX()
  high-water mark (gotcha 30 / STALE-BEST-ODDS), so thresholding on it asks
  "what if we had filtered on a price nobody offered".
* CUMULATIVE FLOORS ARE NESTED, SO BANDS ARE ALSO REPORTED. ROI at a floor is a
  volume-weighted blend dragged toward its neighbours and will always look
  "similar" no matter how bad one band is -- the framing scripts/odds_floor_ab.py
  had to fix for 1x2. Raising a floor one step IS "drop this disjoint band", so
  the bands localise the damage and the floors answer the question as asked.
* MULTIPLE COMPARISONS. Twelve correlated statistics on n=157; picking the best
  post-hoc is how noise becomes a config change. Max-|t| permutation over the
  whole grid gives a family-wise p (design from scripts/odds_band_by_market.py).
* MONTHLY SIGN beside every headline cell. Positive in one month of five is not
  a finding.

Usage:
    python3 scripts/ou_odds_floor_sweep.py
    python3 scripts/ou_odds_floor_sweep.py --bot bot_v10_ou --perms 2000
"""
from __future__ import annotations

import argparse
import os
import random
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query

FLOORS = [1.8, 2.0, 2.2, 2.4, 2.6, 2.8]
BUG_START = "2026-09-03"   # OU-CALIBRATOR-DOMAIN-MISMATCH shipped
BUG_END = "2026-09-13"     # migration 335 removed it

ERAS = [
    ("A pre-bug",  None,      BUG_START),
    ("B BUG",      BUG_START, BUG_END),
    ("C post-fix", BUG_END,   None),
]


def load(bot: str) -> list[dict]:
    """Settled picks with the executable price and the de-vigged Pinnacle CLV.

    `clv_pinnacle_devig` is used rather than raw `clv` deliberately: raw CLV
    breaks even at the CLOSING BOOK'S MARGIN (~8%), not at zero (gotcha 70), so
    a raw-CLV sweep would rank every band against the wrong baseline.
    """
    return execute_query("""
        SELECT s.pick_time,
               COALESCE(NULLIF(s.odds_at_pick_live, 0), s.odds_at_pick)::float AS px,
               s.odds_at_pick::float                                           AS px_hw,
               s.result,
               s.clv_pinnacle_devig::float                                     AS clv,
               (CASE WHEN s.result = 'won'
                     THEN COALESCE(NULLIF(s.odds_at_pick_live, 0), s.odds_at_pick) - 1
                     ELSE -1 END)::float                                       AS ret
          FROM simulated_bets s
          JOIN bots b ON b.id = s.bot_id
         WHERE b.name = %s AND s.result IN ('won', 'lost')
         ORDER BY s.pick_time
    """, (bot,))


def _mean_ci(xs: list[float]) -> tuple[float | None, float | None, float | None]:
    """mean, half-width of a 95% CI, and t. None when n < 2."""
    if len(xs) < 2:
        return (st.mean(xs) if xs else None), None, None
    m = st.mean(xs)
    se = st.stdev(xs) / (len(xs) ** 0.5)
    if se == 0:
        return m, 0.0, None
    return m, 1.96 * se, m / se


def _era(rows, lo, hi):
    out = []
    for r in rows:
        d = r["pick_time"].date().isoformat()
        if lo and d < lo:
            continue
        if hi and d >= hi:
            continue
        out.append(r)
    return out


def _cell(rows) -> dict:
    clvs = [r["clv"] for r in rows if r["clv"] is not None]
    rets = [r["ret"] for r in rows]
    cm, cci, ct = _mean_ci(clvs)
    rm, rci, _ = _mean_ci(rets)
    return {"n": len(rows), "n_clv": len(clvs),
            "clv": cm, "clv_ci": cci, "clv_t": ct,
            "roi": rm, "roi_ci": rci}


def _fmt(c: dict) -> str:
    clv = "      —    " if c["clv"] is None else f"{100*c['clv']:+6.2f}%"
    cci = "         " if c["clv_ci"] is None else f"±{100*c['clv_ci']:.2f}"
    roi = "      —" if c["roi"] is None else f"{100*c['roi']:+6.1f}%"
    rci = "        " if c["roi_ci"] is None else f"±{100*c['roi_ci']:.0f}"
    return f"n={c['n']:>4} {clv} {cci:>8}   {roi} {rci:>7}"


def _monthly_signs(rows) -> str:
    by = defaultdict(list)
    for r in rows:
        if r["clv"] is not None:
            by[r["pick_time"].strftime("%Y-%m")].append(r["clv"])
    if not by:
        return ""
    return " ".join(f"{m[-2:]}{'+' if st.mean(v) > 0 else '−'}" for m, v in sorted(by.items()))


def sweep(rows, title: str, perms: int) -> None:
    print(f"\n{'='*94}\n{title}  (n={len(rows)})\n{'='*94}")
    if not rows:
        print("  no settled rows")
        return

    print(f"  {'gate':<14}{'':>6}{'de-vig Pinnacle CLV':>26}{'ROI (executable)':>20}   months")
    base = _cell(rows)
    print(f"  {'ALL':<14}{_fmt(base)}   {_monthly_signs(rows)}")

    print("\n  -- CUMULATIVE FLOORS (as asked; nested, so each blends its neighbours) --")
    for f in FLOORS:
        kept = [r for r in rows if r["px"] >= f]
        if not kept:
            print(f"  {'>= ' + str(f):<14}n=   0")
            continue
        print(f"  {'>= ' + str(f):<14}{_fmt(_cell(kept))}   {_monthly_signs(kept)}")

    print("\n  -- DISJOINT BANDS (this is what a floor step actually removes) --")
    edges = [0.0] + FLOORS + [99.0]
    bands = []
    for lo, hi in zip(edges, edges[1:]):
        sub = [r for r in rows if lo <= r["px"] < hi]
        if not sub:
            continue
        lbl = f"{lo:g}-{hi:g}" if hi < 99 else f"{lo:g}+"
        bands.append((lbl, sub))
        print(f"  {lbl:<14}{_fmt(_cell(sub))}   {_monthly_signs(sub)}")

    # ── family-wise significance over the whole grid ──────────────────────────
    # Every cell above is a subset of ONE population chosen after seeing the
    # data. Under the null (price carries no information about CLV) the labels
    # are exchangeable, so shuffling CLV across rows and re-taking the largest
    # |t| anywhere in the grid gives the distribution of "best cell found by
    # chance". Reporting a single cell's own p would be the multiple-comparison
    # error this repo has made before.
    pop = [r for r in rows if r["clv"] is not None]
    if len(pop) < 20 or perms <= 0:
        print("\n  (grid permutation skipped — n too small or --perms 0)")
        return

    # CENTRED WITHIN THE POPULATION — and this correction is the whole test.
    #
    # Uncentred, the first run of this script reported best |t| = 7.17 at a
    # family-wise p of 0.59: nonsense on its face. The cause is that the `>= 1.8`
    # cell holds 130 of 157 rows, so its mean is the POPULATION mean whatever the
    # shuffle does — and the population mean is -4.17%, a real fact about the bot
    # that has nothing to do with price. The grid's largest |t| was therefore
    # measuring "the O/U bot has negative CLV", which is not in question, and
    # every permutation reproduced it.
    #
    # The question a floor decision actually asks is "is any PRICE BAND different
    # from the bot's own average", so the level is subtracted first and the test
    # is on deviations. Same correction scripts/odds_band_by_market.py describes
    # as "centred within market", and gotcha 47 is the same trap from the other
    # side: an odds-band effect is not an odds-band effect until the thing it is
    # nested inside has been removed.
    _mu = st.mean([r["clv"] for r in pop])

    def grid_max_t(clv_vals) -> float:
        best = 0.0
        tagged = list(zip([r["px"] for r in pop], clv_vals))
        for f in FLOORS:
            xs = [c for p, c in tagged if p >= f]
            _, _, t = _mean_ci(xs)
            if t is not None:
                best = max(best, abs(t))
        for lo, hi in zip(edges, edges[1:]):
            xs = [c for p, c in tagged if lo <= p < hi]
            _, _, t = _mean_ci(xs)
            if t is not None:
                best = max(best, abs(t))
        return best

    obs = grid_max_t([r["clv"] - _mu for r in pop])
    rng = random.Random(20260922)
    vals = [r["clv"] - _mu for r in pop]
    hits = 0
    for _ in range(perms):
        rng.shuffle(vals)
        if grid_max_t(vals) >= obs:
            hits += 1
    p = (hits + 1) / (perms + 1)
    verdict = ("NO price band differs from the bot's own average by more than "
               "chance produces — a floor cannot fix this bot"
               if p > 0.05 else
               "survives the whole grid at 5% — worth a forward test, not a config change")
    print(f"\n  GRID PERMUTATION, centred (n={len(pop)}, {perms} draws): "
          f"best |t| in grid = {obs:.2f}, family-wise p = {p:.4f}")
    print(f"  -> {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default="bot_v10_ou")
    ap.add_argument("--perms", type=int, default=2000)
    a = ap.parse_args()

    rows = load(a.bot)
    print(f"\nOU-ODDS-FLOOR-SWEEP — {a.bot} — {len(rows)} settled picks")
    print("Primary metric: de-vigged Pinnacle CLV (gotcha 8 — ROI CIs span zero here).")
    print("Price basis: COALESCE(odds_at_pick_live, odds_at_pick) — executable, not the")
    print("high-water odds_at_pick (gotcha 30).")

    for name, lo, hi in ERAS:
        sub = _era(rows, lo, hi)
        note = ""
        if name.startswith("B"):
            note = ("\n  ⚠️  OU-CALIBRATOR-DOMAIN-MISMATCH was LIVE in this window. These picks "
                    "were\n      selected by an inflated probability that maximised long prices. "
                    "They are NOT\n      a sample of the bot's rule and must never be pooled with "
                    "era A.")
        if name.startswith("C"):
            note = ("\n  ℹ️  Post-fix. The O/U Platt row is gone, so the 8% edge floor now sees "
                    "honest\n      probabilities — migration 335 predicted only 1% of picks would "
                    "clear it, and\n      the bot has emitted nothing since 2026-09-13.")
        sweep(sub, f"ERA {name}", a.perms if name.startswith("A") else 0)
        if note:
            print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
