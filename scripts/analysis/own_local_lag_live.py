#!/usr/bin/env python3
"""[[#191]] OWN research idea 3 — LOCAL LAG vs a LIVE anchor.

READ-ONLY research. Nothing in production reads or is changed by this script.

MOTIVATION. #186 (dev/active/local-lead-signal-findings.md, ANALYSIS_GOTCHAS §88) found that when the
Estonian books split from API-Football's Pinnacle, the LIVE Betfair Exchange sided with the Estonian
books 17 of 17 times. AF-Pinnacle's value is typically ~2 h old, so it cannot judge a local price.
Hypothesis for the 🤖 OWN track: when the LIVE market (the exchange, and the faster Estonian books) has
moved and ONE of our Estonian books has not updated yet, that lagging book's stale price is value. The
judge is the exchange or an independent consensus close. It is NEVER AF-Pinnacle.

PRE-REGISTRATION — written 2026-09-26 BEFORE the first run. Nothing below is tuned on output.

DATA
  Matches   finished, result known, kick-off in [2026-09-24 08:00 UTC, run time), with at least one
            Betfair Exchange 1x2 capture (the reader started 2026-09-24 07:34 and runs every 15 min).
  Markets   1x2 (home/draw/away) and over_under_25 (over/under). These are the two markets the exchange
            reader and all four local books carry.
  LOCAL     Coolbet, Epicbet, Unibet-Site, Tonybet (Optibet: too few rows, ignored).
  Retention odds_snapshots keeps the full price path for ~7 days only (§59). The whole window here is
            within it, so every local scrape is present. Local scrapes run about every 30 min
            (Unibet-Site 30-120 min), and every scrape writes a row, changed or not.
  Sets      one COMPLETE set per book per fetch: all legs within 120 s of each other (§62). Pre-KO
            only, is_live false.

DEFINITIONS
  LIVE fair at instant t, for local book b:
    (i)  'exchange': the latest exchange capture at or before t that is no more than 30 min old and
         LIQUID (anchor.exchange_fair: every runner's back/lay spread <= 5% and >= EUR 1k matched).
         Fair = the normalised harmonic mid.
    (ii) else 'locals': the per-side MEDIAN of the de-vigged (devig.fair_prob) latest complete sets of
         the OTHER local books (never b), each no more than 30 min old at t. Needs >= 2 books.
         Renormalised to 1.
    (iii) else no live fair; b is not evaluated at t.
  LAG leg. Book b has consecutive complete sets at t0 < t1, with t1 - t0 <= 90 min and
    t1 in [KO-12h, KO-5min]. Side s fires at t1 when ALL of the following hold:
      * b's price on s is IDENTICAL at t0 and t1 (unchanged since before the move);
      * the live fair was formed from the SAME source at both instants, and its prob on s rose by
        >= MOVE = 0.02 between t0 and t1 (the live market moved toward s);
      * edge = odds_b,s(t1) x p_live,s(t1) - 1 >= X;
      * data-fault guards: no data_quality_findings row for (match, b) in wrong_fixture_board /
        mirrored_1x2 / single_market_off / swapped_two_way; no leg of b more than 1.5625x from the
        live fair odds (§67 — a divergence signal hunts for data faults).
    Bet: flat 1 unit on s at b's price at t1. Only the FIRST firing per (match, market, book, side).
  X grid   {0.02, 0.05}.

JUDGES
  EXCHANGE CLOSE   the last LIQUID exchange capture in [KO-30min, KO]. CLV = odds x p_close,s - 1.
  CONSENSUS CLOSE  anchor.compute_anchor at KO over the NON-local books' last complete pre-KO sets,
                   each <= 60 min old at KO, min_books 5 ('consensus' tier only). Excluded: Pinnacle
                   (frozen in AF, §88), the four local books, Unibet/Kambi and Coolbet feeds, and
                   anchor.NEVER_IN_ANCHOR. Caveat stated in advance: these are AF books too, so this
                   close carries AF's ~2 h refresh latency (§88). It is independent of the bet book,
                   but it is not live.

FAMILY (Holm, alpha 0.05) — 8 tests = 2 markets x 2 X x 2 judges; statistic = mean CLV.
  Discovery / holdout. The eligible matches are sorted by kick-off and split at the median kick-off.
  The FAMILY is evaluated on the HOLDOUT (later half) only. Discovery is reported descriptively.
  Nothing is tuned on it, because everything above is fixed now. It exists so that a reader can see
  whether an effect replicates.
  Uncertainty: bootstrap over MATCHES (all of a match's bets move together), 10,000 resamples,
  seed 191. Two-sided p = 2 x min(share <= 0, share >= 0). Judged only with n >= 30; otherwise
  'too few' (counted in Holm with p = 1). 'Positive' = Holm p < 0.05 and mean > 0; 'negative' =
  Holm p < 0.05 and mean < 0; else 'undetermined'.

DESCRIPTIVE, OUTSIDE THE FAMILY
  * flat ROI at the taken price (bootstrap by match); n per book; per live-fair source;
  * how long the lag lasts: at b's NEXT scrape after t1, is the price on s still >= the taken price?
    Also the minutes from t1 until b's price on s first changes (censored at KO);
  * how often it fires: bets per day of kick-offs;
  * UPDATE RHYTHM (part 3). A LIVE MOVE = a liquid exchange capture c whose fair prob on side s is
    >= 0.03 above a liquid capture 20-40 min earlier (c0), in [KO-12h, KO-15min]. Only the rising
    side with the largest rise counts. Moves in the same (match, market) closer than 60 min apart are
    collapsed to the first. The move must PERSIST: the next liquid capture keeps >= half the rise.
    For each local book with a complete set <= 60 min before c0 (baseline p_b0): follow time = the
    first of b's scrapes where p_b,s - p_b0 >= half the exchange rise, in minutes from c. It is
    negative when the book was already there (the book LED). Censored at KO = 'never followed'.
    Reported: median follow time, share led, share followed within 30/60/120 min, share never.
    Also each book's median scrape interval, because a follow time cannot be shorter than the
    interval.

EXPECTED RESULT (stated before the run)
  * Firing: frequent. A book that simply has not been re-scraped looks like a lag, and the exchange mid
    moves 2 pts on thin markets. Expect tens of fires per day at X = 0.02 and a handful at 0.05.
  * vs the EXCHANGE close: slightly positive mean CLV (+1..+3%). The move partly persists (#186: the
    exchange and the locals co-move). CI spans 0 on ~2.5 days of data; nothing survives Holm.
  * vs the CONSENSUS close: around -(local margin), -3..-6%. The AF books lag the live market, so they
    have not yet moved toward s.
  * ROI: noise. n is in the tens to low hundreds.
  * Update rhythm: Epicbet and Tonybet follow within one or two scrapes (median 30-60 min).
    Coolbet and Unibet-Site are slower (Unibet-Site is scraped only every 30-120 min).
  * Verdict expected: 'undetermined'. The exchange history is ~2.5 days long, and it cannot get longer
    than 7 days of local price path unless the results are accumulated run by run, because of the
    §59 retention.

AMENDMENT 1 (2026-09-26, AFTER the first run — post-hoc, descriptive, NOT in the family). The first run
fired ZERO bets at either X, so every family cell was 'too few'. Added: every UNCHANGED local leg with
the same live source at t0 and t1, split into 'lag' (live move >= 0.02) and 'control' (|move| < 0.005).
For each group: the edge distribution against the live fair, and CLV / ROI. This asks whether a lag
carries ANY information, even if too little to beat the margin. Also added: the exchange's share of
liquid captures by minutes to KO, which answers "is the exchange history long enough".

Run:  PYTHONPATH=. python3 scripts/analysis/own_local_lag_live.py [--dump out.json]
Findings: dev/active/own-research-local-lag-live.md
"""
from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median

