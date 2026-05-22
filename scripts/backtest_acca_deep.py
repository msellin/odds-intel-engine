"""
ACCA / SYSTEM DEEP ANALYSIS — uses backtest CSV for market/odds/combo analysis.

Answers:
  1. Which market+selection types have the best single-bet win rates / ROI?
  2. Which odds buckets are most combo-friendly?
  3. How do combos perform when filtered to specific markets?
  4. How many combos can we do per day, and with what volume?
  5. Multiple-combo-per-day simulation (market-segmented).

Usage:
  python3 scripts/backtest_acca_deep.py
  python3 scripts/backtest_acca_deep.py --csv dev/active/backtest-3year.csv
  python3 scripts/backtest_acca_deep.py --min-edge 0.08 --max-odds 2.5
  python3 scripts/backtest_acca_deep.py --days 90   (recent window only)
"""

import argparse
import csv
import math
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path

DEFAULT_CSV = Path("dev/active/backtest-pre-match-results.csv")
STAKE = 1.0


# ── helpers ──────────────────────────────────────────────────────────────────

def load_singles(min_edge: float, max_odds: float, min_odds: float, days: int | None,
                 csv_path: Path = DEFAULT_CSV) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat() if days else None
    rows = []
    seen_per_day: dict[tuple, dict] = {}  # (pick_date, match_id) → best-edge row
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            if r["won"] not in ("True", "False"):
                continue
            pick_date = r["date"][:10]
            if cutoff and pick_date < cutoff:
                continue
            odds = float(r["odds"])
            edge = float(r["edge"])
            if edge < min_edge or odds > max_odds or odds < min_odds:
                continue
            key = (pick_date, r["match_id"])
            if key not in seen_per_day or edge > seen_per_day[key]["edge"]:
                seen_per_day[key] = {
                    "pick_date": pick_date,
                    "match_id": r["match_id"],
                    "market": r["market"],
                    "selection": r["selection"],
                    "odds": odds,
                    "edge": edge,
                    "won": r["won"] == "True",
                    "bot": r["bot"],
                }
    return list(seen_per_day.values())


def odds_bucket(odds: float) -> str:
    if odds < 1.60:  return "<1.60"
    if odds < 1.90:  return "1.60-1.90"
    if odds < 2.20:  return "1.90-2.20"
    if odds < 2.60:  return "2.20-2.60"
    if odds < 3.00:  return "2.60-3.00"
    return "3.00+"


def market_label(market: str, selection: str) -> str:
    """Collapse to a readable combo-friendly label."""
    if market == "1x2":
        return f"1x2/{selection}"
    if market == "over_under_25":
        return f"OU2.5/{selection}"
    if market == "over_under_15":
        return f"OU1.5/{selection}"
    if market == "over_under_35":
        return f"OU3.5/{selection}"
    if market == "btts":
        return f"BTTS/{selection}"
    if market == "double_chance":
        return f"DC/{selection}"
    if market == "draw_no_bet":
        return f"DNB/{selection}"
    return f"{market}/{selection}"


def market_group(market: str) -> str:
    if market in ("over_under_25", "over_under_15", "over_under_35"):
        return "over_under"
    return market


def simulate_combos(by_day: dict, n_legs: int, market_filter=None) -> list[dict]:
    """1 combo per day: pick top n_legs by edge, one per match, optional market filter."""
    combos = []
    for day, singles in sorted(by_day.items()):
        pool = [s for s in singles if market_filter is None or market_group(s["market"]) == market_filter]
        pool.sort(key=lambda x: -x["edge"])
        picked, seen = [], set()
        for s in pool:
            if s["match_id"] not in seen:
                picked.append(s)
                seen.add(s["match_id"])
            if len(picked) == n_legs:
                break
        if len(picked) < n_legs:
            continue
        combined_odds = math.prod(s["odds"] for s in picked)
        all_won = all(s["won"] for s in picked)
        pnl = STAKE * (combined_odds - 1) if all_won else -STAKE
        combos.append({"date": day, "n": n_legs, "combined_odds": combined_odds,
                       "won": all_won, "pnl": pnl, "legs": picked})
    return combos


