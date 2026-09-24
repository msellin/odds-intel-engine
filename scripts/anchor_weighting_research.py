"""SMARTER CONSENSUS? ([[#116]], 2026-09-24) — do weighted combinations of the books beat
the equal-weight consensus of workers/utils/anchor.py?

Read-only. Writes nothing. Heavy — run on the VPS next to the DB:
    venv/bin/python3 scripts/anchor_weighting_research.py

WHY
---
#113 made the consensus a first-class anchor: a Shin-de-vigged EQUAL-WEIGHT mean of >=5
books (Pinnacle excluded). Equal weight is the naive choice. Books differ in margin, in
measured accuracy and in independence (skins, shared supplier feeds), so a weighted
consensus might be sharper. It might equally be noise-fitting: the forecast-combination
literature's "forecast combination puzzle" (Stock & Watson 2004; Smith & Wallis 2009) is
that estimated weights usually FAIL to beat the simple mean out of sample, because the
estimation error in the weights costs more than the true weight differences are worth.
The expected outcome, stated before the run, is therefore a TIE.

VARIANTS (all use the SAME member books the production resolver picks — skins deduped,
stale/out-of-window dropped, ratio guard applied — so only the COMBINATION differs)
  ew        equal-weight mean (production; the reference)
  acc       weights from each book's measured accuracy: mean log-loss EXCESS vs the
            leave-one-out EW consensus on the TRAINING window, w = 1/(e - e_min + d),
            d = median(e - e_min). Books with < MIN_TRAIN fixtures get the median weight.
  margin    per fixture, w = 1/max(overround, 0.01) — tighter book, more weight
  decorr    w_b = 1/(1 + sum over OTHER members j of max(rho_bj, 0)), rho = correlation of
            the two books' logit residuals vs the LOO consensus on the TRAINING window —
            near-duplicates (skins, one supplier) share one vote
  trim      per side drop the highest and lowest book, mean the rest, renormalise
  median    per side median, renormalise

WINDOWS (no look-ahead)
  outcomes  finished fixtures, last OUTCOME_DAYS (120). TRAIN = older than HOLDOUT_DAYS
            (40) — weights estimated here only; HOLDOUT = last 40 d — every variant
            scored here, paired per fixture against ew.
  closing   finished fixtures, last CLOSE_DAYS (7; full intraday history exists only ~7 d,
            ANALYSIS_GOTCHAS §59) — inside the holdout. Anchor at KO-120 (primary) and
            KO-30 (secondary, not in the test family). Targets:
              pin_close   Pinnacle's latest complete set <=15 min before KO
              loo_close   EW consensus AT KO of 3 books held out at random from the anchor
                          (fixtures with >=8 members) — the anchor never sees them, so no
                          variant is scored against itself
            error = max over sides |p_anchor - p_target| (same metric as #113).

TEST FAMILY — fixed before the run: 5 variants x 3 criteria (outcome LL on holdout,
closing error vs pin_close at KO-120, closing error vs loo_close at KO-120) x 2 markets
(1x2, over_under_25) = 30 paired tests, Holm-corrected at alpha 0.05.
ADOPTION RULE: a variant is adopted only if, in BOTH markets, it is significantly better
than ew on outcome LL AND on the pin_close error, and not significantly worse on loo_close.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402
from workers.utils.anchor import PIN, compute_anchor, market_sides, sets_from_rows  # noqa: E402

MARKETS = ("1x2", "over_under_25")
OUTCOME_DAYS, HOLDOUT_DAYS, CLOSE_DAYS = 120, 40, 7
MIN_TRAIN = 300
VARIANTS = ("acc", "margin", "decorr", "trim", "median")


# ── combiners ───────────────────────────────────────────────────────────────
def _norm(v):
    s = sum(v)
    return [x / s for x in v]


def combine(per_book: dict, scheme: str, *, over: dict | None = None, acc_w: dict | None = None,
            rho: dict | None = None) -> list[float]:
    books = list(per_book)
    n = len(per_book[books[0]])
    if scheme == "ew":
        w = {b: 1.0 for b in books}
    elif scheme == "acc":
        w = {b: acc_w.get(b, acc_w["_median"]) for b in books}
    elif scheme == "margin":
        w = {b: 1.0 / max(over[b], 0.01) for b in books}
    elif scheme == "decorr":
        w = {b: 1.0 / (1.0 + sum(max(rho.get(tuple(sorted((b, j))), 0.0), 0.0)
                                 for j in books if j != b)) for b in books}
    elif scheme == "trim":
        out = []
        for i in range(n):
            xs = sorted(per_book[b][i] for b in books)
            out.append(mean(xs[1:-1]) if len(xs) >= 5 else mean(xs))
        return _norm(out)
    elif scheme == "median":
        return _norm([median(per_book[b][i] for b in books) for i in range(n)])
    else:
        raise ValueError(scheme)
    tw = sum(w.values())
    return _norm([sum(w[b] * per_book[b][i] for b in books) / tw for i in range(n)])


def ll(p, k):
    return -math.log(max(p[k], 1e-9))


def err(p, q):
    return max(abs(a - b) for a, b in zip(p, q))


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


# ── data ────────────────────────────────────────────────────────────────────
def outcome_rows(market: str, days: int):
    """Latest pre-kickoff row per (fixture, book, side) — after ~7 d retention keeps
    only that row anyway (§59); sets_from_rows then rejects any book whose legs are not
    from one fetch (§62)."""
    sides = market_sides(market)
    rows = execute_query(
        """SELECT DISTINCT ON (o.match_id, o.bookmaker, lower(o.selection))
                  o.match_id::text mid, o.bookmaker, lower(o.selection) sel, o.odds::float odds,
                  o.timestamp, m.date ko, m.result res, m.score_home sh, m.score_away sa
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.market = %s AND o.is_live IS NOT TRUE AND o.odds > 1.01
              AND o.timestamp <= m.date AND o.timestamp > m.date - interval '6 hours'
              AND m.status = 'finished' AND m.date > now() - make_interval(days => %s)
              AND m.date < now() - interval '3 hours'
              AND lower(o.selection) = ANY(%s)
            ORDER BY o.match_id, o.bookmaker, lower(o.selection), o.timestamp DESC""",
        (market, days, list(sides))) or []
    fx = defaultdict(list)
    meta = {}
    for r in rows:
        fx[r["mid"]].append(r)
        meta[r["mid"]] = (r["ko"], r["res"], r["sh"], r["sa"])
    return fx, meta


def outcome_index(market, res, sh, sa):
    if market == "1x2":
        return {"home": 0, "draw": 1, "away": 2}.get(str(res))
    if sh is None or sa is None:
        return None
    return 0 if sh + sa > 2.5 else 1


def members(rows, sides, at, max_age_min=180, exclude=()):
    sets = sets_from_rows(rows, sides)
    sets = {b: v for b, v in sets.items() if b != PIN and b not in exclude}
    a = compute_anchor(sets, sides, at=at, max_age_min=max_age_min)
    if a.source != "consensus":
        return None, None
    per_book = {b: [p[s] for s in sides] for b, p in a.books.items()}
    over = {b: sum(1 / o for o in sets[b][0]) - 1 for b in per_book}
    return per_book, over


# ── training: accuracy weights and residual correlations ────────────────────
def train(market, fixtures, meta, sides, holdout_start):
    exc = defaultdict(list)
    resid = defaultdict(dict)       # book -> {(mid, side): residual}
    for mid, rows in fixtures.items():
        ko, res, sh, sa = meta[mid]
        if ko >= holdout_start:
            continue
        k = outcome_index(market, res, sh, sa)
        if k is None:
            continue
        pb, _ = members(rows, sides, ko)
        if not pb:
            continue
        for b in pb:
            loo = _norm([mean(pb[o][i] for o in pb if o != b) for i in range(len(sides))])
            exc[b].append(ll(pb[b], k) - ll(loo, k))
            for i in (0, len(sides) - 1):
                resid[b][(mid, i)] = logit(pb[b][i]) - logit(loo[i])
    e = {b: mean(v) for b, v in exc.items() if len(v) >= MIN_TRAIN}
    emin = min(e.values())
    d = median(x - emin for x in e.values()) or 1e-4
    acc_w = {b: 1.0 / (x - emin + d) for b, x in e.items()}
    acc_w["_median"] = median(acc_w.values())
    rho = {}
    bl = [b for b in resid if len(resid[b]) >= MIN_TRAIN]
    for i, a in enumerate(bl):
        for b in bl[i + 1:]:
            common = resid[a].keys() & resid[b].keys()
            if len(common) < 200:
                continue
            xa = [resid[a][c] for c in common]
            xb = [resid[b][c] for c in common]
            ma, mb = mean(xa), mean(xb)
            va = sum((x - ma) ** 2 for x in xa)
            vb = sum((x - mb) ** 2 for x in xb)
            if va > 0 and vb > 0:
                rho[tuple(sorted((a, b)))] = sum((x - ma) * (y - mb) for x, y in zip(xa, xb)) / math.sqrt(va * vb)
    return e, {b: len(v) for b, v in exc.items()}, acc_w, rho


# ── stats ───────────────────────────────────────────────────────────────────
def paired(d):
    if len(d) < 30:
        return None
    n, mu = len(d), mean(d)
    sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (n - 1))
    t = mu / (sd / math.sqrt(n)) if sd else 0.0
    p = math.erfc(abs(t) / math.sqrt(2))
    return {"n": n, "mean": mu, "median": median(d), "wins": sum(x < 0 for x in d) / n, "t": t, "p": p}


def holm(tests: list[dict], alpha=0.05):
    order = sorted(range(len(tests)), key=lambda i: tests[i]["p"])
    m = len(tests)
    stop = False
    for rank, i in enumerate(order):
        if stop or tests[i]["p"] > alpha / (m - rank):
            stop = True
            tests[i]["holm_sig"] = False
        else:
            tests[i]["holm_sig"] = True
    return tests


# ── main ────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=116)
    a = ap.parse_args()
    now = execute_query("SELECT now() t")[0]["t"]
    holdout_start = now - timedelta(days=HOLDOUT_DAYS)
    tests = []
    for market in MARKETS:
        sides = market_sides(market)
        fixtures, meta = outcome_rows(market, OUTCOME_DAYS)
        e, n_tr, acc_w, rho = train(market, fixtures, meta, sides, holdout_start)
        print(f"\n######## {market} — {len(fixtures)} fixtures in {OUTCOME_DAYS} d; TRAIN < {holdout_start:%Y-%m-%d}")
        print("  book accuracy on TRAIN (mean LL excess vs LOO consensus; negative = better than the rest) → acc weight")
        for b, x in sorted(e.items(), key=lambda kv: kv[1]):
            print(f"    {b:16s} n={n_tr[b]:6d} excess {x:+.5f}  w {acc_w[b]:8.1f}")
        top = sorted(rho.items(), key=lambda kv: -kv[1])[:10]
        print("  most correlated residual pairs (TRAIN): " + ", ".join(f"{p[0]}~{p[1]} {r:+.2f}" for p, r in top))
        print(f"  median pairwise rho {median(rho.values()):+.3f} over {len(rho)} pairs")
        kw = {"acc_w": acc_w, "rho": rho}

        # (i) outcome log-loss on HOLDOUT
        d = defaultdict(list)
        base = []
        for mid, rows in fixtures.items():
            ko, res, sh, sa = meta[mid]
            if ko < holdout_start:
                continue
            k = outcome_index(market, res, sh, sa)
            if k is None:
                continue
            pb, over = members(rows, sides, ko)
            if not pb:
                continue
            l0 = ll(combine(pb, "ew"), k)
            base.append(l0)
            for v in VARIANTS:
                d[v].append(ll(combine(pb, v, over=over, **kw), k) - l0)
        print(f"\n  (i) OUTCOME LOG-LOSS, HOLDOUT — {len(base)} fixtures, ew LL {mean(base):.5f}"
              "  (Δ = variant − ew; negative = variant better)")
        for v in VARIANTS:
            r = paired(d[v])
            print(f"    {v:8s} n={r['n']:6d} Δ {r['mean']:+.5f} (med {r['median']:+.5f}, wins {100*r['wins']:4.1f}%, t {r['t']:+.2f})")
            tests.append(dict(r, market=market, crit="outcome_LL", variant=v))

        # (ii) closing-line error, last CLOSE_DAYS
        fx = execute_query(
            """SELECT m.id::text id, m.date ko FROM matches m
                WHERE m.status = 'finished' AND m.date > now() - make_interval(days => %s)
                  AND m.date < now() - interval '3 hours'""", (CLOSE_DAYS,)) or []
        ko_of = {f["id"]: f["ko"] for f in fx}
        ids = list(ko_of)
        rng = random.Random(a.seed)
        LEADS = (120, 30)
        dp = {L: defaultdict(list) for L in LEADS}
        dl = {L: defaultdict(list) for L in LEADS}
        base_p = {L: [] for L in LEADS}
        base_l = {L: [] for L in LEADS}
        for i in range(0, len(ids), 300):            # batch: fetch, score, discard
            by = defaultdict(list)
            for r in execute_query(
                    """SELECT o.match_id::text mid, o.bookmaker, lower(o.selection) sel,
                              o.odds::float odds, o.timestamp
                         FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                        WHERE o.match_id = ANY(%s::uuid[]) AND o.market = %s
                          AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND lower(o.selection) = ANY(%s)
                          AND o.timestamp <= m.date AND o.timestamp > m.date - interval '215 minutes'""",
                    (ids[i:i + 300], market, list(sides))) or []:
                by[r["mid"]].append(r)
            for mid, rows in by.items():
                ko = ko_of[mid]
                close = sets_from_rows(rows, sides)
                pin = close.get(PIN)
                pin_close = None
                if pin and (ko - pin[1]).total_seconds() / 60 <= 15:
                    pin_close = devig(pin[0])
                for lead in LEADS:
                    t = ko - timedelta(minutes=lead)
                    rt = [r for r in rows if r["timestamp"] <= t]
                    pb, over = members(rt, sides, t, max_age_min=90)
                    if not pb:
                        continue
                    if pin_close:
                        e0 = err(combine(pb, "ew"), pin_close)
                        base_p[lead].append(e0)
                        for v in VARIANTS:
                            dp[lead][v].append(err(combine(pb, v, over=over, **kw), pin_close) - e0)
                    if len(pb) >= 8:
                        held = rng.sample(sorted(pb), 3)
                        hc = []
                        for b in held:
                            s_ = close.get(b)
                            if s_ and (ko - s_[1]).total_seconds() / 60 <= 60:
                                p = devig(s_[0])
                                if p and min(p) > 0:
                                    hc.append(p)
                        if len(hc) == 3:
                            target = _norm([mean(p[j] for p in hc) for j in range(len(sides))])
                            pb2 = {b: v for b, v in pb.items() if b not in held}
                            ov2 = {b: over[b] for b in pb2}
                            e0 = err(combine(pb2, "ew"), target)
                            base_l[lead].append(e0)
                            for v in VARIANTS:
                                dl[lead][v].append(err(combine(pb2, v, over=ov2, **kw), target) - e0)
        for lead in LEADS:
            print(f"\n  (ii) CLOSING-LINE ERROR, anchor at KO−{lead} — Δ in prob points (negative = variant closer)"
                  f"{'' if lead == 120 else '   [secondary, not in the test family]'}")
            bp, bl_ = base_p[lead], base_l[lead]
            print(f"    vs pin_close: {len(bp)} fixtures, ew err {mean(bp) if bp else float('nan'):.4f}")
            for v in VARIANTS:
                r = paired(dp[lead][v])
                if r:
                    print(f"      {v:8s} n={r['n']:5d} Δ {r['mean']:+.5f} (med {r['median']:+.5f}, wins {100*r['wins']:4.1f}%, t {r['t']:+.2f})")
                    if lead == 120:
                        tests.append(dict(r, market=market, crit="err_pin_close", variant=v))
            print(f"    vs loo_close (3 held-out books): {len(bl_)} fixtures, ew err {mean(bl_) if bl_ else float('nan'):.4f}")
            for v in VARIANTS:
                r = paired(dl[lead][v])
                if r:
                    print(f"      {v:8s} n={r['n']:5d} Δ {r['mean']:+.5f} (med {r['median']:+.5f}, wins {100*r['wins']:4.1f}%, t {r['t']:+.2f})")
                    if lead == 120:
                        tests.append(dict(r, market=market, crit="err_loo_close", variant=v))

    holm(tests)
    print(f"\n######## HOLM over {len(tests)} tests (family fixed at 30)")
    for x in sorted(tests, key=lambda x: x["p"]):
        tag = ("BETTER" if x["mean"] < 0 else "WORSE") if x["holm_sig"] else "tie"
        print(f"  {x['market']:14s} {x['crit']:14s} {x['variant']:8s} Δ {x['mean']:+.5f} t {x['t']:+6.2f} p {x['p']:.2e}  {tag}")
    adopted = []
    for v in VARIANTS:
        ok = True
        for market in MARKETS:
            def get(c):
                return next((x for x in tests if x["market"] == market and x["crit"] == c and x["variant"] == v), None)
            o, p_, l_ = get("outcome_LL"), get("err_pin_close"), get("err_loo_close")
            if not (o and p_ and o["holm_sig"] and o["mean"] < 0 and p_["holm_sig"] and p_["mean"] < 0):
                ok = False
            if l_ and l_["holm_sig"] and l_["mean"] > 0:
                ok = False
        if ok:
            adopted.append(v)
    print(f"\nADOPTION RULE → {'ADOPT ' + ', '.join(adopted) if adopted else 'no variant qualifies; equal weight stays'}")


if __name__ == "__main__":
    main()