from workers.api_clients.db import execute_query
from workers.model.devig import fair_prob
from workers.utils.anchor import GUARD_RATIO, NEVER_IN_ANCHOR, PIN, compute_anchor, exchange_fair

LOCAL = ("Coolbet", "Epicbet", "Unibet-Site", "Tonybet")
NOT_IN_CONSENSUS = set(LOCAL) | {PIN, "Optibet", "Unibet", "Unibet-Kambi", "Coolbet-OddsAPI", "20bet"} \
    | set(NEVER_IN_ANCHOR)
MARKETS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
START = "2026-09-24 08:00+00"
X_GRID = (0.02, 0.05)
MOVE = 0.02
LIVE_MAX_AGE = 30
PAIR_MAX_GAP = 90
WINDOW_H = 12
MIN_BEFORE_KO = 5
SET_TOL = 120
CONS_MAX_AGE = 60
EX_CLOSE = 30
RHY_MOVE, RHY_GAP_LO, RHY_GAP_HI, RHY_DEDUP, RHY_BASE_AGE = 0.03, 20, 40, 60, 60
MIN_N = 30
B = 10_000
SEED = 191
DQ_CHECKS = ("wrong_fixture_board", "mirrored_1x2", "single_market_off", "swapped_two_way")


# ── loading ──────────────────────────────────────────────────────────────────
def load():
    matches = execute_query(
        """SELECT m.id::text AS id, m.date AS ko, m.result, m.score_home, m.score_away, l.name AS league
             FROM matches m LEFT JOIN leagues l ON l.id = m.league_id
            WHERE m.status = 'finished' AND m.result IS NOT NULL AND m.date >= %s AND m.date < now()
              AND EXISTS (SELECT 1 FROM exchange_quotes e WHERE e.match_id = m.id AND e.market = '1x2')""",
        (START,)) or []
    ids = [m["id"] for m in matches]
    snaps, ex = [], []
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        snaps += execute_query(
            """SELECT o.match_id::text AS mid, o.bookmaker AS book, o.market, lower(o.selection) AS sel,
                      o.odds::float AS odds, o.timestamp AS ts
                 FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
                WHERE o.match_id = ANY(%s::uuid[]) AND o.market IN ('1x2','over_under_25')
                  AND COALESCE(o.is_live,false) = false AND o.odds > 1.01 AND o.timestamp <= m.date
                  AND (   (o.bookmaker = ANY(%s) AND o.timestamp >= m.date - interval '14 hours')
                       OR o.timestamp >= m.date - interval '75 minutes')""",
            (chunk, list(LOCAL))) or []
        ex += execute_query(
            """SELECT match_id::text AS mid, market, market_id, selection AS sel, back::float AS back,
                      lay::float AS lay, market_matched::float AS market_matched, captured_at AS ts
                 FROM exchange_quotes
                WHERE match_id = ANY(%s::uuid[]) AND market IN ('1x2','over_under_25')""",
            (chunk,)) or []
    dq = execute_query(
        "SELECT match_id::text AS mid, bookmaker FROM data_quality_findings WHERE check_name = ANY(%s)",
        (list(DQ_CHECKS),)) or []
    return matches, snaps, ex, {(d["mid"], d["bookmaker"]) for d in dq}