def simulate_multi_combo(by_day: dict, n_legs: int, combos_per_day: int, strategy: str = "round_robin") -> list[dict]:
    """Multiple non-overlapping combos per day.
    round_robin: sort by edge, assign legs 0→combo0, 1→combo1, ... in rotation.
    market_seg: first combo = best OU legs, second = best 1x2 legs, etc.
    """
    all_combos = []
    for day, singles in sorted(by_day.items()):
        pool = sorted(singles, key=lambda x: -x["edge"])
        # dedupe by match
        deduped, seen = [], set()
        for s in pool:
            if s["match_id"] not in seen:
                deduped.append(s)
                seen.add(s["match_id"])

        if strategy == "round_robin":
            # assign legs in round-robin to combos
            buckets: list[list] = [[] for _ in range(combos_per_day)]
            for i, s in enumerate(deduped):
                buckets[i % combos_per_day].append(s)
            for b in buckets:
                legs = b[:n_legs]
                if len(legs) < n_legs:
                    continue
                combined_odds = math.prod(s["odds"] for s in legs)
                all_won = all(s["won"] for s in legs)
                pnl = STAKE * (combined_odds - 1) if all_won else -STAKE
                all_combos.append({"date": day, "n": n_legs, "combined_odds": combined_odds,
                                   "won": all_won, "pnl": pnl, "legs": legs})

        elif strategy == "market_seg":
            # one combo per market group, n_legs from each
            by_mkt: dict[str, list] = defaultdict(list)
            for s in deduped:
                by_mkt[market_group(s["market"])].append(s)
            for mkt, pool_m in sorted(by_mkt.items()):
                legs = pool_m[:n_legs]
                if len(legs) < n_legs:
                    continue
                combined_odds = math.prod(s["odds"] for s in legs)
                all_won = all(s["won"] for s in legs)
                pnl = STAKE * (combined_odds - 1) if all_won else -STAKE
                all_combos.append({"date": day, "market_seg": mkt, "n": n_legs,
                                   "combined_odds": combined_odds, "won": all_won,
                                   "pnl": pnl, "legs": legs})
    return all_combos


