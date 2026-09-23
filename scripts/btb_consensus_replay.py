#!/usr/bin/env python3
"""Grade B / C / A on the Beat the Bookie time series ([[#098]]).

Implements docs/PUBLISHED_PICKS_GRADING_2026_09_23.md §5, pre-registered before
the first run. Mirrors scripts/consensus_arm_replay.py on a different dataset:
the constants and the de-vig are IMPORTED from the live publisher, so a rule
change there changes this replay too.

DATA: data/raw/beat_the_bookie/odds_series{,_b}.csv — per match, 32 ANONYMOUS
books x 72 hourly 1x2 prices; index 71 = the hour before kickoff (verified: far
more books hold a price at 71 than at 0). League names come from the matching
*_matches.csv.

Two phases:
    extract  stream the 2.7 GB CSVs once, keep only the decision window
             (index 58..71 = 14h..1h before kickoff) -> a compact pickle
    run      calibrate the panel on the earliest 20%, replay + grade the rest

    python3 scripts/btb_consensus_replay.py extract
    python3 scripts/btb_consensus_replay.py run
"""
from __future__ import annotations

import csv
import math
import pickle
import re
import sys
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.publish_picks_forward_test import (  # noqa: E402
    ALIGN_MIN, CONSENSUS_MAX_EDGE, CONSENSUS_MIN_BOOKS, WEAK_MAX_EDGE,
    LOOKAHEAD_H, MAX_ODDS, MAX_RATIO, MIN_EDGE, MIN_LEAD_MIN,
)
from workers.model.devig import devig  # noqa: E402

RAW = Path("data/raw/beat_the_bookie")
CACHE = Path("/tmp/claude-501/btb_window.pkl")
N_BOOKS, LAST = 32, 71
FIRST = LAST - (LOOKAHEAD_H - 1)          # index 58 = 14h before kickoff
STALE_H = 6
LOOKBACK = FIRST - STALE_H                # carry-forward needs 6h before the window
SIDES = ("home", "draw", "away")
TIER0 = re.compile(r"women|u1[7-9]|u2[0-3]|youth|reserve|amateur|junior|primavera", re.I)
PANEL_N = 5
CALIB_FRAC = 0.20
HOLM_M = 3


# ─── extract ─────────────────────────────────────────────────────────────────

def extract() -> int:
    leagues = {}
    for f in ("odds_series_matches.csv", "odds_series_b_matches.csv"):
        for r in csv.DictReader(open(RAW / f, encoding="latin-1"), skipinitialspace=True):
            leagues[r["match_id"].strip()] = r["league"].strip()
    out, seen = [], set()
    for f in ("odds_series.csv", "odds_series_b.csv"):
        rd = csv.reader(open(RAW / f))
        h = next(rd)
        col = {c: i for i, c in enumerate(h)}
        idx = {(s, b, t): col[f"{s}_b{b}_{t}"] for s in SIDES
               for b in range(1, N_BOOKS + 1) for t in range(LOOKBACK, LAST + 1)}
        for row in rd:
            mid = row[col["match_id"]]
            if mid in seen:
                continue
            seen.add(mid)
            try:
                sh, sa = int(float(row[col["score_home"]])), int(float(row[col["score_away"]]))
            except ValueError:
                continue
            series = {}
            for b in range(1, N_BOOKS + 1):
                pts = []
                for t in range(LOOKBACK, LAST + 1):
                    v = [row[idx[(s, b, t)]] for s in SIDES]
                    if all(x not in ("", "nan", "NaN") for x in v):
                        pts.append((t, tuple(float(x) for x in v)))
                if pts:
                    series[b] = pts
            if series:
                out.append(dict(id=mid, date=row[col["match_date"]], sh=sh, sa=sa,
                                league=leagues.get(mid, ""), series=series))
            if len(out) % 20000 == 0 and out:
                print(f"  {len(out):,} matches", file=sys.stderr)
    out.sort(key=lambda m: (m["date"], m["id"]))
    pickle.dump(out, open(CACHE, "wb"))
    print(f"extracted {len(out):,} matches -> {CACHE}")
    return 0


# ─── replay ──────────────────────────────────────────────────────────────────

