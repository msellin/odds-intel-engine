#!/usr/bin/env python3
"""#106 DE-VIG BAKE-OFF — step 0: the frozen BEFORE panel.

Owner, 2026-09-24: "take some example fixtures, measure them now, and after #106
measure again". The bookmaker's margin does not change with our method — what changes
is how we SPLIT it across outcomes, i.e. the fair probability, the edge and whether a
pick clears the 3% floor. So the panel freezes the RAW quotes a real publish run saw,
plus what today's method (Shin, everywhere) made of them. After #106 the same raw
quotes are re-priced under every method; nothing is re-fetched, so any difference is
the method and only the method.

REPLAY. The live rule (`scripts/publish_picks_forward_test.py::load_candidates`) runs
with now() = the moment of a past publish run: latest quote per (match, market,
selection, book) in the 6 h before, fixtures kicking off 45 min - 14 h later, phantom
books excluded. The rule's own constants and grading are imported, not copied.
CHECK P: before the panel is trusted, the replay must reproduce the stored p_sharp and
edge of the published legs.

PANEL. Every published leg of the two published arms (live = Pinnacle anchor,
consensus_anchor = de-vig-each-book-then-average), plus a stratified sample of pool
markets that were NOT published (by arm × market × league tier), so the panel shows
both what we published and what we passed on.

    python3 scripts/devig_bakeoff.py baseline      # writes dev/active/devig-panel-2026-09-24.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / "dev" / "active" / "devig-panel-2026-09-24.json"
SAMPLE_PER_STRATUM = 25


def replay(asof):
    """side_q per (match, market) exactly as load_candidates would have seen it at `asof`."""
    from workers.api_clients.db import execute_query
    from scripts.publish_picks_forward_test import (
        EXCLUDED_BOOKS, LOOKAHEAD_H, MARKETS, MIN_LEAD_MIN)
    rows = execute_query("""
        SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
               o.match_id::text mid, o.market, o.selection, o.bookmaker,
               o.odds::float AS odds, o.timestamp, m.date AS kickoff,
               l.name AS league, l.tier AS league_tier
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE o.market = ANY(%s) AND o.is_live IS NOT TRUE
           AND NOT (o.bookmaker = ANY(%s))
           AND m.date > %s::timestamptz + make_interval(mins => %s)
           AND m.date < %s::timestamptz + make_interval(hours => %s)
           AND o.timestamp > %s::timestamptz - interval '6 hours'
           AND o.timestamp <= %s::timestamptz
         ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o.timestamp DESC""",
        (list(MARKETS), list(EXCLUDED_BOOKS), asof, MIN_LEAD_MIN, asof, LOOKAHEAD_H, asof, asof))
    q, meta = defaultdict(lambda: defaultdict(dict)), {}
    for r in rows:
        q[(r["mid"], r["market"])][r["selection"]][r["bookmaker"]] = (r["odds"], r["timestamp"])
        meta[r["mid"]] = r
    return q, meta


def price_market(sides, side_q, anchor, devig_fn, tier):
    """Mirror of load_candidates' per-market body, with the de-vig method as a parameter.
    Returns {side: leg} or None when the market has no usable anchor."""
    from scripts.publish_picks_forward_test import (
        ALIGN_MIN, CONSENSUS_MIN_BOOKS, GRADE_PANEL, MAX_ANCHOR_OVERROUND, MAX_ODDS, MAX_RATIO,
        MIN_EDGE, grade_consensus_pick)
    if anchor == "consensus":
        per, stamps = [], []
        for b in {b for s in sides for b in (side_q.get(s) or {})}:
            qs = [(side_q.get(s) or {}).get(b) for s in sides]
            if any(x is None or x[0] <= 1.0 for x in qs):
                continue
            p = devig_fn([x[0] for x in qs])
            if p:
                per.append(p); stamps.append(max(x[1] for x in qs))
        if len(per) < CONSENSUS_MIN_BOOKS:
            return None
        probs = [sum(p[i] for p in per) / len(per) for i in range(len(sides))]
        anchor_ts, anchor_odds, overround = max(stamps), {s: 1 / p for s, p in zip(sides, probs)}, 0.0
    else:
        pin = {s: (side_q.get(s) or {}).get("Pinnacle") for s in sides}
        if any(pin[s] is None for s in sides):
            return None
        anchor_ts = max(pin[s][1] for s in sides)
        anchor_odds = {s: pin[s][0] for s in sides}
        overround = sum(1 / o for o in anchor_odds.values()) - 1
        if overround > MAX_ANCHOR_OVERROUND:
            return None
        probs = devig_fn([anchor_odds[s] for s in sides])
        if probs is None:
            return None
    out = {}
    for s, p in zip(sides, probs):
        aligned = {b: v for b, v in (side_q.get(s) or {}).items()
                   if abs((v[1] - anchor_ts).total_seconds()) / 60 <= ALIGN_MIN}
        if not aligned:
            continue
        book, (odds, _ts) = max(aligned.items(), key=lambda kv: kv[1][0])
        if odds > MAX_ODDS or odds / anchor_odds[s] - 1 > MAX_RATIO:
            continue
        edge = p * odds - 1
        grade = None
        if anchor == "consensus":
            idx = sides.index(s)
            panel = {}
            for pb in GRADE_PANEL:
                qs = [(side_q.get(x) or {}).get(pb) for x in sides]
                if all(x is not None and x[0] > 1.0 for x in qs):
                    bp = devig_fn([x[0] for x in qs])
                    if bp:
                        panel[pb] = bp[idx]
            grade = grade_consensus_pick(edge, odds, book, tier, panel)[0]
        out[s] = {"p": p, "odds": odds, "book": book, "edge": edge,
                  "qualifies": edge >= MIN_EDGE, "grade": grade, "overround": overround}
    return out


def _jsonable_quotes(side_q):
    return {s: {b: [o, t.isoformat()] for b, (o, t) in books.items()} for s, books in side_q.items()}


def baseline() -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from workers.api_clients.db import execute_query
    from workers.model.devig import devig
    from scripts.publish_picks_forward_test import MARKETS

    pub = execute_query("""
        SELECT arm, match_id::text mid, market, selection, odds::float odds, bookmaker,
               edge::float edge, p_sharp::float p_sharp, grade, published_at, rule_version
          FROM picks_forward_test WHERE arm IN ('live', 'consensus_anchor')
         ORDER BY published_at""")
    runs = sorted({p["published_at"].replace(second=0, microsecond=0) for p in pub})
    records, seen, check = [], set(), []
    pool_by_stratum = defaultdict(list)
    pub_keys = {(p["arm"], p["mid"], p["market"]) for p in pub}
    pub_by_run = defaultdict(list)
    for p in pub:
        pub_by_run[p["published_at"].replace(second=0, microsecond=0)].append(p)
    for i, run in enumerate(runs):
        asof = run + timedelta(minutes=1)          # the run's own quotes, nothing later
        q, meta = replay(asof)
        for (mid, market), side_q in q.items():
            sides = MARKETS[market]
            tier = meta[mid].get("league_tier")
            for anchor, arm in (("pinnacle", "live"), ("consensus", "consensus_anchor")):
                legs = price_market(sides, side_q, anchor, devig, tier)
                if not legs:
                    continue
                rec = {"run": run.isoformat(), "arm": arm, "match_id": mid, "market": market,
                       "sides": sides, "kickoff": meta[mid]["kickoff"].isoformat(),
                       "league": meta[mid].get("league"), "tier": tier,
                       "quotes": _jsonable_quotes(side_q),
                       "shin": legs, "published": None}
                for p in pub_by_run.get(run, []):
                    if p["arm"] == arm and p["mid"] == mid and p["market"] == market:
                        rec["published"] = {k: p[k] for k in ("selection", "odds", "bookmaker", "edge",
                                                              "p_sharp", "grade", "rule_version")}
                        leg = legs.get(p["selection"])
                        check.append((p, leg))
                key = (arm, mid, market)
                if rec["published"]:
                    records.append(rec); seen.add(key)
                elif key not in pub_keys and key not in seen:
                    seen.add(key)
                    pool_by_stratum[(arm, market, tier)].append(rec)
        print(f"  run {i + 1}/{len(runs)} {run:%m-%d %H:%M}  markets {len(q)}", flush=True)

    # CHECK P — the replay must reproduce what was actually published
    ok = sum(1 for p, leg in check if leg and abs(leg["p"] - p["p_sharp"]) < 1e-6
             and abs(leg["odds"] - p["odds"]) < 1e-9)
    print(f"\nCHECK P: replay reproduces {ok}/{len(pub)} published legs exactly "
          f"({len(check)} found in a replayed pool)")
    rng = random.Random(106)
    for k in sorted(pool_by_stratum, key=str):
        v = pool_by_stratum[k]
        records += rng.sample(v, min(SAMPLE_PER_STRATUM, len(v)))
    PANEL.write_text(json.dumps({"created": "2026-09-24", "method_before": "shin (every market)",
                                 "check_p": {"reproduced": ok, "published": len(pub)},
                                 "records": records}, indent=0, default=str))
    print(f"panel: {sum(1 for r in records if r['published'])} published + "
          f"{sum(1 for r in records if not r['published'])} sampled pool markets -> {PANEL.relative_to(ROOT)}")
    return 0


# ── TEST 1: which method is right, per market ────────────────────────────────
SCORE_MARKETS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under"),
                 "over_under_35": ("over", "under"), "1x2_1h": ("home", "draw", "away")}
PAIR_TOL_S = 120


def _outcome(market, r):
    if market == "1x2_1h":
        h, a = r["hth"], r["hta"]
    else:
        h, a = r["sh"], r["sa"]
    if h is None or a is None:
        return None
    if market.startswith("1x2"):
        return 0 if h > a else (1 if h == a else 2)
    line = 2.5 if market.endswith("25") else 3.5
    return 0 if h + a > line else 1


def load_sets():
    """Per (match, market): Pinnacle EARLY set (first complete, non-closing) and CLOSE set
    (the is_closing set if present — football-data ingests are stamped AT kickoff, see
    ANALYSIS_GOTCHAS §77 — else the latest complete pre-kickoff set <= 60 min old)."""
    from workers.api_clients.db import execute_query
    out = {}
    for market, sides in SCORE_MARKETS.items():
        rows = execute_query("""
            WITH x AS (
              SELECT o.match_id, o.selection, o.odds::float od, o.timestamp ts,
                     COALESCE(o.is_closing, false) cl, m.date ko
                FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
               WHERE o.bookmaker = 'Pinnacle' AND o.market = %s AND o.is_live IS NOT TRUE
                 AND o.odds > 1.01 AND m.status = 'finished'
                 AND (o.timestamp < m.date OR COALESCE(o.is_closing, false)))
            SELECT e.match_id::text mid, e.selection, e.od e_od, e.ts e_ts, c.od c_od, c.ts c_ts, c.cl c_cl, e.ko
              FROM (SELECT DISTINCT ON (match_id, selection) * FROM x WHERE NOT cl
                     ORDER BY match_id, selection, ts ASC) e
              FULL JOIN (SELECT DISTINCT ON (match_id, selection) * FROM x
                     ORDER BY match_id, selection, cl DESC, ts DESC) c
                ON c.match_id = e.match_id AND c.selection = e.selection""", (market,))
        by = defaultdict(dict)
        for r in rows:
            if r["mid"] and r["selection"] in sides:
                by[r["mid"]][r["selection"]] = r
        for mid, d in by.items():
            if not all(s in d for s in sides):
                continue
            rec = {}
            for kind, od, ts in (("early", "e_od", "e_ts"), ("close", "c_od", "c_ts")):
                if any(d[s][od] is None for s in sides):
                    continue
                stamps = [d[s][ts] for s in sides]
                if (max(stamps) - min(stamps)).total_seconds() > PAIR_TOL_S:
                    continue
                if kind == "close" and not d[sides[0]]["c_cl"] and \
                        (d[sides[0]]["ko"] - max(stamps)).total_seconds() > 3600:
                    continue
                rec[kind] = [d[s][od] for s in sides]
            if rec:
                out[(mid, market)] = rec
    mids = list({m for m, _ in out})
    res = {}
    for i in range(0, len(mids), 5000):
        for r in execute_query("""SELECT id::text mid, date, score_home sh, score_away sa,
                   ht_score_home hth, ht_score_away hta FROM matches WHERE id = ANY(%s::uuid[])""",
                               (mids[i:i + 5000],)):
            res[r["mid"]] = r
    return out, res


def score() -> int:
    import math
    import random
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from workers.model.devig import METHODS
    sets, res = load_sets()
    alts = [m for m in METHODS if m != "shin"]
    rng = random.Random(106)
    cells = []
    table = []
    for market, sides in SCORE_MARKETS.items():
        for kind in ("early", "close"):
            rows = []
            for (mid, mk), rec in sets.items():
                if mk != market or kind not in rec or mid not in res:
                    continue
                y = _outcome(market, res[mid])
                if y is None:
                    continue
                probs = {m: f(rec[kind]) for m, f in METHODS.items()}
                if any(p is None for p in probs.values()):
                    continue
                rows.append((str(res[mid]["date"])[:10], y, rec[kind], probs))
            if not rows:
                continue
            ll = {m: [-math.log(max(r[3][m][r[1]], 1e-12)) for r in rows] for m in METHODS}
            days = sorted({r[0] for r in rows})
            idx = defaultdict(list)
            for i, r in enumerate(rows):
                idx[r[0]].append(i)
            line = {"market": market, "kind": kind, "n": len(rows),
                    "ll": {m: sum(v) / len(v) for m, v in ll.items()}}
            for m in alts:
                d = [a - b for a, b in zip(ll["shin"], ll[m])]      # > 0: m beats Shin
                mean = sum(d) / len(d)
                boots = []
                for _ in range(1000):
                    pick = [i for day in (rng.choice(days) for _ in days) for i in idx[day]]
                    boots.append(sum(d[i] for i in pick) / len(pick))
                p = sum(1 for b in boots if b <= 0) / len(boots)
                cells.append([market, kind, m, len(rows), mean, p])
            # calibration by the Shin-probability band of each outcome (favourite-longshot)
            for m in METHODS:
                for lo, hi in ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.72), (0.72, 0.84), (0.84, 1.0)):
                    pred = obs = n = 0
                    for r in rows:
                        for j in range(len(sides)):
                            if lo <= r[3]["shin"][j] < hi:
                                pred += r[3][m][j]; obs += (r[1] == j); n += 1
                    if n >= 50:
                        table.append((market, kind, m, f"{lo:.2f}-{hi:.2f}", n, pred / n, obs / n))
            print(f"{market:14s} {kind:5s} n={len(rows):6d}  " + "  ".join(
                f"{m} {line['ll'][m]:.5f}" for m in METHODS), flush=True)
    # Holm over the whole family (markets × price points × 4 alternatives)
    order = sorted(range(len(cells)), key=lambda i: cells[i][5])
    run = 0.0
    for r_, i in enumerate(order):
        run = max(run, min(1.0, (len(cells) - r_) * cells[i][5]))
        cells[i].append(run)
    print(f"\nALTERNATIVE vs SHIN — mean LL gain per fixture (nats; > 0 = better than Shin), Holm m={len(cells)}")
    for c in cells:
        print(f"  {c[0]:14s} {c[1]:5s} {c[2]:13s} n={c[3]:6d} gain {c[4]:+.6f}  p={c[5]:.3f}  Holm={c[6]:.3f}"
              f"{'  BETTER THAN SHIN' if c[4] > 0 and c[6] < 0.05 else ''}")
    print("\nCALIBRATION (close) — predicted vs observed, by the outcome's Shin-probability band")
    for market in SCORE_MARKETS:
        for band in sorted({t[3] for t in table if t[0] == market and t[1] == "close"}):
            row = [t for t in table if t[0] == market and t[1] == "close" and t[3] == band]
            if not row:
                continue
            obs, n = row[0][6], row[0][4]
            print(f"  {market:14s} {band}  n={n:6d} observed {obs:.3f}  " +
                  "  ".join(f"{t[2]} {t[5]:.3f}" for t in row))
    return 0


# ── TEST 2: the AFTER — re-price the frozen panel under every method ─────────
def _band(odds):
    return "1.20-1.60" if 1.2 <= odds <= 1.6 else ("<1.20" if odds < 1.2 else ("1.60-2.50" if odds <= 2.5 else ">2.50"))


# Methods NOT measurably worse than Shin in Test 1 (2026-09-24): proportional and
# odds-ratio lose to Shin on 1x2 at both price points (bootstrap p(gain<=0) ≈ 1.0);
# additive and power tie or edge ahead. A worst-method gate must span only these —
# gating on proportional would reject picks on a method we measured to be wrong.
CREDIBLE = ("shin", "additive", "power")


def compare() -> int:
    from datetime import datetime
    from workers.model.devig import METHODS, devig_by
    from scripts.publish_picks_forward_test import MIN_EDGE
    d = json.loads(PANEL.read_text())
    rows = []                 # one per (record, side) that Shin qualified OR any method qualifies
    for r in d["records"]:
        side_q = {s: {b: (v[0], datetime.fromisoformat(v[1])) for b, v in books.items()}
                  for s, books in r["quotes"].items()}
        anchor = "consensus" if r["arm"] == "consensus_anchor" else "pinnacle"
        per_m = {m: price_market(r["sides"], side_q, anchor, lambda o, m=m: devig_by(m, o), r["tier"]) or {}
                 for m in METHODS}
        for s in r["sides"]:
            legs = {m: per_m[m].get(s) for m in METHODS}
            shin = legs["shin"]
            if shin is None:
                continue
            # the frozen BEFORE must be reproduced exactly by today's Shin (inputs untouched)
            before = r["shin"].get(s)
            assert before and abs(before["edge"] - shin["edge"]) < 1e-9, (r["match_id"], s)
            edges = {m: (l["edge"] if l else None) for m, l in legs.items()}
            defined = [e for e in edges.values() if e is not None]
            pub = r["published"] and r["published"]["selection"] == s
            cred = [edges[m] for m in CREDIBLE if edges[m] is not None]
            rows.append({"arm": r["arm"], "market": r["market"], "side": s, "published": bool(pub),
                         "worst_cred": min(cred),
                         "grade": (r["published"] or {}).get("grade") if pub else shin["grade"],
                         "band": _band(shin["odds"]), "edges": edges, "worst": min(defined),
                         "best": max(defined), "shin_q": shin["edge"] >= MIN_EDGE})
    pubs = [x for x in rows if x["published"]]
    print(f"PUBLISHED legs in the panel: {len(pubs)} — edge under each method (mean) and survival at >= {MIN_EDGE:.0%}")
    groups = defaultdict(list)
    for x in pubs:
        groups[(x["arm"], x["grade"] or "-", x["band"])].append(x)
    hdr = "  ".join(f"{m[:6]:>6s}" for m in METHODS)
    print(f"  {'arm':17s} {'grade':5s} {'odds band':10s} {'n':>3s}   mean edge: {hdr}   survive worst / worst credible")
    for k in sorted(groups, key=str):
        v = groups[k]
        means = "  ".join(f"{100*sum(x['edges'][m] for x in v if x['edges'][m] is not None)/max(1,sum(1 for x in v if x['edges'][m] is not None)):+5.1f}%" for m in METHODS)
        surv = sum(1 for x in v if x["worst"] >= MIN_EDGE)
        cs = sum(1 for x in v if x["worst_cred"] >= MIN_EDGE)
        print(f"  {k[0]:17s} {k[1]:5s} {k[2]:10s} {len(v):3d}   {means}   {surv}/{len(v)} / {cs}/{len(v)}")
    for arm in ("live", "consensus_anchor"):
        v = [x for x in pubs if x["arm"] == arm]
        worst_m = defaultdict(int)
        for x in v:
            worst_m[min((e, m) for m, e in x["edges"].items() if e is not None)[1]] += 1
        print(f"  {arm}: {sum(1 for x in v if x['worst'] >= MIN_EDGE)}/{len(v)} survive the worst method, "
              f"{sum(1 for x in v if x['worst_cred'] >= MIN_EDGE)}/{len(v)} the worst CREDIBLE {CREDIBLE}, "
              f"{sum(1 for x in v if x['edges']['shin'] >= MIN_EDGE)}/{len(v)} clear 3% under Shin in this replay; "
              f"worst method was {dict(worst_m)}")
    pool = [x for x in rows if not x["published"]]
    print(f"\nPOOL sample: {len(pool)} legs — qualify under Shin {sum(x['shin_q'] for x in pool)}, "
          f"under ANY method {sum(1 for x in pool if x['best'] >= MIN_EDGE)}, under EVERY method "
          f"{sum(1 for x in pool if x['worst'] >= MIN_EDGE)}")
    spread = [100 * (x["best"] - x["worst"]) for x in rows]
    for arm in ("live", "consensus_anchor"):
        sp = sorted(100 * (x["best"] - x["worst"]) for x in rows if x["arm"] == arm)
        print(f"  {arm:17s} edge spread best−worst method: median {sp[len(sp)//2]:.2f}pp, 90th pct {sp[int(.9*len(sp))]:.2f}pp")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["baseline", "score", "compare"])
    a = ap.parse_args()
    return {"baseline": baseline, "score": score, "compare": compare}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
