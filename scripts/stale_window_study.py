"""[[#121]] ODDS-OBSERVATORY Phase 2 (i) — stale windows at the Estonian books after a
sharp (Pinnacle) move. READ-ONLY.

Pre-registered in docs/STALE_WINDOW_STUDY_2026_09_24.md (committed before the first run).
Summary of the design (the doc is authoritative):
  * window  : finished matches that kicked off in the last 7 days (full intraday
              history, ANALYSIS_GOTCHAS §59); markets 1x2 and over_under_25 (line 2.5/NULL)
  * sets    : one complete set per fetch per book, legs grouped by GAP (<=120 s from
              the set's first row) — Coolbet stamps each leg separately (§62)
  * move    : consecutive Pinnacle sets <=90 min apart, Shin p1[s]/p0[s]-1 >= 3%
  * stale   : book's price on s at its first set in [t1, t1+30'] within 1% of its
              price at its last set in [t0-90', t0]
  * treat   : stale AND q*p1-1 >= X (X in 0%, 3%) AND §9 guard; first per unit
  * control : quiet Pinnacle (<1.5% vs previous set and every set in the prior 120')
              AND q*p-1 >= X AND guard; first per unit, treatment units removed
  * exclude : (fixture, book) whose set is >1.5625x AND >4 pp from a >=4-book median
  * grade   : CLV = q * p_close - 1 (Shin, break-even 0, §70) vs
              (1) Betfair-Exchange close (n=0 expected, counted),
              (2) PRIMARY leave-Pinnacle-out >=5-book consensus close (compute_anchor),
              (3) SECONDARY Pinnacle close (<=180' old)
  * tests   : 4 books x 2 markets x 2 X x 2 graders x {vs0, T-C} = 64, Holm.

Usage: python3 scripts/stale_window_study.py [--days 7]
"""
from __future__ import annotations

import argparse
import math
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.model.devig import devig  # noqa: E402
from workers.utils.anchor import compute_anchor, sets_from_rows  # noqa: E402

EE_BOOKS = ("Coolbet", "Epicbet", "Unibet-Site", "Tonybet")
PIN = "Pinnacle"
SIDES = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
GUARD = {"1x2": 1.25, "over_under_25": 1.30}
MOVE_MIN = 0.03            # Pinnacle fair-prob rise on the backed side
PIN_PAIR_MAX_MIN = 90
PRE_LOOKBACK_MIN = 90
DECISION_WINDOW_MIN = 30
STALE_TOL = 0.01
QUIET_TOL = 0.015
QUIET_LOOKBACK_MIN = 120
THRESHOLDS = (0.0, 0.03)
SET_GAP_S = 120
PIN_CLOSE_MAX_AGE_MIN = 180
NEXT_SWEEP_MAX_MIN = 45
WF_RATIO, WF_PP, WF_MIN_BOOKS = 1.5625, 0.04, 4
EDGE_BINS = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 9.9)
N_TESTS = 64
MIN_N = 10
FOLLOWERS = ("1xBet", "Marathonbet")   # track Pinnacle, §74 — sensitivity only


# ---------------------------------------------------------------- pure helpers
def complete_sets(rows, sides):
    """rows of ONE (match, market, book): (ts, sel, odds). Group by gap (a new set
    starts > SET_GAP_S after the set's FIRST row); keep sets with every side."""
    out, cur, head = [], {}, None
    for ts, sel, odds in sorted(rows, key=lambda r: r[0]):
        if head is None or (ts - head).total_seconds() > SET_GAP_S:
            if head is not None and all(s in cur for s in sides):
                out.append((head, [cur[s] for s in sides]))
            cur, head = {}, ts
        cur[sel] = odds          # last write wins inside one fetch
    if head is not None and all(s in cur for s in sides):
        out.append((head, [cur[s] for s in sides]))
    return out


def rel(a, b):
    return a / b - 1.0


def edge_bin(e):
    for i in range(len(EDGE_BINS) - 1):
        if EDGE_BINS[i] <= e < EDGE_BINS[i + 1]:
            return i
    return len(EDGE_BINS) - 2


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    m, out, run = len(pvals), [1.0] * len(pvals), 0.0
    for k, i in enumerate(order):
        run = max(run, min(1.0, (m - k) * pvals[i]))
        out[i] = run
    return out


