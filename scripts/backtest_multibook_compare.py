"""
Path B (2026-06-24): Compare Bet365-only backtest ROI vs best-of-N-book ROI
on the same Pinnacle-fair edge strategy across the cached football-data.co.uk
CSVs.

Hypothesis: WinnerOdds' +6.35% advantage over our Bet365-only backtest's
-3 to -5% is largely driven by book selection (they spread across many
books, capturing soft-book mispricing). If that's true, the same picks
booked at MAX odds instead of B365 should close most of the gap.

Method (cheap proof-of-concept):
  For each match in cache:
    1. Compute Pinnacle-fair probabilities (margin-stripped).
    2. For each selection (1X2 and OU 2.5):
       a. If pinnacle_fair * b365_odds - 1 >= min_edge → BET-B365 row.
       b. If pinnacle_fair * max_odds  - 1 >= min_edge → BET-MAX  row.
    3. Compute won + pnl using the actual match result.
  Aggregate both variants. Report side-by-side ROI / CLV / volume.

Notes:
- "Max" is football-data.co.uk's best-of-N pre-match offer (varies by season
  but typically the higher of ~5-15 listed books — proxies what a price-
  shopping bettor at multiple books could have taken).
- min_edge configurable; default 2% — same default as backtest_football_data.
- Stake = 10 units flat per bet (same as the baseline backtest).
- VOIDS not modeled (push handling is identical in both variants and rare).

Usage:
    python3 scripts/backtest_multibook_compare.py
    python3 scripts/backtest_multibook_compare.py --min-edge 3
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
CACHE = ROOT / "dev/active/fd_cache"


def fair_3way(h: float, d: float, a: float) -> tuple[float, float, float]:
    s = 1/h + 1/d + 1/a
    return 1/h/s, 1/d/s, 1/a/s


def fair_2way(o: float, u: float) -> tuple[float, float]:
    s = 1/o + 1/u
    return 1/o/s, 1/u/s


def f(v) -> float | None:
    try:
        x = float(v)
        return x if x > 1.0 else None
    except (TypeError, ValueError):
        return None


# Per-season "best book" pre-match column triple — pick first column set
# that exists (newer seasons have MaxH/D/A; 2018-19 has BbMxH/D/A).
MAX_TRIPLES_1X2 = [
    ("MaxH", "MaxD", "MaxA"),
    ("BbMxH", "BbMxD", "BbMxA"),
]
MAX_TRIPLES_OU25 = [
    ("Max>2.5", "Max<2.5"),
    ("BbMx>2.5", "BbMx<2.5"),
]


def pick_triple(df: pd.DataFrame, options: list[tuple]) -> tuple | None:
    for opt in options:
        if all(c in df.columns for c in opt):
            return opt
    return None


def evaluate_csv(df: pd.DataFrame, league: str, season: str,
                  min_edge: float, agg: dict) -> int:
    n_used = 0
    max_1x2 = pick_triple(df, MAX_TRIPLES_1X2)
    max_ou = pick_triple(df, MAX_TRIPLES_OU25)

    for _, r in df.iterrows():
        result = str(r.get("FTR", "")).strip().upper()
        if result not in ("H", "D", "A"):
            continue
        try:
            sh = int(r.get("FTHG", 0) or 0)
            sa = int(r.get("FTAG", 0) or 0)
        except (ValueError, TypeError):
            continue

        # 1X2
        b365 = (f(r.get("B365H")), f(r.get("B365D")), f(r.get("B365A")))
        ps = (f(r.get("PSH")), f(r.get("PSD")), f(r.get("PSA")))
        psc = (f(r.get("PSCH")), f(r.get("PSCD")), f(r.get("PSCA")))
        maxodds = (None, None, None)
        if max_1x2:
            maxodds = tuple(f(r.get(c)) for c in max_1x2)

        if all(b365) and all(ps):
            fp = fair_3way(*ps)
            outcomes = (result == "H", result == "D", result == "A")
            for i, (sel, won) in enumerate(zip(("home", "draw", "away"), outcomes)):
                for variant, taken in (("b365", b365[i]),
                                          ("max",  maxodds[i] if max_1x2 else None)):
                    if taken is None:
                        continue
                    edge = fp[i] * taken - 1
                    if edge < min_edge / 100:
                        continue
                    pnl = (taken - 1) * 10.0 if won else -10.0
                    clv = (taken / psc[i] - 1) if psc[i] else None
                    s = agg[(variant, "1x2", sel)]
                    s["n"] += 1
                    s["stake"] += 10.0
                    s["pnl"] += pnl
                    s["w"] += 1 if won else 0
                    if clv is not None:
                        s["clv_sum"] += clv
                        s["clv_n"] += 1
                    n_used += 1

        # Over/Under 2.5
        b365_ov = f(r.get("B365>2.5"))
        b365_un = f(r.get("B365<2.5"))
        ps_ov = f(r.get("P>2.5"))
        ps_un = f(r.get("P<2.5"))
        pc_ov = f(r.get("PC>2.5"))
        pc_un = f(r.get("PC<2.5"))
        max_ov = max_un = None
        if max_ou:
            max_ov = f(r.get(max_ou[0]))
            max_un = f(r.get(max_ou[1]))

        if b365_ov and b365_un and ps_ov and ps_un:
            fp_ov, fp_un = fair_2way(ps_ov, ps_un)
            total = sh + sa
            won_ov, won_un = total > 2, total < 3
            for sel, fair_p, b_taken, max_taken, cl, won in (
                ("over",  fp_ov, b365_ov, max_ov, pc_ov, won_ov),
                ("under", fp_un, b365_un, max_un, pc_un, won_un),
            ):
                for variant, taken in (("b365", b_taken), ("max", max_taken)):
                    if taken is None:
                        continue
                    edge = fair_p * taken - 1
                    if edge < min_edge / 100:
                        continue
                    pnl = (taken - 1) * 10.0 if won else -10.0
                    clv = (taken / cl - 1) if cl else None
                    s = agg[(variant, "ou25", sel)]
                    s["n"] += 1
                    s["stake"] += 10.0
                    s["pnl"] += pnl
                    s["w"] += 1 if won else 0
                    if clv is not None:
                        s["clv_sum"] += clv
                        s["clv_n"] += 1
                    n_used += 1

    return n_used


TOP5_EUROPEAN = {"E0", "D1", "SP1", "I1", "F1"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-edge", type=float, default=2.0)
    ap.add_argument("--seasons", nargs="+", default=None,
                    help="Limit to specific seasons (e.g. 2324 2425). "
                         "Default: all cached.")
    ap.add_argument("--top5", action="store_true",
                    help="Restrict to top-5 European leagues "
                         "(E0 Premier, D1 Bundesliga, SP1 La Liga, "
                         "I1 Serie A, F1 Ligue 1) — the most liquid + "
                         "where soft books limit slowest.")
    ap.add_argument("--leagues", nargs="+", default=None,
                    help="Custom league codes (football-data.co.uk format).")
    args = ap.parse_args()

    # Resolve league filter
    league_filter: set | None = None
    if args.top5:
        league_filter = TOP5_EUROPEAN
    elif args.leagues:
        league_filter = set(args.leagues)

    seasons = args.seasons
    if not seasons:
        seasons = sorted(p.name for p in CACHE.iterdir() if p.is_dir())

    agg: dict = defaultdict(lambda: {"n": 0, "stake": 0.0, "pnl": 0.0, "w": 0,
                                      "clv_sum": 0.0, "clv_n": 0})
    n_csv = 0
    for season in seasons:
        sdir = CACHE / season
        if not sdir.exists():
            continue
        for csv in sdir.glob("*.csv"):
            if league_filter is not None and csv.stem not in league_filter:
                continue
            try:
                df = pd.read_csv(csv, encoding="latin-1")
            except Exception:
                continue
            evaluate_csv(df, csv.stem, season, args.min_edge, agg)
            n_csv += 1
    league_label = (
        f"top-5 European ({','.join(sorted(league_filter))})"
        if league_filter else "all cached"
    )
    print(f"Processed {n_csv} CSVs across {len(seasons)} seasons "
          f"(min-edge {args.min_edge}%, leagues: {league_label})")
    print()

    # Side-by-side
    print(f"{'variant':6s} {'market':6s} {'sel':6s} {'n':>7s} {'stake':>10s} {'pnl':>10s} "
          f"{'ROI':>8s} {'hit':>6s} {'avg CLV':>8s} {'CLV beat':>9s}")
    print("-" * 86)
    for key in sorted(agg):
        v, mkt, sel = key
        d = agg[key]
        if d["n"] == 0:
            continue
        roi = 100 * d["pnl"] / d["stake"] if d["stake"] else 0
        hit = 100 * d["w"] / d["n"] if d["n"] else 0
        clv_avg = 100 * d["clv_sum"] / d["clv_n"] if d["clv_n"] else 0
        print(f"{v:6s} {mkt:6s} {sel:6s} {d['n']:>7d} {d['stake']:>10.0f} "
              f"{d['pnl']:>+10.0f} {roi:>+7.2f}% {hit:>5.1f}% {clv_avg:>+7.2f}% "
              f"{d['clv_n']:>9d}")
    print()

    # Aggregates per variant
    print("HEADLINE — bet at B365 vs bet at MAX, all bets pooled:")
    for variant in ("b365", "max"):
        n = sum(d["n"] for k, d in agg.items() if k[0] == variant)
        stake = sum(d["stake"] for k, d in agg.items() if k[0] == variant)
        pnl = sum(d["pnl"] for k, d in agg.items() if k[0] == variant)
        w = sum(d["w"] for k, d in agg.items() if k[0] == variant)
        clv_n = sum(d["clv_n"] for k, d in agg.items() if k[0] == variant)
        clv_s = sum(d["clv_sum"] for k, d in agg.items() if k[0] == variant)
        roi = 100 * pnl / stake if stake else 0
        hit = 100 * w / n if n else 0
        clv = 100 * clv_s / clv_n if clv_n else 0
        print(f"  {variant:5s}  n={n:>6d}  stake={stake:>9.0f}  pnl={pnl:>+9.0f}  "
              f"ROI={roi:>+6.2f}%  hit={hit:>5.1f}%  avg_CLV={clv:>+6.2f}%")


if __name__ == "__main__":
    main()
