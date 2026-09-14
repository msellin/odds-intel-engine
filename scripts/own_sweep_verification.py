#!/usr/bin/env python3
"""ADVERSARIAL VERIFICATION of docs/OWN_SHARP_CONFIG_SWEEP_2026_09_14.md.

WHY THIS EXISTS
---------------
A prior agent concluded that no sharp-edge configuration is profitable at the
three EMTA-legal, self-scraped books (Coolbet, Epicbet, Unibet-Site). A negative
result of that size stops a product line, so it gets attacked before it is
acted on. This script is an INDEPENDENT re-implementation of the leg
construction — it deliberately does not import `own_sharp_config_sweep`, so a
bug in that harness cannot propagate into its own verification.

WHAT IT TESTS, and why each test is here
----------------------------------------
T1  REPLICATION. Rebuild the §1 table (publish rule per book + pooled) and the
    §3(a) vig dipstick from scratch. If these do not land on the published
    numbers, nothing downstream is comparable.

T2  THE FLOOR THE LIVE BOTS ACTUALLY USE (attack (a)). `pick_triggers._window`
    gates on `min_odds = 1/(cal - floor)`, i.e. a PROBABILITY-DIFFERENCE floor
    `P - 1/o >= f`. The sweep swept only the EXPECTED-ROI floor `P*o - 1 >= f`.
    These are not the same gate: `roi_edge = prob_edge * odds`, so at odds 3.0 a
    3% ROI floor admits everything with a 1% probability overlay. The sweep's
    14,040 (now 70,200) cells therefore never contained the live gate. This test
    sweeps the probability-difference floor, and adds the SHORT odds band
    (1.01-2.00) that the sweep's ODDS_BANDS omits entirely and the live bots
    favour.

T3  IS THE JUNK ANCHOR A FAIR NULL? (attack (b)). The control keeps every leg's
    price, outcome and timing and only permutes the anchor across fixtures. But
    the anchor decides WHICH legs pass the edge floor, so the two arms select
    different populations. This test prints the two arms' covariates side by
    side (n, odds quantiles, market mix, book mix, Pinnacle overround, lead,
    anchor gap) and computes the n required to detect the real-vs-junk ROI gap
    the report calls damning.

T4  EPICBET ALONE (attack (c)). Pooling books that differ in margin, coverage
    and era is this repo's §10 trap. Epicbet reads +4.48% inside a -9.55% pool.
    Folds, per-book IS/OOS, own-book margin-corrected CLV, and date span.

T5  THE ANCHOR DIPSTICK THE REPORT DOES NOT HAVE. The vig baseline is
    anchor-INDEPENDENT by construction (the script's own comment says it must be
    identical in both arms), so it cannot validate the de-vig path at all — a
    broken de-vig would pass it. This bins legs by de-vigged Pinnacle
    probability and compares predicted with realised hit rate.

T6  THE LIVE BOTS AS A THIRD DATA POINT. `shadow_bets_unique` sharp bots, by
    odds band, with fixture-clustered CIs and date spans, plus the power to
    detect the band effect being read off them.

METHOD RULES OBEYED (violating any invalidates the result)
----------------------------------------------------------
* Books assembled from a +/-2 min window BEFORE any cross-book alignment
  (ANALYSIS_GOTCHAS §63); never an exact-timestamp join.
* Every cell prints its DATE SPAN next to its n (the +16.00% retraction).
* Anchor-to-bet gap bounded and its median printed on every cell.
* `clv` is a RAW price ratio; break-even is the closing book's OWN margin,
  computed per fixture, left NULL where uncomputable (never a flat 7.6%).
* `shadow_bets_unique` only (shadow_bets duplicates 8.43x, outcome-correlated).
* Book whitelist: only the three self-scraped books; every phantom/synthetic
  feed is excluded by construction.
* All CIs cluster on `match_id`.

Read-only. Touches no bot, no config, no table.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime
from statistics import median

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2.extras

from workers.api_clients.db import get_conn
from workers.model.devig import devig

BOOKS = ["Coolbet", "Epicbet", "Unibet-Site"]
ANCHOR = "Pinnacle"
POOLED = "POOLED"
SIDES = {"1x2": ("home", "draw", "away"),
         "over_under_25": ("over", "under"),
         "over_under_15": ("over", "under"),
         "over_under_35": ("over", "under")}
OUTLIER_MULT = {"1x2": 1.35, "over_under_15": 1.30,
                "over_under_25": 1.30, "over_under_35": 1.30}
WINDOW = 2.0


# --------------------------------------------------------------------------
def assemble(obs, sides, window=WINDOW):
    """A book's complete market, allowing its rows to straddle `window` min.

    §63: `odds_snapshots` stamps each ROW. Coolbet's 1x2 triple spans ~100 ms,
    so exact-timestamp grouping finds a complete triple in 0.1% of its groups.
    Assemble per book FIRST, align across books after.
    """
    obs = sorted(obs, key=lambda x: x[0])
    out = []
    for i, (t0, _, _) in enumerate(obs):
        picked = {}
        for t, sel, o in obs[i:]:
            if (t - t0) / 60.0 > window:
                break
            picked.setdefault(sel, o)
        if all(s in picked for s in sides):
            out.append((t0, {s: picked[s] for s in sides}))
    return out


def won(market, sel, sh, sa):
    if market == "1x2":
        return {"home": sh > sa, "draw": sh == sa, "away": sh < sa}[sel]
    line = float(market.rsplit("_", 1)[1]) / 10.0
    tot = sh + sa
    if tot == line:
        return None
    return (tot > line) if sel == "over" else (tot < line)


def load(days, markets):
    matches, odds = {}, defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT m.id, extract(epoch from m.date) ko, m.score_home, m.score_away,
                          m.league_id
                     FROM matches m
                    WHERE m.status='finished' AND m.score_home IS NOT NULL
                      AND m.score_away IS NOT NULL
                      AND m.date > now() - (%s || ' days')::interval AND m.date < now()""",
                (str(days),))
            for r in cur.fetchall():
                matches[r["id"]] = (float(r["ko"]), int(r["score_home"]),
                                    int(r["score_away"]), r["league_id"])
        sql = """SELECT o.match_id, o.bookmaker, o.market, o.selection,
                        o.odds::float, extract(epoch from o.timestamp)
                   FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
                  WHERE o.bookmaker = ANY(%s) AND o.market = ANY(%s)
                    AND o.timestamp <= m.date
                    AND m.status='finished' AND m.score_home IS NOT NULL
                    AND m.date > now() - (%s || ' days')::interval AND m.date < now()
                  {extra}"""
        with conn.cursor(name="c1") as cur:
            cur.itersize = 50_000
            cur.execute(sql.format(extra=""), (BOOKS, markets, str(days)))
            for mid, bk, mk, sel, o, ts in cur:
                if mid in matches:
                    odds[mid][bk][mk].append((float(ts), sel, o))
        cov = list(odds.keys())
        with conn.cursor(name="c2") as cur:
            cur.itersize = 50_000
            cur.execute(sql.format(extra="AND o.match_id = ANY(%s::uuid[])"),
                        ([ANCHOR], markets, str(days), cov))
            for mid, bk, mk, sel, o, ts in cur:
                odds[mid][bk][mk].append((float(ts), sel, o))
    return matches, odds