def t_p(t, df):
    from scipy import stats
    return float(2 * stats.t.sf(abs(t), df))


def clustered_vs0(units):
    """units: list of (match_id, clv). Per-match mean, one-sample t over matches."""
    by = defaultdict(list)
    for mid, c in units:
        by[mid].append(c)
    xs = [sum(v) / len(v) for v in by.values()]
    n = len(xs)
    if n < MIN_N:
        return n, (sum(xs) / n if n else float("nan")), float("nan"), 1.0
    mu = sum(xs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1))
    t = mu / (sd / math.sqrt(n)) if sd > 0 else float("inf")
    return n, mu, t, t_p(t, n - 1)


def matched_diff(treat, ctrl):
    """treat/ctrl: lists of (edge, clv). Control re-weighted to treatment's edge bins."""
    tb, cb = defaultdict(list), defaultdict(list)
    for e, c in treat:
        tb[edge_bin(e)].append(c)
    for e, c in ctrl:
        cb[edge_bin(e)].append(c)
    nT = sum(len(v) for b, v in tb.items() if len(cb.get(b, [])) >= 2)
    if nT < MIN_N:
        return nT, float("nan"), float("nan"), 1.0
    d, var = 0.0, 0.0
    for b, tv in tb.items():
        cv = cb.get(b, [])
        if len(cv) < 2:
            continue
        w = len(tv) / nT
        mt, mc = sum(tv) / len(tv), sum(cv) / len(cv)
        vt = (sum((x - mt) ** 2 for x in tv) / (len(tv) - 1)) if len(tv) > 1 else 0.0
        vc = sum((x - mc) ** 2 for x in cv) / (len(cv) - 1)
        d += w * (mt - mc)
        var += w * w * (vt / len(tv) + vc / len(cv))
    se = math.sqrt(var)
    z = d / se if se > 0 else float("inf")
    return nT, d, z, float(math.erfc(abs(z) / math.sqrt(2)))


def wrong_fixture_flags(sets_by_book, sides):
    """{book: [(ts, odds)]} for ONE match+market → set of EE books flagged."""
    flagged = set()
    others_ts = {b: [ts for ts, _ in s] for b, s in sets_by_book.items()}
    for b in EE_BOOKS:
        for ts, q in sets_by_book.get(b, []):
            ref = []
            for o, s in sets_by_book.items():
                if o == b:
                    continue
                i = bisect_right(others_ts[o], ts + timedelta(minutes=10)) - 1
                if i >= 0 and others_ts[o][i] >= ts - timedelta(minutes=60):
                    ref.append(s[i][1])
            if len(ref) < WF_MIN_BOOKS:
                continue
            for k in range(len(sides)):
                med = median(r[k] for r in ref)
                ratio = max(q[k] / med, med / q[k])
                if ratio > WF_RATIO and abs(1 / q[k] - 1 / med) > WF_PP:
                    flagged.add(b)
                    break
            if b in flagged:
                break
    return flagged


def last_before(sets, t, lo=None):
    ts = [s[0] for s in sets]
    i = bisect_right(ts, t) - 1
    if i < 0 or (lo is not None and ts[i] < lo):
        return None
    return i


def first_in(sets, a, b):
    ts = [s[0] for s in sets]
    i = bisect_left(ts, a)
    if i < len(ts) and ts[i] <= b:
        return i
    return None


def survival(sets, i, k, ko):
    q0 = sets[i][1][k]
    for ts, q in sets[i + 1:]:
        if ts >= ko:
            break
        if abs(rel(q[k], q0)) >= STALE_TOL:
            return (ts - sets[i][0]).total_seconds() / 60, False
    return (ko - sets[i][0]).total_seconds() / 60, True


def placeable(sets, i, k, ko):
    if i + 1 >= len(sets):
        return False
    ts, q = sets[i + 1]
    return (ts < ko and (ts - sets[i][0]).total_seconds() / 60 <= NEXT_SWEEP_MAX_MIN
            and q[k] >= 0.99 * sets[i][1][k])


