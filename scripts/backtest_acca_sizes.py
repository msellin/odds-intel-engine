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
from itertools import combinations
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


# ── System bet structures (user's "safety net" combos) ──────────────────────
#
# A system bet = N picks + a specific subset of combo sizes covered.
# Same EV per euro as straight combos (when book doesn't compound margin —
# Phase A confirmed Coolbet doesn't), but lower variance because partial hits
# still pay something. Total stake is N_BETS_IN_SYSTEM × unit_stake.

SYSTEM_STRUCTURES = {
    # name: (num_picks, list of combo sizes covered, includes_singles)
    "trixie":     (3, [2, 3],            False),  # 3 doubles + 1 treble = 4 bets
    "patent":     (3, [1, 2, 3],         True),   # 3 singles + Trixie = 7 bets
    "yankee":     (4, [2, 3, 4],         False),  # 6+4+1 = 11 bets
    "lucky_15":   (4, [1, 2, 3, 4],      True),   # 4 + 11 = 15 bets
    "canadian":   (5, [2, 3, 4, 5],      False),  # 10+10+5+1 = 26 bets
    "lucky_31":   (5, [1, 2, 3, 4, 5],   True),   # 5 + 26 = 31 bets
    "heinz":      (6, [2, 3, 4, 5, 6],   False),  # 15+20+15+6+1 = 57 bets
    "lucky_63":   (6, [1, 2, 3, 4, 5, 6], True),  # 6 + 57 = 63 bets
}


def simulate_system(singles: list[dict], structure: str, selection: str) -> dict:
    """For each day with ≥ N picks, build the system bet (all sub-combos of the
    structure's covered sizes), simulate every sub-bet's outcome, and aggregate.
    Returns same shape as analyse() plus 'avg_stake_per_day' for variance check."""
    n_picks, sizes_covered, _includes_singles = SYSTEM_STRUCTURES[structure]
    by_day: dict = defaultdict(list)
    for s in singles:
        by_day[s["pick_date"]].append(s)

    n_days_played = 0
    total_stake = 0.0
    total_pnl = 0.0
    daily_pnls: list[float] = []
    any_win_days = 0
    biggest_day = float("-inf")
    worst_day = float("inf")
    max_subbet_payout = 0.0

    for day, day_singles in sorted(by_day.items()):
        picks = pick_combo(day_singles, n_picks, selection)
        if picks is None:
            continue
        n_days_played += 1

        day_stake = 0.0
        day_pnl = 0.0
        day_any_win = False
        for size in sizes_covered:
            for combo in combinations(picks, size):
                day_stake += STAKE
                odds_product = math.prod(c["odds"] for c in combo)
                if all(c["result"] == "won" for c in combo):
                    payout_pnl = STAKE * (odds_product - 1)
                    day_pnl += payout_pnl
                    day_any_win = True
                    max_subbet_payout = max(max_subbet_payout, payout_pnl)
                else:
                    day_pnl -= STAKE

        total_stake += day_stake
        total_pnl += day_pnl
        daily_pnls.append(day_pnl)
        if day_any_win:
            any_win_days += 1
        biggest_day = max(biggest_day, day_pnl)
        worst_day = min(worst_day, day_pnl)

    if n_days_played == 0:
        return {"days": 0, "structure": structure}

    return {
        "structure": structure,
        "n_picks": n_picks,
        "bets_per_day": sum(math.comb(n_picks, s) for s in sizes_covered),
        "days": n_days_played,
        "total_stake": total_stake,
        "total_pnl": total_pnl,
        "roi_pct": total_pnl / total_stake * 100,
        "any_win_rate": any_win_days / n_days_played,
        "biggest_day_pnl": biggest_day,
        "worst_day_pnl": worst_day,
        "max_subbet_payout": max_subbet_payout,
        "avg_daily_stake": total_stake / n_days_played,
    }


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

    # ── System bets ──────────────────────────────────────────────────────
    # Apply same picks logic, but cover all sub-combos of the structure's
    # included sizes instead of just the top-level acca.
    print()
    print("=== SYSTEM BETS (same daily picks, different sub-combo coverage) ===")
    print(f"{'structure':<12} {'picks':>5} {'bets/d':>6} {'days':>5} {'stake/d':>9} {'roi':>8} "
          f"{'any_hit_rate':>13} {'big_day':>9} {'worst_day':>10} {'max_subbet':>11}")
    print("-" * 120)
    for name in ("trixie", "yankee", "lucky_15", "canadian", "lucky_31", "heinz", "lucky_63"):
        s = simulate_system(singles, name, args.selection)
        if s.get("days", 0) == 0:
            print(f"{name:<12} (no days with enough picks)")
            continue
        print(
            f"{name:<12} {s['n_picks']:>5d} {s['bets_per_day']:>6d} {s['days']:>5d} "
            f"€{s['avg_daily_stake']:>7.0f}  {s['roi_pct']:>+7.1f}% "
            f"{s['any_win_rate']*100:>11.1f}%  €{s['biggest_day_pnl']:>+6.0f}  €{s['worst_day_pnl']:>+7.0f}  "
            f"€{s['max_subbet_payout']:>+8.0f}"
        )
    print()
    print("Notes:")
    print("  • bets/d  = number of separate sub-combos placed each qualifying day")
    print("  • stake/d = total daily outlay assuming €1 per sub-bet")
    print("  • roi     = total_pnl / total_stake — comparable to straight acca ROI per €")
    print("  • any_hit_rate = % of days where AT LEAST ONE sub-combo won")
    print("  • Higher any_hit_rate vs straight = the variance-reduction value")

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
