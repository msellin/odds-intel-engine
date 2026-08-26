"""SHADOW-CLV-BOOKMAKER-FIX-2026-08-26 — which CLV definition actually predicts ROI?

Computes three CLV variants on the same settled-bet rows and compares their
predictive power against realised return:

  clv_anybook  = odds_at_pick / closing_odds(any book, whatever sorted last)
                 -- what settlement.py:get_closing_odds() stores today
  clv_samebook = odds_at_pick / closing_odds(the book we actually picked at)
                 -- "did MY price get worse", the honest execution measure
  clv_pinraw   = odds_at_pick / Pinnacle close - 1
                 -- what simulated_bets.clv_pinnacle already stores today
  clv_pindevig = odds_at_pick * devig(Pinnacle close) - 1
                 -- the sharp-money validator, margin removed

Predictive power is measured three ways so a single flattering statistic can't
carry the argument: Spearman rank correlation with realised return, the
top-minus-bottom quintile ROI spread, and a bucket-monotonicity count.

Usage:
    python3 scripts/clv_variant_backtest.py [--since 2026-01-01] [--table both]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.jobs.settlement import (  # noqa: E402
    _normalize_bet_market,
    _normalize_bet_selection,
)

# 1X2 / OU / BTTS complements, used to rebuild the overround for de-vigging.
_COMPLEMENTS = {
    "1x2": ("home", "draw", "away"),
    "btts": ("yes", "no"),
}


def _ou_sides() -> tuple[str, str]:
    return ("over", "under")


def _sibling_selections(market: str, selection: str) -> tuple[str, ...]:
    if market in _COMPLEMENTS:
        return _COMPLEMENTS[market]
    if market.startswith("over_under"):
        return _ou_sides()
    return (selection,)


def fetch_rows(table: str, since: str) -> list[dict]:
    """Settled bets that have a recommended_bookmaker (needed for same-book CLV)."""
    return execute_query(
        f"""
        SELECT b.id, b.match_id, b.market, b.selection, b.odds_at_pick,
               b.recommended_bookmaker, b.result, b.clv AS clv_stored,
               bo.name AS bot_name, b.pick_time
          FROM {table} b
          JOIN bots bo ON bo.id = b.bot_id
         WHERE b.result IN ('won','lost')
           AND b.pick_time >= %s
           AND b.recommended_bookmaker IS NOT NULL
        """,
        [since],
    )


def fetch_closes(match_ids: list[str]) -> dict:
    """Last pre-kickoff price per (match, market, selection, bookmaker).

    Uses the pre-KO cutoff rather than is_closing because is_closing coverage is
    only ~73% and its absence is not random — see CLOSING-PRE-KO-FALLBACK.
    """
    if not match_ids:
        return {}
    rows = execute_query(
        """
        SELECT DISTINCT ON (o.match_id, o.market, o.selection, o.bookmaker)
               o.match_id, o.market, o.selection, o.bookmaker, o.odds
          FROM odds_snapshots o
          JOIN matches m ON m.id = o.match_id
         WHERE o.match_id = ANY(%s::uuid[])
           AND o.timestamp <= m.date
         ORDER BY o.match_id, o.market, o.selection, o.bookmaker, o.timestamp DESC
        """,
        [match_ids],
    )
    out: dict = {}
    for r in rows:
        out[(str(r["match_id"]), r["market"], r["selection"], r["bookmaker"])] = float(r["odds"])
    return out


def devigged_pin_prob(closes: dict, match_id: str, market: str, selection: str) -> float | None:
    """Proportional de-vig of the Pinnacle close across the market's full set."""
    sibs = _sibling_selections(market, selection)
    probs = []
    for s in sibs:
        o = closes.get((match_id, market, s, "Pinnacle"))
        if not o or o <= 1.0:
            return None
        probs.append(1.0 / o)
    if len(probs) != len(sibs):
        return None
    total = sum(probs)
    own = closes.get((match_id, market, selection, "Pinnacle"))
    if not own or total <= 0:
        return None
    return (1.0 / own) / total


