#!/usr/bin/env python3
"""OWN segment + signal search on margin-corrected own-book CLV.

TARGET (the only definition of "profitable" in the OWN brief):

    mc_clv = (1 + clv) / (1 + m) - 1

with `clv` a RAW price ratio against the close AT THE BOOK BET AT, and `m` that
book's OWN overround on that fixture/market computed PER ROW. Break-even is 0.
A randomly chosen leg sits at about -7pct because that is -m/(1+m); the whole
question is whether any conditioning moves a cell above zero.

WHY ROI IS NOT REPORTED AS A VERDICT. Per-bet return sd is ~1.3, so confirming a
true +3pct ROI at 80pct power needs ~15,600 settled bets (ANALYSIS_GOTCHAS 8).
mc_clv here has sd ~0.06-0.12, so 1,000 legs resolve a 1pp difference. Every
verdict below is on mc_clv.

MULTIPLE COMPARISONS ARE THE MAIN RISK AND ARE TREATED AS SUCH.
This is a search over hundreds of cells. A junk anchor once produced CIs
excluding zero on 23pct of cells (ANALYSIS_GOTCHAS 52/60). So the script:
  * counts and prints every cell tested,
  * clusters standard errors on `match_id` (three 1x2 legs of one fixture are
    mechanically dependent: one shortening means another drifting),
  * splits TIME-ORDERED into train (discovery) and test (confirmation) and
    prints both, never one,
  * applies Benjamini-Hochberg across the whole scan,
  * reports per-day fold signs,
  * computes the n needed to detect what it just measured,
  * and runs a GATE-MATCHED PLACEBO (a junk anchor scored through the identical
    selection machinery) so "cells survive" can be compared against "cells
    survive under noise".

    python3 scripts/own_segment_signal_search.py --panel /tmp/own_clv_panel.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
from collections import defaultdict

Z = 1.959963985

# ---------------------------------------------------------------- statistics


def cluster_mean(rows, key="mc_clv", cluster="match_id"):
    """Mean of `key` with a cluster-robust SE on `cluster`.

    The three legs of one 1x2 fixture are not independent draws: the book's
    overround is shared and a shortening on one side is mechanically a drift on
    another. Treating them as independent understates the SE by up to sqrt(3)."""
    ys = [r[key] for r in rows if r.get(key) is not None]
    n = len(ys)
    if n < 2:
        return (float("nan"), float("nan"), n, 0)
    mu = sum(ys) / n
    g = defaultdict(float)
    for r in rows:
        if r.get(key) is None:
            continue
        g[r[cluster]] += r[key] - mu
    var = sum(v * v for v in g.values()) / (n * n)
    ng = len(g)
    if ng > 1:                       # small-cluster correction
        var *= ng / (ng - 1.0)
    return (mu, math.sqrt(var), n, ng)


def sd_of(rows, key="mc_clv"):
    ys = [r[key] for r in rows if r.get(key) is not None]
    if len(ys) < 2:
        return float("nan")
    m = sum(ys) / len(ys)
    return math.sqrt(sum((y - m) ** 2 for y in ys) / (len(ys) - 1))


def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_sided_p(t):
    if t != t:
        return float("nan")
    return 2.0 * (1.0 - norm_cdf(abs(t)))


def bh_fdr(pvals, q=0.10):
    """Benjamini-Hochberg. Returns the p threshold declared significant."""
    ps = sorted(p for p in pvals if p == p)
    m = len(ps)
    thr = 0.0
    for i, p in enumerate(ps, start=1):
        if p <= q * i / m:
            thr = p
    return thr, m


def required_n(effect, sd, power_z=0.84):
    """n needed for a one-sample test of `effect` at 80pct power, 5pct level."""
    if not effect or effect != effect or sd != sd or effect == 0:
        return float("inf")
    return ((Z + power_z) * sd / abs(effect)) ** 2


# ---------------------------------------------------------------- labelling

ODDS_BANDS = [(1.0, 1.5), (1.5, 2.0), (2.0, 2.75), (2.75, 4.0), (4.0, 1e9)]
EDGE_BANDS = [(-1e9, -0.02), (-0.02, 0.0), (0.0, 0.02), (0.02, 0.05), (0.05, 1e9)]


def _band(v, bands, fmt="{:.2f}"):
    if v is None:
        return None
    for lo, hi in bands:
        if lo <= v < hi:
            return f"[{fmt.format(lo)},{fmt.format(hi)})" if hi < 1e8 else f">={fmt.format(lo)}"
    return None


def _has(s, *words):
    if not s:
        return False
    low = s.lower()
    return any(w in low for w in words)


def label(r):
    """Every categorical dimension this search scans, as {dimension: level}.

    Only fields knowable BEFORE the decision moment appear here. `odds_dec` and
    the de-vigged decision price are decision-time observables; nothing derived
    from the close, the score, or any post-kickoff row is used."""
    d = {}
    d["book"] = r["book"]
    d["market"] = r["market"]
    d["selection"] = r["selection"]
    d["odds_band"] = _band(r["odds_dec"], ODDS_BANDS)
    d["is_favourite"] = ("fav" if r["p_dec"] is not None and r["p_dec"] >= 0.5
                         else "dog")
    d["league_tier"] = f"tier{r['tier']}" if r["tier"] is not None else "tier_null"
    d["country"] = r["country"] or "country_null"
    d["ko_hour_bucket"] = ("00-08" if r["ko_hour"] < 8 else
                           "08-12" if r["ko_hour"] < 12 else
                           "12-16" if r["ko_hour"] < 16 else
                           "16-20" if r["ko_hour"] < 20 else "20-24")
    d["dow"] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][r["ko_dow"]]
    d["competition"] = ("cup" if _has(r["league"], "cup", "trophy", "copa", "coupe",
                                      "pokal", "beker")
                        or _has(r["round_label"], "round of", "quarter", "semi",
                                "final", "qualifying")
                        else "league")
    d["womens"] = "womens" if _has(r["league"], "women", "feminin", "frauen",
                                   "femenin", " (w)", "damallsvenskan") else "mens"
    d["youth_reserve"] = ("youth" if _has(r["league"], "u19", "u20", "u21", "u23",
                                          "youth", "junior", "primavera",
                                          "reserve", " ii", " b team")
                          else "senior")
    fam = min(r["fam_home"], r["fam_away"])
    d["familiarity"] = ("fam<10" if fam < 10 else "fam10-50" if fam < 50
                        else "fam50-200" if fam < 200 else "fam200+")
    d["travel_km"] = (_band(r["travel_km"], [(0, 100), (100, 400), (400, 1000),
                                             (1000, 1e9)], "{:.0f}")
                      or "travel_null")
    rd = None
    if r["rest_days_home"] is not None and r["rest_days_away"] is not None:
        rd = min(r["rest_days_home"], r["rest_days_away"])
    d["rest_days"] = (_band(rd, [(0, 4), (4, 7), (7, 1e9)], "{:.0f}")
                      or "rest_null")
    inj = None
    if r["injury_count_home"] is not None and r["injury_count_away"] is not None:
        inj = r["injury_count_home"] + r["injury_count_away"]
    d["injuries"] = (_band(inj, [(0, 1), (1, 4), (4, 1e9)], "{:.0f}")
                     or "inj_null")
    d["lineup_confirmed"] = ("lineup_yes" if r["lineup_confirmed"] else
                             "lineup_no" if r["lineup_confirmed"] is not None
                             else "lineup_null")
    d["elo_gap"] = (_band(abs(r["elo_diff"]), [(0, 50), (50, 150), (150, 1e9)],
                          "{:.0f}") if r["elo_diff"] is not None else "elo_null")
    d["data_tier"] = str(r["data_tier"]) if r["data_tier"] is not None else "dt_null"
    d["sharp_edge"] = _band(r["prob_edge"], EDGE_BANDS) or "no_pinnacle"
    return d


# ---------------------------------------------------------------- the scan

def scan(rows, dims, min_n, split_date, extra_prefix=""):
    """Every (dimension, level) cell and its interaction with sharp_edge."""
    cells = defaultdict(list)
    for r in rows:
        lab = r["_lab"]
        for dim in dims:
            lv = lab.get(dim)
            if lv is None:
                continue
            cells[(dim, lv)].append(r)
            if dim != "sharp_edge" and lab.get("sharp_edge") not in (None,
                                                                    "no_pinnacle"):
                cells[(f"{dim} x sharp_edge",
                       f"{lv} | {lab['sharp_edge']}")].append(r)

    out = []
    for (dim, lv), rs in cells.items():
        if len(rs) < min_n:
            continue
        tr = [r for r in rs if r["ko_date"] < split_date]
        te = [r for r in rs if r["ko_date"] >= split_date]
        mu, se, n, ng = cluster_mean(rs)
        mtr, str_, ntr, _ = cluster_mean(tr)
        mte, ste, nte, _ = cluster_mean(te)
        days = sorted({r["ko_date"] for r in rs})
        per_day = {}
        for dday in days:
            sub = [r for r in rs if r["ko_date"] == dday]
            if len(sub) >= 20:
                per_day[dday] = sum(r["mc_clv"] for r in sub) / len(sub)
        out.append({
            "dim": extra_prefix + dim, "level": lv,
            "n": n, "n_fixtures": ng, "mu": mu, "se": se,
            "lo": mu - Z * se, "hi": mu + Z * se,
            "t": (mu / se) if se else float("nan"),
            "p": two_sided_p(mu / se) if se else float("nan"),
            "n_train": ntr, "mu_train": mtr, "se_train": str_,
            "n_test": nte, "mu_test": mte, "se_test": ste,
            "days": len(days), "span": f"{days[0]}..{days[-1]}" if days else "-",
            "folds_pos": sum(1 for v in per_day.values() if v > 0),
            "folds": len(per_day),
            "sd": sd_of(rs),
        })
    return out


DIMS = ["book", "market", "selection", "odds_band", "is_favourite",
        "league_tier", "country", "ko_hour_bucket", "dow", "competition",
        "womens", "youth_reserve", "familiarity", "travel_km", "rest_days",
        "injuries", "lineup_confirmed", "elo_gap", "data_tier", "sharp_edge"]


def fmt(row):
    return (f"  {row['dim']:26s} {str(row['level'])[:30]:30s} "
            f"n={row['n']:6d} fx={row['n_fixtures']:5d} "
            f"mc={row['mu']*100:+7.2f}% [{row['lo']*100:+7.2f},{row['hi']*100:+7.2f}] "
            f"tr={row['mu_train']*100:+7.2f}% te={row['mu_test']*100:+7.2f}% "
            f"folds {row['folds_pos']}/{row['folds']}  {row['span']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default="/tmp/own_clv_panel.json.gz")
    ap.add_argument("--lead", type=float, default=6.0)
    ap.add_argument("--min-n", type=int, default=100)
    ap.add_argument("--split", default="2026-09-11")
    ap.add_argument("--fdr", type=float, default=0.10)
    ap.add_argument("--placebo", action="store_true",
                    help="gate-matched placebo: shuffle the target within "
                         "(book, market, day) and rerun the identical scan")
    ap.add_argument("--seed", type=int, default=20260914)
    a = ap.parse_args()

    with gzip.open(a.panel, "rt") as fh:
        panel = json.load(fh)
    rows = [r for r in panel if r["lead_h"] == a.lead]
    for r in rows:
        r["_lab"] = label(r)

    days = sorted({r["ko_date"] for r in rows})
    print(f"OWN SEGMENT / SIGNAL SEARCH — target: margin-corrected own-book CLV")
    print(f"  lead time arm      : {a.lead:.0f}h before kickoff")
    print(f"  legs               : {len(rows)}")
    print(f"  fixtures           : {len({r['match_id'] for r in rows})}")
    print(f"  DATE SPAN          : {days[0]} .. {days[-1]}  ({len(days)} match days)")
    print(f"  train / test split : < {a.split}  /  >= {a.split}")
    base_mu, base_se, base_n, base_ng = cluster_mean(rows)
    print(f"  UNSELECTED BASELINE: {base_mu*100:+.2f}% "
          f"[{(base_mu-Z*base_se)*100:+.2f},{(base_mu+Z*base_se)*100:+.2f}] "
          f"n={base_n} fixtures={base_ng}  sd={sd_of(rows)*100:.2f}pp")
    print(f"  break-even         : 0.00%  -> a cell must beat baseline by "
          f"{-base_mu*100:.2f}pp to be placeable\n")

    if a.placebo:
        rnd = random.Random(a.seed)
        pools = defaultdict(list)
        for r in rows:
            pools[(r["book"], r["market"], r["ko_date"])].append(r["mc_clv"])
        for v in pools.values():
            rnd.shuffle(v)
        idx = defaultdict(int)
        for r in rows:
            k = (r["book"], r["market"], r["ko_date"])
            r["mc_clv"] = pools[k][idx[k]]
            idx[k] += 1
        print("  *** PLACEBO ARM: target shuffled within (book, market, day). "
              "Any surviving cell is noise by construction. ***\n")

    res = scan(rows, DIMS, a.min_n, a.split)
    res.sort(key=lambda x: -x["mu"])
    thr, m = bh_fdr([r["p"] for r in res], a.fdr)

    print(f"CELLS TESTED: {m}  (BH-FDR q={a.fdr} threshold p<={thr:.5f})\n")
    print("TOP 25 CELLS BY POINT ESTIMATE")
    for r in res[:25]:
        print(fmt(r))
    print("\nBOTTOM 10 CELLS BY POINT ESTIMATE")
    for r in res[-10:]:
        print(fmt(r))

    pos = [r for r in res if r["lo"] > 0]
    print(f"\nCELLS WITH 95pct CI ABOVE ZERO (placeable on the stated criterion): "
          f"{len(pos)} of {m}")
    for r in pos:
        print(fmt(r))

    conf = [r for r in res if r["lo"] > 0 and r["mu_test"] > 0
            and r["p"] <= max(thr, 1e-12)]
    print(f"\nCELLS SURVIVING CI>0 + BH-FDR + POSITIVE OUT-OF-SAMPLE: {len(conf)}")
    for r in conf:
        print(fmt(r))

    # how much better than baseline, and can we even see it
    print("\nPOWER — n needed to resolve each top cell's gap to baseline at 80pct")
    for r in res[:8]:
        diff = r["mu"] - base_mu
        need = required_n(diff, r["sd"])
        print(f"  {r['dim']:26s} {str(r['level'])[:26]:26s} "
              f"gap={diff*100:+6.2f}pp  sd={r['sd']*100:5.2f}pp  "
              f"n_needed={need:8.0f}  have={r['n']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
