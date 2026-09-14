#!/usr/bin/env python3
"""Reconcile the LIVE bot ledger against the unselected own-book CLV panel.

WHY THIS EXISTS. `dev/active/own-sharp-tight-preregistration.md` records that on
the sharp-anchor legs the margin-corrected OWN-BOOK CLV reads -5.36pct to
-7.56pct. The unselected panel built by `own_book_clv_universe.py` says the SAME
nominal gate (Shin prob-edge >= 2pct) sits at roughly break-even. Two numbers
that far apart on the same stated rule mean one of them is measuring something
else, and which one decides whether the live instrument is mis-gated or the
panel is mis-built. This script finds out, rather than picking a favourite.

It reports, for every bot with own-book rows at the three EMTA-legal books:
  * n, DATE SPAN, raw own-book CLV, and margin-corrected own-book CLV with
    cluster-robust SEs on match_id -- margin per row, same definition as
    settlement.closing_book_margin, re-derived in SQL;
  * the lead time of the picks (kickoff minus pick_time), because the panel's
    arms are fixed lead times and a ledger whose picks land at a different
    distance from kickoff is not comparable to a 3h arm;
  * whether `odds_at_pick` was actually available AT that book at that moment,
    which is the ANALYSIS_GOTCHAS 52 / 55 failure -- a price taken as the best
    across books is not the price the named book was showing.

    python3 scripts/own_ledger_segment_check.py
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from workers.api_clients.db import execute_query  # noqa: E402

BOOKS = ("Coolbet", "Epicbet", "Unibet-Site")
Z = 1.959963985
COMPLEMENTS = {"1x2": ("home", "draw", "away"), "1x2_1h": ("home", "draw", "away"),
               "btts": ("yes", "no")}


def complement(market):
    m = (market or "").lower()
    if m in COMPLEMENTS:
        return COMPLEMENTS[m]
    if m.startswith(("over_under", "corners_", "team_total_")):
        return ("over", "under")
    return None


def cluster_mean(pairs):
    """pairs = [(value, cluster_key)]"""
    ys = [p[0] for p in pairs]
    n = len(ys)
    if n < 2:
        return float("nan"), float("nan"), n, 0
    mu = sum(ys) / n
    g = defaultdict(float)
    for y, c in pairs:
        g[c] += y - mu
    var = sum(v * v for v in g.values()) / (n * n)
    ng = len(g)
    if ng > 1:
        var *= ng / (ng - 1.0)
    return mu, math.sqrt(var), n, ng


def load(days):
    return execute_query(
        """
        SELECT s.bot_name, s.bot_id::text AS bot_id, s.match_id::text AS match_id,
               s.market, s.selection, s.recommended_bookmaker AS book,
               s.odds_at_pick::float AS odds, s.closing_odds::float AS cod,
               s.clv::float AS clv, s.pick_time, m.date AS ko,
               EXTRACT(EPOCH FROM (m.date - s.pick_time)) / 3600.0 AS lead_h
          FROM shadow_bets_unique s
          JOIN matches m ON m.id = s.match_id
         WHERE s.closing_bookmaker IS NOT NULL
           AND s.recommended_bookmaker = ANY(%s)
           AND s.result IS NOT NULL
           AND s.pick_time > now() - (%s || ' days')::interval
        """,
        (list(BOOKS), str(days)),
    )


def margins(rows):
    """That book's own closing overround per (match, market, book), from the
    latest pre-kickoff quote of every leg of the complement -- exactly what
    settlement.closing_book_margin does, in one query instead of 4n."""
    keys = {(r["match_id"], r["market"], r["book"]) for r in rows}
    if not keys:
        return {}
    mids = list({k[0] for k in keys})
    mkts = list({k[1] for k in keys})
    q = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.bookmaker, o.selection)
               o.match_id::text AS match_id, o.market, o.bookmaker, o.selection,
               o.odds::float AS odds
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s)
           AND o.bookmaker = ANY(%s)
           AND COALESCE(o.is_live, FALSE) = FALSE
           AND o.timestamp <= m.date AND o.odds > 1.01
         ORDER BY o.match_id, o.market, o.bookmaker, o.selection, o.timestamp DESC
        """,
        (mids, mkts, list(BOOKS)),
    )
    got = defaultdict(dict)
    for r in q:
        got[(r["match_id"], r["market"], r["bookmaker"])][r["selection"]] = r["odds"]
    out = {}
    for k in keys:
        comp = complement(k[1])
        if not comp:
            continue
        legs = got.get(k, {})
        if not all(s in legs for s in comp):
            continue
        m = sum(1.0 / legs[s] for s in comp) - 1.0
        if 0.0 <= m <= 0.5:
            out[k] = m
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--min-n", type=int, default=25)
    a = ap.parse_args()

    rows = load(a.days)
    mg = margins(rows)
    for r in rows:
        m = mg.get((r["match_id"], r["market"], r["book"]))
        r["m"] = m
        r["mc"] = (None if m is None or r["clv"] is None
                   else (1.0 + r["clv"]) / (1.0 + m) - 1.0)

    by = defaultdict(list)
    for r in rows:
        by[(r["bot_name"], r["market"])].append(r)

    print(f"LIVE LEDGER — own-book rows at {BOOKS}, last {a.days} days")
    print("target: margin-corrected own-book CLV; break-even 0.00pct\n")
    print(f"  {'bot / market':58s} {'n':>5s} {'span':>23s} "
          f"{'raw clv':>9s} {'mc clv':>9s} {'95pct CI':>19s} {'lead h':>14s}")
    for k in sorted(by, key=lambda k: -len(by[k])):
        rs = [r for r in by[k] if r["mc"] is not None]
        if len(rs) < a.min_n:
            continue
        mu, se, n, ng = cluster_mean([(r["mc"], r["match_id"]) for r in rs])
        raw = sum(r["clv"] for r in rs) / n
        ds = sorted(r["pick_time"].date() for r in rs)
        leads = sorted(r["lead_h"] for r in rs)
        print(f"  {(k[0] + ' / ' + k[1])[:58]:58s} {n:5d} "
              f"{str(ds[0])+'..'+str(ds[-1]):>23s} {raw*100:+8.2f}% {mu*100:+8.2f}% "
              f"[{(mu-Z*se)*100:+7.2f},{(mu+Z*se)*100:+7.2f}] "
              f"p50={leads[len(leads)//2]:6.1f}h")

    print("\nPOOLED, by book (all bots, own-book rows)")
    for bk in BOOKS:
        rs = [r for r in rows if r["book"] == bk and r["mc"] is not None]
        if len(rs) < a.min_n:
            continue
        mu, se, n, ng = cluster_mean([(r["mc"], r["match_id"]) for r in rs])
        ds = sorted(r["pick_time"].date() for r in rs)
        print(f"  {bk:14s} n={n:5d} fx={ng:5d} {ds[0]}..{ds[-1]} "
              f"mc={mu*100:+7.2f}% [{(mu-Z*se)*100:+7.2f},{(mu+Z*se)*100:+7.2f}]")

    print("\nLEAD-TIME DISTRIBUTION of ledger picks (hours before kickoff)")
    leads = sorted(r["lead_h"] for r in rows if r["lead_h"] is not None)
    if leads:
        for q in (5, 25, 50, 75, 95):
            print(f"  p{q:02d} = {leads[int(len(leads)*q/100)]:7.2f}h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