def book_sets(rows, sides):
    """rows of one (match, market, book) → chronological [(ts, {side: odds})], complete sets only."""
    rows = sorted((r for r in rows if r["sel"] in sides), key=lambda r: r["ts"])
    out, cur, head = [], {}, None
    for r in rows:
        if head is None or (r["ts"] - head).total_seconds() > SET_TOL:
            if head is not None and all(s in cur for s in sides):
                out.append((head, cur))
            cur, head = {}, r["ts"]
        cur[r["sel"]] = r["odds"]
    if head is not None and all(s in cur for s in sides):
        out.append((head, cur))
    return out


def exchange_series(rows, sides):
    """→ chronological [(ts, probs, liquid)] one per capture."""
    by = defaultdict(dict)
    for r in rows:
        by[(r["ts"], r["market_id"])][r["sel"]] = r
    out = []
    for (ts, _), q in sorted(by.items(), key=lambda kv: kv[0][0]):
        probs, liquid, _ = exchange_fair(q, sides)
        if probs:
            out.append((ts, probs, liquid))
    return out


def fp(odds, sides):
    p = fair_prob([odds[s] for s in sides])
    return dict(zip(sides, p)) if p else None


def latest_before(series, t, max_age):
    """series: chronological list of tuples whose [0] is ts."""
    i = bisect_right([x[0] for x in series], t) - 1
    if i < 0 or (t - series[i][0]).total_seconds() / 60 > max_age:
        return None
    return series[i]