def combo_stats(combos: list[dict], label: str = "") -> dict:
    n = len(combos)
    if n == 0:
        return {"label": label, "n": 0}
    wins = sum(1 for c in combos if c["won"])
    total_pnl = sum(c["pnl"] for c in combos)
    biggest = max((c["pnl"] for c in combos if c["won"]), default=0.0)
    avg_odds = sum(c["combined_odds"] for c in combos) / n

    running = peak = max_dd = 0.0
    dry = longest_dry = 0
    for c in combos:
        running += c["pnl"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        if c["won"]:
            dry = 0
        else:
            dry += 1
            longest_dry = max(longest_dry, dry)

    return {
        "label": label, "n": n, "wins": wins,
        "hit_pct": wins / n * 100,
        "roi_pct": total_pnl / (n * STAKE) * 100,
        "total_pnl": total_pnl,
        "avg_odds": avg_odds,
        "biggest_hit": biggest,
        "max_dd": max_dd,
        "longest_dry": longest_dry,
        "days_per_hit": n / wins if wins else float("inf"),
    }


def print_combo_row(s: dict):
    if s["n"] == 0:
        print(f"  {s['label']:<35}  (no qualifying days)")
        return
    print(
        f"  {s['label']:<35}  "
        f"n={s['n']:>4d}  hits={s['wins']:>3d} ({s['hit_pct']:>5.1f}%)  "
        f"roi={s['roi_pct']:>+7.1f}%  avg_odds={s['avg_odds']:>6.1f}x  "
        f"big=€{s['biggest_hit']:>6.1f}  dry={s['longest_dry']:>2d}  "
        f"d/hit={s['days_per_hit']:>5.1f}"
    )


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                    help="Backtest CSV to analyse (default: backtest-pre-match-results.csv)")
    ap.add_argument("--min-edge", type=float, default=0.05)
    ap.add_argument("--max-odds", type=float, default=3.0)
    ap.add_argument("--min-odds", type=float, default=1.40)
    ap.add_argument("--days", type=int, default=None, help="Limit to last N days (default: all)")
    args = ap.parse_args()

    print(f"CSV:      {args.csv}")
    singles = load_singles(args.min_edge, args.max_odds, args.min_odds, args.days, args.csv)
    if not singles:
        print("No qualifying singles.")
        return

    dates = sorted(set(s["pick_date"] for s in singles))
    n_days = len(dates)
    print(f"Filters:  min_edge={args.min_edge*100:.0f}%  odds={args.min_odds:.2f}–{args.max_odds:.2f}  "
          f"days={'all' if not args.days else args.days}")
    print(f"Singles:  {len(singles)} across {n_days} days  ({len(singles)/n_days:.1f}/day)")
    print(f"Window:   {dates[0]}  →  {dates[-1]}")

    by_day: dict[str, list] = defaultdict(list)
    for s in singles:
        by_day[s["pick_date"]].append(s)

    # ── SECTION 1: Singles quality by market+selection ────────────────────────
    print()
    print("=" * 90)
    print("§1  SINGLE-BET STATS BY MARKET / SELECTION  (sorted by ROI)")
    print("=" * 90)
    print(f"  {'market/sel':<22} {'n':>5} {'win%':>6} {'avg_edge':>9} {'avg_odds':>9} {'roi%':>7}")
    print("-" * 70)
    by_mkt_sel: dict[str, list] = defaultdict(list)
    for s in singles:
        by_mkt_sel[market_label(s["market"], s["selection"])].append(s)
    rows_ms = []
    for label, bets in by_mkt_sel.items():
        wins = sum(1 for b in bets if b["won"])
        roi = sum((b["odds"] - 1) if b["won"] else -1 for b in bets) / len(bets) * 100
        rows_ms.append((label, len(bets), wins/len(bets)*100,
                        sum(b["edge"] for b in bets)/len(bets)*100,
                        sum(b["odds"] for b in bets)/len(bets), roi))
    for row in sorted(rows_ms, key=lambda x: -x[5]):
        label, n, winpct, avg_edge, avg_odds, roi = row
        print(f"  {label:<22} {n:>5d}  {winpct:>5.1f}%  {avg_edge:>8.1f}%  {avg_odds:>8.2f}x  {roi:>+6.1f}%")

    # ── SECTION 2: Win rate by odds bucket ────────────────────────────────────
    print()
    print("=" * 90)
    print("§2  WIN RATE BY ODDS BUCKET  (all markets combined)")
    print("=" * 90)
    print(f"  {'bucket':<14} {'n':>5} {'win%':>6} {'roi%':>7}   combo_hit_rate_if_all_legs_same_bucket")
    print("-" * 80)
    by_bucket: dict[str, list] = defaultdict(list)
    for s in singles:
        by_bucket[odds_bucket(s["odds"])].append(s)
    for bkt in ["<1.60", "1.60-1.90", "1.90-2.20", "2.20-2.60", "2.60-3.00", "3.00+"]:
        bets = by_bucket.get(bkt, [])
        if not bets:
            continue
        wins = sum(1 for b in bets if b["won"])
        wp = wins / len(bets)
        roi = sum((b["odds"] - 1) if b["won"] else -1 for b in bets) / len(bets) * 100
        combo_rates = "  ".join(f"{n}L={wp**n*100:.1f}%" for n in [2, 3, 4, 5, 6])
        print(f"  {bkt:<14} {len(bets):>5d}  {wp*100:>5.1f}%  {roi:>+6.1f}%   {combo_rates}")

    # ── SECTION 3: Per-market single-combo sweep (n=2..6) ─────────────────────
    print()
    print("=" * 90)
    print("§3  STRAIGHT ACCA BY MARKET FILTER  (1 combo/day, top-edge legs)")
    print("=" * 90)
    print(f"  {'strategy':<35} {'n':>4}  hits  hit%    roi%  avg_odds  big_hit  dry  d/hit")
    print("-" * 90)
    market_filters = [None, "1x2", "over_under", "btts", "double_chance", "draw_no_bet"]
    filter_labels = {None: "ALL markets", "1x2": "1x2 only", "over_under": "OU only",
                     "btts": "BTTS only", "double_chance": "DC only", "draw_no_bet": "DNB only"}
    for mf in market_filters:
        for n_legs in [2, 3, 4, 5, 6]:
            combos = simulate_combos(by_day, n_legs, market_filter=mf)
            label = f"{filter_labels[mf]}, {n_legs}-leg"
            print_combo_row(combo_stats(combos, label))
        print()

    # ── SECTION 4: Short-odds combos (legs capped at 2.0x) ───────────────────
    print()
    print("=" * 90)
    print("§4  SHORT-ODDS ACCA  (legs capped at 2.0x odds, 1 combo/day)")
    print("=" * 90)
    singles_short = load_singles(args.min_edge, 2.0, args.min_odds, args.days, args.csv)
    by_day_short: dict[str, list] = defaultdict(list)
    for s in singles_short:
        by_day_short[s["pick_date"]].append(s)
    print(f"  Short-odds qualifying singles: {len(singles_short)} across "
          f"{len(set(s['pick_date'] for s in singles_short))} days")
    print()
    print(f"  {'strategy':<35} {'n':>4}  hits  hit%    roi%  avg_odds  big_hit  dry  d/hit")
    print("-" * 90)
    for mf in [None, "over_under", "1x2"]:
        for n_legs in [2, 3, 4, 5]:
            combos = simulate_combos(by_day_short, n_legs, market_filter=mf)
            label = f"{filter_labels[mf]}, {n_legs}-leg ≤2.0x"
            print_combo_row(combo_stats(combos, label))
        print()

    # ── SECTION 5: Multiple combos per day ───────────────────────────────────
    print()
    print("=" * 90)
    print("§5  MULTIPLE COMBOS PER DAY")
    print("=" * 90)
    print()

    # 5a: round-robin split (2 or 3 combos of N legs each)
    print("  5a  Round-robin split (legs assigned to combos in rotation):")
    print(f"  {'strategy':<42} {'n_combos':>8} {'hits':>5} {'hit%':>6}  {'roi%':>7}  {'avg_odds':>9}  {'d/hit':>6}")
    print("-" * 90)
    for n_legs in [3, 4, 5]:
        for cpd in [2, 3, 4]:
            combos = simulate_multi_combo(by_day, n_legs, cpd, strategy="round_robin")
            if not combos:
                continue
            wins = sum(1 for c in combos if c["won"])
            roi = sum(c["pnl"] for c in combos) / len(combos) * 100
            avg_odds = sum(c["combined_odds"] for c in combos) / len(combos)
            dph = len(combos) / wins if wins else float("inf")
            # unique days
            unique_days = len(set(c["date"] for c in combos))
            combos_per_day = len(combos) / unique_days
            label = f"{n_legs}-leg × {cpd}/day"
            print(f"  {label:<42} {len(combos):>8d}  {wins:>5d}  {wins/len(combos)*100:>5.1f}%  "
                  f"{roi:>+7.1f}%  {avg_odds:>9.1f}x  {dph:>6.1f}")
    print()

    # 5b: market-segmented combos
    print("  5b  Market-segmented combos (one combo per market type per day):")
    print(f"  {'market + n_legs':<35} {'total_combos':>12} {'hits':>5} {'hit%':>6}  {'roi%':>7}  {'avg_odds':>9}  {'d/hit':>6}")
    print("-" * 90)
    for n_legs in [3, 4]:
        combos = simulate_multi_combo(by_day, n_legs, combos_per_day=99, strategy="market_seg")
        by_mkt_combo: dict[str, list] = defaultdict(list)
        for c in combos:
            by_mkt_combo[c.get("market_seg", "?")].append(c)
        for mkt, mkt_combos in sorted(by_mkt_combo.items()):
            wins = sum(1 for c in mkt_combos if c["won"])
            roi = sum(c["pnl"] for c in mkt_combos) / len(mkt_combos) * 100
            avg_odds = sum(c["combined_odds"] for c in mkt_combos) / len(mkt_combos)
            dph = len(mkt_combos) / wins if wins else float("inf")
            label = f"{mkt}, {n_legs}-leg"
            print(f"  {label:<35} {len(mkt_combos):>12d}  {wins:>5d}  {wins/len(mkt_combos)*100:>5.1f}%  "
                  f"{roi:>+7.1f}%  {avg_odds:>9.1f}x  {dph:>6.1f}")
        print()

    # ── SECTION 6: Volume on busy days ────────────────────────────────────────
    print()
    print("=" * 90)
    print("§6  DAILY VOLUME — qualifying singles per day (sorted by volume)")
    print("=" * 90)
    daily_counts = sorted(
        ((day, len(bets)) for day, bets in by_day.items()),
        key=lambda x: -x[1]
    )
    print(f"  Top 20 busiest days (edge≥{args.min_edge*100:.0f}%, odds≤{args.max_odds:.2f}, deduped by match):")
    print(f"  {'date':<12} {'singles':>8}  {'OU':>4}  {'1x2':>4}  {'DC':>4}  {'DNB':>4}  {'BTTS':>4}")
    print("-" * 60)
    for day, cnt in daily_counts[:20]:
        bets = by_day[day]
        ou_n = sum(1 for b in bets if market_group(b["market"]) == "over_under")
        x12_n = sum(1 for b in bets if b["market"] == "1x2")
        dc_n = sum(1 for b in bets if b["market"] == "double_chance")
        dnb_n = sum(1 for b in bets if b["market"] == "draw_no_bet")
        btts_n = sum(1 for b in bets if b["market"] == "btts")
        print(f"  {day:<12} {cnt:>8d}  {ou_n:>4d}  {x12_n:>4d}  {dc_n:>4d}  {dnb_n:>4d}  {btts_n:>4d}")

    # percentiles
    counts = [c for _, c in daily_counts]
    p50 = counts[len(counts)//2]
    p75 = counts[len(counts)//4]
    p90 = counts[len(counts)//10]
    print()
    print(f"  Median day: {p50} singles   P75: {p75}   P90: {p90}")
    print()
    print("  Combos possible on a busy day (e.g. P75 = {p75} singles):".format(p75=p75))
    for n in [3, 4, 5, 6]:
        print(f"    {n}-leg combos (non-overlapping, 1 per match): ~{p75 // n}")

    # ── SECTION 7: Recommended strategy summary ───────────────────────────────
    print()
    print("=" * 90)
    print("§7  SUMMARY — best combo strategies found")
    print("=" * 90)

    candidates = []
    for mf in [None, "over_under", "1x2", "double_chance", "draw_no_bet"]:
        for n_legs in [2, 3, 4, 5]:
            combos = simulate_combos(by_day, n_legs, market_filter=mf)
            s = combo_stats(combos, f"{filter_labels.get(mf, mf)}, {n_legs}-leg")
            if s["n"] >= 20 and s["roi_pct"] > 0:
                candidates.append(s)

    if not candidates:
        print("  No positive-ROI strategies found with current filters.")
        print("  Try loosening --min-edge or --max-odds.")
    else:
        candidates.sort(key=lambda x: -x["roi_pct"])
        print(f"  {'strategy':<35} {'n':>4}  hits  hit%    roi%  avg_odds  d/hit")
        print("-" * 80)
        for s in candidates[:10]:
            print_combo_row(s)

    print()


if __name__ == "__main__":
    main()
