"""LINESHOP-SHIN-DEVIG-2026-08-26 — is Shin better calibrated than proportional?

The line-shopping bots treat the de-vigged Pinnacle close as truth. So the only
question that matters is: which de-vig method produces probabilities that match
what actually happens?

This is deliberately NOT an ROI comparison. PER-BOT-SWEEP-2026-08-24 established
that selecting configs on backtest ROI is anti-predictive (-9.2% out of sample),
so ROI cannot arbitrate a mechanism choice. Calibration can: a de-vig method is
right or wrong independently of whether betting on it made money, and a method
that is better calibrated over 20 months and ~100k outcomes will still be better
calibrated next month.

Metrics, all lower-is-better except where noted:
  * Brier score   — mean squared error of the probability
  * Log loss      — penalises confident mistakes harder
  * Calibration   — mean(p) vs realised frequency, per probability decile
  * Longshot bias — the specific failure proportional de-vig is predicted to
                    have: over-stating low-probability outcomes

Usage:
    python3 scripts/devig_calibration_backtest.py --start 2025-01-01 --end 2026-08-27
    python3 scripts/devig_calibration_backtest.py --market btts     # 2-way control
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402
from workers.model.devig import proportional_devig, shin_devig  # noqa: E402

SIDES = {
    "1x2": ["home", "draw", "away"],
    "btts": ["yes", "no"],
    "over_under_25": ["over", "under"],
    "over_under_35": ["over", "under"],
}


def outcome(market: str, selection: str, sh: int, sa: int) -> int | None:
    if market == "1x2":
        return int({"home": sh > sa, "draw": sh == sa, "away": sa > sh}[selection])
    if market == "btts":
        yes = sh > 0 and sa > 0
        return int(yes if selection == "yes" else not yes)
    if market.startswith("over_under"):
        line = float(market.replace("over_under_", "")) / 10.0
        total = sh + sa
        if total == line:
            return None
        return int(total > line if selection == "over" else total < line)
    return None


def fetch(market: str, start: str, end: str) -> list[dict]:
    """Last pre-kickoff Pinnacle price per selection, plus the final score."""
    return execute_query(
        """
        WITH px AS (
          SELECT DISTINCT ON (o.match_id, o.selection)
                 o.match_id, o.selection, o.odds
            FROM odds_snapshots o
            JOIN matches m ON m.id = o.match_id
           WHERE o.bookmaker = 'Pinnacle' AND o.market = %s
             AND o.timestamp <= m.date
             AND m.status = 'finished'
             AND m.score_home IS NOT NULL AND m.score_away IS NOT NULL
             AND m.date >= %s AND m.date < %s
           ORDER BY o.match_id, o.selection, o.timestamp DESC
        )
        SELECT px.match_id, px.selection, px.odds, m.score_home, m.score_away, m.date
          FROM px JOIN matches m ON m.id = px.match_id
        """,
        [market, start, end],
    )


def summarise(name: str, pairs: list[tuple[float, int]]) -> dict:
    n = len(pairs)
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    eps = 1e-12
    ll = -sum(
        y * math.log(max(p, eps)) + (1 - y) * math.log(max(1 - p, eps)) for p, y in pairs
    ) / n
    mean_p = sum(p for p, _ in pairs) / n
    freq = sum(y for _, y in pairs) / n
    return {"name": name, "n": n, "brier": brier, "logloss": ll, "mean_p": mean_p, "freq": freq}


def deciles(pairs: list[tuple[float, int]], k: int = 10) -> list[tuple[float, float, int]]:
    pairs = sorted(pairs, key=lambda t: t[0])
    n = len(pairs)
    out = []
    for b in range(k):
        lo, hi = n * b // k, n * (b + 1) // k
        chunk = pairs[lo:hi]
        if not chunk:
            continue
        out.append(
            (
                sum(p for p, _ in chunk) / len(chunk),
                sum(y for _, y in chunk) / len(chunk),
                len(chunk),
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-08-27")
    ap.add_argument("--market", default="1x2", choices=sorted(SIDES))
    ap.add_argument("--by-year", action="store_true")
    args = ap.parse_args()

    sides = SIDES[args.market]
    rows = fetch(args.market, args.start, args.end)
    by_match: dict = defaultdict(dict)
    scores: dict = {}
    dates: dict = {}
    for r in rows:
        mid = str(r["match_id"])
        by_match[mid][r["selection"]] = float(r["odds"])
        scores[mid] = (int(r["score_home"]), int(r["score_away"]))
        dates[mid] = r["date"]

    prop_pairs: list[tuple[float, int]] = []
    shin_pairs: list[tuple[float, int]] = []
    raw_pairs: list[tuple[float, int]] = []
    per_year: dict = defaultdict(lambda: {"prop": [], "shin": []})
    complete = 0

    for mid, quotes in by_match.items():
        if any(s not in quotes or quotes[s] <= 1.0 for s in sides):
            continue
        odds = [quotes[s] for s in sides]
        pp = proportional_devig(odds)
        sp = shin_devig(odds)
        if pp is None or sp is None:
            continue
        sh, sa = scores[mid]
        yr = dates[mid].strftime("%Y")
        ok = True
        staged = []
        for i, s in enumerate(sides):
            y = outcome(args.market, s, sh, sa)
            if y is None:
                ok = False
                break
            staged.append((i, s, y))
        if not ok:
            continue
        complete += 1
        for i, s, y in staged:
            prop_pairs.append((pp[i], y))
            shin_pairs.append((sp[i], y))
            raw_pairs.append((1.0 / odds[i], y))
            per_year[yr]["prop"].append((pp[i], y))
            per_year[yr]["shin"].append((sp[i], y))

    if not prop_pairs:
        print("no complete markets found")
        return 1

    print(f"market={args.market}  window {args.start} → {args.end}")
    print(f"complete markets: {complete}   outcome rows: {len(prop_pairs)}\n")

    res = [
        summarise("raw (vig left in)", raw_pairs),
        summarise("proportional de-vig", prop_pairs),
        summarise("shin de-vig", shin_pairs),
    ]
    print(f"{'method':24s} {'brier':>10s} {'logloss':>10s} {'mean p':>9s} {'actual':>9s} {'gap':>8s}")
    print("-" * 74)
    for r in res:
        print(
            f"{r['name']:24s} {r['brier']:10.6f} {r['logloss']:10.6f} "
            f"{r['mean_p']*100:8.3f}% {r['freq']*100:8.3f}% {(r['mean_p']-r['freq'])*100:+7.3f}pp"
        )

    b_prop, b_shin = res[1]["brier"], res[2]["brier"]
    print(f"\nBrier: shin - proportional = {b_shin - b_prop:+.8f}  "
          f"({'shin better' if b_shin < b_prop else 'proportional better'})")

    print("\nLongshot check — deciles of de-vigged probability (predicted vs actual)")
    print(f"{'decile':>7s} {'n':>7s} {'prop pred':>10s} {'prop act':>9s} {'prop gap':>9s}"
          f" {'shin pred':>10s} {'shin act':>9s} {'shin gap':>9s}")
    dp, ds = deciles(prop_pairs), deciles(shin_pairs)
    for i, ((pp_, pa, pn), (sp_, sa_, _)) in enumerate(zip(dp, ds), 1):
        print(
            f"{i:7d} {pn:7d} {pp_*100:9.2f}% {pa*100:8.2f}% {(pp_-pa)*100:+8.2f}pp"
            f" {sp_*100:9.2f}% {sa_*100:8.2f}% {(sp_-sa_)*100:+8.2f}pp"
        )

    if args.by_year:
        print("\nStability by year (Brier, lower better)")
        print(f"{'year':>6s} {'n':>8s} {'proportional':>14s} {'shin':>12s} {'winner':>14s}")
        for yr in sorted(per_year):
            p, s = per_year[yr]["prop"], per_year[yr]["shin"]
            if len(p) < 500:
                continue
            bp = summarise("p", p)["brier"]
            bs = summarise("s", s)["brier"]
            print(f"{yr:>6s} {len(p):8d} {bp:14.6f} {bs:12.6f} "
                  f"{'shin' if bs < bp else 'proportional':>14s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