def live_fair(t, b, exs, locsets, sides):
    e = latest_before(exs, t, LIVE_MAX_AGE)
    if e and e[2]:
        return "exchange", e[1]
    others = []
    for ob, ss in locsets.items():
        if ob == b:
            continue
        x = latest_before(ss, t, LIVE_MAX_AGE)
        if x:
            p = fp(x[1], sides)
            if p:
                others.append(p)
    if len(others) >= 2:
        m = {s: median(o[s] for o in others) for s in sides}
        tot = sum(m.values())
        return "locals", {s: v / tot for s, v in m.items()}
    return None, None


def outcome(m, market):
    if market == "1x2":
        return m["result"]
    if m["score_home"] is None or m["score_away"] is None:
        return None
    return "over" if m["score_home"] + m["score_away"] > 2 else "under"


# ── per match ────────────────────────────────────────────────────────────────
def analyse(matches, snaps, ex, dq):
    s_by = defaultdict(list)
    for r in snaps:
        s_by[(r["mid"], r["market"], r["book"])].append(r)
    e_by = defaultdict(list)
    for r in ex:
        e_by[(r["mid"], r["market"])].append(r)
    books_by = defaultdict(set)
    for (mid, mk, b) in s_by:
        books_by[(mid, mk)].add(b)

    bets, moves, cadence, cands = [], [], defaultdict(list), []
    cover, liq_by = defaultdict(int), defaultdict(lambda: [0, 0])
    for m in matches:
        ko = m["ko"]
        for mk, sides in MARKETS.items():
            res = outcome(m, mk)
            exs = exchange_series(e_by.get((m["id"], mk), []), sides)
            for tx, _, lq in exs:
                mtk = (ko - tx).total_seconds() / 60
                bucket = next(lab for lim, lab in ((30, "0-30"), (120, "30-120"), (360, "2-6h"),
                                                   (720, "6-12h"), (1e9, ">12h")) if mtk <= lim)
                liq_by[(mk, bucket)][0] += 1
                liq_by[(mk, bucket)][1] += bool(lq)
            locsets = {}
            for b in LOCAL:
                if (m["id"], b) in dq:
                    continue
                ss = [x for x in book_sets(s_by.get((m["id"], mk, b), []), sides) if x[0] >= ko - timedelta(hours=WINDOW_H + 2)]
                if ss:
                    locsets[b] = ss
                    for (a, _), (c, _) in zip(ss, ss[1:]):
                        cadence[b].append((c - a).total_seconds() / 60)
            if not locsets:
                continue
            cover[mk] += 1
            # closes
            exc = [x for x in exs if x[2] and ko - timedelta(minutes=EX_CLOSE) <= x[0] <= ko]
            ex_close = exc[-1][1] if exc else None
            cons_sets = {}
            for b in books_by[(m["id"], mk)]:
                if b in NOT_IN_CONSENSUS or b.lower().startswith(("unibet", "coolbet")):
                    continue
                ss = book_sets(s_by[(m["id"], mk, b)], sides)
                if ss and (ko - ss[-1][0]).total_seconds() / 60 <= CONS_MAX_AGE:
                    cons_sets[b] = ([ss[-1][1][s] for s in sides], ss[-1][0])
            ca = compute_anchor(cons_sets, sides, at=ko, min_books=5, max_age_min=CONS_MAX_AGE)
            cons_close = ca.probs if ca.source == "consensus" else None

            # ── LAG legs
            fired, seen = set(), set()
            for b, ss in locsets.items():
                for (t0, o0), (t1, o1) in zip(ss, ss[1:]):
                    if (t1 - t0).total_seconds() / 60 > PAIR_MAX_GAP:
                        continue
                    if not (ko - timedelta(hours=WINDOW_H) <= t1 <= ko - timedelta(minutes=MIN_BEFORE_KO)):
                        continue
                    src0, p0 = live_fair(t0, b, exs, locsets, sides)
                    src1, p1 = live_fair(t1, b, exs, locsets, sides)
                    if not src1 or src0 != src1:
                        continue
                    if any(max(o1[s] * p1[s], 1 / (o1[s] * p1[s])) > GUARD_RATIO for s in sides):
                        continue
                    for s in sides:
                        if o1[s] != o0[s]:
                            continue
                        mv = p1[s] - p0[s]
                        edge = o1[s] * p1[s] - 1
                        # AMENDMENT 1 (post-hoc, descriptive): every unchanged leg, lag vs control
                        cls = "lag" if mv >= MOVE else ("control" if abs(mv) < 0.005 else None)
                        if cls and (b, s, cls) not in seen:
                            seen.add((b, s, cls))
                            cands.append({
                                "mid": m["id"], "market": mk, "book": b, "cls": cls, "src": src1,
                                "edge": edge, "move": mv,
                                "clv_ex": o1[s] * ex_close[s] - 1 if ex_close else None,
                                "clv_cons": o1[s] * cons_close[s] - 1 if cons_close else None,
                                "roi": (o1[s] - 1 if res == s else -1.0) if res else None})
                        if (b, s) in fired:
                            continue
                        if mv < MOVE or edge < min(X_GRID):
                            continue
                        fired.add((b, s))
                        # lag duration
                        nxt = [x for x in ss if x[0] > t1]
                        still = (nxt[0][1][s] >= o1[s]) if nxt else None
                        chg = next((x for x in nxt if x[1][s] != o1[s]), None)
                        bets.append({
                            "mid": m["id"], "ko": ko, "league": m["league"], "market": mk, "book": b,
                            "side": s, "t": t1, "mtk": (ko - t1).total_seconds() / 60, "src": src1,
                            "odds": o1[s], "edge": edge, "move": mv,
                            "clv_ex": o1[s] * ex_close[s] - 1 if ex_close else None,
                            "clv_cons": o1[s] * cons_close[s] - 1 if cons_close else None,
                            "roi": (o1[s] - 1 if res == s else -1.0) if res else None,
                            "next_gap": (nxt[0][0] - t1).total_seconds() / 60 if nxt else None,
                            "still_next": still,
                            "min_to_change": (chg[0] - t1).total_seconds() / 60 if chg else None,
                        })

            # ── update rhythm
            liq = [x for x in exs if x[2]]
            last_move = None
            for j, (tc, pc, _) in enumerate(liq):
                if not (ko - timedelta(hours=WINDOW_H) <= tc <= ko - timedelta(minutes=15)):
                    continue
                base = [x for x in liq[:j] if RHY_GAP_LO <= (tc - x[0]).total_seconds() / 60 <= RHY_GAP_HI]
                if not base:
                    continue
                t0, pb, _ = base[-1]
                s = max(sides, key=lambda k: pc[k] - pb[k])
                rise = pc[s] - pb[s]
                if rise < RHY_MOVE:
                    continue
                if last_move and (tc - last_move).total_seconds() / 60 < RHY_DEDUP:
                    continue
                nxt = liq[j + 1] if j + 1 < len(liq) else None
                if not nxt or nxt[1][s] - pb[s] < rise / 2:
                    continue
                last_move = tc
                for b, ss in locsets.items():
                    bl = latest_before(ss, t0, RHY_BASE_AGE)
                    if not bl:
                        continue
                    pb0 = fp(bl[1], sides)
                    if not pb0:
                        continue
                    fol = None
                    for (tb, ob) in ss:
                        if tb <= bl[0]:
                            continue
                        pbb = fp(ob, sides)
                        if pbb and pbb[s] - pb0[s] >= rise / 2:
                            fol = (tb - tc).total_seconds() / 60
                            break
                    moves.append({"mid": m["id"], "market": mk, "book": b, "rise": rise, "follow": fol})
    return bets, moves, cadence, cover, cands, liq_by