def build(matches, odds, markets, align_min, lead, seed):
    """One leg per (fixture, book, market, selection) at a single `lead`.

    Both arms are built in ONE pass over the SAME legs, so the real and junk
    anchors differ in nothing but the anchor: `p_real`/`p_junk`, and the two
    edge pairs derived from them, sit on the same row. That is what makes the
    covariate comparison in T3 exact rather than approximate.
    """
    apin, pool = defaultdict(dict), defaultdict(list)
    for mid, bb in odds.items():
        for mk in markets:
            rows = bb.get(ANCHOR, {}).get(mk)
            if not rows:
                continue
            tri = assemble(rows, SIDES[mk])
            if tri:
                apin[mid][mk] = tri
                pool[mk].append((mid, tri[-1][1]))
    junk = {}
    rng = random.Random(seed)
    for mk, pl in pool.items():
        if len(pl) < 2:
            continue
        src = list(pl)
        rng.shuffle(src)
        for i, (mid, _) in enumerate(pl):
            j = i
            while src[j % len(src)][0] == mid:
                j += 1
            junk[(mid, mk)] = src[j % len(src)][1]

    legs = []
    for mid, (ko, sh, sa, lg_id) in matches.items():
        bb = odds.get(mid)
        if not bb:
            continue
        for mk in markets:
            pin = apin.get(mid, {}).get(mk)
            if not pin:
                continue
            sides, mult = SIDES[mk], OUTLIER_MULT[mk]
            for book in BOOKS:
                rows = bb.get(book, {}).get(mk)
                if not rows:
                    continue
                bq = assemble(rows, sides)
                if not bq:
                    continue
                t_c, q_c = bq[-1]
                close_m = sum(1.0 / q_c[s] for s in sides) - 1.0
                cand = [(t, q) for t, q in bq if (ko - t) / 60.0 >= lead]
                if not cand:
                    continue
                t_b, q_b = max(cand, key=lambda x: x[0])
                t_p, q_p = min(pin, key=lambda x: abs(x[0] - t_b))
                gap = abs(t_p - t_b) / 60.0
                if gap > align_min:
                    continue
                pr = devig([q_p[s] for s in sides])
                jq = junk.get((mid, mk))
                pj = devig([jq[s] for s in sides]) if jq else None
                if not pr:
                    continue
                overround = sum(1.0 / q_p[s] for s in sides) - 1.0
                for i, s in enumerate(sides):
                    o = q_b[s]
                    if o is None or o <= 1.0 or o > q_p[s] * mult:
                        continue
                    w = won(mk, s, sh, sa)
                    if w is None:
                        continue
                    has_c = t_c > t_b
                    clv = (o / q_c[s] - 1.0) if has_c else None
                    legs.append({
                        "mid": mid, "book": book, "market": mk, "sel": s,
                        "league": lg_id, "odds": o, "ret": (o - 1.0) if w else -1.0,
                        "won": 1 if w else 0, "ko": ko, "gap": gap,
                        "lead_actual": (ko - t_b) / 60.0,
                        "p_real": pr[i], "p_junk": (pj[i] if pj else None),
                        "roi_edge": pr[i] * o - 1.0, "prob_edge": pr[i] - 1.0 / o,
                        "roi_edge_j": (pj[i] * o - 1.0) if pj else None,
                        "prob_edge_j": (pj[i] - 1.0 / o) if pj else None,
                        "clv": clv,
                        "clv_ev": ((1 + clv) / (1 + close_m) - 1) if has_c else None,
                        "close_margin": close_m, "pin_overround": overround,
                        "ratio": o / q_p[s] - 1.0,
                    })
    # pooled = best aligned price of the three
    best = {}
    for l in legs:
        k = (l["mid"], l["market"], l["sel"])
        if k not in best or l["odds"] > best[k]["odds"]:
            best[k] = l
    for l in list(best.values()):
        p = dict(l)
        p["book"] = POOLED
        legs.append(p)
    # per-book time-ordered folds + 70/30 era (books start on different dates)
    byb = defaultdict(list)
    for l in legs:
        byb[l["book"]].append(l["ko"])
    bd = {}
    for b, k in byb.items():
        k.sort()
        bd[b] = (k[int(len(k) * .70)], k[len(k) // 3], k[2 * len(k) // 3])
    for l in legs:
        cut, f1, f2 = bd[l["book"]]
        l["era"] = "IS" if l["ko"] <= cut else "OOS"
        l["fold"] = 0 if l["ko"] <= f1 else (1 if l["ko"] <= f2 else 2)
    return legs


# --------------------------------------------------------------------------
def stats(rows, key="ret"):
    """Mean with a cluster-robust (on fixture) 95% CI, plus the date span."""
    if not rows:
        return None
    v = [r[key] for r in rows]
    n = len(v)
    mean = sum(v) / n
    by = defaultdict(float)
    for r in rows:
        by[r["mid"]] += r[key] - mean
    var = sum(x * x for x in by.values()) / (n * n)
    se = math.sqrt(var) if var > 0 else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in v) / n) if n > 1 else 0.0
    kos = sorted(r["ko"] for r in rows)
    return {"n": n, "clusters": len(by), "roi": mean, "se": se, "sd": sd,
            "lo": mean - 1.96 * se, "hi": mean + 1.96 * se,
            "t": mean / se if se else 0.0,
            "d0": datetime.utcfromtimestamp(kos[0]).date(),
            "d1": datetime.utcfromtimestamp(kos[-1]).date(),
            "span": max((kos[-1] - kos[0]) / 86400.0, 1.0),
            "gap": median(r["gap"] for r in rows) if "gap" in rows[0] else None}


