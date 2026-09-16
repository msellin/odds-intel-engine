#!/usr/bin/env python3
"""OWN-MARGIN-BY-MARKET (2026-09-16) — the kill criterion, per market.

`own_path_kill_criterion.py` measured ONE market: 1x2, best-of-3 de-vigged
overround 5.66% against a pre-specified 2.00% threshold. The owner asked the
obvious next question: we also collect O/U at five lines, BTTS, Asian handicap,
team totals and first-half markets — is any of them CHEAPER to bet than 1x2?

Same arithmetic AND the same time-alignment discipline as the 1x2 criterion.

⚠️ METHOD, and why it is written this way (this script's FIRST version was
wrong and reported numbers that were too good — 2026-09-16):

    The first cut selected `max(odds) ... GROUP BY match, book, line, selection`
    over a 10-day window. That takes each book's BEST-EVER price per selection
    and sums those into an "overround", so a book is credited with prices it
    never showed simultaneously, and one book's Tuesday peak is compared against
    another's Thursday peak. It biases every overround DOWNWARD and manufactures
    cross-book dispersion out of nothing but time. It is ANALYSIS_GOTCHAS
    §52/§55 (the best-of-books mirage) in its time dimension, and it made Asian
    handicap look 0.70pp cheaper than 1x2 when the aligned gap is different.

    So: a book's complement must be assembled from rows inside
    ASSEMBLE_WINDOW_MIN of each other, and the books compared must be aligned
    inside `--align-min` of each other. Both are the 1x2 criterion's defaults.

Two-leg complements only (the overround of a partial market is not an overround,
it is the sum of whatever legs happened to be stored), grouped by handicap line
so Asian handicap is compared like-for-like, pre-match only.

THE ANSWER, 10 days to 2026-09-16, time-aligned within 15 min (median):

    market                fixtures   single   best-of-N   recovered
    over_under_45              447    6.00%      4.97%      1.02pp
    over_under_15              654    6.97%      5.15%      1.82pp
    team_total_home_15         421    7.18%      5.36%      1.82pp
    team_total_away_15         421    7.14%      5.55%      1.59pp
    asian_handicap             666    7.54%      5.96%      1.58pp
    over_under_35              889    7.03%      6.01%      1.02pp
    over_under_25              986    7.19%      6.22%      0.97pp
    btts                      1554    7.06%      6.23%      0.83pp
    (1x2, kill criterion)             7.71%      5.66%      2.05pp

NOT ONE MARKET CLEARS THE 2% LINE, and 1x2 — the market already measured and
already closed — is still among the best of them. O/U 4.5 is nominally cheapest
at 4.97% on 447 fixtures, which is a thin, high-line market where our books
quote few fixtures; it is not a door.

⚠️ THE FIRST VERSION OF THIS TABLE WAS WRONG and is corrected above. It reported
asian_handicap at 4.96% and called it "0.70pp cheaper than 1x2, the cheapest
market we can bet". The aligned number is 5.96% and AH is FIFTH. The unaligned
run also understated every single-book margin by 1-2pp and understated the
line-shopping recovery in derivatives (reported 0.05-0.93pp; actual 0.83-1.82pp),
which reversed the secondary conclusion too: shopping in derivatives recovers
somewhat LESS than 1x2's 2.05pp, not almost nothing.

Read-only.

    python3 scripts/own_market_margin_by_market.py [--days 10] [--align-min 15]
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402

BOOKS = ["Coolbet", "Unibet-Site", "Epicbet"]
KILL_THRESHOLD = 0.02
ASSEMBLE_WINDOW_MIN = 15.0
MARKETS = [
    "btts", "over_under_25", "over_under_35", "over_under_15", "over_under_45",
    "team_total_home_15", "team_total_away_15", "over_under_1h_15", "asian_handicap",
]


def load(days: int, markets: list[str]):
    rows = execute_query(
        """SELECT o.match_id, o.market, o.bookmaker, o.selection, o.handicap_line,
                  o.odds::float AS odds, o.timestamp
             FROM odds_snapshots o
             JOIN matches m ON m.id = o.match_id
            WHERE o.bookmaker = ANY(%s) AND o.market = ANY(%s)
              AND o.is_live IS NOT TRUE
              AND m.date > now() - (%s || ' days')::interval
              AND o.timestamp < m.date""",
        (BOOKS, markets, str(days)),
    ) or []
    d: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        key = (r["market"], r["match_id"], str(r["handicap_line"]))
        d[key][r["bookmaker"]].append(
            (r["timestamp"], str(r["selection"]).lower(), r["odds"]))
    return d


def assemble(obs, window=ASSEMBLE_WINDOW_MIN):
    """[(anchor_ts, {sel: odds})] — a book's COMPLETE two-leg complements, the
    two rows allowed to straddle `window` minutes. Identical in shape to the 1x2
    criterion's assemble(), which needs three."""
    obs = sorted(obs, key=lambda x: x[0])
    out = []
    for i, (t0, _, _) in enumerate(obs):
        picked: dict[str, float] = {}
        for t, sel, o in obs[i:]:
            if (t - t0).total_seconds() / 60.0 > window:
                break
            picked.setdefault(sel, o)
        if len(picked) == 2:
            out.append((t0, dict(picked)))
    return out


