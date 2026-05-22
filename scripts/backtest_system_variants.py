"""
SYSTEM BET VARIANT BACKTEST

Answers: which sub-combo structure is most profitable for our leg pool?

Structures tested per N legs (3, 4, 5):
  straight        — N-fold only (all legs must win)
  doubles_only    — 2-leg combos only
  trebles_only    — 3-leg combos only
  doubles+trebles — 2 and 3-leg combos
  trebles_up      — size 3..N
  fours_up        — size 4..N
  top2_sizes      — N-fold + (N-1)-folds only
  no_singles      — all combos size 2..N  [current production]
  kelly_weighted  — same as no_singles but stake proportional to per-combo Kelly

All structures normalise to the same total daily stake (€1) for fair ROI comparison.

Markets: btts, ou25 (over_under_25), ou35 (over_under_35), ou15 (over_under_15)
Leg filters: edge >= 5%, odds 1.40-2.50, one leg per match per day, best edge wins

Usage:
  python3 scripts/backtest_system_variants.py
  python3 scripts/backtest_system_variants.py --csv dev/active/backtest-3year.csv
  python3 scripts/backtest_system_variants.py --min-edge 0.08
  python3 scripts/backtest_system_variants.py --legs 4      # only N=4
  python3 scripts/backtest_system_variants.py --days 365   # last year only
"""

import argparse
import csv
import math
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

DEFAULT_CSV = Path("dev/active/backtest-3year.csv")

ACCA_MARKETS = {
    "btts":           ("btts",),
    "over_under_25":  ("over_under_25",),
    "over_under_35":  ("over_under_35",),
    "over_under_15":  ("over_under_15",),
}
ACCA_MARKET_SET = set(ACCA_MARKETS.keys())

DAILY_STAKE = 1.0   # normalised — all structures compared at same total daily outlay


# ── data loading ──────────────────────────────────────────────────────────────

def load_legs(min_edge: float, max_odds: float, min_odds: float,
              days: int | None, csv_path: Path) -> dict[str, list[dict]]:
    """Load acca-eligible settled singles, one best-edge leg per match per day.
    Returns {pick_date: [leg, ...]} sorted by edge desc."""
    cutoff = (date.today() - timedelta(days=days)).isoformat() if days else None
    best: dict[tuple, dict] = {}   # (date, match_id) → best row

    with open(csv_path) as f:
        for r in csv.DictReader(f):
            if r["won"] not in ("True", "False"):
                continue
            if r["market"] not in ACCA_MARKET_SET:
                continue
            pick_date = r["date"][:10]
            if cutoff and pick_date < cutoff:
                continue
            odds = float(r["odds"])
            edge = float(r["edge"])
            if edge < min_edge or odds > max_odds or odds < min_odds:
                continue
            key = (pick_date, r["match_id"])
            if key not in best or edge > best[key]["edge"]:
                best[key] = {
                    "date":     pick_date,
                    "match_id": r["match_id"],
                    "market":   r["market"],
                    "sel":      r["selection"],
                    "odds":     odds,
                    "prob":     float(r["model_prob"]),
                    "edge":     edge,
                    "won":      r["won"] == "True",
                }

    by_day: dict[str, list] = defaultdict(list)
    for leg in best.values():
        by_day[leg["date"]].append(leg)
    for legs in by_day.values():
        legs.sort(key=lambda x: -x["edge"])
    return dict(by_day)


# ── structure definitions ─────────────────────────────────────────────────────

def subcombos_for_structure(legs: list[dict], structure: str) -> list[list[dict]]:
    """Return list of sub-bets (each a list of legs) for the given structure."""
    n = len(legs)
    if structure == "straight":
        return [legs]
    if structure == "doubles_only":
        return [list(c) for c in combinations(legs, 2)]
    if structure == "trebles_only":
        return [list(c) for c in combinations(legs, 3)] if n >= 3 else []
    if structure == "doubles+trebles":
        return ([list(c) for c in combinations(legs, 2)] +
                ([list(c) for c in combinations(legs, 3)] if n >= 3 else []))
    if structure == "trebles_up":
        return [list(c) for k in range(3, n + 1) for c in combinations(legs, k)]
    if structure == "fours_up":
        return [list(c) for k in range(4, n + 1) for c in combinations(legs, k)] if n >= 4 else []
    if structure == "top2_sizes":
        # N-fold + (N-1)-folds
        result = [list(c) for c in combinations(legs, n)]
        if n > 2:
            result += [list(c) for c in combinations(legs, n - 1)]
        return result
    if structure in ("no_singles", "kelly_weighted"):
        return [list(c) for k in range(2, n + 1) for c in combinations(legs, k)]
    raise ValueError(f"Unknown structure: {structure!r}")


def kelly_stake(prob: float, odds: float) -> float:
    """Fractional Kelly for a single bet (capped at 0)."""
    return max(0.0, (prob * odds - 1) / (odds - 1))


