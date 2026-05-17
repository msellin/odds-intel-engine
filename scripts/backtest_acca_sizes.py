"""
COMBO-RESEARCH-PHASE-D BACKTEST — what combo size maximises long-run growth
on our historical single-bet menu?

For each past day with at least N settled single bets, take the top-N
highest-edge singles (independence enforced: one leg per match), check
whether all legs won, and tally the resulting hit/miss/ROI/biggest-win.

Done across N = 2..6 so you can see the variance/return trade-off and pick
a target "hit frequency" — the user goal is roughly "one hit per week" which
corresponds to ~14% combo hit rate (one combo per day) or higher hit rate
with multiple combos per day.

Outputs a side-by-side comparison:
  N   bets  hit_rate  ROI%   biggest_hit  max_drawdown  longest_dry_streak

Usage:
  python scripts/backtest_acca_sizes.py
  python scripts/backtest_acca_sizes.py --days 90
  python scripts/backtest_acca_sizes.py --min-edge 0.05 --max-odds-per-leg 3.0
  python scripts/backtest_acca_sizes.py --selection balanced
"""

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query  # noqa: E402


STAKE = 1.0  # nominal per-combo stake for ROI math


def fetch_settled_singles(days: int, min_edge: float, max_odds_per_leg: float, exclude_bots: list[str] | None = None):
    """All settled paper bets in the last N days, with computed edge.
    Excludes combo bets themselves (combo_legs IS NULL filter would only
    matter post-migration 108 — for now all simulated_bets are singles).

    Edge = model_probability × odds_at_pick - 1 (the standard EV definition).
    """
    exclude_clause = ""
    params = [days, max_odds_per_leg]
    if exclude_bots:
        exclude_clause = " AND b.name <> ALL(%s)"
        params.append(exclude_bots)
    sql = f"""
        SELECT
            sb.id::text                                         AS bet_id,
            sb.match_id::text                                   AS match_id,
            DATE(sb.pick_time AT TIME ZONE 'UTC')               AS pick_date,
            sb.market,
            sb.selection,
            sb.odds_at_pick,
            COALESCE(sb.calibrated_prob, sb.model_probability)  AS prob,
            sb.result,
            b.name                                              AS bot
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.result IN ('won', 'lost')
          AND sb.pick_time >= NOW() - INTERVAL '%s days'
          AND b.name NOT LIKE 'inplay%%'
          AND sb.odds_at_pick <= %s
          {exclude_clause}
        ORDER BY pick_date, prob DESC
    """
    rows = execute_query(sql, params) or []
    # Compute edge per row, drop legs below min_edge
    out = []
    for r in rows:
        odds = float(r["odds_at_pick"])
        prob = float(r["prob"] or 0)
        edge = prob * odds - 1
        if edge < min_edge:
            continue
        r["edge"] = edge
        r["odds"] = odds
        r["prob"] = prob
        out.append(r)
    return out


def pick_combo(day_singles: list[dict], n_legs: int, selection: str) -> list[dict] | None:
    """Pick `n_legs` legs from the day's available singles. Independence enforced
    by match_id (no two legs from same match). Returns the picked list or None
    if fewer than n_legs independent qualifying singles exist."""
    if selection == "top_edge":
        sorted_singles = sorted(day_singles, key=lambda x: -x["edge"])
    elif selection == "balanced":
        # Mix: prefer short odds (high hit rate) but break ties on edge
        sorted_singles = sorted(day_singles, key=lambda x: (x["odds"], -x["edge"]))
    elif selection == "short_odds":
        # Strictest short-odds preference — favourite-heavy combos
        sorted_singles = [s for s in day_singles if s["odds"] <= 1.80]
        sorted_singles.sort(key=lambda x: (x["odds"], -x["edge"]))
    else:
        raise ValueError(f"Unknown selection mode: {selection}")

    picked = []
    seen_matches: set[str] = set()
    for s in sorted_singles:
        if s["match_id"] in seen_matches:
            continue
        picked.append(s)
        seen_matches.add(s["match_id"])
        if len(picked) >= n_legs:
            break
    return picked if len(picked) == n_legs else None


def simulate_combos(singles: list[dict], n_legs: int, selection: str):
    """For each day, build one combo of n_legs. Return per-combo results."""
    by_day: dict = defaultdict(list)
    for s in singles:
        by_day[s["pick_date"]].append(s)

    combos = []
    for day, day_singles in sorted(by_day.items()):
        combo = pick_combo(day_singles, n_legs, selection)
        if combo is None:
            continue
        combined_odds = math.prod(c["odds"] for c in combo)
        all_won = all(c["result"] == "won" for c in combo)
        pnl = STAKE * (combined_odds - 1) if all_won else -STAKE
        combos.append({
            "date": day,
            "n_legs": n_legs,
            "combined_odds": combined_odds,
            "won": all_won,
            "pnl": pnl,
            "legs": combo,
        })
    return combos


