"""
PER-BOT-EDGE-THRESHOLD-OPTIMIZE — sweep per-bot edge thresholds against the
expanded backtest pool, find each bot's ROI-maximizing threshold.

Reads `dev/active/backtest-pre-match-results.csv` (output of
backtest_pre_match_bots.py — currently 19K bot-bet candidates across 9K
matches with predictions). For each bot, sweeps `edge ≥ X%` across
[1%, 2%, …, 15%] and computes:
  - bets that survive (count)
  - hits
  - ROI %
  - P&L (per €10 flat stake)

Reports the threshold that maximizes ROI (with min-sample guard).

This is ANALYSIS ONLY — does NOT modify BOTS_CONFIG. The output is a
recommendation; humans decide what to actually deploy.

Usage:
  python scripts/per_bot_edge_threshold_sweep.py
  python scripts/per_bot_edge_threshold_sweep.py --min-bets 100
  python scripts/per_bot_edge_threshold_sweep.py --bot bot_ou25_global
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

CSV_PATH = Path(__file__).parent.parent / "dev" / "active" / "backtest-pre-match-results.csv"


def load_backtest_rows():
    if not CSV_PATH.exists():
        print(f"ERROR: {CSV_PATH} not found. Run backtest_pre_match_bots.py first.")
        sys.exit(1)
    rows = []
    with CSV_PATH.open() as f:
        for r in csv.DictReader(f):
            won_str = r.get("won", "").lower().strip()
            if won_str not in ("true", "false"):
                continue
            rows.append({
                "bot": r["bot"],
                "edge": float(r["edge"]),
                "odds": float(r["odds"]),
                "won": won_str == "true",
                "stake": float(r["stake"]),
                "pnl": float(r["pnl"]),
            })
    return rows


def sweep_bot(bot_rows: list[dict], thresholds: list[float], min_bets: int) -> list[dict]:
    """For one bot, compute stats at each edge threshold."""
    out = []
    for t in thresholds:
        kept = [r for r in bot_rows if r["edge"] >= t]
        n = len(kept)
        if n == 0:
            continue
        wins = sum(1 for r in kept if r["won"])
        stake = sum(r["stake"] for r in kept)
        pnl = sum(r["pnl"] for r in kept)
        roi = (pnl / stake * 100) if stake else 0.0
        out.append({
            "threshold": t,
            "bets": n,
            "wins": wins,
            "hit_rate": wins / n,
            "stake": stake,
            "pnl": pnl,
            "roi_pct": roi,
            "trustworthy": n >= min_bets,
        })
    return out


def best_threshold(sweep: list[dict], min_bets: int) -> dict | None:
    """Pick the threshold with highest ROI that meets min-bets threshold."""
    eligible = [s for s in sweep if s["bets"] >= min_bets]
    if not eligible:
        return None
    return max(eligible, key=lambda s: s["roi_pct"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-bets", type=int, default=50,
                   help="Min bets at a threshold to consider it 'trustworthy' for recommendation")
    p.add_argument("--bot", default=None, help="Filter to a single bot")
    args = p.parse_args()

    rows = load_backtest_rows()
    print(f"Loaded {len(rows):,} backtest rows from {CSV_PATH}")
    print(f"Trustworthy threshold: ≥ {args.min_bets} bets at a given edge level\n")

    by_bot = defaultdict(list)
    for r in rows:
        by_bot[r["bot"]].append(r)

    bots = [args.bot] if args.bot else sorted(by_bot.keys(), key=lambda b: -len(by_bot[b]))
    thresholds = [round(0.01 * i, 2) for i in range(1, 16)]  # 1% to 15%

    # Summary table at the end
    summary_rows = []

    for bot in bots:
        bot_rows = by_bot.get(bot, [])
        if not bot_rows:
            continue
        # Current "live" baseline = no threshold (all bets the bot placed in backtest)
        all_wins = sum(1 for r in bot_rows if r["won"])
        all_stake = sum(r["stake"] for r in bot_rows)
        all_pnl = sum(r["pnl"] for r in bot_rows)
        all_roi = all_pnl / all_stake * 100 if all_stake else 0

        sweep = sweep_bot(bot_rows, thresholds, args.min_bets)
        best = best_threshold(sweep, args.min_bets)

        print(f"━━━ {bot} ━━━")
        print(f"  Baseline (all bets):       n={len(bot_rows):>5d}  wins={all_wins:>4d}  ROI={all_roi:+6.1f}%  pnl=€{all_pnl:+7.0f}")
        if best:
            improvement = best["roi_pct"] - all_roi
            arrow = "↑" if improvement > 0 else "↓"
            print(f"  Best threshold ({best['threshold']*100:>3.0f}% edge): n={best['bets']:>5d}  wins={best['wins']:>4d}  ROI={best['roi_pct']:+6.1f}%  pnl=€{best['pnl']:+7.0f}  {arrow} {improvement:+.1f}pp")
            summary_rows.append({
                "bot": bot,
                "baseline_n": len(bot_rows),
                "baseline_roi": all_roi,
                "best_threshold": best["threshold"],
                "best_n": best["bets"],
                "best_roi": best["roi_pct"],
                "improvement": improvement,
            })
        else:
            print(f"  No threshold reaches min-bets ({args.min_bets})")
        # Show top 5 thresholds
        sorted_sweep = sorted(sweep, key=lambda s: -s["roi_pct"])[:5]
        print("  Top 5 thresholds by ROI:")
        for s in sorted_sweep:
            tw = "✓" if s["trustworthy"] else " "
            print(f"    {tw} ≥{s['threshold']*100:>4.0f}%  n={s['bets']:>4d}  hits={s['wins']:>3d} ({s['hit_rate']*100:>4.1f}%)  ROI={s['roi_pct']:+6.1f}%  pnl=€{s['pnl']:+6.0f}")
        print()

    if summary_rows and not args.bot:
        print("━" * 90)
        print("RECOMMENDATION SUMMARY (sorted by improvement)")
        print("━" * 90)
        print(f"{'bot':<25} {'baseline_n':>10} {'baseline_roi':>12} → {'best_thr':>8}  {'best_n':>7} {'best_roi':>10} {'gain':>8}")
        for s in sorted(summary_rows, key=lambda x: -x["improvement"]):
            arrow = "↑" if s["improvement"] > 0 else "↓"
            print(f"{s['bot']:<25} {s['baseline_n']:>10d} {s['baseline_roi']:>+11.1f}% → "
                  f"{s['best_threshold']*100:>+6.0f}%  {s['best_n']:>7d} {s['best_roi']:>+9.1f}% {arrow}{s['improvement']:>+6.1f}pp")
        print()
        print("Note: ROI numbers are RAW backtest (no Pinnacle veto / sharp_consensus / calibration).")
        print("      Live ROI typically much better due to filter stack. Use thresholds as directional, not absolute.")


if __name__ == "__main__":
    main()