def f(s):
    if not s:
        return "n=0"
    g = f" gap{s['gap']:5.0f}m" if s.get("gap") is not None else ""
    return (f"n={s['n']:5d} ROI {s['roi']*100:+7.2f}% "
            f"CI[{s['lo']*100:+7.2f},{s['hi']*100:+7.2f}] t={s['t']:+5.2f}{g} "
            f"{s['d0']}..{s['d1']} ({s['span']:.0f}d)")


def n_needed(sd, diff):
    """Per-arm n to detect `diff` at 80% power, two-sided alpha=0.05 (§60)."""
    return 2 * ((1.96 + 0.84) * sd / diff) ** 2 if diff else float("inf")


# --------------------------------------------------------------------------
def t1_replication(legs, markets):
    print("\n" + "=" * 100)
    print("T1 — REPLICATION of the report's §1 table and §3(a) vig dipstick")
    print("=" * 100)
    print("\n-- publish rule: ROI-edge >= 3%, odds <= 4.0, all markets pooled --")
    for b in BOOKS + [POOLED]:
        s = stats([l for l in legs if l["book"] == b and l["roi_edge"] >= .03
                   and l["odds"] <= 4.0])
        print(f"   {b:12s} {f(s)}")
    print("\n-- vig dipstick: flat-back EVERY aligned leg (must be ~ -m/(1+m)) --")
    for b in BOOKS:
        for mk in markets:
            rows = [l for l in legs if l["book"] == b and l["market"] == mk]
            s = stats(rows)
            if s and s["n"] >= 100:
                m = median(r["close_margin"] for r in rows)
                print(f"   {b:12s} {mk:14s} n={s['n']:6d} ROI {s['roi']*100:+6.2f}% "
                      f"| close margin {m*100:5.2f}% -> predicted {-m/(1+m)*100:+6.2f}%")
    s = stats([l for l in legs if l["book"] != POOLED])
    print(f"   {'ALL 3 BOOKS':12s} {'all markets':14s} n={s['n']:6d} "
          f"ROI {s['roi']*100:+6.2f}%   <- the report's -7.5% baseline")


def t2_prob_floor(legs, markets):
    print("\n" + "=" * 100)
    print("T2 — THE GATE THE LIVE BOTS ACTUALLY USE: probability-difference floor")
    print("     roi_edge = prob_edge x odds, so these are DIFFERENT gates.")
    print("     The sweep swept only the ROI form and has no odds band ending at 2.00.")
    print("=" * 100)
    bands = [(1.01, 2.00), (1.01, 2.50), (1.01, 4.00), (1.01, 1000.0),
             (2.00, 4.00), (2.80, 1000.0)]
    floors = [0.01, 0.02, 0.03, 0.05]
    print(f"\n{'book':12s} {'gate':22s} {'odds band':13s} result")
    for b in [POOLED] + BOOKS:
        for fl in floors:
            for lo, hi in bands:
                for gate, key in (("prob_edge>=", "prob_edge"),
                                  ("roi_edge >=", "roi_edge")):
                    rows = [l for l in legs if l["book"] == b and l[key] >= fl
                            and lo <= l["odds"] <= hi]
                    s = stats(rows)
                    if s and s["n"] >= 60:
                        star = " *" if (s["lo"] > 0 or s["hi"] < 0) else "  "
                        print(f"{b:12s} {gate}{fl*100:2.0f}%{'':9s} "
                              f"{lo:5.2f}-{hi:6.1f} {f(s)}{star}")
        print()


