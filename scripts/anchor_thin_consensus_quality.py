"""THIN CONSENSUS CLOSE — how good is a 3–4-book close? ([[#116]], 2026-09-24)

Read-only. Heavy — run on the VPS next to the DB:
    venv/bin/python3 scripts/anchor_thin_consensus_quality.py [--no-table | --table-only]

WHY
---
leg_clv_sharp now carries clv_cons_thin (migration 394): the leg's CLV against a
3–4-book consensus close, filled only where no >=5-book close exists. Before anyone
reads that number we need to know how far a 3–4-book close sits from a real one.

WHAT IS MEASURED
  C1  coverage — legs/fixtures that gained a CLV, by ledger and market (from the table)
  C2  closing quality by SUBSAMPLING, last 7 d (full intraday history, §59): on fixtures
      with a >=5-book close, a random 3- and 4-book subset close vs the full close,
      and each vs the Pinnacle close. Error units:
        maxp   max over sides |Δp| (probability points)
        clvpt  mean over sides |p_sub/p_ref − 1| — the CLV error a leg inherits
      Subsampling well-covered fixtures is OPTIMISTIC: real thin fixtures are lower
      leagues priced by fewer, softer books. So also:
  C3  REAL thin fixtures that also have a Pinnacle close: thin close vs Pinnacle close,
      beside >=5-book fixtures' consensus close vs Pinnacle close.
  C4  outcome log-loss, 120 d settled fixtures: random 3-/4-book subset vs the full
      consensus, paired per fixture (mean of 10 draws).
  C5  agreement of clv_cons_thin with clv_sharp where both exist, beside clv_cons vs
      clv_sharp on the same period (mean diff, median, r).
"""
from __future__ import annotations

import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402
from workers.utils.anchor import PIN, compute_anchor, market_sides, sets_from_rows  # noqa: E402

MARKETS = ("1x2", "over_under_25")
DRAWS = 10


def _norm(v):
    s = sum(v)
    return [x / s for x in v]


def ew(per_book, books):
    return _norm([mean(per_book[b][i] for b in books) for i in range(len(per_book[books[0]]))])


def maxp(p, q):
    return max(abs(a - b) for a, b in zip(p, q))


def clvpt(p, q):
    return mean(abs(a / b - 1) for a, b in zip(p, q))


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(int(q * len(xs)), len(xs) - 1)]


def dist(label, xs):
    if not xs:
        print(f"    {label:34s} n=0")
        return
    print(f"    {label:34s} n={len(xs):5d} mean {mean(xs):.4f} med {median(xs):.4f} "
          f"p90 {pct(xs, .9):.4f} p99 {pct(xs, .99):.4f}")


def paired(d):
    n, mu = len(d), mean(d)
    sd = math.sqrt(sum((x - mu) ** 2 for x in d) / (n - 1))
    return n, mu, median(d), mu / (sd / math.sqrt(n)) if sd else 0.0


def corr(x, y):
    mx, my = mean(x), mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return num / math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))


