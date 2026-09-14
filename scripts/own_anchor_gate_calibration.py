#!/usr/bin/env python3
"""Where does the sharp-anchor gate actually break even on own-book CLV?

The companion to `own_anchor_placebo.py`, which established that the ladder is
information (odds-decile-matched placebo slope +0.007, t=0.22) and not the
arithmetic of sharing `odds_soft(T)` between predictor and target.

This script answers the operational question instead: given a gate
`prob_edge >= g` (and optionally an odds cap), what is the realised
margin-corrected own-book CLV, how many legs per day does it admit, and is the
answer stable under

  * tail trimming        -- ANALYSIS_GOTCHAS 9, odds outliers dominate any
                            unguarded search, and the target here is a price
                            RATIO with a long right tail;
  * a time-ordered split -- discovery on the early days, confirmation on the late;
  * the book dimension   -- compared only at a MATCHED gate, because the
                            withdrawn "junk beats real" claim came from
                            comparing two arms that had selected different
                            populations.

Every table prints n and the date span beside the estimate.

    python3 scripts/own_anchor_gate_calibration.py --panel /tmp/own_clv_panel.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from own_segment_signal_search import Z, cluster_mean, required_n, sd_of  # noqa: E402

GATES = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]


def winsorise(rows, frac):
    if not frac:
        return rows
    ys = sorted(r["mc_clv"] for r in rows)
    k = max(1, int(len(ys) * frac))
    lo, hi = ys[k], ys[-k - 1]
    return [dict(r, mc_clv=min(max(r["mc_clv"], lo), hi)) for r in rows]


def line(tag, rows, n_days, split):
    if len(rows) < 2:
        print(f"  {tag:34s} n={len(rows):5d}  -- too few")
        return
    mu, se, n, ng = cluster_mean(rows)
    tr = [r for r in rows if r["ko_date"] < split]
    te = [r for r in rows if r["ko_date"] >= split]
    mtr = sum(r["mc_clv"] for r in tr) / len(tr) if tr else float("nan")
    mte = sum(r["mc_clv"] for r in te) / len(te) if te else float("nan")
    perday = defaultdict(list)
    for r in rows:
        perday[r["ko_date"]].append(r["mc_clv"])
    fp = sum(1 for v in perday.values() if len(v) >= 5 and sum(v) / len(v) > 0)
    fn = sum(1 for v in perday.values() if len(v) >= 5)
    print(f"  {tag:34s} n={n:5d} fx={ng:4d} {n/n_days:6.1f}/day  "
          f"mc={mu*100:+7.2f}% [{(mu-Z*se)*100:+7.2f},{(mu+Z*se)*100:+7.2f}]  "
          f"tr={mtr*100:+7.2f}% te={mte*100:+7.2f}%  folds+ {fp}/{fn}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/tmp/own_clv_panel.json.gz")
    ap.add_argument("--lead", type=float, default=3.0)
    ap.add_argument("--market", default="1x2")
    ap.add_argument("--split", default="2026-09-11")
    a = ap.parse_args()

    with gzip.open(a.panel, "rt") as fh:
        panel = json.load(fh)
    rows = [r for r in panel if r["lead_h"] == a.lead and r["market"] == a.market
            and r.get("prob_edge") is not None]
    days = sorted({r["ko_date"] for r in rows})
    nd = len(days)
    print(f"GATE CALIBRATION — market={a.market} lead={a.lead}h  "
          f"DATE SPAN {days[0]}..{days[-1]} ({nd} match days)")
    print(f"legs with a Pinnacle anchor: {len(rows)}   "
          f"target: margin-corrected own-book CLV, break-even 0.00pct\n")

    for trim in (0.0, 0.01, 0.025):
        print(f"--- tail winsorisation: {trim*100:.1f}pct each side")
        w = winsorise(rows, trim)
        for g in GATES:
            line(f"prob_edge >= {g*100:.0f}pct",
                 [r for r in w if r["prob_edge"] >= g], nd, a.split)
        print()

    print("--- the LIVE instrument's configuration "
          "(bot_trigger_1x2_sharp_tight_v1: edge >= 2pct, odds <= 2.50)")
    for trim in (0.0, 0.01, 0.025):
        sel = [r for r in winsorise(rows, trim)
               if r["prob_edge"] >= 0.02 and r["odds_dec"] <= 2.50]
        line(f"live gate, trim {trim*100:.1f}pct", sel, nd, a.split)
    print("--- same gate WITHOUT the odds cap")
    for trim in (0.0, 0.01, 0.025):
        sel = [r for r in winsorise(rows, trim) if r["prob_edge"] >= 0.02]
        line(f"edge>=2pct any odds, trim {trim*100:.1f}pct", sel, nd, a.split)

    print("\n--- odds cap interaction at a MATCHED gate (edge >= 2pct)")
    for lo, hi in [(1.0, 2.0), (2.0, 2.5), (2.5, 3.5), (3.5, 5.0), (5.0, 1e9)]:
        sel = [r for r in rows if r["prob_edge"] >= 0.02
               and lo <= r["odds_dec"] < hi]
        line(f"odds [{lo:.1f},{hi:.1f})", sel, nd, a.split)

    print("\n--- book comparison at a MATCHED gate (edge >= 2pct, no odds cap)")
    for bk in ("Coolbet", "Epicbet", "Unibet-Site"):
        sel = [r for r in rows if r["prob_edge"] >= 0.02 and r["book"] == bk]
        line(bk, sel, nd, a.split)
    print("--- and at the baseline (every leg) for the same books")
    for bk in ("Coolbet", "Epicbet", "Unibet-Site"):
        sel = [r for r in rows if r["book"] == bk]
        line(bk + " (all legs)", sel, nd, a.split)

    print("\n--- selection split at a MATCHED gate (edge >= 2pct) "
          "-- ANALYSIS_GOTCHAS 57 asks whether draws are exploitable at a soft book")
    for s in sorted({r["selection"] for r in rows}):
        sel = [r for r in rows if r["prob_edge"] >= 0.02 and r["selection"] == s]
        line(f"selection {s}", sel, nd, a.split)

    print("\nPOWER at the gates that matter")
    for g in (0.02, 0.03, 0.05):
        sel = [r for r in rows if r["prob_edge"] >= g]
        if len(sel) < 2:
            continue
        mu, se, n, ng = cluster_mean(sel)
        sd = sd_of(sel)
        need = required_n(0.02, sd)     # resolve a 2pp departure from break-even
        print(f"  gate {g*100:.0f}pct: n={n} ({n/nd:.1f}/day), sd={sd*100:.2f}pp, "
              f"n needed to resolve a 2pp departure from 0 = {need:.0f} "
              f"-> {need/(n/nd):.0f} days at this volume")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
