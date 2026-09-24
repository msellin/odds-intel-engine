"""#119 step C — would our sharp-anchored strategies do better with the Betfair
EXCHANGE close as the anchor instead of (or blended with) Pinnacle?

READ-ONLY. Uses the football-data.co.uk CSV closes in `odds_snapshots`
(`scripts/ingest_football_data_csvs.py`, CSV-FULL-EXTRACT), 2024-07..2026-05.

DATA RULE. Every book's CSV close for a match carries the SAME timestamp (the
CSV row's kickoff stamp; ANALYSIS_GOTCHAS §77), while AF-live closes for the
same books carry other timestamps. So every book is read at the exact timestamp
of that match's 'Betfair Exchange' close row: this keeps Pinnacle, the exchange
and the soft books on one CSV row — the same instant — and excludes AF-live rows.

DE-VIG. football-data's BFEC* columns are exchange BACK prices at the close,
before commission. Their overround is ~100-102% (measured and printed below),
not a bookmaker's 102-108%. Both anchors go through the same Shin de-vig
(`workers.model.devig.devig`); when an exchange book sums to <=100% (back prices
can cross) Shin has nothing to attribute and `devig` falls back to proportional
normalisation. Commission is irrelevant to the anchor — it is a cost to a bettor
on the exchange, not a property of the probability.

PRE-REGISTERED (written before the first run, 2026-09-24):
  Anchors: PIN (Pinnacle), EXC (exchange), BLEND (mean of the two de-vigged
    vectors), AGREE (BLEND, but only on matches where every leg of the two
    de-vigged vectors is within AGREE_TOL = 2.0 pp; otherwise no bet).
  Rule = the live sharp trigger (`pick_triggers._SHARP_MIN_EDGE_BY_MARKET`,
    `bot_trigger_*_sharp_v1`): edge = p_anchor - 1/price >= 3%, <= 8% ceiling,
    no odds floor; price = best CSV close among the soft books (1X2: Bet365,
    BetWin, Betfred, William Hill, 1xBet; O/U 2.5: Bet365 — the only soft book
    with an O/U column); outlier guard price <= fair_odds x 1.25 (1X2) / 1.30
    (O/U) per §9. Flat 1 unit, settled on matches.result / score.
  Tests: 6 = {EXC, BLEND, AGREE} vs PIN x {1X2, O/U 2.5}. Statistic = per-match
    profit difference (variant minus PIN, 0 where neither bets), two-sided t;
    Holm over the 6. Everything else printed (sharpness, ROI vs 0, the
    forward-test rule, the disagreement split) is DESCRIPTIVE.
  Sharpness: paired per-match log-loss, PIN vs EXC vs BLEND, both markets;
    mean, median, t, and a 1%/99% winsorised mean as the outlier guard (§9/§58).
  Expected outcome, stated in advance: EXC ~= PIN on log-loss (the 7,328-match
    note said identical to 4 dp); no variant differs from PIN on ROI after Holm.

KNOWN LIMITATION. This is CLOSE-to-close: the soft price and the anchor are
both closing prices. The live bots decide hours before kickoff, when the anchor
is less sharp and soft prices are staler. The replay answers "which anchor
separates good from bad soft prices at the close", not "what the live bots
would have earned".

Usage:  python3 scripts/sharp_anchor_exchange_replay.py
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ANCHORS = ("PIN", "EXC", "BLEND", "AGREE")
AGREE_TOL = 0.02
EDGE_FLOOR, EDGE_CEIL = 0.03, 0.08
GUARD = {"1x2": 1.25, "over_under_25": 1.30}
SOFT = {"1x2": ("Bet365", "BetWin", "Betfred", "William Hill", "1xBet"),
        "over_under_25": ("Bet365",)}
SIDES = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
BOOKS = ("Pinnacle", "Betfair Exchange") + SOFT["1x2"]
N_TESTS = 6


def load():
    from workers.api_clients.db import execute_query
    return execute_query("""
        WITH bx AS (
            SELECT DISTINCT match_id, timestamp AS ts FROM odds_snapshots
             WHERE bookmaker = 'Betfair Exchange' AND is_closing
               AND market IN ('1x2', 'over_under_25'))
        SELECT DISTINCT ON (o.match_id, o.bookmaker, o.market, o.selection)
               o.match_id::text AS mid, o.bookmaker, o.market, o.selection,
               o.odds::float AS odds, m.result, m.score_home, m.score_away,
               m.season, l.name AS league, l.country
          FROM odds_snapshots o
          JOIN bx ON bx.match_id = o.match_id AND o.timestamp = bx.ts
          JOIN matches m ON m.id = o.match_id
          JOIN leagues l ON l.id = m.league_id
         WHERE o.is_closing AND o.market IN ('1x2', 'over_under_25')
           AND o.bookmaker = ANY(%s) AND m.result IS NOT NULL
           AND m.score_home IS NOT NULL
         ORDER BY o.match_id, o.bookmaker, o.market, o.selection, o.odds DESC
    """, [list(BOOKS)])


def build(rows):
    """{market: {mid: {'q': {book: [odds by side]}, 'y': idx, 'meta': ...}}}"""
    from workers.model.devig import devig
    raw = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    meta = {}
    for r in rows:
        raw[r["market"]][r["mid"]][r["bookmaker"]][r["selection"]] = r["odds"]
        meta[r["mid"]] = r
    out = {}
    for mk, by_mid in raw.items():
        sides = SIDES[mk]
        recs = {}
        for mid, books in by_mid.items():
            q = {b: [s.get(x) for x in sides] for b, s in books.items()}
            q = {b: v for b, v in q.items() if all(o and o > 1.0 for o in v)}
            if "Pinnacle" not in q or "Betfair Exchange" not in q:
                continue
            r = meta[mid]
            if mk == "1x2":
                y = sides.index(r["result"])
            else:
                y = 0 if (r["score_home"] + r["score_away"]) > 2.5 else 1
            pp, pe = devig(q["Pinnacle"]), devig(q["Betfair Exchange"])
            if pp is None or pe is None:
                continue
            pb = [(a + b) / 2 for a, b in zip(pp, pe)]
            recs[mid] = {
                "q": q, "y": y, "season": r["season"],
                "league": f"{r['country']} / {r['league']}",
                "or_pin": sum(1 / o for o in q["Pinnacle"]),
                "or_exc": sum(1 / o for o in q["Betfair Exchange"]),
                "p": {"PIN": pp, "EXC": pe, "BLEND": pb},
                "gap": max(abs(a - b) for a, b in zip(pp, pe)),
            }
        out[mk] = recs
    return out


def tstat(x):
    x = np.asarray(x, float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return float("nan"), float("nan")
    t = x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))
    return t, 2 * stats.t.sf(abs(t), len(x) - 1)


def winsor_mean(x, lo=1, hi=99):
    x = np.asarray(x, float)
    a, b = np.percentile(x, [lo, hi])
    return float(np.clip(x, a, b).mean())


def coverage(data):
    print("\n=== 1. DATA: matches with BOTH a Pinnacle and an exchange CSV close ===")
    for mk, recs in data.items():
        by = defaultdict(int)
        for r in recs.values():
            by[(r["league"], r["season"])] += 1
        print(f"\n{mk}: {len(recs)} matches, {len({k[0] for k in by})} leagues, "
              f"seasons {sorted({k[1] for k in by})}")
        leagues = defaultdict(int)
        for (lg, _), n in by.items():
            leagues[lg] += n
        for lg, n in sorted(leagues.items(), key=lambda kv: -kv[1]):
            print(f"   {lg:45s} {n:5d}")
        orp = np.array([r["or_pin"] for r in recs.values()])
        ore = np.array([r["or_exc"] for r in recs.values()])
        for name, o in (("Pinnacle", orp), ("Exchange", ore)):
            print(f"   overround {name:9s} median {np.median(o):.4f}  p5 {np.percentile(o, 5):.4f}"
                  f"  p95 {np.percentile(o, 95):.4f}  <=1.000: {np.mean(o <= 1.0):.1%}")
        soft = defaultdict(int)
        for r in recs.values():
            for b in SOFT[mk]:
                soft[b] += b in r["q"]
        print("   soft-book coverage:", dict(soft))


def sharpness(data):
    print("\n=== 2. SHARPNESS: paired outcome log-loss (lower = sharper) ===")
    res = {}
    for mk, recs in data.items():
        ll = {a: np.array([-math.log(max(r["p"][a][r["y"]], 1e-12)) for r in recs.values()])
              for a in ("PIN", "EXC", "BLEND")}
        print(f"\n{mk}  n={len(recs)}")
        for a, v in ll.items():
            print(f"   {a:6s} mean LL {v.mean():.5f}")
        for a in ("EXC", "BLEND"):
            d = ll[a] - ll["PIN"]
            t, p = tstat(d)
            print(f"   {a}-PIN  mean {d.mean():+.6f}  median {np.median(d):+.6f}  "
                  f"winsor1/99 {winsor_mean(d):+.6f}  t={t:+.2f}  p={p:.3f}")
            res[(mk, a)] = (d.mean(), t, p)
        # per season, so a pooled tie cannot hide opposite halves
        for s in sorted({r["season"] for r in recs.values()}):
            idx = [i for i, r in enumerate(recs.values()) if r["season"] == s]
            d = ll["EXC"][idx] - ll["PIN"][idx]
            t, _ = tstat(d)
            print(f"      season {s}: EXC-PIN {d.mean():+.6f} (n={len(idx)}, t={t:+.2f})")
        gaps = np.array([r["gap"] for r in recs.values()])
        # when they DISAGREE, which one was right? (the stale-flag question)
        dis = gaps > AGREE_TOL
        for a in ("EXC", "BLEND"):
            d = (ll[a] - ll["PIN"])[dis]
            t, p = tstat(d)
            print(f"   disagree>2pp only (n={dis.sum()}): {a}-PIN mean {d.mean():+.6f} "
                  f"median {np.median(d):+.6f} t={t:+.2f} p={p:.3f}")
        print(f"   max-leg |pEXC-pPIN|: median {np.median(gaps)*100:.2f}pp  p90 "
              f"{np.percentile(gaps, 90)*100:.2f}pp  share >2pp {np.mean(gaps > AGREE_TOL):.1%}")
    return res


def bets(data, mk, anchor, rule="trigger"):
    """Per-match list of (profit, odds, won, sel_gap). One bet per match-selection
    at the best soft close."""
    out = {}
    for mid, r in data[mk].items():
        if anchor == "AGREE" and r["gap"] > AGREE_TOL:
            out[mid] = []
            continue
        p = r["p"]["BLEND" if anchor == "AGREE" else anchor]
        placed = []
        for i in range(len(SIDES[mk])):
            prices = [r["q"][b][i] for b in SOFT[mk] if b in r["q"]]
            if not prices or p[i] <= 0:
                continue
            price = max(prices)
            if price > GUARD[mk] / p[i]:
                continue
            if rule == "trigger":
                edge = p[i] - 1.0 / price
                ok = EDGE_FLOOR <= edge <= EDGE_CEIL
            else:  # published forward-test rule: p*price-1 >= 3%, odds <= 4.0
                ok = p[i] * price - 1.0 >= 0.03 and price <= 4.0
            if ok:
                won = i == r["y"]
                gap_i = abs(r["p"]["EXC"][i] - r["p"]["PIN"][i])
                placed.append((price - 1.0 if won else -1.0, price, won, gap_i))
        out[mid] = placed
    return out


def summarise(b):
    flat = [x for v in b.values() for x in v]
    if not flat:
        return 0, float("nan"), float("nan"), float("nan"), float("nan")
    pr = np.array([x[0] for x in flat])
    se = pr.std(ddof=1) / math.sqrt(len(pr)) if len(pr) > 1 else float("nan")
    return (len(pr), pr.mean(), se, np.mean([x[2] for x in flat]),
            np.mean([x[1] for x in flat]))


def replay(data):
    print("\n=== 3. STRATEGY REPLAY at the CLOSE (sharp trigger rule: 3% <= edge <= 8%) ===")
    tests = []
    for mk in data:
        base = {a: bets(data, mk, a) for a in ANCHORS}
        print(f"\n{mk}: {len(data[mk])} candidate matches")
        print(f"   {'anchor':6s} {'n':>6s} {'ROI':>8s} {'SE':>7s} {'hit':>6s} {'avg odds':>8s}")
        for a in ANCHORS:
            n, roi, se, hit, ao = summarise(base[a])
            print(f"   {a:6s} {n:6d} {roi:+8.2%} {se:7.2%} {hit:6.1%} {ao:8.2f}")
        for a in ("EXC", "BLEND", "AGREE"):
            d = [sum(x[0] for x in base[a][m]) - sum(x[0] for x in base["PIN"][m])
                 for m in data[mk]]
            t, p = tstat(d)
            tests.append((f"{mk} {a} vs PIN", float(np.sum(d)), t, p))
        # descriptive: does exchange disagreement flag bad Pinnacle-anchored bets?
        pin = [x for v in base["PIN"].values() for x in v]
        for lab, keep in (("agree <=2pp", lambda g: g <= AGREE_TOL),
                          ("disagree >2pp", lambda g: g > AGREE_TOL)):
            sub = np.array([x[0] for x in pin if keep(x[3])])
            if len(sub) > 1:
                print(f"   PIN bets where exchange {lab:13s}: n={len(sub):5d}  ROI "
                      f"{sub.mean():+.2%}  SE {sub.std(ddof=1)/math.sqrt(len(sub)):.2%}")
        # descriptive: the published forward-test rule under each anchor
        for a in ANCHORS:
            n, roi, se, hit, _ = summarise(bets(data, mk, a, rule="forward"))
            print(f"   [forward-test rule] {a:6s} n={n:5d} ROI {roi:+.2%} SE {se:.2%} hit {hit:.1%}")
        # context: every soft-close selection, no gate (the margin)
        allp = []
        for r in data[mk].values():
            for i in range(len(SIDES[mk])):
                pr = [r["q"][b][i] for b in SOFT[mk] if b in r["q"]]
                if pr:
                    allp.append(max(pr) - 1 if i == r["y"] else -1)
        print(f"   [context] every selection at best soft close: n={len(allp)} ROI {np.mean(allp):+.2%}")

    from scripts.wheatcroft_replication import holm
    assert len(tests) == N_TESTS, f"pre-registered {N_TESTS} tests, ran {len(tests)}"
    adj = holm([p for *_, p in tests])
    print(f"\n=== 4. PRE-REGISTERED TESTS ({N_TESTS}, Holm) — total-profit difference vs PIN ===")
    for (lab, tot, t, p), pa in zip(tests, adj):
        print(f"   {lab:32s} Δunits {tot:+8.2f}  t={t:+.2f}  p={p:.3f}  Holm p={pa:.3f}")


def main():
    from dotenv import load_dotenv
    load_dotenv()
    data = build(load())
    coverage(data)
    sharpness(data)
    replay(data)


if __name__ == "__main__":
    main()