def sub_kelly(sub: list[dict]) -> float:
    """Kelly stake for a sub-combo (product of probs and odds)."""
    p = math.prod(l["prob"] for l in sub)
    o = math.prod(l["odds"] for l in sub)
    if o <= 1.0:
        return 0.0
    return max(0.0, (p * o - 1) / (o - 1))


# ── simulation ────────────────────────────────────────────────────────────────

def simulate(by_day: dict, n_legs: int, structure: str) -> list[dict]:
    """Simulate one structure for N legs. Returns list of day-results."""
    results = []
    for day in sorted(by_day):
        pool = by_day[day]
        if len(pool) < n_legs:
            continue
        legs = pool[:n_legs]
        subs = subcombos_for_structure(legs, structure)
        if not subs:
            continue

        # Stake allocation: normalise so total daily outlay = DAILY_STAKE
        if structure == "kelly_weighted":
            raw = [sub_kelly(s) for s in subs]
            total_raw = sum(raw)
            if total_raw <= 0:
                continue
            stakes = [DAILY_STAKE * k / total_raw for k in raw]
        else:
            per_sub = DAILY_STAKE / len(subs)
            stakes = [per_sub] * len(subs)

        day_pnl = 0.0
        for sub, stake in zip(subs, stakes):
            combined_odds = math.prod(l["odds"] for l in sub)
            won = all(l["won"] for l in sub)
            day_pnl += stake * (combined_odds - 1) if won else -stake

        results.append({
            "date":      day,
            "n_subs":    len(subs),
            "pnl":       day_pnl,
            "won_any":   day_pnl > 0,
            "legs_won":  sum(l["won"] for l in legs),
        })

    return results


# ── stats ─────────────────────────────────────────────────────────────────────