def c1_c5():
    print("\n==== C1 · coverage gained (leg_clv_sharp, kickoff in the last 7 d) ====")
    rows = execute_query(
        """SELECT c.ledger, CASE WHEN c.market LIKE 'over_under%%' THEN 'over_under'
                                 WHEN c.market LIKE 'corners%%' THEN 'corners' ELSE c.market END mk,
                  count(*) legs,
                  count(*) FILTER (WHERE c.cons_status = 'ok') cons_ok,
                  count(*) FILTER (WHERE c.cons_thin_status = 'ok_thin') thin_ok,
                  count(*) FILTER (WHERE c.cons_thin_status = 'ok_thin' AND c.status <> 'ok') thin_only_clv,
                  count(*) FILTER (WHERE c.status = 'ok' OR c.cons_status = 'ok') had_any
             FROM leg_clv_sharp c JOIN matches m ON m.id = c.match_id
            WHERE m.date > now() - interval '7 days'
            GROUP BY 1, 2 ORDER BY 1, 3 DESC""") or []
    print(f"  {'ledger':20s} {'market':16s} {'legs':>6s} {'cons>=5':>8s} {'thin':>6s} {'thin, no sharp':>15s} {'had any CLV':>12s}")
    tot = defaultdict(int)
    for r in rows:
        if r["legs"] < 20:
            continue
        print(f"  {r['ledger']:20s} {r['mk']:16s} {r['legs']:6d} {r['cons_ok']:8d} {r['thin_ok']:6d} "
              f"{r['thin_only_clv']:15d} {r['had_any']:12d}")
    for r in rows:
        for k in ("legs", "cons_ok", "thin_ok", "thin_only_clv", "had_any"):
            tot[k] += r[k]
    print(f"  TOTAL legs {tot['legs']}, had a sharp or >=5 CLV {tot['had_any']}, thin added {tot['thin_ok']} "
          f"({tot['thin_only_clv']} with no clv_sharp either)")
    fx = execute_query(
        """SELECT count(DISTINCT c.match_id) FILTER (WHERE c.cons_thin_status = 'ok_thin') thin_fx,
                  count(DISTINCT c.match_id) FILTER (WHERE c.cons_thin_status = 'ok_thin'
                        AND NOT EXISTS (SELECT 1 FROM leg_clv_sharp z WHERE z.match_id = c.match_id
                                        AND (z.status = 'ok' OR z.cons_status = 'ok'))) thin_only_fx,
                  count(DISTINCT c.match_id) all_fx
             FROM leg_clv_sharp c JOIN matches m ON m.id = c.match_id
            WHERE m.date > now() - interval '7 days'""")[0]
    print(f"  fixtures: {fx['all_fx']} with legs; {fx['thin_fx']} gained a thin close; "
          f"{fx['thin_only_fx']} had NO other CLV at all")

    print("\n==== C5 · agreement with clv_sharp (legs with both, last 7 d) ====")
    for col, cond in (("clv_cons_thin", "c.cons_thin_status = 'ok_thin'"), ("clv_cons", "c.cons_status = 'ok'")):
        r = execute_query(
            f"""SELECT c.clv_sharp::float s, c.{col}::float x, c.market
                  FROM leg_clv_sharp c JOIN matches m ON m.id = c.match_id
                 WHERE m.date > now() - interval '7 days' AND c.status = 'ok' AND {cond}
                   AND abs(c.clv_sharp) < 1 AND abs(c.{col}) < 1""") or []
        if len(r) < 20:
            print(f"  {col:14s} n={len(r)} (too few)")
            continue
        d = [a["x"] - a["s"] for a in r]
        print(f"  {col:14s} n={len(r):5d} mean {col}−clv_sharp {100*mean(d):+.2f} pts (median {100*median(d):+.2f}), "
              f"mean |diff| {100*mean(abs(v) for v in d):.2f} pts, r {corr([a['s'] for a in r], [a['x'] for a in r]):.3f}")


