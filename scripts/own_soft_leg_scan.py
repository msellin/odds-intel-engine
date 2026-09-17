#!/usr/bin/env python3
"""OWN-SOFT-LEG-SCAN — how often does a placeable price BEAT the sharp fair price?

WHY (2026-09-17, owner's question). "Coolbet has softer odds than Pinnacle, so
how does it charge more?" The premise is correct and the framing of overround as
"what you pay" is too coarse. A book's overround is an AVERAGE across the
outcomes of a market, and a soft book does not spread it evenly: it shades the
selections the public backs and is generous on the ones nobody wants. So its
average margin can be worse than Pinnacle's while its BEST legs beat Pinnacle
outright.

Worked on one real fixture (Besiktas v Marseille), against Pinnacle de-vigged:

    home   Coolbet 1.83  vs fair 1.8765   -2.5 pct
    draw   Coolbet 4.03  vs fair 4.2611   -5.4 pct
    away   Coolbet 4.05  vs fair 4.3026   -5.9 pct

Coolbet's home price is better than Pinnacle's own 1.81. The average says 4.15
pct; the home leg says 2.5 pct.

So the question that actually decides whether a strategy exists is not "what is
the vig" but "how often is a leg we can BET priced ABOVE its sharp fair value,
and by how much". That is a per-LEG question, and this measures it directly.

METHOD. Pinnacle's 1x2 triple is the anchor, de-vigged two ways (proportional
and Shin, because proportional overstates longshots and that is exactly where a
spurious edge would appear). Only anchors with a TIGHT overround are used — a
wide Pinnacle triple is not a reliable fair price. Every quote is time-aligned
within 15 minutes, assembled from complete triples, pre-match, never closing.
Edge per leg = fair_prob - 1/our_best_placeable_price.

WHAT A POSITIVE RESULT WOULD MEAN. Legs priced above fair exist in every market;
what matters is whether enough of them clear a threshold that survives the
uncertainty in the anchor itself. AF's "Pinnacle" is lagged (AF-PINNACLE-NOT-
PINNACLE), so a small positive edge is inside the noise of the reference.

RESULTS (2026-09-17, 21 days) — and the gate matters more than the edge:

    anchor gate   fixtures   legs   legs >3pp   per day
    <= 4 pct            38    114      3         0.1
    <= 9 pct           246    738     12         0.6

THE GATE WAS THROWING AWAY USABLE ANCHORS. A separate calibration test over
23,462 finished matches shows the de-vigged anchor stays accurate as its
overround widens, because de-vigging normalises the margin away and what
survives is the SHAPE of the price:

    anchor overround   fixtures   weighted calibration MAE
    <= 4 pct              1,308        0.496pp
    4-6 pct               3,578        0.565pp
    6-9 pct               4,676        0.551pp
    9-15 pct             13,568        0.683pp

So the <=4 pct gate discarded ~94 pct of usable anchors to buy 0.19pp of
accuracy. Relaxing it to 9 pct multiplies fixtures 6.5x.

IT DOES NOT CHANGE THE VERDICT. 0.6 legs/day clearing 3pp is ~220 bets a year;
at EUR 10 and a TRUE 3 pct edge that is EUR 66/yr, and the two bots already
running exactly this strategy read margin-corrected CLV of +1.14pp and -2.28pp
over 194 legs — indistinguishable from zero. The gate finding improves the
INSTRUMENT, not the economics.

AND THE ANCHOR ITSELF IS DEGRADED. Our stored "Pinnacle" 1x2 has a median
overround of 7.77 pct (a LOWER bound — the query that produced it takes the best
price per selection over time). Real Pinnacle runs 2-3 pct. Stored "Betfair"
reads 9.30 pct against a real exchange's 1-2 pct. So neither is the book it is
named after; AF-PINNACLE-NOT-PINNACLE is worse than the previously measured
"+0.81pp lag". A genuine sharp feed would be a real upgrade to every measurement
in this project.

Read-only.

    python3 scripts/own_soft_leg_scan.py [--days 21] [--anchor-max-ov 0.09]
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

PLACEABLE = ["Coolbet", "Unibet-Site", "Epicbet"]
ANCHOR = "Pinnacle"
SIDES = ("home", "draw", "away")
WINDOW_MIN = 15.0


def shin3(odds: dict) -> dict:
    """Shin de-vig for a 3-way market. Removes proportionally more margin from
    the longshot than proportional scaling does."""
    q = [1.0 / odds[s] for s in SIDES]
    t = sum(q)
    z = 0.0
    for _ in range(60):
        zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z)) for qi in q]
        s_ = sum(zs)
        if s_ > 1:
            z += (s_ - 1) * 0.5
        else:
            z -= (1 - s_) * 0.5
        z = max(0.0, min(0.5, z))
    zs = [((z * z + 4 * (1 - z) * qi * qi / t) ** 0.5 - z) / (2 * (1 - z)) for qi in q]
    tot = sum(zs)
    return {s: zs[i] / tot for i, s in enumerate(SIDES)}


def assemble(obs, window=WINDOW_MIN):
    obs = sorted(obs, key=lambda x: x[0])
    out = []
    for i, (t0, _, _) in enumerate(obs):
        picked = {}
        for t, sel, o in obs[i:]:
            if (t - t0).total_seconds() / 60.0 > window:
                break
            picked.setdefault(sel, o)
        if all(s in picked for s in SIDES):
            out.append((t0, {s: picked[s] for s in SIDES}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    ap.add_argument("--anchor-max-ov", type=float, default=0.09,
                    help="max anchor overround. 0.09 not 0.04: calibration holds to "
                         "0.55pp out to 9 pct and the tighter gate discarded 94 pct of "
                         "usable anchors")
    ap.add_argument("--align-min", type=float, default=15.0)
    a = ap.parse_args()

    rows = execute_query(
        """SELECT o.match_id, o.bookmaker, o.selection, o.odds::float AS odds, o.timestamp
             FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
            WHERE o.bookmaker = ANY(%s) AND o.market = '1x2'
              AND o.is_live IS NOT TRUE AND o.is_closing = false
              AND m.date > now() - make_interval(days => %s)
              AND o.timestamp < m.date""",
        (PLACEABLE + [ANCHOR], a.days)) or []

    per = defaultdict(lambda: defaultdict(list))
    for r in rows:
        per[r["match_id"]][r["bookmaker"]].append(
            (r["timestamp"], r["selection"], r["odds"]))

    edges_prop, edges_shin = [], []
    n_fixtures = n_anchor_rejected = 0
    for _mid, bybook in per.items():
        anc = assemble(bybook.get(ANCHOR, []))
        ours = {b: assemble(bybook.get(b, [])) for b in PLACEABLE}
        ours = {b: v for b, v in ours.items() if v}
        if not anc or not ours:
            continue
        best = None
        for t0, qa in anc:
            cand = {b: min(v, key=lambda tq: abs((tq[0] - t0).total_seconds()))
                    for b, v in ours.items()}
            gap = max(abs((c[0] - t0).total_seconds()) / 60.0 for c in cand.values())
            if best is None or gap < best[0]:
                best = (gap, qa, cand)
        if best is None or best[0] > a.align_min:
            continue
        _g, qa, cand = best
        ov = sum(1.0 / qa[s] for s in SIDES) - 1.0
        if not (0 <= ov <= a.anchor_max_ov):
            n_anchor_rejected += 1
            continue
        n_fixtures += 1
        fair_prop = {s: (1.0 / qa[s]) / (1.0 + ov) for s in SIDES}
        fair_shin = shin3(qa)
        bo = {s: max(c[1][s] for c in cand.values()) for s in SIDES}
        for s in SIDES:
            edges_prop.append(fair_prop[s] - 1.0 / bo[s])
            edges_shin.append(fair_shin[s] - 1.0 / bo[s])

    if not edges_prop:
        print("no aligned fixtures with a tight anchor")
        return 1

    print(f"\n=== placeable legs vs the Pinnacle fair price, {a.days}d ===")
    print(f"  fixtures with a TIGHT anchor (<= {a.anchor_max_ov*100:.0f} pct) and alignment: "
          f"{n_fixtures:,}   (rejected for a wide anchor: {n_anchor_rejected:,})")
    print(f"  placeable legs priced against it: {len(edges_prop):,}\n")

    for name, e in (("PROPORTIONAL de-vig", edges_prop), ("SHIN de-vig", edges_shin)):
        e = sorted(e)
        n = len(e)
        print(f"  {name}")
        print(f"    median {st.median(e)*100:+.2f}pp   "
              f"p75 {e[int(n*0.75)]*100:+.2f}pp   p90 {e[int(n*0.90)]*100:+.2f}pp   "
              f"p99 {e[int(n*0.99)]*100:+.2f}pp")
        for thr in (0.0, 0.01, 0.02, 0.03):
            k = sum(1 for x in e if x > thr)
            print(f"    legs beating fair by more than {thr*100:>2.0f}pp: {k:5,} "
                  f"({k/n*100:5.2f} pct)   ~{k/a.days:.1f}/day")
        print()
    print("  A leg above 0 is priced better than the sharp fair value. Whether that")
    print("  is an EDGE depends on the anchor being right: AF's Pinnacle feed is")
    print("  lagged (AF-PINNACLE-NOT-PINNACLE), so small positives sit inside the")
    print("  reference's own error.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