# ── stats ────────────────────────────────────────────────────────────────────
def boot_cluster(items, key, seed=SEED):
    import numpy as np
    by = defaultdict(list)
    for r in items:
        if r[key] is not None:
            by[r["mid"]].append(r[key])
    if not by:
        return {"n": 0, "m": 0, "mean": None, "lo": None, "hi": None, "p": 1.0}
    sums = np.array([sum(v) for v in by.values()])
    cnts = np.array([len(v) for v in by.values()])
    n, k = int(cnts.sum()), len(by)
    mean = float(sums.sum() / n)
    if k < 2:
        return {"n": n, "m": k, "mean": mean, "lo": None, "hi": None, "p": 1.0}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(B, k))
    ms = np.sort(sums[idx].sum(axis=1) / cnts[idx].sum(axis=1))
    le, ge = float((ms <= 0).mean()), float((ms >= 0).mean())
    return {"n": n, "m": k, "mean": mean, "lo": float(np.quantile(ms, 0.025)),
            "hi": float(np.quantile(ms, 0.975)), "p": min(1.0, 2 * min(le, ge))}


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    adj, run = [0.0] * len(ps), 0.0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - k) * ps[i]))
        adj[i] = run
    return adj


def f(s):
    if not s or s["mean"] is None:
        return "n 0"
    ci = f"[{s['lo']*100:+.1f}, {s['hi']*100:+.1f}]" if s["lo"] is not None else "[—]"
    return f"n {s['n']:>4} ({s['m']} m)  {s['mean']*100:+6.2f}% {ci}  p {s['p']:.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump")
    a = ap.parse_args()
    matches, snaps, ex, dq = load()
    print(f"matches {len(matches)}  local/other snapshot rows {len(snaps)}  exchange rows {len(ex)}")
    bets, moves, cadence, cover, cands, liq_by = analyse(matches, snaps, ex, dq)
    print("matches with >= 1 local set, per market:", dict(cover))
    kos = sorted(m["ko"] for m in matches)
    split = kos[len(kos) // 2]
    days = max(1e-9, (kos[-1] - kos[0]).total_seconds() / 86400)
    print(f"KO span {kos[0]:%m-%d %H:%M} → {kos[-1]:%m-%d %H:%M} ({days:.2f} d); holdout from {split:%m-%d %H:%M}")

    print("\n== FAMILY (holdout, Holm over 8; mean CLV) ==")
    fam = []
    for mk in MARKETS:
        for x in X_GRID:
            for judge in ("clv_ex", "clv_cons"):
                sub = [b for b in bets if b["market"] == mk and b["edge"] >= x and b["ko"] >= split]
                s = boot_cluster(sub, judge)
                fam.append((mk, x, judge, s))
    ps = [s["p"] if s["n"] >= MIN_N else 1.0 for *_, s in fam]
    adj = holm(ps)
    for (mk, x, judge, s), pa in zip(fam, adj):
        if s["n"] < MIN_N:
            v = "too few"
        elif pa < 0.05:
            v = "POSITIVE" if s["mean"] > 0 else "NEGATIVE"
        else:
            v = "undetermined"
        print(f"  {mk:14} X={x:.2f} {judge:8}  {f(s)}  Holm {pa:.3f}  {v}")

    for label, cond in (("DISCOVERY", lambda b: b["ko"] < split), ("ALL", lambda b: True)):
        print(f"\n== {label} (descriptive) ==")
        for mk in MARKETS:
            for x in X_GRID:
                sub = [b for b in bets if b["market"] == mk and b["edge"] >= x and cond(b)]
                print(f"  {mk:14} X={x:.2f}")
                for key in ("clv_ex", "clv_cons", "roi"):
                    print(f"     {key:8} {f(boot_cluster(sub, key))}")

    print("\n== ALL bets, X=0.02: per book / per live source ==")
    base = [b for b in bets if b["edge"] >= 0.02]
    for grp, keyf in (("book", lambda b: b["book"]), ("src", lambda b: b["src"]),
                      ("market", lambda b: b["market"])):
        vals = sorted({keyf(b) for b in base})
        for v in vals:
            sub = [b for b in base if keyf(b) == v]
            print(f"  {grp}={v:12} clv_ex {f(boot_cluster(sub, 'clv_ex'))}")
            print(f"  {'':17} clv_cons {f(boot_cluster(sub, 'clv_cons'))}   roi {f(boot_cluster(sub, 'roi'))}")
    for x in X_GRID:
        sub = [b for b in bets if b["edge"] >= x]
        print(f"\nfires X={x:.2f}: {len(sub)} bets on {len({b['mid'] for b in sub})} matches "
              f"= {len(sub)/days:.1f}/day; median edge {median([b['edge'] for b in sub])*100:.1f}% "
              f"median mins before KO {median([b['mtk'] for b in sub]):.0f}" if sub else f"fires X={x}: 0")
    sub = [b for b in base if b["still_next"] is not None]
    if sub:
        print(f"lag duration (X=0.02): next scrape median {median([b['next_gap'] for b in sub]):.0f} min later; "
              f"price still >= taken at next scrape {sum(b['still_next'] for b in sub)}/{len(sub)}")
        ch = [b["min_to_change"] for b in base if b["min_to_change"] is not None]
        print(f"   price on the side changed before KO in {len(ch)}/{len(base)}; median {median(ch) if ch else float('nan'):.0f} min after the fire")
    print(f"   exchange close present {sum(b['clv_ex'] is not None for b in base)}/{len(base)}; "
          f"consensus close present {sum(b['clv_cons'] is not None for b in base)}/{len(base)}")

    print("\n== UPDATE RHYTHM (exchange moves >= 0.03 in 20-40 min, persisting) ==")
    print(f"  distinct moves {len({(mv['mid'], mv['market']) for mv in moves})} (match-market), book-move pairs {len(moves)}")
    for b in LOCAL:
        sub = [mv for mv in moves if mv["book"] == b]
        cad = cadence.get(b) or [0]
        if not sub:
            print(f"  {b:12} no moves  scrape interval median {median(cad):.0f} min")
            continue
        fol = [mv["follow"] for mv in sub if mv["follow"] is not None]
        n = len(sub)
        led = sum(1 for v in fol if v <= 0)
        w = lambda lim: sum(1 for v in fol if v <= lim)
        print(f"  {b:12} n {n:>4}  median follow {median(fol) if fol else float('nan'):+5.0f} min  "
              f"led {led/n:4.0%}  ≤30 {w(30)/n:4.0%}  ≤60 {w(60)/n:4.0%}  ≤120 {w(120)/n:4.0%}  "
              f"never {1-len(fol)/n:4.0%}  | scrape interval median {median(cad):.0f} min")
    print("\n== AMENDMENT 1 (post-hoc, descriptive): every UNCHANGED local leg, lag (live move >= 0.02) vs control (|move| < 0.005) ==")
    for mk in MARKETS:
        for cls in ("lag", "control"):
            sub = [c for c in cands if c["market"] == mk and c["cls"] == cls]
            if not sub:
                continue
            e = sorted(c["edge"] for c in sub)
            q = lambda z: e[min(len(e) - 1, int(z * len(e)))] * 100
            print(f"  {mk:14} {cls:8} n {len(sub):>5}  edge vs live fair p50 {q(.5):+.1f}% p90 {q(.9):+.1f}% "
                  f"max {e[-1]*100:+.1f}%  >=0: {sum(x >= 0 for x in e)}  >=2%: {sum(x >= .02 for x in e)}")
            for key in ("clv_ex", "clv_cons", "roi"):
                print(f"     {key:8} {f(boot_cluster(sub, key))}")
    print("  per book, lag minus control (clv_ex means):")
    for b in LOCAL:
        for mk in MARKETS:
            la = [c for c in cands if c["book"] == b and c["market"] == mk and c["cls"] == "lag"]
            co = [c for c in cands if c["book"] == b and c["market"] == mk and c["cls"] == "control"]
            sl, sc = boot_cluster(la, "clv_ex"), boot_cluster(co, "clv_ex")
            if sl["mean"] is not None and sc["mean"] is not None:
                print(f"    {b:12} {mk:14} lag {sl['mean']*100:+.2f}% (n {sl['n']})  control {sc['mean']*100:+.2f}% (n {sc['n']})"
                      f"  edge p50 lag {median([c['edge'] for c in la])*100:+.1f}%")
    print("\n== exchange capture liquidity by minutes to KO (liquid / all captures) ==")
    for mk in MARKETS:
        print("  " + mk + ": " + "  ".join(f"{bk} {liq_by[(mk, bk)][1]}/{liq_by[(mk, bk)][0]}"
                                           for bk in ("0-30", "30-120", "2-6h", "6-12h", ">12h")))
    if a.dump:
        with open(a.dump, "w") as fh:
            json.dump({"bets": bets, "moves": moves}, fh, default=str, indent=1)


if __name__ == "__main__":
    main()
