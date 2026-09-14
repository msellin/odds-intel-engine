#!/usr/bin/env python3
"""Is the sharp anchor's own-book CLV ladder INFORMATION, or arithmetic?

THE PROBLEM THIS EXISTS TO SOLVE
--------------------------------
On the unselected own-book panel, margin-corrected own-book CLV rises
monotonically in the sharp prob-edge (-15.8pct at edge <= -10pct up to ~0pct at
edge +2..3pct). That looks like the sharp anchor predicting where our own book
will move. It may be nothing of the kind, because

    prob_edge = p_shin(Pinnacle, T) - 1 / odds_soft(T)
    mc_clv    = [odds_soft(T) / odds_soft(close)] / (1 + m) - 1

share `odds_soft(T)`. A leg quoted unusually LONG at T gets a high prob_edge
AND, by the book's own mean reversion toward its close, tends to shorten — which
IS positive CLV. The ladder would then be an identity, not a discovery. This is
the same family of error as ANALYSIS_GOTCHAS 52 (best-of-books selects the most
mispriced book and calls its error an edge).

THREE ARMS, IDENTICAL MACHINERY (the gate must be matched before comparing —
the sweep's original "junk beats real" claim was withdrawn for exactly this):

  REAL     p_anchor = Shin de-vig of the Pinnacle complement at T
  PLACEBO  p_anchor = a REAL Pinnacle de-vig, but taken from a DIFFERENT fixture
           drawn within the same (market, selection, odds_dec decile) stratum.
           The mechanical dependence on odds_dec survives the shuffle; only
           Pinnacle's fixture-specific information is destroyed. If the ladder
           survives here it was arithmetic.
  SOFT     p_anchor = Shin de-vig of ANOTHER self-scraped soft book's complement
           at T. Tests whether the anchor has to be sharp at all, or whether any
           second opinion does the same work (which would make the instrument
           much cheaper, and would also mean Pinnacle is not the moat).

    python3 scripts/own_anchor_placebo.py --panel /tmp/own_clv_panel.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from own_segment_signal_search import Z, cluster_mean  # noqa: E402

BANDS = [(-1, -0.10), (-0.10, -0.05), (-0.05, -0.02), (-0.02, 0.0),
         (0.0, 0.01), (0.01, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 1)]


def ols_cluster(rows, xk, yk="mc_clv", cluster="match_id"):
    """y = a + b x with cluster-robust SEs on `cluster`. Returns (a, b, se_a,
    se_b, n, n_clusters). Hand-rolled: numpy/statsmodels are not needed for a
    univariate fit and the cluster meat matrix is four lines."""
    pts = [(r[xk], r[yk], r[cluster]) for r in rows
           if r.get(xk) is not None and r.get(yk) is not None]
    n = len(pts)
    if n < 10:
        return (float("nan"),) * 4 + (n, 0)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return (float("nan"),) * 4 + (n, 0)
    b = sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx
    a = my - b * mx
    # cluster-robust sandwich for the 2x2 design matrix
    g_s0, g_s1 = defaultdict(float), defaultdict(float)
    for x, y, c in pts:
        e = y - a - b * x
        g_s0[c] += e
        g_s1[c] += e * x
    meat00 = sum(v * v for v in g_s0.values())
    meat01 = sum(g_s0[c] * g_s1[c] for c in g_s0)
    meat11 = sum(v * v for v in g_s1.values())
    sx = sum(p[0] for p in pts)
    sxx_raw = sum(p[0] ** 2 for p in pts)
    det = n * sxx_raw - sx * sx
    if det == 0:
        return (float("nan"),) * 4 + (n, 0)
    b00, b01, b11 = sxx_raw / det, -sx / det, n / det
    v_a = (b00 * b00 * meat00 + 2 * b00 * b01 * meat01 + b01 * b01 * meat11)
    v_b = (b01 * b01 * meat00 + 2 * b01 * b11 * meat01 + b11 * b11 * meat11)
    ng = len(g_s0)
    corr = ng / (ng - 1.0) if ng > 1 else 1.0
    return a, b, (v_a * corr) ** 0.5, (v_b * corr) ** 0.5, n, ng


def shin(odds_list):
    q = [1.0 / o for o in odds_list]
    t = sum(q)
    z = 0.0
    for _ in range(80):
        zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z))
              for qi in q]
        s_ = sum(zs)
        z = z + (s_ - 1) * 0.5 if s_ > 1 else z - (1 - s_) * 0.5
        z = max(0.0, min(0.5, z))
    zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z))
          for qi in q]
    tot = sum(zs)
    return [x / tot for x in zs]


def decile(v, cuts):
    for i, c in enumerate(cuts):
        if v < c:
            return i
    return len(cuts)


def make_placebo(rows, seed):
    """Shuffle p_sharp across fixtures WITHIN (market, selection, odds decile).

    Stratifying on the decision odds is the whole point: it keeps every
    mechanical route from odds_dec to the target intact, so a surviving ladder
    can only be arithmetic."""
    rnd = random.Random(seed)
    src = [r for r in rows if r.get("p_sharp") is not None]
    strata = defaultdict(list)
    for r in src:
        strata[(r["market"], r["selection"])].append(r["odds_dec"])
    cuts = {}
    for k, v in strata.items():
        v = sorted(v)
        cuts[k] = [v[int(len(v) * q / 10)] for q in range(1, 10)]
    pool = defaultdict(list)
    for r in src:
        k = (r["market"], r["selection"])
        pool[(k, decile(r["odds_dec"], cuts[k]))].append(r["p_sharp"])
    for v in pool.values():
        rnd.shuffle(v)
    idx = defaultdict(int)
    out = []
    for r in src:
        k = (r["market"], r["selection"])
        key = (k, decile(r["odds_dec"], cuts[k]))
        p = pool[key][idx[key] % len(pool[key])]
        idx[key] += 1
        q = dict(r)
        q["p_anchor"] = p
        q["edge"] = p - 1.0 / r["odds_dec"]
        out.append(q)
    return out


def make_soft(panel, lead):
    """Anchor = another self-scraped soft book's de-vigged complement at T."""
    # de-vigged decision price of every book on every (fixture, market)
    dev = defaultdict(dict)
    grp = defaultdict(list)
    for r in panel:
        if r["lead_h"] != lead:
            continue
        grp[(r["match_id"], r["market"], r["book"])].append(r)
    for k, legs in grp.items():
        ps = shin([l["odds_dec"] for l in legs])
        for l, p in zip(legs, ps):
            dev[(k[0], k[1], l["selection"])][k[2]] = p
    out = []
    for r in panel:
        if r["lead_h"] != lead:
            continue
        others = {b: p for b, p in
                  dev[(r["match_id"], r["market"], r["selection"])].items()
                  if b != r["book"]}
        if not others:
            continue
        p = sum(others.values()) / len(others)
        q = dict(r)
        q["p_anchor"] = p
        q["edge"] = p - 1.0 / r["odds_dec"]
        out.append(q)
    return out