def spearman(xs: list[float], ys: list[float]) -> float:
    def rank(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0


def quintiles(pairs: list[tuple[float, float]]) -> list[tuple[int, float, float]]:
    """(bucket, mean clv, mean return) for 5 equal-count buckets sorted by CLV."""
    pairs = sorted(pairs, key=lambda p: p[0])
    n = len(pairs)
    out = []
    for b in range(5):
        lo, hi = n * b // 5, n * (b + 1) // 5
        chunk = pairs[lo:hi]
        if not chunk:
            continue
        out.append(
            (b + 1, sum(c for c, _ in chunk) / len(chunk), sum(r for _, r in chunk) / len(chunk))
        )
    return out


def report(label: str, pairs: list[tuple[float, float]]) -> dict:
    if len(pairs) < 50:
        print(f"\n{label}: only {len(pairs)} rows — skipped")
        return {}
    q = quintiles(pairs)
    rho = spearman([c for c, _ in pairs], [r for _, r in pairs])
    spread = (q[-1][2] - q[0][2]) * 100
    mono = sum(1 for i in range(1, len(q)) if q[i][2] >= q[i - 1][2])
    print(f"\n{label}  (n={len(pairs)})")
    print(f"  spearman rho = {rho:+.4f}   Q5-Q1 ROI spread = {spread:+.1f}pp   monotone steps {mono}/4")
    for b, c, r in q:
        print(f"    Q{b}  clv {c*100:+7.1f}%   roi {r*100:+7.2f}%")
    return {"n": len(pairs), "rho": rho, "spread": spread, "mono": mono}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--table", default="both", choices=["simulated_bets", "shadow_bets", "both"])
    ap.add_argument("--bots", default=None, help="substring filter on bot name")
    args = ap.parse_args()

    tables = ["simulated_bets", "shadow_bets"] if args.table == "both" else [args.table]
    rows: list[dict] = []
    for t in tables:
        got = fetch_rows(t, args.since)
        print(f"{t}: {len(got)} settled rows with a recommended_bookmaker since {args.since}")
        rows.extend(got)

    if args.bots:
        rows = [r for r in rows if args.bots in (r["bot_name"] or "")]
        print(f"filtered to bot name containing {args.bots!r}: {len(rows)} rows")

    # shadow_bets carries one row per refresh cohort; collapse to unique picks so
    # a pick that sat on the board all day is not counted 48 times.
    seen: dict = {}
    for r in rows:
        key = (r["bot_name"], str(r["match_id"]), r["market"], r["selection"])
        if key not in seen or r["pick_time"] < seen[key]["pick_time"]:
            seen[key] = r
    rows = list(seen.values())
    print(f"after cohort dedup: {len(rows)} unique picks")

    closes = fetch_closes(list({str(r["match_id"]) for r in rows}))
    print(f"loaded {len(closes)} closing prices")

    anybook: list[tuple[float, float]] = []
    samebook: list[tuple[float, float]] = []
    pinraw: list[tuple[float, float]] = []
    pindevig: list[tuple[float, float]] = []
    # Same-row triples, so the three variants can be compared on identical
    # picks. Coverage differs a lot between them (de-vigged Pinnacle needs the
    # FULL Pinnacle market at close), and comparing different samples would let
    # a coverage difference masquerade as a skill difference.
    common: list[tuple[float, float, float, float, float]] = []
    coverage = defaultdict(int)
    per_market_pin = defaultdict(lambda: [0, 0])

    for r in rows:
        mid = str(r["match_id"])
        # Use production's own normalisers, not a reimplementation — otherwise
        # this measures the backtest's market-name gaps rather than the CLV
        # variants ("o/u" + "over 3.5" -> over_under_35, "1X2" -> "1x2", ...).
        mkt = _normalize_bet_market(r["market"], r["selection"])
        sel = _normalize_bet_selection(r["selection"])
        odds = float(r["odds_at_pick"])
        ret = (odds - 1.0) if r["result"] == "won" else -1.0

        if r["clv_stored"] is not None:
            anybook.append((float(r["clv_stored"]), ret))
            coverage["anybook"] += 1

        own_close = closes.get((mid, mkt, sel, r["recommended_bookmaker"]))
        if own_close and own_close > 1.0:
            samebook.append((odds / own_close - 1.0, ret))
            coverage["samebook"] += 1

        pin_close = closes.get((mid, mkt, sel, "Pinnacle"))
        raw = None
        if pin_close and pin_close > 1.0:
            raw = odds / pin_close - 1.0
            pinraw.append((raw, ret))
            coverage["pinraw"] += 1

        p = devigged_pin_prob(closes, mid, mkt, sel)
        per_market_pin[mkt][1] += 1
        if p and 0.0 < p < 1.0:
            pindevig.append((odds * p - 1.0, ret))
            coverage["pindevig"] += 1
            per_market_pin[mkt][0] += 1

        if (r["clv_stored"] is not None and own_close and own_close > 1.0
                and raw is not None and p and 0.0 < p < 1.0):
            common.append(
                (float(r["clv_stored"]), odds / own_close - 1.0, raw, odds * p - 1.0, ret)
            )

    print(f"\ncoverage: {dict(coverage)}")
    print("de-vigged-Pinnacle coverage by market (needs the full market at close):")
    for mkt, (got, tot) in sorted(per_market_pin.items(), key=lambda kv: -kv[1][1]):
        print(f"    {mkt:18s} {got:6d}/{tot:6d}  {100.0*got/tot if tot else 0:5.1f}%")
    res = {
        "anybook": report("clv_anybook   (today's stored value)", anybook),
        "samebook": report("clv_samebook  (own book's close)", samebook),
        "pinraw": report("clv_pinraw    (raw Pinnacle close - today's clv_pinnacle)", pinraw),
        "pindevig": report("clv_pindevig  (de-vigged Pinnacle close)", pindevig),
    }

    if len(common) >= 50:
        print("\n" + "=" * 68)
        print(f"APPLES-TO-APPLES — same {len(common)} picks, all three variants")
        report("  [common] clv_anybook", [(a, r) for a, _, _, _, r in common])
        report("  [common] clv_samebook", [(b, r) for _, b, _, _, r in common])
        report("  [common] clv_pinraw", [(c, r) for _, _, c, _, r in common])
        report("  [common] clv_pindevig", [(d, r) for _, _, _, d, r in common])

    print("\n" + "=" * 68)
    print("VERDICT — higher rho and a wider monotone spread = better validator")
    for k, v in res.items():
        if v:
            print(f"  {k:10s} n={v['n']:6d}  rho={v['rho']:+.4f}  spread={v['spread']:+6.1f}pp  mono={v['mono']}/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