def t2b_live_gate(legs):
    """The LIVE gate, exactly: prob_edge >= 3%, odds >= 1.01, odds <= 1.6*min_odds.

    `pick_triggers._window`: min_odds = max(1/(cal-0.03), 1.01); max_odds =
    min_odds*1.6. The upper bound is an anti-stale guard, not an edge cap, and
    the sweep has no equivalent of it.
    """
    print("\n-- T2b: the live sharp gate reproduced EXACTLY on backtest legs --")
    print("   (prob_edge>=3%, odds in [max(1/(P-.03),1.01), 1.6x that])")
    for b in [POOLED] + BOOKS:
        rows = []
        for l in legs:
            if l["book"] != b or l["p_real"] <= 0.03:
                continue
            mn = max(1.0 / (l["p_real"] - 0.03), 1.01)
            if mn <= l["odds"] <= mn * 1.6:
                rows.append(l)
        s = stats(rows)
        if s:
            print(f"   {b:12s} ALL bands     {f(s)}")
        for lo, hi in [(1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 1e9)]:
            sb = stats([r for r in rows if lo <= r["odds"] < hi])
            if sb and sb["n"] >= 30:
                print(f"   {b:12s} odds {lo:4.1f}-{hi if hi < 1e8 else 99:4.1f} "
                      f"{f(sb)}")
        print()


def t3_junk_balance(legs):
    print("\n" + "=" * 100)
    print("T3 — IS THE JUNK ANCHOR A FAIR NULL? covariate balance of the two arms")
    print("=" * 100)
    real = [l for l in legs if l["book"] == POOLED and l["roi_edge"] >= .03
            and l["odds"] <= 4.0]
    junk = [l for l in legs if l["book"] == POOLED and l["roi_edge_j"] is not None
            and l["roi_edge_j"] >= .03 and l["odds"] <= 4.0]
    allp = [l for l in legs if l["book"] == POOLED]
    sr, sj, sa = stats(real), stats(junk), stats(allp)
    print(f"\n   REAL anchor  {f(sr)}")
    print(f"   JUNK anchor  {f(sj)}")
    print(f"   FLAT-BACK    {f(sa)}   <- every pooled leg, no edge filter")

    def q(rows, k):
        v = sorted(x[k] for x in rows if x[k] is not None)
        if not v:
            return "n/a"
        return (f"{v[len(v)//10]:6.2f}/{v[len(v)//4]:6.2f}/{v[len(v)//2]:6.2f}/"
                f"{v[3*len(v)//4]:6.2f}/{v[9*len(v)//10]:6.2f}")
    print(f"\n   {'covariate':24s} {'REAL':>42s} {'JUNK':>42s}")
    print(f"   {'n':24s} {len(real):>42d} {len(junk):>42d}")
    print(f"   {'odds p10/25/50/75/90':24s} {q(real,'odds'):>42s} {q(junk,'odds'):>42s}")
    print(f"   {'ratio(book/pin-1)':24s} {q(real,'ratio'):>42s} {q(junk,'ratio'):>42s}")
    print(f"   {'P_anchor(selected)':24s} {q(real,'p_real'):>42s} {q(junk,'p_junk'):>42s}")
    print(f"   {'pinnacle overround':24s} {q(real,'pin_overround'):>42s} "
          f"{q(junk,'pin_overround'):>42s}")
    print(f"   {'anchor gap (min)':24s} {q(real,'gap'):>42s} {q(junk,'gap'):>42s}")
    print(f"   {'lead (min to ko)':24s} {q(real,'lead_actual'):>42s} "
          f"{q(junk,'lead_actual'):>42s}")
    for lbl, k in (("market mix", "market"), ("source book", "sel")):
        for grp in (real, junk):
            pass
    def mix(rows, k):
        c = defaultdict(int)
        for r in rows:
            c[r[k]] += 1
        tot = max(len(rows), 1)
        return " ".join(f"{a}:{b/tot*100:.0f}%" for a, b in
                        sorted(c.items(), key=lambda x: -x[1])[:5])
    for k in ("market", "sel"):
        print(f"   {'mix '+k:24s} {mix(real,k):>42s} {mix(junk,k):>42s}")
    print(f"\n   POWER (§60): to call REAL vs JUNK a difference you need")
    if sr and sj:
        d = abs(sr["roi"] - sj["roi"])
        sd = (sr["sd"] + sj["sd"]) / 2
        print(f"      observed gap {d*100:.2f} pp, per-bet sd {sd:.2f} -> "
              f"n per arm = {n_needed(sd, d):,.0f}")
        print(f"      have: REAL n={sr['n']} (the binding arm). "
              f"REAL's own CI is {sr['hi']*100-sr['lo']*100:.1f} pp wide.")
        print(f"      JUNK point estimate inside REAL's CI? "
              f"{'YES — the arms are indistinguishable' if sr['lo'] <= sj['roi'] <= sr['hi'] else 'no'}")