# ---------------------------------------------------------------- per match
def analyse_match(mid, ko, market, rows_by_book, exch_close):
    """Returns (treat_units, ctrl_units, feas, flagged_books)."""
    sides = SIDES[market]
    sets = {b: complete_sets(r, sides) for b, r in rows_by_book.items()}
    sets = {b: s for b, s in sets.items() if s}
    flagged = wrong_fixture_flags(sets, sides)
    pin = sets.get(PIN, [])
    if len(pin) < 2:
        return [], [], defaultdict(lambda: [0, 0]), flagged
    pin_p = [(ts, devig(q)) for ts, q in pin]

    # closes
    flat = [{"bookmaker": b, "sel": s, "odds": o, "timestamp": ts}
            for b, rs in rows_by_book.items() if b not in EE_BOOKS
            for ts, s, o in rs if ts <= ko]
    csets = sets_from_rows(flat, sides)
    cons = compute_anchor(csets, sides, at=ko, exclude_book=PIN)
    cons_p = [cons.probs[s] for s in sides] if cons.source == "consensus" else None
    csets_nf = {b: v for b, v in csets.items() if b not in FOLLOWERS}
    cons2 = compute_anchor(csets_nf, sides, at=ko, exclude_book=PIN)
    cons2_p = [cons2.probs[s] for s in sides] if cons2.source == "consensus" else None
    j = last_before(pin, ko)
    pin_close = None
    if j is not None and (ko - pin[j][0]).total_seconds() / 60 <= PIN_CLOSE_MAX_AGE_MIN:
        pin_close = pin_p[j][1]
    grades = {"cons": cons_p, "pin": pin_close, "cons_nofollow": cons2_p, "exch": exch_close}

    def grade(q, k):
        return {g: (q * p[k] - 1.0 if p else None) for g, p in grades.items()}

    treat, ctrl = [], []
    feas = defaultdict(lambda: [0, 0])          # book -> [observed, stale]
    seen_t = set()
    # treatment
    for n in range(1, len(pin_p)):
        (t0, p0), (t1, p1) = pin_p[n - 1], pin_p[n]
        if not p0 or not p1 or t1 >= ko:
            continue
        if (t1 - t0).total_seconds() / 60 > PIN_PAIR_MAX_MIN:
            continue
        for k, s in enumerate(sides):
            if rel(p1[k], p0[k]) < MOVE_MIN:
                continue
            for b in EE_BOOKS:
                if b not in sets or b in flagged:
                    continue
                bs = sets[b]
                ib = last_before(bs, t0, lo=t0 - timedelta(minutes=PRE_LOOKBACK_MIN))
                it = first_in(bs, t1, min(t1 + timedelta(minutes=DECISION_WINDOW_MIN),
                                          ko - timedelta(minutes=1)))
                if ib is None or it is None:
                    continue
                feas[b][0] += 1
                q = bs[it][1][k]
                if abs(rel(q, bs[ib][1][k])) >= STALE_TOL:
                    continue
                feas[b][1] += 1
                fair = 1.0 / p1[k]
                if q > fair * GUARD[market]:
                    continue
                edge = q * p1[k] - 1.0
                for X in THRESHOLDS:
                    if edge < X or (b, k, X) in seen_t:
                        continue
                    seen_t.add((b, k, X))
                    surv, cens = survival(bs, it, k, ko)
                    treat.append(dict(mid=mid, market=market, book=b, side=s, X=X, edge=edge,
                                      hours_to_ko=(ko - bs[it][0]).total_seconds() / 3600,
                                      surv=surv, censored=cens,
                                      placeable=placeable(bs, it, k, ko), **grade(q, k)))
    # control
    seen_c = set()
    for n in range(1, len(pin_p)):
        tk, pk = pin_p[n]
        if not pk or tk >= ko:
            continue
        quiet = True
        for m in range(n - 1, -1, -1):
            tm, pm = pin_p[m]
            if m < n - 1 and (tk - tm).total_seconds() / 60 > QUIET_LOOKBACK_MIN:
                break
            if not pm or max(abs(rel(pk[k], pm[k])) for k in range(len(sides))) >= QUIET_TOL:
                quiet = False
                break
        if not quiet:
            continue
        for b in EE_BOOKS:
            if b not in sets or b in flagged:
                continue
            bs = sets[b]
            it = first_in(bs, tk, min(tk + timedelta(minutes=DECISION_WINDOW_MIN),
                                      ko - timedelta(minutes=1)))
            if it is None:
                continue
            for k, s in enumerate(sides):
                q = bs[it][1][k]
                if q > (1.0 / pk[k]) * GUARD[market]:
                    continue
                edge = q * pk[k] - 1.0
                for X in THRESHOLDS:
                    if edge < X or (b, k, X) in seen_c:
                        continue
                    seen_c.add((b, k, X))
                    surv, cens = survival(bs, it, k, ko)
                    ctrl.append(dict(mid=mid, market=market, book=b, side=s, X=X, edge=edge,
                                     hours_to_ko=(ko - bs[it][0]).total_seconds() / 3600,
                                     surv=surv, censored=cens,
                                     placeable=placeable(bs, it, k, ko), **grade(q, k)))
    return treat, ctrl, feas, flagged


