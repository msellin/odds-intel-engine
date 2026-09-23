#!/usr/bin/env python3
"""#090 (a) — does our goals rating predict WHERE Pinnacle's O/U 2.5 price will move?

Pre-registered in dev/active/per-market-feature-sets-design.md ("#090 (a) PRICE-MOVE
SIGNAL") before this was written.

    r    = logit(P_rating(over 2.5)) − logit(p_early)
    move = logit(p_close) − logit(p_early)

P_rating: Poisson with λ = ht_expected_total + h2_expected_total (the stored,
walk-forward #084 ratings). p_early / p_close: Shin de-vig of Pinnacle's FIRST and
LAST complete O/U 2.5 pair strictly before kickoff, the close a LATER snapshot.

Test 1: OLS move ~ r on the first half (date order), scored on the second.
Test 2: on scored O/U 2.5 legs (clv_sharp_legs, one row per bet), clv_sharp when r
        AGREES with the pick's side vs DISAGREES.

    python3 scripts/ou_price_move_signal.py
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def p_over(lam: float, line: float = 2.5) -> float:
    k = int(math.floor(line))
    return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))


def ols(x, y):
    mx, my = mean(x), mean(y)
    sxx = sum((a - mx) ** 2 for a in x)
    b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / sxx
    a0 = my - b * mx
    res = [c - a0 - b * a for a, c in zip(x, y)]
    se = math.sqrt(sum(e * e for e in res) / (len(x) - 2) / sxx)
    return a0, b, se


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    from workers.api_clients.db import execute_query
    from workers.model.devig import devig

    rows = execute_query("""
        SELECT o.match_id::text mid, o.selection, o.timestamp ts, o.odds::float od, m.date ko,
               m.league_id::text lg,
               f.ht_expected_total::float ht, f.h2_expected_total::float h2
          FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
          JOIN match_feature_vectors f ON f.match_id = m.id
         WHERE o.bookmaker = 'Pinnacle' AND o.market = 'over_under_25' AND o.is_live IS NOT TRUE
           AND o.odds > 1.01 AND o.timestamp < m.date AND m.status = 'finished'
           AND f.ht_expected_total IS NOT NULL AND f.h2_expected_total IS NOT NULL
         ORDER BY o.match_id, o.timestamp""")
    by = defaultdict(lambda: defaultdict(dict))
    meta = {}
    for r in rows:
        by[r["mid"]][r["ts"]][r["selection"]] = r["od"]
        meta[r["mid"]] = (r["ko"], r["ht"] + r["h2"], r["lg"])
    data = []
    for mid, snaps in by.items():
        full = sorted((ts, d) for ts, d in snaps.items() if "over" in d and "under" in d)
        if len(full) < 2:
            continue
        (t0, d0), (t1, d1) = full[0], full[-1]
        if t1 <= t0:
            continue
        pe, pc = devig([d0["over"], d0["under"]]), devig([d1["over"], d1["under"]])
        if not pe or not pc:
            continue
        ko, lam, lg = meta[mid]
        r = logit(p_over(lam)) - logit(pe[0])
        data.append((ko, mid, r, logit(pc[0]) - logit(pe[0]), pe[0], pc[0], lg))
    data.sort()
    n = len(data)
    half = n // 2
    tr, te = data[:half], data[half:]
    a0, b, _ = ols([d[2] for d in tr], [d[3] for d in tr])
    _, bte, sete = ols([d[2] for d in te], [d[3] for d in te])
    corr = lambda ds: (lambda x, y: sum((a - mean(x)) * (c - mean(y)) for a, c in zip(x, y)) /  # noqa: E731
                       math.sqrt(sum((a - mean(x)) ** 2 for a in x) * sum((c - mean(y)) ** 2 for c in y)))(
        [d[2] for d in ds], [d[3] for d in ds])
    t = bte / sete
    p1 = 0.5 * math.erfc(t / math.sqrt(2))
    q = sorted(te, key=lambda d: d[2])
    k = len(q) // 5
    top, bot = q[-k:], q[:k]
    top_move = mean(d[5] - d[4] for d in top)
    bot_move = mean(d[5] - d[4] for d in bot)
    print(f"#090 (a) PRICE-MOVE SIGNAL — {n:,} fixtures with an early AND a later Pinnacle O/U 2.5 pair\n")
    print(f"  corr(r, move): train {corr(tr):+.3f}   held-out {corr(te):+.3f}")
    print(f"  held-out slope {bte:+.4f} ± {sete:.4f}  t={t:+.1f}  one-sided p={p1:.2g}   (train slope {b:+.4f})")
    print(f"  held-out quintiles: TOP r (rating says more goals) → Pinnacle over-prob moves {100*top_move:+.2f}pp;"
          f" BOTTOM → {100*bot_move:+.2f}pp")
    ok1 = bte > 0 and p1 < 0.01 and top_move > 0.005
    print(f"  TEST 1: {'PASS' if ok1 else 'FAIL'}  (slope>0 at p<0.01 AND top-quintile move > +0.5pp)")

    # POST-HOC CHECKS, added after the first run (2026-09-23) — not part of the pass bar.
    # (1) Drift: the held-out half's AVERAGE move is not zero (Pinnacle drifted toward
    #     overs), and it inflates the one-sided top-quintile figure. The drift-free
    #     size is half the top-minus-bottom spread.
    # (2) Mean-reversion control: r contains −logit(p_early), so a noisy early price
    #     that reverts would look like signal with ANY rating. Replace the rating
    #     with a walk-forward league mean of early prices (knows nothing about teams):
    #     if that control predicts the move too, the rating adds nothing.
    drift = mean(d[5] - d[4] for d in te)
    print(f"  post-hoc: held-out mean move {100*drift:+.2f}pp (drift); drift-free size "
          f"±{100*(top_move - bot_move)/2:.2f}pp per side")
    acc, glob, ctl = defaultdict(lambda: [0.0, 0]), [0.0, 0], []
    for d in data:
        e = logit(d[4]); s = acc[d[6]]
        lm = s[0] / s[1] if s[1] >= 10 else (glob[0] / glob[1] if glob[1] else e)
        ctl.append(lm - e)
        s[0] += e; s[1] += 1; glob[0] += e; glob[1] += 1
    _, bc, sec = ols(ctl[half:], [d[3] for d in te])
    print(f"  post-hoc CONTROL (league-mean 'rating'): held-out slope {bc:+.4f} ± {sec:.4f}  t={bc/sec:+.1f}"
          f"   vs the rating's t={t:+.1f}")

    rmap = {d[1]: d[2] for d in data}
    legs = execute_query("""SELECT ledger, match_id::text mid, selection, clv_sharp::float c
        FROM clv_sharp_legs WHERE market = 'over_under_25' AND dup_rank = 1
         AND abs(clv_sharp) <= 0.5""")
    print("\n  TEST 2 — clv_sharp of scored O/U 2.5 legs, signal AGREES vs DISAGREES with the pick:")
    g = defaultdict(lambda: ([], []))
    for l in legs:
        r = rmap.get(l["mid"])
        if r is None or l["selection"] not in ("over", "under"):
            continue
        agree = (r > 0) == (l["selection"] == "over")
        for key in (l["ledger"], "ALL"):
            g[key][0 if agree else 1].append(l["c"])
    for key, (ag, dis) in sorted(g.items()):
        if len(ag) < 5 or len(dis) < 5:
            continue
        diff = mean(ag) - mean(dis)
        se = math.sqrt(stdev(ag) ** 2 / len(ag) + stdev(dis) ** 2 / len(dis))
        tt = diff / se if se else 0.0
        p2 = 0.5 * math.erfc(tt / math.sqrt(2))
        print(f"    {key:20s} agree n={len(ag):5d} {100*mean(ag):+6.2f}%   disagree n={len(dis):5d} "
              f"{100*mean(dis):+6.2f}%   gap {100*diff:+5.2f}pp  t={tt:+.1f}  p={p2:.3f}"
              f"{'  PASS' if (key == 'ALL' and diff > 0 and p2 < 0.05) else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