def run(days: int, align_min: float, min_n: int):
    d = load(days, MARKETS)
    single: dict = defaultdict(list)
    bestn: dict = defaultdict(list)
    for (market, _mid, _line), bybook in d.items():
        comp = {b: assemble(v) for b, v in bybook.items()}
        comp = {b: v for b, v in comp.items() if v}
        if len(comp) < 2:          # need ≥2 books to say anything about shopping
            continue
        books = sorted(comp)
        # tightest simultaneous window across the books that quote this market
        best = None
        for t0, q0 in comp[books[0]]:
            cand = [(t0, q0)]
            for b in books[1:]:
                cand.append(min(comp[b],
                                key=lambda tq: abs((tq[0] - t0).total_seconds())))
            gap = (max(c[0] for c in cand) - min(c[0] for c in cand)).total_seconds() / 60.0
            if best is None or gap < best[0]:
                best = (gap, cand)
        if best is None or best[0] > align_min:
            continue
        quotes = dict(zip(books, [c[1] for c in best[1]]))
        # the two books must be quoting the SAME pair of selections
        sels = set.intersection(*(set(q) for q in quotes.values()))
        if len(sels) != 2:
            continue
        for b in books:
            single[market].append(sum(1.0 / quotes[b][s] for s in sels) - 1.0)
        bo = {s: max(quotes[b][s] for b in books) for s in sels}
        bestn[market].append(sum(1.0 / bo[s] for s in sels) - 1.0)

    out = []
    for m in sorted(bestn, key=lambda k: st.median(bestn[k]) if len(bestn[k]) >= min_n else 9):
        if len(bestn[m]) < min_n:
            continue
        out.append((m, len(bestn[m]), st.median(single[m]), st.median(bestn[m])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--align-min", type=float, default=15.0)
    ap.add_argument("--min-n", type=int, default=30)
    a = ap.parse_args()

    res = run(a.days, a.align_min, a.min_n)
    print(f"\n=== overround by market, {a.days}d, TIME-ALIGNED within {a.align_min:.0f} min ===")
    print(f"{'market':22s} {'fixtures':>9s} {'single':>9s} {'best-of-N':>10s} {'recovered':>10s}  verdict")
    for m, n, s, b in res:
        v = "UNDER THRESHOLD" if b < KILL_THRESHOLD else "above 2% kill line"
        print(f"{m:22s} {n:9d} {s*100:8.2f}% {b*100:9.2f}% {(s-b)*100:9.2f}pp  {v}")
    if res:
        m, _n, _s, b = res[0]
        print(f"\ncheapest market we can bet: {m} at {b*100:.2f}% "
              f"({'UNDER' if b < KILL_THRESHOLD else 'still above'} the {KILL_THRESHOLD*100:.2f}% kill line)")
    print("\nNOTE: same-line comparisons only. Cross-book LINE discrepancies (Coolbet AH -0.5 vs")
    print("Unibet AH -0.75 on one fixture) are NOT measured here and remain an open question.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