def latest_at(series, T):
    """{book: (t, (h, d, a))} — each book's last price at or before T, within 6h."""
    out = {}
    for b, pts in series.items():
        best = None
        for t, v in pts:
            if t <= T:
                best = (t, v)
            else:
                break
        if best and best[0] > T - STALE_H:
            out[b] = best
    return out


def consensus(lat):
    per = {}
    for b, (t, v) in lat.items():
        if all(x > 1.0 for x in v):
            p = devig(list(v))
            if p:
                per[b] = (t, p)
    if len(per) < CONSENSUS_MIN_BOOKS:
        return None
    avg = [mean(p[i] for _, p in per.values()) for i in range(3)]
    return avg, max(t for t, _ in per.values()), per


def result(sh, sa):
    return 0 if sh > sa else (2 if sa > sh else 1)


def replay_match(m):
    last_run = LAST                                  # index 71 = 1h >= MIN_LEAD_MIN (45 min)
    assert 60 >= MIN_LEAD_MIN
    for T in range(FIRST, last_run + 1):
        lat = latest_at(m["series"], T)
        got = consensus(lat)
        if not got:
            continue
        probs, anchor_t, per = got
        best = None
        for i in range(3):
            fair = 1.0 / probs[i]
            aligned = [(v[i], b) for b, (t, v) in lat.items()
                       if (anchor_t - t) * 60 <= ALIGN_MIN]
            if not aligned:
                continue
            odds, book = max(aligned)
            if odds > MAX_ODDS or odds / fair - 1 > MAX_RATIO:
                continue
            edge = probs[i] * odds - 1
            if edge < MIN_EDGE or edge > CONSENSUS_MAX_EDGE:
                continue
            if best is None or edge > best["edge"]:
                best = dict(i=i, odds=odds, book=book, edge=edge, T=T,
                            per={b: p[i] for b, (_, p) in per.items()})
        if best:
            close = consensus(latest_at(m["series"], LAST))
            p_close = close[0][best["i"]] if close else None
            won = result(m["sh"], m["sa"]) == best["i"]
            best.update(pnl=(best["odds"] - 1) if won else -1.0,
                        clv=(p_close * best["odds"] - 1) if p_close else None)
            return best
    return None


def grade(pk, league, panel):
    reasons = []
    if TIER0.search(league or ""):
        reasons.append("tier0")
    for b in panel:
        p = pk["per"].get(b)
        if b != pk["book"] and p is not None and p * pk["odds"] - 1 <= 0:
            reasons.append("panel")
            break
    if pk["edge"] > WEAK_MAX_EDGE:
        reasons.append("edge")
    return "C" if reasons else "B"


def stats(x):
    n = len(x)
    if n < 2:
        return dict(n=n, roi=mean(x) if x else 0.0, se=float("nan"), p=1.0)
    m, se = mean(x), stdev(x) / math.sqrt(n)
    return dict(n=n, roi=m, se=se, p=0.5 * math.erfc((m / se if se else 0) / math.sqrt(2)))


def fmt(s):
    return f"n={s['n']:6d}  ROI {100*s['roi']:+6.2f}% ± {100*s['se']:4.2f}  p={s['p']:.4f}"