def stats(results: list[dict], label: str) -> dict:
    if not results:
        return {"label": label, "n": 0}
    n = len(results)
    total_pnl = sum(r["pnl"] for r in results)
    profit_days = sum(1 for r in results if r["pnl"] > 0)
    biggest = max(r["pnl"] for r in results)

    running = peak = max_dd = 0.0
    dry = longest_dry = 0
    for r in results:
        running += r["pnl"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        if r["pnl"] > 0:
            dry = 0
        else:
            dry += 1
            longest_dry = max(longest_dry, dry)

    # avg legs won per day
    avg_legs_won = sum(r["legs_won"] for r in results) / n

    return {
        "label":        label,
        "n":            n,
        "profit_days":  profit_days,
        "hit_pct":      profit_days / n * 100,
        "roi_pct":      total_pnl / (n * DAILY_STAKE) * 100,
        "total_pnl":    total_pnl,
        "biggest":      biggest,
        "max_dd":       max_dd,
        "longest_dry":  longest_dry,
        "avg_legs_won": avg_legs_won,
        "d_per_hit":    n / profit_days if profit_days else float("inf"),
    }


def print_row(s: dict):
    if s["n"] == 0:
        print(f"  {s['label']:<30}  (no qualifying days)")
        return
    roi_marker = " ◄" if s["roi_pct"] > 0 else ""
    print(
        f"  {s['label']:<30}  "
        f"days={s['n']:>4d}  "
        f"hit={s['hit_pct']:>5.1f}%  "
        f"roi={s['roi_pct']:>+7.1f}%{roi_marker:<2}  "
        f"big=€{s['biggest']:>6.2f}  "
        f"dd=€{s['max_dd']:>5.2f}  "
        f"dry={s['longest_dry']:>3d}d  "
        f"d/hit={s['d_per_hit']:>5.1f}  "
        f"avg_legs_won={s['avg_legs_won']:.2f}"
    )


# ── main ──────────────────────────────────────────────────────────────────────

STRUCTURES = [
    "straight",
    "doubles_only",
    "trebles_only",
    "doubles+trebles",
    "trebles_up",
    "fours_up",
    "top2_sizes",
    "no_singles",
    "kelly_weighted",
]

LEG_COUNTS = [3, 4, 5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv",       type=Path,  default=DEFAULT_CSV)
    ap.add_argument("--min-edge",  type=float, default=0.05)
    ap.add_argument("--max-odds",  type=float, default=2.50)
    ap.add_argument("--min-odds",  type=float, default=1.40)
    ap.add_argument("--days",      type=int,   default=None)
    ap.add_argument("--legs",      type=int,   default=None, help="Only run N legs (3/4/5)")
    args = ap.parse_args()

    by_day = load_legs(args.min_edge, args.max_odds, args.min_odds, args.days, args.csv)
    all_dates = sorted(by_day)

    print(f"\nCSV:     {args.csv}")
    print(f"Filters: edge≥{args.min_edge*100:.0f}%  odds {args.min_odds:.2f}–{args.max_odds:.2f}  "
          f"days={'all' if not args.days else args.days}  markets=btts/ou25/ou35/ou15")
    print(f"Window:  {all_dates[0]} → {all_dates[-1]}  ({len(all_dates)} days with ≥1 qualifying leg)")

    # show how many days have enough legs for each N
    for n in LEG_COUNTS:
        qualifying = sum(1 for d in by_day.values() if len(d) >= n)
        print(f"  N={n}: {qualifying} days with ≥{n} qualifying legs")

    leg_counts = [args.legs] if args.legs else LEG_COUNTS

    all_stats = []

    for n in leg_counts:
        print(f"\n{'=' * 100}")
        print(f"  N = {n} LEGS  (stake normalised to €{DAILY_STAKE:.0f}/day total)")
        print(f"{'=' * 100}")

        section_stats = []
        for structure in STRUCTURES:
            # fours_up only makes sense for N≥4, trebles_only for N≥3
            if structure == "fours_up" and n < 4:
                continue
            if structure == "trebles_only" and n < 3:
                continue
            if structure == "top2_sizes" and n < 3:
                continue

            res = simulate(by_day, n, structure)
            s = stats(res, f"{structure} / N={n}")
            section_stats.append(s)
            all_stats.append(s)

        # print sorted by ROI
        section_stats.sort(key=lambda x: -(x.get("roi_pct") or -999))
        for s in section_stats:
            print_row(s)

    # ── summary: best ROI across all N×structure combos ──────────────────────
    print(f"\n{'=' * 100}")
    print("  SUMMARY — all structures × all N, sorted by ROI")
    print(f"{'=' * 100}")
    all_stats.sort(key=lambda x: -(x.get("roi_pct") or -999))
    for s in all_stats:
        if s["n"] > 0:
            print_row(s)

    # ── insight: leg win rate vs single-leg win rate ──────────────────────────
    print(f"\n{'=' * 100}")
    print("  SINGLE-LEG WIN RATES by market (base hit rate for combo modelling)")
    print(f"{'=' * 100}")
    all_legs_flat = [leg for legs in by_day.values() for leg in legs]
    by_mkt: dict[str, list] = defaultdict(list)
    for leg in all_legs_flat:
        by_mkt[f"{leg['market']}/{leg['sel']}"].append(leg)
    rows = []
    for label, legs in by_mkt.items():
        wins = sum(1 for l in legs if l["won"])
        avg_e = sum(l["edge"] for l in legs) / len(legs) * 100
        avg_o = sum(l["odds"] for l in legs) / len(legs)
        roi = sum((l["odds"] - 1 if l["won"] else -1) for l in legs) / len(legs) * 100
        rows.append((label, len(legs), wins / len(legs) * 100, avg_e, avg_o, roi))
    rows.sort(key=lambda x: -x[5])
    print(f"  {'market/sel':<28} {'n':>5}  {'win%':>6}  {'avg_edge':>8}  {'avg_odds':>9}  {'roi%':>7}")
    print(f"  {'-'*75}")
    for label, n, wp, ae, ao, roi in rows:
        print(f"  {label:<28} {n:>5d}  {wp:>5.1f}%  {ae:>7.1f}%  {ao:>8.2f}x  {roi:>+6.1f}%")


def main_ou15_split():
    """Extra cut: N=5, 8% edge, split by whether OU15/over is in the day's leg pool."""
    import argparse
    csv_path = Path("dev/active/backtest-3year.csv")
    by_day = load_legs(0.08, 2.50, 1.40, None, csv_path)

    with_ou15, without_ou15 = {}, {}
    for day, legs in by_day.items():
        pool = legs  # already sorted by edge desc
        if len(pool) < 5:
            continue
        top5 = pool[:5]
        has_ou15 = any(l["market"] == "over_under_15" and l["sel"] == "over" for l in top5)
        if has_ou15:
            with_ou15[day] = pool
        else:
            without_ou15[day] = pool

    print(f"\n{'='*100}")
    print(f"  OU15/OVER ISOLATION — N=5, edge≥8%, all years")
    print(f"  Days with OU15/over in top-5 legs:    {len(with_ou15)}")
    print(f"  Days without OU15/over in top-5 legs: {len(without_ou15)}")
    print(f"{'='*100}")

    for label, subset in [("WITH OU15/over", with_ou15), ("WITHOUT OU15/over", without_ou15)]:
        print(f"\n  --- {label} ({len(subset)} days) ---")
        section = []
        for structure in ["straight", "fours_up", "top2_sizes", "trebles_up", "no_singles", "doubles+trebles"]:
            res = simulate(subset, 5, structure)
            s = stats(res, structure)
            section.append(s)
        section.sort(key=lambda x: -(x.get("roi_pct") or -999))
        for s in section:
            print_row(s)

    # Also show per-market win rates for the two pools
    print(f"\n  --- LEG QUALITY: avg win rate of top-5 legs ---")
    for label, subset in [("WITH OU15", with_ou15), ("WITHOUT OU15", without_ou15)]:
        all_top5 = [l for legs in subset.values() for l in legs[:5]]
        wins = sum(l["won"] for l in all_top5)
        avg_odds = sum(l["odds"] for l in all_top5) / len(all_top5) if all_top5 else 0
        print(f"  {label}: {len(all_top5)} legs, win%={wins/len(all_top5)*100:.1f}%, avg_odds={avg_odds:.2f}x")


if __name__ == "__main__":
    import sys
    if "--ou15" in sys.argv:
        main_ou15_split()
    else:
        main()