def t4_epicbet(legs, markets):
    print("\n" + "=" * 100)
    print("T4 — EPICBET ON ITS OWN (the pooled figure may be hiding it: §10)")
    print("=" * 100)
    for gate, key, fl in (("roi_edge>=3%", "roi_edge", .03),
                          ("prob_edge>=3%", "prob_edge", .03),
                          ("prob_edge>=2%", "prob_edge", .02)):
        rows = [l for l in legs if l["book"] == "Epicbet" and l[key] >= fl
                and l["odds"] <= 4.0]
        s = stats(rows)
        if not s:
            continue
        print(f"\n   {gate} odds<=4.0   {f(s)}")
        for i in range(3):
            sf = stats([r for r in rows if r["fold"] == i])
            print(f"      fold{i+1}: {f(sf) if sf else 'n=0'}")
        for era in ("IS", "OOS"):
            se = stats([r for r in rows if r["era"] == era])
            print(f"      {era:4s} : {f(se) if se else 'n=0'}")
        cl = [r for r in rows if r["clv_ev"] is not None]
        if cl:
            ev = sum(r["clv_ev"] for r in cl) / len(cl)
            raw = sum(r["clv"] for r in cl) / len(cl)
            mm = median(r["close_margin"] for r in cl)
            print(f"      own-book CLV n={len(cl)}: raw {raw*100:+.2f}% | "
                  f"book margin {mm*100:.2f}% | margin-corrected EV {ev*100:+.2f}%")
        else:
            print("      own-book CLV: n/a (leg IS the last surviving quote)")


def t5_anchor_dipstick(legs):
    print("\n" + "=" * 100)
    print("T5 — THE DIPSTICK THE REPORT LACKS: is the de-vigged anchor calibrated?")
    print("     The vig baseline is anchor-INDEPENDENT, so it cannot test this.")
    print("=" * 100)
    rows = [l for l in legs if l["book"] != POOLED and l["market"] == "1x2"]
    print(f"\n   1x2 legs n={len(rows)}   P_shin(Pinnacle) vs realised")
    print(f"   {'P bucket':14s} {'n':>7s} {'mean P':>8s} {'hit rate':>9s} {'err':>7s}")
    bs = [(0, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .65), (.65, 1.01)]
    for lo, hi in bs:
        sub = [r for r in rows if lo <= r["p_real"] < hi]
        if len(sub) < 50:
            continue
        mp = sum(r["p_real"] for r in sub) / len(sub)
        hr = sum(r["won"] for r in sub) / len(sub)
        print(f"   {lo:.2f}-{hi:.2f}     {len(sub):7d} {mp:8.3f} {hr:9.3f} "
              f"{(hr-mp)*100:+6.1f}pp")
    junk = [r for r in rows if r["p_junk"] is not None]
    print(f"\n   same for the JUNK anchor (must be FLAT — it knows nothing):")
    for lo, hi in bs:
        sub = [r for r in junk if lo <= r["p_junk"] < hi]
        if len(sub) < 50:
            continue
        mp = sum(r["p_junk"] for r in sub) / len(sub)
        hr = sum(r["won"] for r in sub) / len(sub)
        print(f"   {lo:.2f}-{hi:.2f}     {len(sub):7d} {mp:8.3f} {hr:9.3f} "
              f"{(hr-mp)*100:+6.1f}pp")


