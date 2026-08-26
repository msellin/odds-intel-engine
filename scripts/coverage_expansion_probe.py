"""COVERAGE-EXPANSION-2026-08-26 — where could the best model bet that it doesn't?

CLV-SLICE-SEARCH established that no threshold change finds a second edge, and
that the edge bot_v10_all has belongs to the newer model versions rather than to
its threshold shape (v20260712: CLV +15.17%, t=+5.79 on its picks).

That points the expansion at coverage rather than configuration. Since 2026-06-01
there are 258 leagues where v20260712 produces predictions and NO active bot has
placed a single bet — 7,778 finished matches — against 166 leagues that are
covered. The question is whether those uncovered leagues are unbet because they
are bad, or merely because nobody pointed a bot at them.

Replays bot_v10_all's exact gate over the uncovered leagues and scores it on
de-vigged Pinnacle CLV, split by tier so the answer is actionable per slice
rather than one blended number.

Point-in-time throughout: predictions and prices both taken at kickoff-3h.

Usage:
    python3 scripts/coverage_expansion_probe.py
    python3 scripts/coverage_expansion_probe.py --start 2026-05-01
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import devig  # noqa: E402

LEAD_H = 3
ACCESSIBLE = ["Unibet", "Betano", "Marathonbet", "10Bet", "888Sport", "Coolbet"]
PRED_MAP = {
    "1x2_home": ("1x2", "home"), "1x2_draw": ("1x2", "draw"), "1x2_away": ("1x2", "away"),
    "over25": ("over_under_25", "over"), "under25": ("over_under_25", "under"),
    "over35": ("over_under_35", "over"), "under35": ("over_under_35", "under"),
}
COMPLEMENT = {"1x2": ["home", "draw", "away"]}
# bot_v10_all, verbatim from daily_pipeline_v2.BOTS_CONFIG.
V10 = {1: {"fav": 0.08, "long": 0.12, "ou": 0.08},
       2: {"fav": 0.05, "long": 0.08, "ou": 0.06},
       3: {"fav": 0.04, "long": 0.06, "ou": 0.05},
       4: {"fav": 0.03, "long": 0.05, "ou": 0.04}}


def sides_for(m: str) -> list[str]:
    return COMPLEMENT.get(m, ["over", "under"])


def won(m, sel, sh, sa):
    if m == "1x2":
        return {"home": sh > sa, "draw": sh == sa, "away": sa > sh}[sel]
    line = float(m.replace("over_under_", "")) / 10.0
    t = sh + sa
    return None if t == line else (t > line if sel == "over" else t < line)


def stats(v):
    n = len(v)
    if n < 2:
        return n, 0.0, 0.0
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    se = math.sqrt(var / n)
    return n, m, (m / se if se else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--min-n", type=int, default=60)
    args = ap.parse_args()

    print("finding leagues with v20260712 predictions but no active-bot bets...")
    uncovered = execute_query(
        """
        WITH pred AS (
          SELECT DISTINCT m.league_id FROM predictions p JOIN matches m ON m.id = p.match_id
           WHERE p.source='ensemble' AND p.model_version='v20260712' AND m.date >= %s),
        bet AS (
          SELECT DISTINCT m.league_id FROM simulated_bets sb
            JOIN matches m ON m.id = sb.match_id JOIN bots b ON b.id = sb.bot_id
           WHERE b.is_active AND sb.pick_time >= %s)
        SELECT p.league_id FROM pred p WHERE p.league_id NOT IN (SELECT league_id FROM bet)
        """,
        [args.start, args.start],
    )
    ids = [str(r["league_id"]) for r in uncovered]
    print(f"  {len(ids)} uncovered leagues\n")
    if not ids:
        return 0

    preds = execute_query(
        """
        SELECT DISTINCT ON (p.match_id, p.market)
               p.match_id, p.market, p.model_probability::float AS prob
          FROM predictions p JOIN matches m ON m.id = p.match_id
         WHERE p.source='ensemble' AND p.model_version='v20260712'
           AND m.league_id = ANY(%s::uuid[]) AND m.status='finished'
           AND m.score_home IS NOT NULL AND m.date >= %s AND m.date < %s
           AND p.created_at <= m.date - (%s || ' hours')::interval
           AND p.market = ANY(%s)
         ORDER BY p.match_id, p.market, p.created_at DESC
        """,
        [ids, args.start, args.end, str(LEAD_H), list(PRED_MAP)],
    )
    print(f"  {len(preds)} point-in-time predictions in uncovered leagues")

    mids = list({str(r["match_id"]) for r in preds})
    best = execute_query(
        """
        SELECT o.match_id, o.market, o.selection, max(o.odds)::float AS best
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker = ANY(%s)
           AND o.timestamp <= m.date - (%s || ' hours')::interval
         GROUP BY 1,2,3
        """,
        [mids, ACCESSIBLE, str(LEAD_H)],
    )
    bm = {(str(r["match_id"]), r["market"], r["selection"]): r["best"] for r in best}
    pinr = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.selection)
               o.match_id, o.market, o.selection, o.odds::float AS odds
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[]) AND o.bookmaker='Pinnacle'
           AND o.timestamp <= m.date
         ORDER BY o.match_id, o.market, o.selection, o.timestamp DESC
        """,
        [mids],
    )
    pm = {(str(r["match_id"]), r["market"], r["selection"]): r["odds"] for r in pinr}
    meta = execute_query(
        "SELECT m.id, m.score_home, m.score_away, l.tier, l.name "
        "FROM matches m JOIN leagues l ON l.id=m.league_id WHERE m.id = ANY(%s::uuid[])",
        [mids],
    )
    mmap = {str(r["id"]): (int(r["score_home"]), int(r["score_away"]), r["tier"], r["name"])
            for r in meta}
    print(f"  {len(bm)} prices, {len(pm)} Pinnacle closes\n")

    cache: dict = {}
    by_tier: dict = defaultdict(lambda: {"clv": [], "ret": []})
    by_league: dict = defaultdict(lambda: {"clv": [], "ret": [], "name": ""})
    fired = out = 0

    for p in preds:
        mid = str(p["match_id"])
        mkt, sel = PRED_MAP[p["market"]]
        mm = mmap.get(mid)
        if not mm:
            continue
        sh, sa, tier, lname = mm
        o = bm.get((mid, mkt, sel))
        if not o or o <= 1.0:
            continue
        if p["prob"] < 0.30 or not (1.30 <= o <= 4.50):
            continue
        th = V10.get(tier if tier in V10 else 4)
        floor = th["ou"] if mkt.startswith("over_under") else (th["fav"] if o < 2.5 else th["long"])
        if o * p["prob"] - 1.0 < floor:
            continue
        w = won(mkt, sel, sh, sa)
        if w is None:
            continue
        pin_own = pm.get((mid, mkt, sel))
        if not pin_own or pin_own <= 1.0:
            continue
        if o > pin_own * (1.35 if mkt == "1x2" else 1.30):
            out += 1
            continue
        key = (mid, mkt)
        if key not in cache:
            odds = [pm.get((mid, mkt, s2)) for s2 in sides_for(mkt)]
            cache[key] = None if any(x is None or x <= 1.0 for x in odds) else devig(odds)
        probs = cache[key]
        if probs is None:
            continue
        clv = o * probs[sides_for(mkt).index(sel)] - 1.0
        ret = (o - 1.0) if w else -1.0
        fired += 1
        t_lab = f"T{tier}" if tier is not None else "T?"
        by_tier[t_lab]["clv"].append(clv); by_tier[t_lab]["ret"].append(ret)
        d = by_league[lname]; d["clv"].append(clv); d["ret"].append(ret)

    print(f"{fired} picks would have fired in uncovered leagues "
          f"({out} rejected as price outliers)\n")

    print(f"{'tier':6s} {'n':>6s} {'CLV':>9s} {'CLV t':>8s} {'ROI':>9s} {'ROI t':>8s}  verdict")
    print("-" * 74)
    for t_lab in sorted(by_tier):
        d = by_tier[t_lab]
        n, cm, ct = stats(d["clv"])
        if n < args.min_n:
            continue
        _, rm, rt = stats(d["ret"])
        v = "EXPAND" if ct >= 1.65 else ("avoid" if ct <= -1.65 else "no edge")
        print(f"{t_lab:6s} {n:6d} {cm*100:+8.2f}% {ct:+8.2f} {rm*100:+8.2f}% {rt:+8.2f}  {v}")

    n, cm, ct = stats([x for d in by_tier.values() for x in d["clv"]])
    _, rm, rt = stats([x for d in by_tier.values() for x in d["ret"]])
    print("-" * 74)
    print(f"{'ALL':6s} {n:6d} {cm*100:+8.2f}% {ct:+8.2f} {rm*100:+8.2f}% {rt:+8.2f}")

    rows = []
    for name, d in by_league.items():
        ln, lcm, lct = stats(d["clv"])
        if ln >= 25:
            rows.append((lct, name, ln, lcm))
    rows.sort(reverse=True)
    if rows:
        print(f"\nBest uncovered leagues (n >= 25), by CLV t:")
        for lct, name, ln, lcm in rows[:10]:
            print(f"  {name[:44]:44s} n={ln:4d}  CLV {lcm*100:+6.2f}%  t {lct:+6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
