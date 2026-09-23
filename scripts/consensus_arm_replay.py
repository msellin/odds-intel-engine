"""Replay the consensus_anchor arm's rule over history, for pattern-mining.

WHY THIS EXISTS (CONSENSUS-ARM-GRADING, 2026-09-23). The consensus arm went live
on 2026-09-22 and had 23 settled picks a day later — far too few to find a
pattern in. The rule is fully mechanical, so it can be re-run over the stored
odds history: this walks every finished match, simulates the publisher's
30-minute cadence (now+45min .. now+14h window, latest quote per book within
6h), and records the FIRST leg per (match, market) that the rule would have
published — the same claim-once semantics as production.

Each replayed pick carries the features that could plausibly grade it (book
count, cross-book disagreement, how far the best price sits above the second
best, which book, lead time, league) plus TWO outcome measures:

  * realised P&L at 1u flat — what a reader would have got, but noisy;
  * closing edge — p_close * odds - 1 where p_close is the same de-vigged
    >=5-book consensus taken at the last snapshot before kickoff. Much less
    noisy than P&L, and the right thing to rank a grading rule on.

Faithful to scripts/publish_picks_forward_test.py constants; imports them so a
rule change there changes the replay too. Known deviations: no cross-arm dedupe
against the live arm, and runs are simulated on the :00/:30 grid.

Usage:  python3 scripts/consensus_arm_replay.py --days 56 --out /tmp/replay.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

from scripts.publish_picks_forward_test import (
    ALIGN_MIN, CONSENSUS_MAX_EDGE, CONSENSUS_MIN_BOOKS, EXCLUDED_BOOKS,
    LOOKAHEAD_H, MARKETS, MAX_ODDS, MAX_RATIO, MIN_EDGE, MIN_LEAD_MIN,
)
from workers.model.devig import devig

STALE_H = 6
# Candidate "second opinion" books recorded per pick, so a grading rule can ask
# "does this book also see an edge?" (owner question 2026-09-23: Marathonbet?).
CHECK_BOOKS = ("Marathonbet", "SBO", "Betfair", "1xBet", "Bet365", "Betano")


def consensus(latest, market, T, sides):
    """Per-book de-vig, then average — mirrors _consensus_anchor. Returns
    (probs, anchor_ts, per_book_probs) or None."""
    per, stamps = {}, []
    books = {b for (mk, s, b) in latest if mk == market}
    for b in books:
        qs = [latest.get((market, s, b)) for s in sides]
        if any(q is None or q[0] <= 1.0 or q[1] <= T - timedelta(hours=STALE_H)
               for q in qs):
            continue
        p = devig([q[0] for q in qs])
        if p:
            per[b] = p
            stamps.append(max(q[1] for q in qs))
    if len(per) < CONSENSUS_MIN_BOOKS:
        return None
    avg = [sum(p[i] for p in per.values()) / len(per) for i in range(len(sides))]
    return avg, max(stamps), per


def outcome(market, sel, sh, sa):
    if market == "1x2":
        res = "home" if sh > sa else "away" if sa > sh else "draw"
        return res == sel
    return (sh + sa > 2.5) == (sel == "over")


def replay_match(m, snaps):
    kickoff = m["kickoff"]
    first = kickoff - timedelta(hours=LOOKAHEAD_H)
    T = first.replace(minute=0 if first.minute < 30 else 30, second=0,
                      microsecond=0) + timedelta(minutes=30)
    last_run = kickoff - timedelta(minutes=MIN_LEAD_MIN)
    latest, i, published, picks = {}, 0, set(), []
    while T < last_run:
        while i < len(snaps) and snaps[i][3] <= T:
            mk, s, b, ts, o = snaps[i]
            latest[(mk, s, b)] = (o, ts)
            i += 1
        for market, sides in MARKETS.items():
            if market in published:
                continue
            got = consensus(latest, market, T, sides)
            if not got:
                continue
            probs, anchor_ts, per = got
            best = None
            for s, p in zip(sides, probs):
                fair = 1.0 / p
                aligned = [(o, b, ts) for (mk, ss, b), (o, ts) in latest.items()
                           if mk == market and ss == s
                           and ts > T - timedelta(hours=STALE_H)
                           and abs((ts - anchor_ts).total_seconds()) / 60 <= ALIGN_MIN]
                if not aligned:
                    continue
                aligned.sort(reverse=True)
                odds, book, ts = aligned[0]
                if odds > MAX_ODDS or odds / fair - 1 > MAX_RATIO:
                    continue
                edge = p * odds - 1
                if edge < MIN_EDGE or edge > CONSENSUS_MAX_EDGE:
                    continue
                if best and best["edge"] >= edge:
                    continue
                idx = sides.index(s)
                sel_ps = [pp[idx] for pp in per.values()]
                mean = sum(sel_ps) / len(sel_ps)
                sd = math.sqrt(sum((x - mean) ** 2 for x in sel_ps) / len(sel_ps))
                second = aligned[1][0] if len(aligned) > 1 else None
                best = dict(
                    match_id=m["id"], league=m["league"], country=m["country"],
                    tier=m["tier"], kickoff=kickoff, run_at=T, market=market,
                    selection=s, odds=odds, bookmaker=book, edge=edge, p=p,
                    n_books=len(per), n_aligned=len(aligned),
                    p_sd=sd, has_pinnacle=int("Pinnacle" in per),
                    p_pinnacle=per["Pinnacle"][idx] if "Pinnacle" in per else None,
                    **{f"p_{b.lower().replace(' ', '_')}": (per[b][idx] if b in per else None)
                       for b in CHECK_BOOKS},
                    second_odds=second,
                    gap_to_second=(odds / second - 1) if second else None,
                    n_books_pos_edge=sum(1 for o, _, _ in aligned if p * o - 1 > 0),
                    lead_h=(kickoff - T).total_seconds() / 3600,
                )
            if best:
                published.add(market)
                picks.append(best)
        T += timedelta(minutes=30)

    if not picks:
        return []
    # closing consensus: every snapshot before kickoff
    while i < len(snaps) and snaps[i][3] <= kickoff:
        mk, s, b, ts, o = snaps[i]
        latest[(mk, s, b)] = (o, ts)
        i += 1
    for pk in picks:
        sides = MARKETS[pk["market"]]
        got = consensus(latest, pk["market"], kickoff, sides)
        pc = got[0][sides.index(pk["selection"])] if got else None
        pk["p_close"] = pc
        pk["close_edge"] = pc * pk["odds"] - 1 if pc else None
        won = outcome(pk["market"], pk["selection"], m["sh"], m["sa"])
        pk["won"] = int(won)
        pk["pnl"] = pk["odds"] - 1 if won else -1.0
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=56)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    load_dotenv()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute("""
        SELECT m.id, m.date, m.score_home, m.score_away, l.name, l.country, l.tier
          FROM matches m JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished' AND m.score_home IS NOT NULL
           AND m.date > now() - (%s || ' days')::interval
           AND m.date < now() - interval '3 hours'""", (str(a.days),))
    matches = {r[0]: dict(id=r[0], kickoff=r[1], sh=r[2], sa=r[3], league=r[4],
                          country=r[5], tier=r[6]) for r in cur.fetchall()}
    print(f"{len(matches)} finished matches", file=sys.stderr)

    sc = conn.cursor(name="snaps")
    sc.itersize = 50000
    sc.execute("""
        SELECT o.match_id, o.market, o.selection, o.bookmaker, o.timestamp,
               o.odds::float
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
         WHERE o.market = ANY(%s) AND o.is_live IS NOT TRUE
           AND NOT (o.bookmaker = ANY(%s))
           AND m.status = 'finished'
           AND m.date > now() - (%s || ' days')::interval
           AND m.date < now() - interval '3 hours'
           AND o.timestamp > m.date - interval '21 hours'
           AND o.timestamp <= m.date
         ORDER BY o.match_id, o.timestamp""",
        (list(MARKETS), list(EXCLUDED_BOOKS), str(a.days)))

    out, cur_id, buf, n = [], None, [], 0
    for mid, mk, s, b, ts, o in sc:
        if mid != cur_id:
            if cur_id in matches:
                out += replay_match(matches[cur_id], buf)
            cur_id, buf = mid, []
            n += 1
            if n % 2000 == 0:
                print(f"  {n} matches, {len(out)} picks", file=sys.stderr)
        buf.append((mk, s, b, ts, o))
    if cur_id in matches:
        out += replay_match(matches[cur_id], buf)

    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} replayed picks -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