def t6_live_bots():
    print("\n" + "=" * 100)
    print("T6 — THE LIVE SHARP BOTS as a third data point (shadow_bets_unique)")
    print("=" * 100)
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT bot_name, match_id, market, selection, closing_bookmaker,
                  clv::float clv, result, pick_time::date d,
                  COALESCE(odds_at_pick_live, odds_at_pick)::float px
             FROM shadow_bets_unique
            WHERE bot_name ILIKE %s AND result IN ('won','lost')""", ("%sharp%",))
    legs = [{"mid": r["match_id"], "ko": 0.0,
             "ret": (r["px"] - 1) if r["result"] == "won" else -1.0,
             "odds": r["px"], "bot": r["bot_name"], "d": r["d"],
             "clv": r["clv"], "cb": r["closing_bookmaker"],
             "market": r["market"], "selection": r["selection"]} for r in rows]
    for l in legs:
        l["ko"] = datetime.combine(l["d"], datetime.min.time()).timestamp()
    s = stats(legs)
    print(f"\n   ALL sharp bots  {f(s)}")
    print(f"\n   by odds band (fixture-clustered):")
    for lo, hi in [(1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 1e9)]:
        sb = stats([l for l in legs if lo <= l["odds"] < hi])
        if sb:
            print(f"      {lo:4.1f}-{hi if hi<1e8 else 99:4.1f}  {f(sb)}")
    sb = stats([l for l in legs if l["odds"] < 2.0])
    if sb:
        print(f"\n   POWER on the short band: ROI {sb['roi']*100:+.2f}%, "
              f"sd {sb['sd']:.2f}, n={sb['n']} -> n needed to detect its OWN "
              f"estimate at 80% power = {n_needed(sb['sd'], sb['roi'])/2:,.0f}")
        print(f"      to detect a true +3% ROI there: "
              f"{n_needed(sb['sd'], 0.03)/2:,.0f}")
    print(f"\n   own-book vs UNANCHORED close (the CLV fallback artefact):")
    for anch in (True, False):
        sub = [l for l in legs if (l["cb"] is not None) == anch and l["clv"] is not None]
        if sub:
            print(f"      closing_bookmaker {'set' if anch else 'NULL'}: n={len(sub)} "
                  f"raw CLV {sum(x['clv'] for x in sub)/len(sub)*100:+.2f}%")



def t7_stress(legs):
    """Stress-test whatever T2 turned up, on the SAME null the report used.

    T2 finds cells the sweep's grid could not express. That is not yet a
    finding: the report's own control shows 23.4% of grid cells exclude zero
    under a junk anchor. So every cell is re-run with the junk anchor's edges
    on the identical leg set, the cells are counted both ways, and the
    survivors get folds, IS/OOS and per-book decomposition.
    """
    print("\n" + "=" * 100)
    print("T7 — STRESS TEST of the T2 survivors against the report's own null")
    print("=" * 100)
    bands = [(1.01, 2.00), (1.01, 2.50), (1.01, 4.00), (1.01, 1000.0),
             (2.00, 4.00), (2.80, 1000.0)]
    floors = [0.01, 0.02, 0.03, 0.05]
    nreal = nrealx = njunk = njunkx = 0
    for b in [POOLED] + BOOKS:
        for fl in floors:
            for lo, hi in bands:
                for kr, kj in (("prob_edge", "prob_edge_j"),
                               ("roi_edge", "roi_edge_j")):
                    r = stats([l for l in legs if l["book"] == b and l[kr] >= fl
                               and lo <= l["odds"] <= hi])
                    if r and r["n"] >= 60:
                        nreal += 1
                        nrealx += 1 if (r["lo"] > 0 or r["hi"] < 0) else 0
                    j = stats([l for l in legs if l["book"] == b
                               and l[kj] is not None and l[kj] >= fl
                               and lo <= l["odds"] <= hi])
                    if j and j["n"] >= 60:
                        njunk += 1
                        njunkx += 1 if (j["lo"] > 0 or j["hi"] < 0) else 0
    print(f"\n   cells with n>=60 — REAL anchor: {nrealx}/{nreal} exclude zero "
          f"({nrealx/max(nreal,1)*100:.1f}%)")
    print(f"   cells with n>=60 — JUNK anchor: {njunkx}/{njunk} exclude zero "
          f"({njunkx/max(njunk,1)*100:.1f}%)")
    print("   (the report's grid-wide junk rate was 23.4%; a real rate at or "
          "below that is not evidence of anything)")

    print("\n   -- the short-band cells, REAL vs JUNK on identical legs --")
    for b in [POOLED] + BOOKS:
        for fl in (0.02, 0.03):
            for lo, hi in ((1.01, 2.00), (1.01, 2.50)):
                r = stats([l for l in legs if l["book"] == b
                           and l["prob_edge"] >= fl and lo <= l["odds"] <= hi])
                j = stats([l for l in legs if l["book"] == b
                           and l["prob_edge_j"] is not None
                           and l["prob_edge_j"] >= fl and lo <= l["odds"] <= hi])
                if r and r["n"] >= 40:
                    print(f"   {b:12s} prob>={fl*100:.0f}% {lo:.2f}-{hi:.2f}")
                    print(f"      REAL {f(r)}")
                    print(f"      JUNK {f(j) if j else 'n=0'}")

    print("\n   -- folds / IS-OOS / CLV on the strongest cells --")
    for b, fl, lo, hi in ((POOLED, .02, 1.01, 2.00), (POOLED, .02, 1.01, 2.50),
                          (POOLED, .03, 1.01, 2.50), ("Coolbet", .02, 1.01, 2.50),
                          ("Epicbet", .02, 1.01, 2.50)):
        rows = [l for l in legs if l["book"] == b and l["prob_edge"] >= fl
                and lo <= l["odds"] <= hi]
        s = stats(rows)
        if not s:
            continue
        print(f"\n   {b} prob_edge>={fl*100:.0f}% odds {lo:.2f}-{hi:.2f}")
        print(f"      FULL {f(s)}")
        for i in range(3):
            sf = stats([r for r in rows if r["fold"] == i])
            print(f"      fold{i+1} {f(sf) if sf else 'n=0'}")
        for era in ("IS", "OOS"):
            se = stats([r for r in rows if r["era"] == era])
            print(f"      {era:4s}  {f(se) if se else 'n=0'}")
        cl = [r for r in rows if r["clv_ev"] is not None]
        if cl:
            print(f"      own-book CLV n={len(cl)}: raw "
                  f"{sum(r['clv'] for r in cl)/len(cl)*100:+.2f}% | "
                  f"margin-corrected EV "
                  f"{sum(r['clv_ev'] for r in cl)/len(cl)*100:+.2f}%")
        else:
            print("      own-book CLV: n/a (the leg IS the last surviving quote)")
        bm = defaultdict(list)
        for r in rows:
            bm[r["market"]].append(r)
        for mk, v in bm.items():
            print(f"      market {mk:15s} {f(stats(v))}")
        print(f"      power: to detect a true +3% ROI here needs n="
              f"{n_needed(s['sd'], 0.03)/2:,.0f}; picks/day "
              f"{s['n']/s['span']:.1f} -> "
              f"{n_needed(s['sd'],0.03)/2/(s['n']/s['span'])/365:.1f} years")


def t8_pooled_is_best_of_books(legs):
    """POOLED = MAX odds across the three books, i.e. a best-of-books selection.

    ANALYSIS_GOTCHAS §52/§55: best-of-books structurally picks whichever book is
    most mispriced and is the documented cause of this repo's biggest measurement
    mirage. It is *defensible* for OWN only if we hold an account at whichever
    book won each leg. Print which book actually supplies the pooled winners.
    """
    print("\n" + "=" * 100)
    print("T8 — WHICH BOOK supplies the POOLED winners (§52 best-of-books check)")
    print("=" * 100)
    for fl, lo, hi in ((.02, 1.01, 2.00), (.02, 1.01, 2.50), (.03, 1.01, 4.00)):
        pooled = [l for l in legs if l["book"] == POOLED and l["prob_edge"] >= fl
                  and lo <= l["odds"] <= hi]
        src = defaultdict(list)
        byk = {}
        for l in legs:
            if l["book"] == POOLED:
                continue
            byk.setdefault((l["mid"], l["market"], l["sel"]), []).append(l)
        for p in pooled:
            cands = byk.get((p["mid"], p["market"], p["sel"]), [])
            w = max(cands, key=lambda x: x["odds"]) if cands else None
            if w:
                src[w["book"]].append(p)
        print(f"\n   prob_edge>={fl*100:.0f}% odds {lo:.2f}-{hi:.2f} "
              f"pooled n={len(pooled)}")
        for bk, v in sorted(src.items(), key=lambda x: -len(x[1])):
            print(f"      won by {bk:12s} n={len(v):4d} "
                  f"({len(v)/max(len(pooled),1)*100:4.1f}%)  {f(stats(v))}")



def t9_sign_and_era(legs):
    """Two corrections the report's null needs.

    (a) THE SIGN. The report offers "23.4% of junk cells exclude zero" as the
        null rate for a POSITIVE finding. But a junk cell excludes zero mostly
        by measuring the VIG precisely — it rejects on the NEGATIVE side, at an
        n an order of magnitude larger than the real arm's (the junk anchor
        passes ~10x as many legs through the same floor). A null for "is this
        positive cell noise" has to count junk cells that are positive.

    (b) THE ERA. Coolbet's usable history starts 2026-08-07, Epicbet's
        2026-09-02, Unibet-Site's 2026-09-09. Any pooled cell therefore mixes
        three different windows. Split it.
    """
    print("\n" + "=" * 100)
    print("T9 — sign of the cells that exclude zero, and the era split")
    print("=" * 100)
    bands = [(1.01, 2.00), (1.01, 2.50), (1.01, 4.00), (1.01, 1000.0),
             (2.00, 4.00), (2.80, 1000.0)]
    cnt = {"real+": 0, "real-": 0, "junk+": 0, "junk-": 0,
           "realN": 0, "junkN": 0}
    for b in [POOLED] + BOOKS:
        for fl in (0.01, 0.02, 0.03, 0.05):
            for lo, hi in bands:
                for arm, kr in (("real", "prob_edge"), ("real", "roi_edge"),
                                ("junk", "prob_edge_j"), ("junk", "roi_edge_j")):
                    r = stats([l for l in legs if l["book"] == b
                               and l[kr] is not None and l[kr] >= fl
                               and lo <= l["odds"] <= hi])
                    if not r or r["n"] < 60:
                        continue
                    cnt[arm + "N"] += 1
                    if r["lo"] > 0:
                        cnt[arm + "+"] += 1
                    elif r["hi"] < 0:
                        cnt[arm + "-"] += 1
    print(f"\n   REAL cells n>=60: {cnt['realN']:4d} | CI excludes zero POSITIVE "
          f"{cnt['real+']:3d} ({cnt['real+']/max(cnt['realN'],1)*100:.1f}%) | "
          f"NEGATIVE {cnt['real-']:3d}")
    print(f"   JUNK cells n>=60: {cnt['junkN']:4d} | CI excludes zero POSITIVE "
          f"{cnt['junk+']:3d} ({cnt['junk+']/max(cnt['junkN'],1)*100:.1f}%) | "
          f"NEGATIVE {cnt['junk-']:3d}")
    print("   -> the junk arm's rejections are the VIG, not false positives. The"
          "\n      false-POSITIVE rate is the only null a positive cell can be"
          "\n      judged against.")

    print("\n   -- the pooled short-band cell, split by the three books' eras --")
    eras = [("Coolbet only   (..09-01)", None, 1788307200.0),
            ("+Epicbet       (09-02..09-08)", 1788307200.0, 1788912000.0),
            ("+Unibet-Site   (09-09..)", 1788912000.0, None)]
    for fl, lo, hi in ((.02, 1.01, 2.50), (.03, 1.01, 2.50)):
        print(f"\n   POOLED prob_edge>={fl*100:.0f}% odds {lo:.2f}-{hi:.2f}")
        for lbl, t0, t1 in eras:
            rows = [l for l in legs if l["book"] == POOLED
                    and l["prob_edge"] >= fl and lo <= l["odds"] <= hi
                    and (t0 is None or l["ko"] >= t0)
                    and (t1 is None or l["ko"] < t1)]
            s = stats(rows)
            print(f"      {lbl:30s} {f(s) if s else 'n=0'}")
        # and Coolbet ALONE across its whole history, the only book with 37 days
        rows = [l for l in legs if l["book"] == "Coolbet"
                and l["prob_edge"] >= fl and lo <= l["odds"] <= hi]
        print(f"      {'Coolbet alone, full history':30s} "
              f"{f(stats(rows)) if rows else 'n=0'}")



def t10_gap_vs_era(legs):
    """Is the short-band effect an ERA or an ALIGNMENT condition?

    T9 shows the effect is absent in 2026-08-07..09-01 (+1.22%, n=81) and
    present after. Two readings compete and they have different consequences:

      * ERA — it appeared when Epicbet and Unibet-Site did, i.e. it is a
        three-book line-shopping effect, or it is an artefact of the newest
        window and will evaporate.
      * ALIGNMENT — before 2026-09-07 retention leaves ONE surviving quote per
        series at our books, so the median anchor gap is ~30 min there against
        ~5 min after. A 30-minute-stale anchor measures noise, and the report's
        own thesis is that alignment is the thing that decides whether a number
        means anything. If the effect is a function of the GAP rather than the
        DATE, it is mechanistic and the old era simply has no usable legs.

    These are separable: bucket by gap, and cross gap with era.
    """
    print("\n" + "=" * 100)
    print("T10 — ERA or ALIGNMENT? the short-band cell by anchor gap")
    print("=" * 100)
    T0, T1 = 1788307200.0, 1788912000.0     # 2026-09-02, 2026-09-09
    for fl, lo, hi in ((.02, 1.01, 2.50),):
        rows = [l for l in legs if l["book"] == POOLED and l["prob_edge"] >= fl
                and lo <= l["odds"] <= hi]
        print(f"\n   POOLED prob_edge>={fl*100:.0f}% odds {lo:.2f}-{hi:.2f} "
              f"n={len(rows)}")
        print("\n   by anchor gap, all eras:")
        for g0, g1 in ((0, 5), (5, 15), (15, 30), (30, 61)):
            s = stats([r for r in rows if g0 <= r["gap"] < g1])
            print(f"      gap {g0:2d}-{g1:2d} min  {f(s) if s else 'n=0'}")
        print("\n   gap x era (does a TIGHT gap work in the OLD era too?):")
        for lbl, t0, t1 in (("old (..09-01)", None, T0),
                            ("new (09-02..)", T0, None)):
            for g0, g1 in ((0, 15), (15, 61)):
                sub = [r for r in rows
                       if (t0 is None or r["ko"] >= t0)
                       and (t1 is None or r["ko"] < t1) and g0 <= r["gap"] < g1]
                s = stats(sub)
                print(f"      {lbl:15s} gap {g0:2d}-{g1:2d} min  "
                      f"{f(s) if s else 'n=0'}")
        print("\n   the same for the flat-back baseline on this band "
              "(a gap effect must NOT show up here):")
        base = [l for l in legs if l["book"] == POOLED and lo <= l["odds"] <= hi]
        for g0, g1 in ((0, 15), (15, 61)):
            s = stats([r for r in base if g0 <= r["gap"] < g1])
            print(f"      flat-back      gap {g0:2d}-{g1:2d} min  "
                  f"{f(s) if s else 'n=0'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--align-min", type=float, default=60.0)
    ap.add_argument("--lead", type=float, default=0.0)
    ap.add_argument("--markets", default="1x2,over_under_25")
    ap.add_argument("--seed", type=int, default=20260914)
    ap.add_argument("--tests", default="1,2,3,4,5,6,7,8,9")
    a = ap.parse_args()
    markets = [m.strip() for m in a.markets.split(",") if m.strip()]
    want = set(a.tests.split(","))

    if want - {"6"}:
        matches, odds = load(a.days, markets)
        legs = build(matches, odds, markets, a.align_min, a.lead, a.seed)
        kos = sorted(l["ko"] for l in legs)
        print(f"\nOWN SWEEP VERIFICATION — independent re-implementation")
        print(f"books: {', '.join(BOOKS)} (+POOLED)  anchor: Shin-de-vigged {ANCHOR}")
        print(f"window {a.days}d | alignment <= {a.align_min:.0f} min | lead "
              f"{a.lead:.0f} min | legs {len(legs):,} | fixtures "
              f"{len({l['mid'] for l in legs}):,}")
        print(f"span {datetime.utcfromtimestamp(kos[0]).date()} .. "
              f"{datetime.utcfromtimestamp(kos[-1]).date()}")
        if "1" in want:
            t1_replication(legs, markets)
        if "2" in want:
            t2_prob_floor(legs, markets)
            t2b_live_gate(legs)
        if "3" in want:
            t3_junk_balance(legs)
        if "4" in want:
            t4_epicbet(legs, markets)
        if "5" in want:
            t5_anchor_dipstick(legs)
        if "7" in want:
            t7_stress(legs)
        if "8" in want:
            t8_pooled_is_best_of_books(legs)
        if "9" in want:
            t9_sign_and_era(legs)
        if "10" in want:
            t10_gap_vs_era(legs)
    if "6" in want:
        t6_live_bots()
    return 0


if __name__ == "__main__":
    sys.exit(main())