def ladder(rows, title, split):
    print(f"\n=== {title}   n={len(rows)}")
    print("   anchor edge band        n    fx     mc_clv            95pct CI"
          "        train    test")
    for lo, hi in BANDS:
        sub = [r for r in rows if lo <= r["edge"] < hi]
        if len(sub) < 25:
            print(f"   [{lo:+.2f},{hi:+.2f})  n={len(sub):5d}   -- below 25, not reported")
            continue
        mu, se, n, ng = cluster_mean(sub)
        tr = [r for r in sub if r["ko_date"] < split]
        te = [r for r in sub if r["ko_date"] >= split]
        mtr = sum(r["mc_clv"] for r in tr) / len(tr) if tr else float("nan")
        mte = sum(r["mc_clv"] for r in te) / len(te) if te else float("nan")
        print(f"   [{lo:+.2f},{hi:+.2f}) {n:6d} {ng:5d}  {mu*100:+7.2f}%  "
              f"[{(mu-Z*se)*100:+7.2f},{(mu+Z*se)*100:+7.2f}]  "
              f"{mtr*100:+7.2f}% {mte*100:+7.2f}%")
    a, b, sa, sb, n, ng = ols_cluster(rows, "edge")
    cross = (-a / b) if b else float("nan")
    print(f"   SLOPE  mc_clv = {a*100:+.2f}pct + {b:+.3f} x edge   "
          f"(se_b={sb:.3f}, t={b/sb if sb else float('nan'):+.2f}, "
          f"n={n}, fixtures={ng})")
    print(f"   zero crossing at anchor edge = {cross*100:+.2f}pct")
    return a, b, sb, cross


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/tmp/own_clv_panel.json.gz")
    ap.add_argument("--lead", type=float, default=3.0)
    ap.add_argument("--market", default="1x2")
    ap.add_argument("--split", default="2026-09-11")
    ap.add_argument("--seed", type=int, default=20260914)
    a = ap.parse_args()

    with gzip.open(a.panel, "rt") as fh:
        panel = json.load(fh)
    rows = [r for r in panel if r["lead_h"] == a.lead and r["market"] == a.market]
    days = sorted({r["ko_date"] for r in rows})
    print(f"ANCHOR PLACEBO — market={a.market} lead={a.lead}h  "
          f"DATE SPAN {days[0]}..{days[-1]} ({len(days)} match days)")
    print("target = margin-corrected own-book CLV; break-even 0.00pct")

    real = [dict(r, p_anchor=r["p_sharp"], edge=r["prob_edge"])
            for r in rows if r.get("prob_edge") is not None]
    ladder(real, "REAL — Pinnacle Shin de-vig", a.split)
    ladder(make_placebo(rows, a.seed),
           "PLACEBO — Pinnacle de-vig from a DIFFERENT fixture, "
           "odds-decile matched", a.split)
    soft = [r for r in make_soft(panel, a.lead) if r["market"] == a.market]
    ladder(soft, "SOFT — other self-scraped books' de-vig as anchor", a.split)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