def run() -> int:
    ms = pickle.load(open(CACHE, "rb"))
    n_cal = int(len(ms) * CALIB_FRAC)
    cal, ev = ms[:n_cal], ms[n_cal:]

    # panel: 5 sharpest books by log-loss of their own de-vigged index-71 price
    ll = {}
    for m in cal:
        y = result(m["sh"], m["sa"])
        for b, pts in m["series"].items():
            t, v = pts[-1]
            if t == LAST and all(x > 1 for x in v):
                p = devig(list(v))
                if p:
                    ll.setdefault(b, []).append(-math.log(max(p[y], 1e-9)))
    ranked = sorted((mean(v), b, len(v)) for b, v in ll.items() if len(v) >= 2000)
    panel = [b for _, b, _ in ranked[:PANEL_N]]
    print(f"calibration: {n_cal:,} earliest matches ({cal[0]['date']}..{cal[-1]['date']}), excluded below")
    print("  sharpest books by log-loss: " + ", ".join(
        f"b{b} {l:.4f} (n={n})" for l, b, n in ranked[:8]))
    print(f"  PANEL = {['b%d' % b for b in panel]}\n")

    picks = []
    for m in ev:
        pk = replay_match(m)
        if pk:
            pk.update(date=m["date"], league=m["league"],
                      grade=grade(pk, m["league"], panel))
            picks.append(pk)
    mid = picks[len(picks) // 2]["date"]
    h1 = [p for p in picks if p["date"] < mid]
    h2 = [p for p in picks if p["date"] >= mid]
    print(f"evaluated {len(ev):,} matches ({ev[0]['date']}..{ev[-1]['date']}) -> "
          f"{len(picks):,} picks; halves split at {mid}\n")

    def show(label, fn):
        a = [p["pnl"] for p in picks if fn(p)]
        s1 = stats([p["pnl"] for p in h1 if fn(p)])
        s2 = stats([p["pnl"] for p in h2 if fn(p)])
        c = [p["clv"] for p in picks if fn(p) and p["clv"] is not None]
        print(f"  {label:26s} {fmt(stats(a))}  | h1 {100*s1['roi']:+6.2f}%  h2 {100*s2['roi']:+6.2f}%"
              f"  | CLV {100*mean(c) if c else 0:+.2f}%")
        return stats(a), s1, s2

    print("ALL AND BY GRADE:")
    show("all consensus picks", lambda p: True)
    sb, b1, b2 = show("grade B", lambda p: p["grade"] == "B")
    sc, c1, c2 = show("grade C", lambda p: p["grade"] == "C")
    print(f"\n  Q1 B profitable:  {'PASS' if sb['roi'] > 0 and sb['p'] < 0.05 and b1['roi'] > 0 and b2['roi'] > 0 else 'FAIL'}")
    print(f"  Q2 C worse than B: {'PASS' if b1['roi'] > c1['roi'] and b2['roi'] > c2['roi'] else 'FAIL'}"
          f"  (h1 gap {100*(b1['roi']-c1['roi']):+.2f}pp, h2 gap {100*(b2['roi']-c2['roi']):+.2f}pp)")

    print("\nEACH C CONDITION (descriptive):")
    show("tier 0 league", lambda p: bool(TIER0.search(p["league"] or "")))
    show("panel book disagrees", lambda p: any(
        b != p["book"] and p["per"].get(b) is not None and p["per"][b] * p["odds"] - 1 <= 0
        for b in panel))
    show("edge > 6%", lambda p: p["edge"] > WEAK_MAX_EDGE)

    def a4(p):
        o = {b: p["per"][b] for b in panel if b != p["book"] and b in p["per"]}
        return len(o) >= 4 and all(v * p["odds"] - 1 > 0 for v in o.values())
    cands = {"A2 edge 4-6%": lambda p: 0.04 <= p["edge"] <= 0.06,
             "A3 odds <= 1.6": lambda p: p["odds"] <= 1.6,
             "A4 full panel agrees": a4}
    B = [p for p in picks if p["grade"] == "B"]
    res = {k: stats([p["pnl"] for p in B if fn(p)]) for k, fn in cands.items()}
    adj, run_ = {}, 0.0
    for i, k in enumerate(sorted(res, key=lambda k: res[k]["p"])):
        run_ = max(run_, min(1.0, (HOLM_M - i) * res[k]["p"]))
        adj[k] = run_
    print(f"\nGRADE A (on grade-B picks; B ROI {100*sb['roi']:+.2f}%), Holm m={HOLM_M}:")
    for k, fn in cands.items():
        s = res[k]
        r1 = stats([p["pnl"] for p in h1 if p["grade"] == "B" and fn(p)])["roi"]
        r2 = stats([p["pnl"] for p in h2 if p["grade"] == "B" and fn(p)])["roi"]
        ok = adj[k] < 0.05 and s["roi"] > 0 and r1 > 0 and r2 > 0 and s["roi"] > sb["roi"]
        print(f"  {k:24s} {fmt(s)}  Holm {adj[k]:.4f}  h1 {100*r1:+.2f}%  h2 {100*r2:+.2f}%  -> {'PASS' if ok else 'FAIL'}")

    print("\nDESCRIPTIVE — grade B by odds band:")
    for lo, hi in ((1.0, 1.6), (1.6, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 4.01)):
        a = [p["pnl"] for p in B if lo <= p["odds"] < hi]
        print(f"  odds {lo:.1f}-{hi:.1f}  {fmt(stats(a))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(extract() if sys.argv[1:] == ["extract"] else run())