# ---------------------------------------------------------------- data
def load(days):
    from workers.api_clients.db import execute_query
    matches = execute_query(
        """SELECT id::text AS id, date FROM matches
            WHERE status = 'finished' AND date > NOW() - make_interval(days => %s)
              AND date < NOW() - INTERVAL '2 hours'
            ORDER BY date""", (days,))
    exch = execute_query(
        """SELECT DISTINCT e.match_id::text AS mid FROM exchange_quotes e
             JOIN matches m ON m.id = e.match_id WHERE m.status = 'finished'""")
    return matches, {r["mid"] for r in exch}


def rows_for(ids):
    from workers.api_clients.db import execute_query
    return execute_query(
        """SELECT o.match_id::text AS mid, o.bookmaker, o.market, lower(o.selection) AS sel,
                  o.odds::float AS odds, o.timestamp
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.match_id = ANY(%s::uuid[])
              AND o.market IN ('1x2', 'over_under_25')
              AND (o.handicap_line IS NULL OR o.handicap_line = 2.5)
              AND COALESCE(o.is_live, false) = false
              AND o.odds > 1.01 AND o.timestamp <= m.date""", (ids,))


def run(days):
    matches, exch_finished = load(days)
    ko_of = {m["id"]: m["date"] for m in matches}
    print(f"matches finished in window: {len(matches)}; with any exchange row: "
          f"{len(exch_finished)} -> exchange close n=0 unless >0 here")
    treat, ctrl = [], []
    feas = defaultdict(lambda: [0, 0])
    flagged = defaultdict(set)
    ids = list(ko_of)
    B = 150
    for i in range(0, len(ids), B):
        chunk = ids[i:i + B]
        by = defaultdict(lambda: defaultdict(list))
        for r in rows_for(chunk):
            by[(r["mid"], r["market"])][r["bookmaker"]].append((r["timestamp"], r["sel"], r["odds"]))
        for (mid, market), rbb in by.items():
            t, c, f, fl = analyse_match(mid, ko_of[mid], market, rbb, None)
            treat += t
            ctrl += c
            for b, (o, s) in f.items():
                feas[(market, b)][0] += o
                feas[(market, b)][1] += s
            for b in fl:
                flagged[b].add(mid)
        print(f"  {min(i + B, len(ids))}/{len(ids)} matches, treat {len(treat)}, ctrl {len(ctrl)}",
              flush=True)
    return treat, ctrl, feas, flagged


