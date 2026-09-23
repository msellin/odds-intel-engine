#!/usr/bin/env python3
"""OWN verdict re-tested as a TAIL test ([[#024]] (c)).

Pre-registered in docs/OWN_PATH_VERDICT_2026_09_14.md, addendum 2026-09-23, before
the first run. Question: do quotes at the books WE can bet, priced >= X% above a
FRESH de-vigged Pinnacle price, beat the de-vigged Pinnacle CLOSE?

For every stored quote at a self-scraped book between 14 h and 45 min before
kickoff: fair = Shin of the latest complete Pinnacle set at or before the quote,
no older than 60 min. A leg = the FIRST such quote per (match, market, selection,
book) with edge = p_fair × odds − 1 >= X, odds <= 4.0, edge <= 8%. Scored with
clv_sharp against the same fresh assembled close as workers/jobs/clv_sharp.py.
`stale` = the book's price had not changed for > 60 min at the decision.

    python3 scripts/own_tail_retest.py --days 21
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BOOKS = ("Coolbet", "Epicbet", "Unibet-Site", "Tonybet")
MARKETS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under"),
           "1x2_1h": ("home", "draw", "away")}
THRESHOLDS = (0.02, 0.03, 0.04, 0.05)
MAX_ODDS, CEILING = 4.0, 0.08
LOOKAHEAD_H, LEAD_MIN, FRESH_MIN, STALE_MIN = 14, 45, 60, 60
BAR_T, BAR_PER_DAY = 3.0, 3.0


def pin_sets(rows, sides):
    """Complete Pinnacle sets keyed by exact write timestamp -> (ts, [odds])."""
    by_ts = defaultdict(dict)
    for ts, sel, od in rows:
        by_ts[ts][sel] = od
    return sorted((ts, [d[s] for s in sides]) for ts, d in by_ts.items()
                  if all(s in d and d[s] > 1.0 for s in sides))


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from workers.api_clients.db import execute_query
    from workers.jobs.clv_sharp import assemble_close
    from workers.model.devig import devig

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    a = ap.parse_args()

    matches = execute_query("""SELECT id::text id, date FROM matches
        WHERE status='finished' AND date > now() - make_interval(days => %s) AND date < now()
        ORDER BY date""", (a.days,))
    ko = {m["id"]: m["date"] for m in matches}
    ids = list(ko)
    legs = defaultdict(list)          # (book, market, X, stale) -> [(clv, day)]
    days_seen = set()
    for i in range(0, len(ids), 250):
        chunk = ids[i:i + 250]
        rows = execute_query("""
            SELECT o.match_id::text mid, o.market, o.selection, o.bookmaker bk,
                   o.timestamp ts, o.odds::float od
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s)
               AND o.bookmaker = ANY(%s) AND o.is_live IS NOT TRUE AND o.odds > 1.01
               AND o.timestamp <= m.date
               AND o.timestamp >= m.date - make_interval(hours => %s) - make_interval(mins => %s)
             ORDER BY o.timestamp""",
            (chunk, list(MARKETS), list(BOOKS) + ["Pinnacle"], LOOKAHEAD_H, FRESH_MIN))
        g = defaultdict(lambda: defaultdict(list))
        for r in rows:
            g[(r["mid"], r["market"])][r["bk"]].append((r["ts"], r["selection"], r["od"]))
        for (mid, market), by_bk in g.items():
            sides = MARKETS[market]
            k = ko[mid]
            pin = by_bk.get("Pinnacle", [])
            sets = pin_sets(pin, sides)
            if not sets:
                continue
            closeq = defaultdict(list)
            for ts, sel, od in pin:
                closeq[sel].append((ts, od))
            c = assemble_close(closeq, sides, k)
            if not c:
                continue
            pclose = devig(c[0])
            if not pclose:
                continue
            fair_cache = {}
            for bk in BOOKS:
                quotes = sorted(by_bk.get(bk, []))
                last_val, since = {}, {}
                hit = set()
                for ts, sel, od in quotes:
                    if sel not in sides:
                        continue
                    if last_val.get(sel) != od:
                        last_val[sel], since[sel] = od, ts
                    if not (k - timedelta(hours=LOOKAHEAD_H) <= ts <= k - timedelta(minutes=LEAD_MIN)):
                        continue
                    anchor = [s for s in sets if s[0] <= ts and (ts - s[0]) <= timedelta(minutes=FRESH_MIN)]
                    if not anchor:
                        continue
                    ats, aodds = anchor[-1]
                    if ats not in fair_cache:
                        fair_cache[ats] = devig(aodds)
                    pf = fair_cache[ats]
                    if not pf:
                        continue
                    i_sel = sides.index(sel)
                    edge = pf[i_sel] * od - 1
                    if od > MAX_ODDS or edge > CEILING:
                        continue
                    stale = (ts - since[sel]) > timedelta(minutes=STALE_MIN)
                    for x in THRESHOLDS:
                        key = (sel, x)
                        if edge >= x and key not in hit:
                            hit.add(key)
                            clv = od * pclose[i_sel] - 1
                            day = str(ts)[:10]
                            days_seen.add(day)
                            legs[(bk, market, x, stale)].append(clv)
    ndays = max(1, len(days_seen))
    cells = []
    for (bk, market, x, stale), v in legs.items():
        n = len(v)
        m = mean(v)
        se = stdev(v) / math.sqrt(n) if n > 1 else float("nan")
        t = m / se if se and se > 0 else 0.0
        p = 0.5 * math.erfc(t / math.sqrt(2))       # one-sided, H1: clv > 0
        cells.append([bk, market, x, stale, n, n / ndays, m, se, t, p])
    # Holm across the whole family of cells
    order = sorted(range(len(cells)), key=lambda i: cells[i][9])
    run, M = 0.0, len(cells)
    for r, i in enumerate(order):
        run = max(run, min(1.0, (M - r) * cells[i][9]))
        cells[i].append(run)
    cells.sort(key=lambda c: (c[0], c[1], c[3], c[2]))
    print(f"OWN TAIL RE-TEST — last {a.days} days, {ndays} decision days, {len(cells)} cells (Holm m={M})\n")
    print(f"  {'book':12}{'market':15}{'X':>4} {'stale':>6}{'n':>6}{'/day':>6}{'clv_sharp':>11}{'t':>7}{'Holm p':>8}")
    for bk, market, x, stale, n, pd_, m, se, t, p, hp in cells:
        passes = (not stale) and m > 0 and hp < 0.00135 and pd_ >= BAR_PER_DAY
        print(f"  {bk:12}{market:15}{x:4.0%} {('stale' if stale else 'fresh'):>6}{n:6d}{pd_:6.1f}"
              f"{100*m:+10.2f}%{t:7.1f}{hp:8.3f}{'  ← PASSES BAR' if passes else ''}")
    print(f"\n  bar: fresh quotes, clv_sharp > 0, Holm-adjusted one-sided p < 0.00135 (t > 3), >= {BAR_PER_DAY:g} legs/day")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
