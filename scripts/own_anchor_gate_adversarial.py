#!/usr/bin/env python3
"""Adversarial verification of the sharp-anchor gate finding (2026-09-14).

WHY THIS SCRIPT EXISTS
----------------------
`docs/OWN_SEGMENT_SIGNAL_SEARCH_2026_09_14.md` ended a day of negative results
with one survivor: own-book CLV rises in the Pinnacle prob-edge (slope +1.314,
t=4.86), the odds-decile placebo is flat (+0.007), break-even is "+6.0%" once
the tail is trimmed, and Epicbet needs a different gate (+14.6%) because its
line "responds at half the rate". The operator was about to re-gate on that.

This script attacks all four claims. Its findings are written up in
`docs/OWN_ANCHOR_GATE_VERIFICATION_2026_09_14.md`. In one line: the INFORMATION
is real and survives a stricter control than the original placebo, but the
GATE CALIBRATION and the per-book split are both produced by the price basis,
not by the books — and the volume a correct gate would admit is ~0-2 legs/day.

THE FOUR TESTS
--------------
A. placebo adequacy. The published placebo shuffles `p_sharp` within
   (market, selection, odds-decile). That inflates the regressor's variance
   3.46x (sd 0.0244 -> 0.0454), so ANY mechanical slope would be attenuated
   ~3.5x by construction and the "+1.314 vs +0.007" contrast is not the clean
   4-sigma-vs-0 it looks like. Replaced with three stricter controls that do
   not have that defect:
     * a VARIANCE-MATCHED placebo -- project p_sharp on (selection x fine
       quantile of the soft implied prob), shuffle only the RESIDUAL. Variance
       of the regressor is preserved by construction.
     * a two-regressor decomposition  y ~ p_sharp + q  (q = 1/odds_dec). The
       prob-edge specification is the restriction b_psharp = -b_q; if the
       ladder were the shared `odds_soft(T)`, b_psharp would vanish.
     * a within-(selection x odds-40ile) demeaned fit, which removes every
       between-odds-level channel non-parametrically.

B. the price basis. `odds_dec` is the last quote we OBSERVED at or before the
   decision moment, not the price the book was showing then. The writers insert
   one row per poll with no dedup-on-change, so a gap is an unobserved
   interval, not a quiet market: across a >12h gap 72pct of Coolbet 1x2 quotes
   have moved, by a mean 5.35pct. At lead 3h the median decision quote is
   another 98-134 minutes older than the nominal lead and 38pct are >4h older.
   This is ANALYSIS_GOTCHAS 44 (a stale price contaminates CLV
   proportionally). Re-fit with the decision quote restricted to one actually
   observed within N minutes of the decision moment.

C. trim sensitivity. Map the break-even crossing over no trim / 0.5 / 1 / 2 /
   5pct, trimming AND winsorising, plus a raw price-ratio cap.

D. the Epicbet split. Count EVERY book x market x lead x window cell, not the
   four that were reported; re-run on the fixtures all three books quote; and
   control for the MEASUREMENT WINDOW (minutes between the decision quote we
   hold and the close quote we hold), which differs 2-3x across books because
   Epicbet's series is 20-24 rows deep against Coolbet's 3.6-9.3.

E. the practical question: legs per day at each gate, per book, and the years
   of accrual needed to resolve a 2pp departure from break-even at that volume.

    python3 scripts/own_book_clv_universe.py --out /tmp/own_clv_panel.json.gz
    python3 scripts/own_anchor_gate_adversarial.py --panel /tmp/own_clv_panel.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from own_segment_signal_search import Z, cluster_mean  # noqa: E402

BOOKS = ("Coolbet", "Epicbet", "Unibet-Site")


# --------------------------------------------------------------------------
# multivariate OLS with cluster-robust SEs on the fixture. Hand-rolled for the
# same reason own_anchor_placebo.ols_cluster is: the sandwich is four lines and
# adding statsmodels to the engine for one regression is not worth it.
# --------------------------------------------------------------------------
def ols_cl(cols, y, cluster):
    X = np.column_stack([np.ones(len(y))] + [np.asarray(c, float) for c in cols])
    y = np.asarray(y, float)
    XtX = np.linalg.pinv(X.T @ X)
    b = XtX @ X.T @ y
    e = y - X @ b
    g = defaultdict(lambda: np.zeros(X.shape[1]))
    for i, c in enumerate(cluster):
        g[c] += X[i] * e[i]
    meat = np.zeros((X.shape[1], X.shape[1]))
    for v in g.values():
        meat += np.outer(v, v)
    ng = len(g)
    V = XtX @ meat @ XtX * (ng / (ng - 1.0) if ng > 1 else 1.0)
    return b, np.sqrt(np.diag(V)), ng


def fit_edge(rows, xk="prob_edge"):
    """Univariate y ~ edge. Returns (a, b, se_b, n, n_fixtures, break_even)."""
    if len(rows) < 40:
        return None
    x = [r[xk] for r in rows]
    y = [r["mc_clv"] for r in rows]
    b, se, ng = ols_cl([x], y, [r["match_id"] for r in rows])
    be = (-b[0] / b[1]) if b[1] > 0 else float("nan")
    return b[0], b[1], se[1], len(rows), ng, be


def show(tag, f):
    if f is None:
        print(f"  {tag:46s} -- too few rows")
        return
    a, b, sb, n, ng, be = f
    print(f"  {tag:46s} n={n:5d} fx={ng:4d}  a={a*100:+6.2f}%  "
          f"b={b:+.3f} (t={b/sb:+5.2f})  break-even={be*100:+8.2f}%")


def span(rows):
    d = sorted({r["ko_date"] for r in rows})
    return f"{d[0]}..{d[-1]} ({len(d)} match days)"


def lag(r, lead_h):
    """Minutes by which the decision quote is OLDER than the nominal decision
    moment. 0 = we observed the book at exactly the decision time."""
    return r["dec_mins_before_ko"] - lead_h * 60.0


def window(r):
    """Minutes of price time actually spanned by the CLV we measure."""
    return max(1.0, r["dec_mins_before_ko"] - r["close_mins_before_ko"])


# --------------------------------------------------------------------------
# A. controls that do not attenuate
# --------------------------------------------------------------------------
def variance_matched_placebo(rows, nq=20, seed=7):
    """Shuffle the part of p_sharp that a fine function of the soft price does
    NOT explain, and keep the part it does.

    The published placebo replaces p_sharp wholesale within an odds decile, so
    the placebo regressor carries the full within-decile variance of p_sharp as
    pure noise ON TOP of the mechanical -q term. Classical errors-in-variables
    then shrinks the fitted slope by Var(signal)/Var(signal+noise) whether or
    not a mechanical channel exists. Shuffling only the residual leaves
    Var(edge) unchanged (the residual is orthogonal to the stratum by
    construction) while destroying exactly the fixture-specific content."""
    q = np.array([1.0 / r["odds_dec"] for r in rows])
    ps = np.array([r["p_sharp"] for r in rows])
    cuts = np.quantile(q, np.linspace(0, 1, nq + 1))[1:-1]
    grp = defaultdict(list)
    for i, r in enumerate(rows):
        grp[(r["selection"], int(np.searchsorted(cuts, q[i])))].append(i)
    out = ps.copy()
    rnd = random.Random(seed)
    for idx in grp.values():
        if len(idx) < 8:
            continue
        mu = ps[idx].mean()
        res = (ps[idx] - mu).tolist()
        rnd.shuffle(res)
        for j, i in enumerate(idx):
            out[i] = mu + res[j]
    return [dict(r, prob_edge=float(out[i] - q[i])) for i, r in enumerate(rows)]


def test_a(rows):
    print("\n" + "=" * 78)
    print("A. IS THE PLACEBO AN ADEQUATE CONTROL, AND DOES THE LADDER SURVIVE A "
          "BETTER ONE?")
    print("=" * 78)
    q = np.array([1.0 / r["odds_dec"] for r in rows])
    ps = np.array([r["p_sharp"] for r in rows])
    e = np.array([r["prob_edge"] for r in rows])
    y = [r["mc_clv"] for r in rows]
    cl = [r["match_id"] for r in rows]

    try:
        from own_anchor_placebo import make_placebo
        pub = make_placebo(rows, 20260914)
        pe = np.array([r["edge"] for r in pub])
        print(f"  sd(edge) REAL            = {e.std():.5f}")
        print(f"  sd(edge) PUBLISHED plac. = {pe.std():.5f}   "
              f"variance ratio {pe.var()/e.var():.2f}x  <-- attenuates any "
              f"mechanical slope by this factor")
        show("PUBLISHED placebo (odds-decile shuffle)",
             fit_edge([dict(r, prob_edge=r["edge"]) for r in pub]))
    except Exception as exc:                                  # pragma: no cover
        print(f"  (published placebo unavailable: {exc})")

    show("REAL — Pinnacle Shin de-vig", fit_edge(rows))
    vm = variance_matched_placebo(rows)
    vme = np.array([r["prob_edge"] for r in vm])
    print(f"  sd(edge) VARIANCE-MATCHED placebo = {vme.std():.5f}  "
          f"(ratio {vme.var()/e.var():.2f}x)")
    show("VARIANCE-MATCHED placebo", fit_edge(vm))

    b, se, ng = ols_cl([ps, q], y, cl)
    print(f"  decomposition  y ~ p_sharp + q :  b_p_sharp={b[1]:+.3f} "
          f"(t={b[1]/se[1]:+.2f})   b_q={b[2]:+.3f} (t={b[2]/se[2]:+.2f})   "
          f"fx={ng}")
    print("     the prob-edge form imposes b_p_sharp = -b_q; if the ladder were "
          "the shared odds_soft(T)\n     alone, b_p_sharp would be ~0.")

    # non-parametric: demean inside (selection x 40-quantile of q)
    cuts = np.quantile(q, np.linspace(0, 1, 41))[1:-1]
    grp = defaultdict(list)
    for i, r in enumerate(rows):
        grp[(r["selection"], int(np.searchsorted(cuts, q[i])))].append(i)
    yd = np.array(y, float).copy()
    ed = e.copy()
    for idx in grp.values():
        if len(idx) < 5:
            continue
        yd[idx] -= np.array(y, float)[idx].mean()
        ed[idx] -= e[idx].mean()
    b, se, ng = ols_cl([ed], yd, cl)
    print(f"  within (selection x odds-40ile) demeaned: b={b[1]:+.3f} "
          f"(t={b[1]/se[1]:+.2f})")


# --------------------------------------------------------------------------
# B. the price basis
# --------------------------------------------------------------------------
def test_b(panel, rows, lead, market):
    print("\n" + "=" * 78)
    print("B. THE DECISION PRICE — is it a price we could have taken at T?")
    print("=" * 78)
    for bk in BOOKS:
        rs = [r for r in rows if r["book"] == bk]
        if not rs:
            continue
        lg = np.array([lag(r, lead) for r in rs])
        print(f"  {bk:12s} n={len(rs):5d}  decision-quote lag: median "
              f"{np.median(lg):6.0f}m  p90 {np.percentile(lg, 90):7.0f}m  "
              f"frac >60m {np.mean(lg > 60):.2f}  frac >240m {np.mean(lg > 240):.2f}")
    print("\n  slope and break-even, restricted to a decision quote actually "
          "OBSERVED within N minutes:")
    for lim in (10, 30, 60, 120, 240, 1e9):
        tag = "any lag" if lim > 1e8 else f"lag <= {lim:g} min"
        show(tag, fit_edge([r for r in rows if lag(r, lead) <= lim]))
    print("\n  same, by era (the panel straddles DIRECT-BOOK-ANCHORS-2026-09-11):")
    for lo, hi, t in (("2026-01-01", "2026-09-11", "09-07..09-10"),
                      ("2026-09-11", "2026-12-31", "09-11..09-13")):
        sub = [r for r in rows if lo <= r["ko_date"] < hi]
        show(f"{t}, any lag", fit_edge(sub))
        show(f"{t}, lag <= 60 min",
             fit_edge([r for r in sub if lag(r, lead) <= 60]))
    print("\n  slope by match day (a stable instrument should not move):")
    for d in sorted({r["ko_date"] for r in rows}):
        rs = [r for r in rows if r["ko_date"] == d]
        f = fit_edge(rs)
        if f is None:
            continue
        a, b, sb, n, ng, be = f
        print(f"    {d}  n={n:5d} fx={ng:4d}  median lag "
              f"{np.median([lag(r, lead) for r in rs]):5.0f}m  b={b:+.3f} "
              f"(t={b/sb:+5.2f})")


# --------------------------------------------------------------------------
# C. trim sensitivity
# --------------------------------------------------------------------------
def _trim(rows, f):
    ys = np.array([r["mc_clv"] for r in rows])
    lo, hi = np.quantile(ys, f), np.quantile(ys, 1 - f)
    return [r for r in rows if lo <= r["mc_clv"] <= hi]


def _winsor(rows, f):
    ys = np.array([r["mc_clv"] for r in rows])
    lo, hi = np.quantile(ys, f), np.quantile(ys, 1 - f)
    return [dict(r, mc_clv=min(max(r["mc_clv"], lo), hi)) for r in rows]


def test_c(rows):
    print("\n" + "=" * 78)
    print("C. HOW ARBITRARY IS '+6.0%'? — full trim / winsor sensitivity")
    print("=" * 78)
    show("no trim", fit_edge(rows))
    for f in (0.005, 0.01, 0.02, 0.05):
        show(f"TRIM  {f*100:.1f}%/side", fit_edge(_trim(rows, f)))
    for f in (0.005, 0.01, 0.02, 0.05):
        show(f"WINSOR {f*100:.1f}%/side", fit_edge(_winsor(rows, f)))
    for cap in (0.20, 0.30, 0.50):
        show(f"|raw clv| <= {cap*100:.0f}% (price-ratio cap)",
             fit_edge([r for r in rows if abs(r["clv"]) <= cap]))


# --------------------------------------------------------------------------
# D. the Epicbet split
# --------------------------------------------------------------------------
def test_d(panel, market_leads):
    print("\n" + "=" * 78)
    print("D. THE EPICBET SPLIT — every cell, then the window control")
    print("=" * 78)
    print("  EVERY market x lead x window cell (not the four that were reported):")
    print(f"    {'market':14s} {'lead':>5s} {'window':>13s} {'n':>6s} {'fx':>5s} "
          f"{'b_other':>8s} {'d_Epic':>8s} {'t':>6s}")
    cells = sig = 0
    for mk in ("1x2", "over_under_25"):
        for lead in (1.0, 3.0, 6.0, 12.0, 24.0):
            for wname, wlo in (("09-07..09-13", "2026-09-07"),
                               ("09-10..09-13", "2026-09-10")):
                rs = [r for r in panel
                      if r["lead_h"] == lead and r["market"] == mk
                      and r.get("prob_edge") is not None and r["ko_date"] >= wlo]
                ep = np.array([1.0 if r["book"] == "Epicbet" else 0.0 for r in rs])
                if len(rs) < 200 or ep.sum() < 50 or (1 - ep).sum() < 50:
                    continue
                e = np.array([r["prob_edge"] for r in rs])
                b, se, ng = ols_cl([e, ep, e * ep], [r["mc_clv"] for r in rs],
                                   [r["match_id"] for r in rs])
                cells += 1
                t = b[3] / se[3]
                sig += abs(t) > 1.96
                print(f"    {mk:14s} {lead:5.0f} {wname:>13s} {len(rs):6d} "
                      f"{ng:5d} {b[1]:+8.3f} {b[3]:+8.3f} {t:+6.2f}")
    print(f"    cells tested: {cells}   |t| > 1.96: {sig}   "
          f"(these overlap heavily; they are not {cells} independent tests)")

    print("\n  restricted to fixtures ALL THREE books quote (controls fixture mix):")
    for lead in (1.0, 3.0, 6.0, 12.0):
        rows = [r for r in panel if r["lead_h"] == lead and r["market"] == "1x2"
                and r.get("prob_edge") is not None]
        fxb = defaultdict(set)
        for r in rows:
            fxb[r["match_id"]].add(r["book"])
        common = {m for m, b in fxb.items() if len(b) == 3}
        rs = [r for r in rows if r["match_id"] in common]
        if len(rs) < 100:
            print(f"    lead {lead:4.0f}h  n={len(rs)} too few")
            continue
        ep = np.array([1.0 if r["book"] == "Epicbet" else 0.0 for r in rs])
        e = np.array([r["prob_edge"] for r in rs])
        b, se, ng = ols_cl([e, ep, e * ep], [r["mc_clv"] for r in rs],
                           [r["match_id"] for r in rs])
        print(f"    lead {lead:4.0f}h  fixtures quoted by all 3 = {len(common):4d} "
              f"of {len(fxb):4d}  n={len(rs):4d}  d_Epic={b[3]:+.3f} "
              f"(t={b[3]/se[3]:+.2f})")

    print("\n  controlling for the MEASUREMENT WINDOW (dec->close minutes),"
          " which differs 2-3x by book\n  because the series densities do:")
    for mk, lead in market_leads:
        rs = [r for r in panel if r["lead_h"] == lead and r["market"] == mk
              and r.get("prob_edge") is not None]
        if len(rs) < 200:
            continue
        lw = np.log(np.array([window(r) for r in rs]))
        lw = lw - lw.mean()
        e = np.array([r["prob_edge"] for r in rs])
        ep = np.array([1.0 if r["book"] == "Epicbet" else 0.0 for r in rs])
        y = [r["mc_clv"] for r in rs]
        cl = [r["match_id"] for r in rs]
        b0, s0, _ = ols_cl([e, ep, e * ep], y, cl)
        b1, s1, _ = ols_cl([e, ep, e * ep, lw, e * lw, ep * lw], y, cl)
        med = {bk: np.median([window(r) for r in rs if r["book"] == bk])
               for bk in BOOKS}
        print(f"    {mk:14s} lead {lead:4.0f}h  d_Epic raw={b0[3]:+.3f} "
              f"(t={b0[3]/s0[3]:+5.2f})  ->  window-controlled={b1[3]:+.3f} "
              f"(t={b1[3]/s1[3]:+5.2f})   edge x log(window)={b1[5]:+.3f} "
              f"(t={b1[5]/s1[5]:+5.2f})   median window CB={med['Coolbet']:.0f}m "
              f"EP={med['Epicbet']:.0f}m UB={med['Unibet-Site']:.0f}m")


# --------------------------------------------------------------------------
# E. the practical question
# --------------------------------------------------------------------------
def test_e(panel, lead, odds_cap=2.50, coverage=0.60):
    print("\n" + "=" * 78)
    print("E. HOW MANY PICKS A DAY WOULD THE GATE ACTUALLY PRODUCE?")
    print("=" * 78)
    for mk in ("1x2", "over_under_25"):
        rows = [r for r in panel if r["lead_h"] == lead and r["market"] == mk
                and r.get("prob_edge") is not None]
        if not rows:
            continue
        nd = len({r["ko_date"] for r in rows})
        print(f"\n  market {mk}   {span(rows)}   Pinnacle-anchored legs {len(rows)}")
        print(f"    {'gate':>7s} | " + " | ".join(f"{b:>20s}" for b in BOOKS)
              + " |          ALL BOOKS")
        for g in (0.02, 0.04, 0.06, 0.08, 0.10, 0.146):
            cells = []
            for bk in list(BOOKS) + [None]:
                s = [r for r in rows if r["prob_edge"] >= g
                     and (bk is None or r["book"] == bk)]
                c = [r for r in s if r["odds_dec"] <= odds_cap]
                cells.append(f"{len(s):3d} = {len(s)/nd:4.1f}/d ({len(c)/nd:4.1f})")
            print(f"    {g*100:6.1f}% | " + " | ".join(f"{c:>20s}" for c in cells))
        print("    (n over the whole panel = legs/day, and in brackets legs/day "
              f"surviving the live odds cap <= {odds_cap})")

    rows = [r for r in panel if r["lead_h"] == lead and r["market"] == "1x2"
            and r.get("prob_edge") is not None]
    nd = len({r["ko_date"] for r in rows})
    print("\n  and what it would take to RESOLVE a 2pp departure from break-even "
          "at that volume:")
    for g in (0.02, 0.04, 0.06, 0.08, 0.146):
        s = [r for r in rows if r["prob_edge"] >= g]
        if len(s) < 3:
            continue
        sd = np.std([r["mc_clv"] for r in s], ddof=1)
        need = ((1.96 + 0.84) * sd / 0.02) ** 2
        per = len(s) / nd
        print(f"    gate {g*100:5.1f}%: n={len(s):4d}  {per:5.2f} legs/d  "
              f"sd={sd*100:5.2f}pp  n needed={need:7.0f}  -> {need/per/365:6.1f} yr, "
              f"or {need/(per*coverage)/365:6.1f} yr at {coverage*100:.0f}% "
              f"own-book CLV coverage")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/tmp/own_clv_panel.json.gz")
    ap.add_argument("--lead", type=float, default=3.0)
    ap.add_argument("--market", default="1x2")
    a = ap.parse_args()

    with gzip.open(a.panel, "rt") as fh:
        panel = json.load(fh)
    rows = [r for r in panel if r["lead_h"] == a.lead and r["market"] == a.market
            and r.get("prob_edge") is not None]
    print(f"ADVERSARIAL VERIFICATION — market={a.market} lead={a.lead}h")
    print(f"DATE SPAN {span(rows)}   legs={len(rows)}   "
          f"fixtures(clusters)={len({r['match_id'] for r in rows})}")
    print("target: margin-corrected own-book CLV, break-even 0.00%; "
          "SEs clustered on match_id throughout")

    test_a(rows)
    test_b(panel, rows, a.lead, a.market)
    test_c(rows)
    test_d(panel, [("1x2", 3.0), ("1x2", 6.0), ("1x2", 12.0),
                   ("over_under_25", 3.0), ("over_under_25", 6.0),
                   ("over_under_25", 12.0)])
    test_e(panel, a.lead)

    print("\n" + "=" * 78)
    print("F. THE SAME TESTS AT A FRESH PRICE — the only rows a gate could act on")
    print("=" * 78)
    fresh = [r for r in rows if lag(r, a.lead) <= 60]
    show("REAL, lag <= 60 min", fit_edge(fresh))
    show("VARIANCE-MATCHED placebo, lag <= 60 min",
         fit_edge(variance_matched_placebo(fresh)))
    for bk in BOOKS:
        for tag, base in (("all rows", rows), ("lag<=60m", fresh)):
            sub = _winsor([r for r in base if r["book"] == bk], 0.01)
            show(f"{bk} ({tag}, 1% winsor)", fit_edge(sub) if len(sub) >= 60 else None)
    for tag, base in (("ALL rows", rows), ("lag<=60m", fresh)):
        sel = [r for r in base if r["prob_edge"] >= 0.02 and r["odds_dec"] <= 2.50]
        if len(sel) < 3:
            continue
        outs = []
        for f in (0.0, 0.01, 0.025):
            w = _winsor(sel, f) if f else sel
            mu, se, n, ng = cluster_mean(w)
            outs.append(f"{mu*100:+.2f}%")
        print(f"  realised at the LIVE gate (edge>=2%, odds<=2.50), {tag:9s} "
              f"n={len(sel):4d}: " + " -> ".join(outs) + "  (untrimmed / 1% / 2.5% winsor)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