def analyse(combos: list[dict]) -> dict:
    """Summary stats: hit rate, ROI, biggest hit, max drawdown, longest dry streak."""
    n = len(combos)
    if n == 0:
        return {"bets": 0}
    wins = sum(1 for c in combos if c["won"])
    total_stake = n * STAKE
    total_pnl = sum(c["pnl"] for c in combos)
    biggest_hit = max((c["pnl"] for c in combos if c["won"]), default=0.0)

    # Drawdown: max cumulative loss from any peak
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    dry_streak = 0
    longest_dry = 0
    for c in combos:
        running += c["pnl"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        if c["won"]:
            dry_streak = 0
        else:
            dry_streak += 1
            longest_dry = max(longest_dry, dry_streak)

    avg_odds = sum(c["combined_odds"] for c in combos) / n
    days_per_hit = n / wins if wins else float("inf")

    return {
        "bets": n,
        "wins": wins,
        "hit_rate": wins / n,
        "roi_pct": total_pnl / total_stake * 100,
        "total_pnl": total_pnl,
        "biggest_hit": biggest_hit,
        "max_drawdown": max_dd,
        "longest_dry_streak": longest_dry,
        "avg_combined_odds": avg_odds,
        "days_per_hit": days_per_hit,
    }


def fmt_stats(s: dict) -> str:
    if s["bets"] == 0:
        return "no qualifying days"
    return (
        f"n={s['bets']:>3d}  "
        f"hits={s['wins']:>3d} ({s['hit_rate']*100:>5.1f}%)  "
        f"roi={s['roi_pct']:>+6.1f}%  "
        f"avg_odds={s['avg_combined_odds']:>5.2f}  "
        f"big_hit=€{s['biggest_hit']:>5.2f}  "
        f"max_dd=€{s['max_drawdown']:>4.1f}  "
        f"dry={s['longest_dry_streak']:>2d}d  "
        f"days/hit={s['days_per_hit']:>4.1f}"
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=60, help="History window (default 60d)")
    p.add_argument("--min-edge", type=float, default=0.05, help="Min per-leg edge (default 5%%)")
    p.add_argument("--max-odds-per-leg", type=float, default=3.0, help="Max odds per leg (default 3.0)")
    p.add_argument(
        "--selection",
        choices=["top_edge", "balanced", "short_odds"],
        default="top_edge",
        help="Leg picking strategy",
    )
    p.add_argument("--show-hits", action="store_true", help="Print every winning combo's legs")
    p.add_argument(
        "--exclude-bot", action="append", default=[],
        help="Bot name to exclude from candidate pool (repeatable). Use to test what the combo bot would look like without a now-retired bot."
    )
    args = p.parse_args()

    print(f"Backtest window:     last {args.days} days")
    print(f"Min per-leg edge:    {args.min_edge*100:.1f}%")
    print(f"Max per-leg odds:    {args.max_odds_per_leg}")
    print(f"Selection strategy:  {args.selection}")
    if args.exclude_bot:
        print(f"Excluded bots:       {args.exclude_bot}")
    print()

    singles = fetch_settled_singles(args.days, args.min_edge, args.max_odds_per_leg, args.exclude_bot or None)
    if not singles:
        print("No qualifying single bets in window.")
        return

    n_days = len({s["pick_date"] for s in singles})
    print(f"Settled qualifying singles: {len(singles)} across {n_days} days "
          f"({len(singles)/n_days:.1f} singles/day)\n")

    print(f"{'n_legs':<8} stats")
    print("-" * 110)
    results = {}
    for n_legs in range(2, 7):
        combos = simulate_combos(singles, n_legs, args.selection)
        stats = analyse(combos)
        results[n_legs] = (combos, stats)
        print(f"{n_legs:<8d} {fmt_stats(stats)}")

    print()
    print("Notes:")
    print("  • hit_rate × days_played = expected weekly hits at 1 combo/day")
    print("  • For 'one hit per week' target: aim for hit_rate ≥ 14% (1/7 days)")
    print("  • Higher n_legs = bigger biggest_hit, smaller hit_rate, larger drawdowns")
    print("  • max_dd = worst peak-to-trough cumulative € (per €1 unit stake)")
    print("  • longest_dry_streak = longest run of consecutive losing combos")

    # Recommendation: pick the n that best matches "once a week" target with positive ROI
    print()
    print("=== Recommendation ===")
    weekly_target = 0.14
    positives = [(n, s) for n, (_, s) in results.items() if s.get("bets", 0) >= 10 and s.get("roi_pct", -999) > 0]
    if not positives:
        print("  No positive-ROI combo size found in this window — combos look -EV with current filters.")
        print("  Try tightening --min-edge or lowering --max-odds-per-leg.")
    else:
        # Closest to weekly target, prefer higher ROI on ties
        best = min(positives, key=lambda ns: (abs(ns[1]["hit_rate"] - weekly_target), -ns[1]["roi_pct"]))
        n, s = best
        print(f"  Best fit for 'once a week' (hit_rate ≈ 14%, positive ROI):")
        print(f"    n_legs={n}  hit_rate={s['hit_rate']*100:.1f}%  ROI={s['roi_pct']:+.1f}%  "
              f"avg_odds={s['avg_combined_odds']:.2f}  biggest_hit=€{s['biggest_hit']:.2f}")

    if args.show_hits:
        print()
        print("=== Winning combos (per n_legs) ===")
        for n_legs, (combos, _) in results.items():
            wins = [c for c in combos if c["won"]]
            if not wins:
                continue
            print(f"\n-- n_legs={n_legs} ({len(wins)} hits) --")
            for w in wins[:15]:
                legs = " + ".join(f"{l['bot'][:8]}:{l['selection']}@{l['odds']:.2f}" for l in w["legs"])
                print(f"  {w['date']}  €{w['pnl']:>5.2f} on {w['combined_odds']:.2f}: {legs}")


if __name__ == "__main__":
    main()
