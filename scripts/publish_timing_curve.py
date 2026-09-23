#!/usr/bin/env python3
"""Best time to publish ([[#024]] (d)) — clv_sharp of the LIVE sharp rule by decision time.

Pre-registered on the #024 row before the first run. Replays the live rule at fixed
checkpoints before kickoff — T−24h, −12h, −6h, −3h, −1h, −45m — on the last N days:
at each checkpoint, fair = Shin of the latest complete Pinnacle set no older than 60
min with overround <= MAX_ANCHOR_OVERROUND; best price per selection across every
non-excluded book (latest quote within 60 min of the anchor); a leg when
p × best − 1 >= MIN_EDGE and best <= MAX_ODDS. Scored with clv_sharp against the same
fresh assembled close as workers/jobs/clv_sharp.py. Also reports clv by decision
hour on the Beat the Bookie replay (external check).

    python3 scripts/publish_timing_curve.py --days 21
"""
from __future__ import annotations

import argparse
import math
import pickle
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKPOINTS_MIN = (24 * 60, 12 * 60, 6 * 60, 3 * 60, 60, 45)
MARKETS = {"1x2": ("home", "draw", "away"), "over_under_25": ("over", "under")}
SCRAPED = {"Coolbet", "Epicbet", "Unibet-Site", "Tonybet"}
FRESH_MIN = 60


def stat(v):
    n = len(v)
    if n < 2:
        return n, (v[0] if v else 0.0), float("nan")
    m = mean(v)
    return n, m, m / (stdev(v) / math.sqrt(n)) if stdev(v) > 0 else 0.0


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from workers.api_clients.db import execute_query
    from workers.jobs.clv_sharp import assemble_close
    from workers.model.devig import devig
    from scripts.publish_picks_forward_test import (
        EXCLUDED_BOOKS, MAX_ANCHOR_OVERROUND, MAX_ODDS, MAX_RATIO, MIN_EDGE)

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    a = ap.parse_args()
    ms = execute_query("""SELECT id::text id, date FROM matches WHERE status='finished'
        AND date > now() - make_interval(days => %s) AND date < now()""", (a.days,))
    ko = {m["id"]: m["date"] for m in ms}
    ids = list(ko)
    res = defaultdict(list)            # (checkpoint, feed) -> [clv]
    same_snap = defaultdict(int)       # legs dropped: close is the SAME snapshot as the anchor
    days = set()
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        rows = execute_query("""
            SELECT o.match_id::text mid, o.market, o.selection, o.bookmaker bk,
                   o.timestamp ts, o.odds::float od
              FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
             WHERE o.match_id = ANY(%s::uuid[]) AND o.market = ANY(%s)
               AND o.is_live IS NOT TRUE AND o.odds > 1.01 AND NOT (o.bookmaker = ANY(%s))
               AND o.timestamp <= m.date AND o.timestamp >= m.date - interval '26 hours'
             ORDER BY o.timestamp""", (chunk, list(MARKETS), list(EXCLUDED_BOOKS)))
        g = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for r in rows:
            g[(r["mid"], r["market"])][r["bk"]][r["selection"]].append((r["ts"], r["od"]))
        for (mid, market), by_bk in g.items():
            sides, k = MARKETS[market], ko[mid]
            pin = by_bk.get("Pinnacle")
            if not pin:
                continue
            c = assemble_close(pin, sides, k)
            pclose = devig(c[0]) if c else None
            if not pclose:
                continue
            for cp in CHECKPOINTS_MIN:
                T = k - timedelta(minutes=cp)
                pre = {s: [(t, o) for t, o in pin.get(s, []) if t <= T] for s in sides}
                if any(not v for v in pre.values()):
                    continue
                anchor = assemble_close(pre, sides, T)      # latest complete set <= 60 min old at T
                if not anchor or sum(1 / o for o in anchor[0]) - 1 > MAX_ANCHOR_OVERROUND:
                    continue
                pf = devig(anchor[0])
                if not pf:
                    continue
                # MECHANICAL-CLV GUARD (found on the first run, 2026-09-23): near
                # kickoff the anchor and the close can be the SAME Pinnacle
                # snapshot, and then clv_sharp == the decision edge (>= 3% by
                # construction). A leg only counts if the close came strictly
                # AFTER the anchor, i.e. Pinnacle had the chance to move.
                if c[1] <= anchor[1]:
                    same_snap[cp] += 1
                    continue
                for i_s, s in enumerate(sides):
                    best = None
                    for bk, sq in by_bk.items():
                        if bk == "Pinnacle":
                            continue
                        q = [(t, o) for t, o in sq.get(s, [])
                             if t <= T and abs((t - anchor[1]).total_seconds()) <= FRESH_MIN * 60]
                        if q and (best is None or q[-1][1] > best[0]):
                            best = (q[-1][1], bk)
                    if not best or best[0] > MAX_ODDS or pf[i_s] * best[0] - 1 < MIN_EDGE:
                        continue
                    if best[0] / anchor[0][i_s] - 1 > MAX_RATIO:     # the live rule's phantom-price filter
                        continue
                    clv = best[0] * pclose[i_s] - 1
                    feed = "scraped" if best[1] in SCRAPED else "af_fed"
                    # Review 2026-09-23: a close with the SAME price as the anchor
                    # scores clv == entry edge — often a frozen / echoed AF-Pinnacle
                    # row, not a market that stood still. Reported separately.
                    moved = "moved" if abs(c[0][i_s] - anchor[0][i_s]) > 1e-9 else "unchanged"
                    res[(cp, feed)].append(clv)
                    res[(cp, "all")].append(clv)
                    res[(cp, moved)].append(clv)
                    days.add(str(k)[:10])
    nd = max(1, len(days))
    print(f"LIVE SHARP RULE BY DECISION TIME — last {a.days} days ({nd} days with a leg)\n")
    print(f"  {'checkpoint':>11} {'feed':>8} {'n':>6} {'/day':>6} {'clv_sharp':>10} {'t':>6}")
    for cp in CHECKPOINTS_MIN:
        for feed in ("all", "af_fed", "scraped", "moved", "unchanged"):
            n, m, t = stat(res[(cp, feed)])
            lab = f"T-{cp // 60}h" if cp >= 60 else f"T-{cp}m"
            extra = f"   ({same_snap[cp]} market-checkpoints dropped: close = anchor)" if feed == "all" else ""
            print(f"  {lab:>11} {feed:>8} {n:6d} {n / nd:6.1f} {100 * m:+9.2f}% {t:6.1f}{extra}")
    p = Path("/tmp/claude-501/btb_picks.pkl")
    if p.exists():
        picks = pickle.load(open(p, "rb"))
        print("\nEXTERNAL — Beat the Bookie consensus replay, clv by decision hour before kickoff:")
        by = defaultdict(list)
        for pk in picks:
            # the BtB close is index 71; a pick decided AT index 71 is scored
            # against its own anchor hour (mechanical clv) — excluded, as above.
            if pk.get("clv") is not None and pk["T"] < 71:
                by[72 - pk["T"]].append(pk["clv"])
        for h in sorted(by, reverse=True):
            n, m, t = stat(by[h])
            print(f"  {h:>3}h before  n={n:5d}  clv {100 * m:+6.2f}%  t={t:5.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
