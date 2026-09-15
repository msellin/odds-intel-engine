"""Does a qualifying pick's price SURVIVE long enough for a reader to act?

RESULT (2026-09-15, 28 sampled run times, 65 qualifying legs):

    T+30 min   same book still clears 3% : 61/65 (94%)   median edge +4.30% -> +4.29%
    T+60 min   same book still clears 3% : 40/65 (62%)   median edge +4.30% -> +3.45%

So a published price is good for about half an hour and has decayed materially
by an hour. That is what sized the publishing cadence: at :05/:35 a reader
acting within the window gets the quoted price 94% of the time.

WHAT THIS DOES **NOT** SHOW. Persistence is not correctness. The rule selects the
maximum across a median of 12 books, and those selected quotes sit at the
98.8th-99.9th percentile of each book's own disagreement with Pinnacle — a price
can be stable for an hour and still be one book's standing error rather than a
market mispricing. This script measures whether a reader can GET the price, not
whether the price is RIGHT. See the handover's evidence section for the latter.

Read-only. Re-run:  PYTHONPATH=. python3 scripts/picks_price_reversion.py


Point-in-time only (ANALYSIS_GOTCHAS §64): every pool is built from rows a job
running at T could actually have seen, and the window is restricted to the 7-day
intact-retention era so the price PATH exists at all.

For each leg qualifying at T we ask two different questions:
  (a) READER: is the SAME book still offering a price that clears the floor at
      T+30 / T+60? That is what somebody acting on the message gets.
  (b) RULE: does the rule still find ANY qualifying price on that selection?
      Best-of-books can rotate to another book and stay above the floor.
"""
import sys, statistics as st
from datetime import datetime, timedelta, timezone
sys.path.insert(0, '/Users/margussellin/www/odds-intel-engine')
from workers.api_clients.db import execute_query
from workers.model.devig import devig
from scripts.publish_picks_forward_test import (
    MIN_EDGE, MAX_ODDS, MAX_RATIO, MAX_ANCHOR_OVERROUND, ALIGN_MIN,
    MIN_LEAD_MIN, LOOKAHEAD_H, EXCLUDED_BOOKS, MARKETS)

SQL = """
  SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
         o.match_id, o.market, o.selection, o.bookmaker,
         o.odds::float odds, o.timestamp, m.date kickoff,
         ht.name home
    FROM odds_snapshots o
    JOIN matches m  ON m.id = o.match_id
    JOIN teams  ht  ON ht.id = m.home_team_id
   WHERE o.market = ANY(%s) AND o.is_live IS NOT TRUE
     AND NOT (o.bookmaker = ANY(%s))
     AND o.timestamp <= %s AND o.timestamp > %s
     AND m.date > %s AND m.date < %s
   ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o.timestamp DESC
"""

def pool_at(T):
    rows = execute_query(SQL, (list(MARKETS), list(EXCLUDED_BOOKS), T,
                               T - timedelta(hours=6),
                               T + timedelta(minutes=MIN_LEAD_MIN),
                               T + timedelta(hours=LOOKAHEAD_H)))
    q, meta = {}, {}
    for r in rows:
        meta[r["match_id"]] = r
        q.setdefault(r["match_id"], {}).setdefault(r["market"], {}) \
         .setdefault(r["selection"], {})[r["bookmaker"]] = (r["odds"], r["timestamp"])
    out = {}
    for mid, bym in q.items():
        for market, sides in MARKETS.items():
            sq = bym.get(market) or {}
            pin = {s: sq.get(s, {}).get("Pinnacle") for s in sides}
            if any(pin[s] is None for s in sides):
                continue
            ats = max(pin[s][1] for s in sides)
            anc = {s: pin[s][0] for s in sides}
            if sum(1.0 / o for o in anc.values()) - 1.0 > MAX_ANCHOR_OVERROUND:
                continue
            probs = devig([anc[s] for s in sides])
            if probs is None:
                continue
            for s, p in zip(sides, probs):
                al = {b: v for b, v in (sq.get(s) or {}).items()
                      if abs((v[1] - ats).total_seconds()) / 60.0 <= ALIGN_MIN}
                if not al:
                    continue
                best_b, (best_o, _) = max(al.items(), key=lambda kv: kv[1][0])
                key = (mid, market, s)
                out[key] = {
                    "p": p, "aligned": al, "anchor": anc[s],
                    "best_book": best_b, "best_odds": best_o,
                    "kickoff": meta[mid]["kickoff"], "home": meta[mid]["home"],
                }
    return out

def qualifies(entry, odds, anchor):
    return (odds <= MAX_ODDS and odds / anchor - 1.0 <= MAX_RATIO
            and entry["p"] * odds - 1.0 >= MIN_EDGE)

now = datetime.now(timezone.utc)
runs = []
for d in range(1, 8):
    for hh in (8, 11, 14, 17):
        T = (now - timedelta(days=d)).replace(hour=hh, minute=5, second=0, microsecond=0)
        runs.append(T)

surv = {30: [], 60: []}          # (a) same book still clears
rule = {30: [], 60: []}          # (b) rule still finds any qualifying price
edge_now, edge_later = {30: [], 60: []}, {30: [], 60: []}
book_gone = {30: 0, 60: 0}
n_legs = 0

for T in runs:
    pool = pool_at(T)
    legs = [(k, v) for k, v in pool.items()
            if qualifies(v, v["best_odds"], v["anchor"])]
    if not legs:
        continue
    later = {d: pool_at(T + timedelta(minutes=d)) for d in (30, 60)}
    for key, v in legs:
        # exclude legs that would leave the window purely on the lead clock
        if v["kickoff"] <= T + timedelta(minutes=MIN_LEAD_MIN + 60):
            continue
        n_legs += 1
        e0 = v["p"] * v["best_odds"] - 1.0
        for d in (30, 60):
            lv = later[d].get(key)
            edge_now[d].append(e0)
            if lv is None:
                surv[d].append(0); rule[d].append(0); book_gone[d] += 1
                edge_later[d].append(None); continue
            same = lv["aligned"].get(v["best_book"])
            if same is None:
                book_gone[d] += 1
                surv[d].append(0)
            else:
                surv[d].append(1 if qualifies(lv, same[0], lv["anchor"]) else 0)
            rule[d].append(1 if qualifies(lv, lv["best_odds"], lv["anchor"]) else 0)
            edge_later[d].append(lv["p"] * lv["best_odds"] - 1.0)

print(f"=== PRICE REVERSION — point-in-time, intact 7-day window ===")
print(f"run times sampled : {len(runs)}   qualifying legs: {n_legs}")
if n_legs:
    for d in (30, 60):
        s, r = surv[d], rule[d]
        el = [x for x in edge_later[d] if x is not None]
        print(f"\n--- T+{d} min ---")
        print(f"  (a) SAME book still clears 3%  : {sum(s)}/{len(s)} ({100*sum(s)/len(s):.0f}%)")
        print(f"  (b) rule finds ANY qualifying  : {sum(r)}/{len(r)} ({100*sum(r)/len(r):.0f}%)")
        print(f"      that book's quote gone     : {book_gone[d]}/{len(s)}")
        if el:
            print(f"      median edge at T   : {st.median(edge_now[d])*100:+.2f}%")
            print(f"      median edge at T+{d}: {st.median(el)*100:+.2f}%")