def report(treat, ctrl, feas, flagged):
    def pct(x):
        return f"{100 * x:+.2f}%" if x == x else "  n/a"

    print("\n== wrong-fixture / mirror exclusions (fixture, book) ==")
    for b in EE_BOOKS:
        print(f"  {b:12s} {len(flagged.get(b, ()))}")
    print("\n== stale share at observed trigger events (feasibility replicate) ==")
    for (mk, b), (o, s) in sorted(feas.items()):
        print(f"  {mk:14s} {b:12s} observed {o:5d}  stale {s:5d}  ({100 * s / o if o else 0:.0f}%)")

    print("\n== exchange close: graded units ==",
          sum(1 for u in treat + ctrl if u["exch"] is not None))

    tests, lines = [], []
    for mk in SIDES:
        for b in EE_BOOKS:
            for X in THRESHOLDS:
                T = [u for u in treat if u["market"] == mk and u["book"] == b and u["X"] == X]
                tunits = {(u["mid"], u["side"]) for u in T}
                C = [u for u in ctrl if u["market"] == mk and u["book"] == b and u["X"] == X
                     and (u["mid"], u["side"]) not in tunits]
                for g in ("cons", "pin"):
                    Tg = [u for u in T if u[g] is not None]
                    Cg = [u for u in C if u[g] is not None]
                    n0, mu0, t0, p0 = clustered_vs0([(u["mid"], u[g]) for u in Tg])
                    nd, d, z, pd = matched_diff([(u["edge"], u[g]) for u in Tg],
                                                [(u["edge"], u[g]) for u in Cg])
                    cm = [u[g] for u in Cg]
                    tv = [u[g] for u in Tg]
                    tests += [p0, pd]
                    lines.append((mk, b, X, g, len(T), len(Tg), n0, mu0, t0, p0,
                                  (median(tv) if tv else float("nan")),
                                  (sum(1 for x in tv if x > 0) / len(tv) if tv else float("nan")),
                                  len(Cg), (sum(cm) / len(cm) if cm else float("nan")), nd, d, z, pd))
    assert len(tests) == N_TESTS, len(tests)
    adj = holm(tests)
    print("\n== H1 / H2 (Holm over 64) ==")
    print("market         book         X   grader  nT  nT_g  nMatch  meanT   medT  %pos   t     "
          "p_holm | nC  meanC   T-C(matched)  z    p_holm")
    for i, L in enumerate(lines):
        (mk, b, X, g, nT, nTg, n0, mu0, t0, p0, med, pos, nC, mC, nd, d, z, pd) = L
        print(f"{mk:14s} {b:12s} {X:.2f} {g:5s} {nT:4d} {nTg:4d} {n0:5d}  {pct(mu0)} {pct(med)} "
              f"{100 * pos if pos == pos else float('nan'):4.0f}% {t0:6.2f} {adj[2 * i]:.3f} | "
              f"{nC:4d} {pct(mC)} {pct(d)} {z:6.2f} {adj[2 * i + 1]:.3f}")

    print("\n== descriptive per book x market (X=0): survival, placeable, hours-to-KO, edge, "
          "follower-free consensus ==")
    for arm, U in (("TREAT", treat), ("CTRL", ctrl)):
        for mk in SIDES:
            for b in EE_BOOKS:
                u = [x for x in U if x["market"] == mk and x["book"] == b and x["X"] == 0.0]
                if not u:
                    continue
                sv = sorted(x["surv"] for x in u)
                cens = sum(x["censored"] for x in u) / len(u)
                pl = sum(x["placeable"] for x in u) / len(u)
                nf = [x["cons_nofollow"] for x in u if x["cons_nofollow"] is not None]
                print(f"  {arm:5s} {mk:14s} {b:12s} n={len(u):4d} surv_med={sv[len(sv) // 2]:6.0f}min "
                      f"censored(KO)={100 * cens:3.0f}% placeable={100 * pl:3.0f}% "
                      f"h_to_KO_med={median(x['hours_to_ko'] for x in u):5.1f} "
                      f"edge_med={pct(median(x['edge'] for x in u))} "
                      f"cons_nofollow n={len(nf)} mean={pct(sum(nf) / len(nf)) if nf else 'n/a'}")
    # observed sd for the power line
    allc = [u["cons"] for u in treat if u["cons"] is not None and u["X"] == 0.0]
    if len(allc) > 2:
        mu = sum(allc) / len(allc)
        sd = math.sqrt(sum((x - mu) ** 2 for x in allc) / (len(allc) - 1))
        print(f"\nobserved per-unit sd of consensus CLV (treatment): {100 * sd:.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    report(*run(a.days))


if __name__ == "__main__":
    main()