def c2_c3(market):
    sides = market_sides(market)
    fx = execute_query("""SELECT m.id::text id, m.date ko FROM matches m
                           WHERE m.status = 'finished' AND m.date > now() - interval '7 days'
                             AND m.date < now() - interval '3 hours'""") or []
    ko_of = {f["id"]: f["ko"] for f in fx}
    ids = list(ko_of)
    rng = random.Random(116)
    e = defaultdict(list)
    for i in range(0, len(ids), 300):
        by = defaultdict(list)
        for r in execute_query(
                """SELECT o.match_id::text mid, o.bookmaker, lower(o.selection) sel, o.odds::float odds, o.timestamp
                     FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                    WHERE o.match_id = ANY(%s::uuid[]) AND o.market = %s AND o.is_live IS NOT TRUE
                      AND o.odds > 1.01 AND lower(o.selection) = ANY(%s)
                      AND o.timestamp <= m.date AND o.timestamp > m.date - interval '60 minutes'""",
                (ids[i:i + 300], market, list(sides))) or []:
            by[r["mid"]].append(r)
        for mid, rows in by.items():
            ko = ko_of[mid]
            sets = sets_from_rows(rows, sides)
            pin = sets.get(PIN)
            pin_c = devig(pin[0]) if pin and (ko - pin[1]).total_seconds() / 60 <= 15 else None
            a = compute_anchor({b: v for b, v in sets.items() if b != PIN}, sides, at=ko,
                               max_age_min=60, min_thin_books=3)
            if a.source == "consensus":
                pb = {b: [p[s] for s in sides] for b, p in a.books.items()}
                full = ew(pb, list(pb))
                if pin_c:
                    e["full→pin maxp"].append(maxp(full, pin_c))
                    e["full→pin clvpt"].append(clvpt(full, pin_c))
                for k in (3, 4):
                    sub = ew(pb, rng.sample(sorted(pb), k))
                    e[f"sub{k}→full maxp"].append(maxp(sub, full))
                    e[f"sub{k}→full clvpt"].append(clvpt(sub, full))
                    if pin_c:
                        e[f"sub{k}→pin maxp"].append(maxp(sub, pin_c))
                        e[f"sub{k}→pin clvpt"].append(clvpt(sub, pin_c))
            elif a.source == "consensus_thin" and pin_c:
                e[f"REAL thin{a.n_books}→pin maxp"].append(maxp([a.probs[s] for s in sides], pin_c))
                e[f"REAL thin{a.n_books}→pin clvpt"].append(clvpt([a.probs[s] for s in sides], pin_c))
    print(f"\n==== C2/C3 · {market} closing quality, last 7 d (lower = closer) ====")
    for k in ("sub3→full", "sub4→full", "full→pin", "sub3→pin", "sub4→pin", "REAL thin3→pin", "REAL thin4→pin"):
        for u in ("maxp", "clvpt"):
            dist(f"{k} {u}", e[f"{k} {u}"])


def c4(market):
    sides = market_sides(market)
    rows = execute_query(
        """SELECT DISTINCT ON (o.match_id, o.bookmaker, lower(o.selection))
                  o.match_id::text mid, o.bookmaker, lower(o.selection) sel, o.odds::float odds,
                  o.timestamp, m.date ko, m.result res, m.score_home sh, m.score_away sa
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.market = %s AND o.is_live IS NOT TRUE AND o.odds > 1.01
              AND o.timestamp <= m.date AND o.timestamp > m.date - interval '6 hours'
              AND m.status = 'finished' AND m.date > now() - interval '120 days'
              AND lower(o.selection) = ANY(%s)
            ORDER BY o.match_id, o.bookmaker, lower(o.selection), o.timestamp DESC""",
        (market, list(sides))) or []
    fx = defaultdict(list)
    meta = {}
    for r in rows:
        fx[r["mid"]].append(r)
        meta[r["mid"]] = r
    rng = random.Random(116)
    d = {3: [], 4: []}
    for mid, rs in fx.items():
        m = meta[mid]
        if market == "1x2":
            k = {"home": 0, "draw": 1, "away": 2}.get(str(m["res"]))
        else:
            k = None if m["sh"] is None or m["sa"] is None else (0 if m["sh"] + m["sa"] > 2.5 else 1)
        if k is None:
            continue
        sets = {b: v for b, v in sets_from_rows(rs, sides).items() if b != PIN}
        a = compute_anchor(sets, sides, at=m["ko"])
        if a.source != "consensus":
            continue
        pb = {b: [p[s] for s in sides] for b, p in a.books.items()}
        full = -math.log(ew(pb, list(pb))[k])
        for n in (3, 4):
            sub = mean(-math.log(ew(pb, rng.sample(sorted(pb), n))[k]) for _ in range(DRAWS))
            d[n].append(sub - full)
    print(f"\n==== C4 · {market} outcome log-loss, 120 d: k-book subset − full consensus (positive = subset worse) ====")
    for n in (3, 4):
        nn, mu, md, t = paired(d[n])
        print(f"    {n} books  n={nn:6d} ΔLL {mu:+.5f} (med {md:+.5f}, t {t:+.2f})")


def main():
    if "--no-table" not in sys.argv:          # C1/C5 need migration 394 + the backfill
        c1_c5()
    if "--table-only" in sys.argv:
        return
    for mk in MARKETS:
        c2_c3(mk)
    for mk in MARKETS:
        c4(mk)


if __name__ == "__main__":
    main()
