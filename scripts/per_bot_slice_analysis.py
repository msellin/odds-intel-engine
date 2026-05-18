"""
PER-BOT-SLICE-TIGHTEN — identify loss-bucket segments per bot in the corrected
backtest CSV and recommend BOTS_CONFIG changes (selection_filter, odds_range,
tier_filter).

Mirrors how AGGRESSIVE-V2 was built: v1's 441 bets broken into loss buckets
(draws -€154, odds≥3.30 -€95, OU under 2.5 -€46) → v2 drops those slices.

Reads `dev/active/backtest-pre-match-results.csv` (corrected, post-1X2-fix).

Slices:
  - By selection (home / draw / away / over / under / yes / no)
  - By odds bucket: <1.50, 1.50-2.00, 2.00-2.50, 2.50-3.00, 3.00-3.50, 3.50+
  - By tier: 1, 2, 3+ combined

For each slice ≥ MIN_SLICE_BETS bets AND negative ROI: flag as a loss bucket
and suggest the specific filter.

Usage:
  python scripts/per_bot_slice_analysis.py
  python scripts/per_bot_slice_analysis.py --bot bot_ou25_global
  python scripts/per_bot_slice_analysis.py --min-bets 30
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

CSV_PATH = Path(__file__).parent.parent / "dev" / "active" / "backtest-pre-match-results.csv"

ODDS_BUCKETS = [
    (0.0, 1.50, "<1.50"),
    (1.50, 2.00, "1.50-2.00"),
    (2.00, 2.50, "2.00-2.50"),
    (2.50, 3.00, "2.50-3.00"),
    (3.00, 3.50, "3.00-3.50"),
    (3.50, 99.0, "3.50+"),
]


def odds_bucket(odds: float) -> str:
    for lo, hi, label in ODDS_BUCKETS:
        if lo <= odds < hi:
            return label
    return "3.50+"


def tier_group(tier: int) -> str:
    if tier <= 1:
        return "T1"
    if tier == 2:
        return "T2"
    return "T3+"


def load_rows() -> list[dict]:
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
                "market": r["market"],
                "selection": r["selection"],
                "odds": float(r["odds"]),
                "edge": float(r["edge"]),
                "stake": float(r["stake"]),
                "pnl": float(r["pnl"]),
                "won": won_str == "true",
                "tier": int(r["tier"]) if r.get("tier") else 0,
                "odds_bucket": odds_bucket(float(r["odds"])),
                "tier_group": tier_group(int(r["tier"]) if r.get("tier") else 0),
            })
    return rows


def slice_stats(rows: list[dict]) -> dict:
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    stake = sum(r["stake"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    roi = pnl / stake * 100 if stake else 0.0
    return {"n": n, "wins": wins, "stake": stake, "pnl": pnl, "roi": roi}


def analyze_bot(bot: str, bot_rows: list[dict], min_bets: int) -> list[dict]:
    """Return loss-bucket slices for one bot, sorted by P&L (worst first)."""
    loss_buckets: list[dict] = []

    # 1. By selection
    by_sel = defaultdict(list)
    for r in bot_rows:
        by_sel[r["selection"]].append(r)
    for sel, srows in by_sel.items():
        s = slice_stats(srows)
        if s["n"] >= min_bets and s["roi"] < -5.0:
            loss_buckets.append({
                "slice_type": "selection",
                "label": sel,
                "filter_suggestion": f'drop selection "{sel}" from selection_filter',
                **s,
            })

    # 2. By odds bucket
    by_odds = defaultdict(list)
    for r in bot_rows:
        by_odds[r["odds_bucket"]].append(r)
    for bucket, brows in by_odds.items():
        s = slice_stats(brows)
        if s["n"] >= min_bets and s["roi"] < -5.0:
            lo = [b[0] for b in ODDS_BUCKETS if b[2] == bucket]
            hi = [b[1] for b in ODDS_BUCKETS if b[2] == bucket]
            lo_v = lo[0] if lo else 0
            hi_v = hi[0] if hi else 99
            loss_buckets.append({
                "slice_type": "odds_bucket",
                "label": f"odds {bucket}",
                "filter_suggestion": f"tighten odds_range to exclude {bucket} (lo={lo_v:.2f}, hi={hi_v:.2f})",
                **s,
            })

    # 3. By tier group
    by_tier = defaultdict(list)
    for r in bot_rows:
        by_tier[r["tier_group"]].append(r)
    for tg, trows in by_tier.items():
        s = slice_stats(trows)
        if s["n"] >= min_bets and s["roi"] < -5.0:
            loss_buckets.append({
                "slice_type": "tier",
                "label": f"tier {tg}",
                "filter_suggestion": f"add tier_filter to exclude {tg}",
                **s,
            })

    # 4. Interaction: selection × odds bucket (only for ≥ min_bets)
    by_sel_odds = defaultdict(list)
    for r in bot_rows:
        by_sel_odds[(r["selection"], r["odds_bucket"])].append(r)
    for (sel, bucket), xrows in by_sel_odds.items():
        s = slice_stats(xrows)
        if s["n"] >= min_bets and s["roi"] < -10.0:
            loss_buckets.append({
                "slice_type": "sel×odds",
                "label": f"{sel} @ {bucket}",
                "filter_suggestion": f'drop "{sel}" OR cap odds at {bucket} boundary',
                **s,
            })

    # 5. Interaction: selection × tier
    by_sel_tier = defaultdict(list)
    for r in bot_rows:
        by_sel_tier[(r["selection"], r["tier_group"])].append(r)
    for (sel, tg), xrows in by_sel_tier.items():
        s = slice_stats(xrows)
        if s["n"] >= min_bets and s["roi"] < -10.0:
            loss_buckets.append({
                "slice_type": "sel×tier",
                "label": f"{sel} in {tg}",
                "filter_suggestion": f'drop "{sel}" in {tg} or add tier_filter',
                **s,
            })

    return sorted(loss_buckets, key=lambda x: x["pnl"])


def print_bot_report(bot: str, bot_rows: list[dict], min_bets: int) -> list[dict] | None:
    s_all = slice_stats(bot_rows)
    print(f"\n{'━'*80}")
    print(f"BOT: {bot}")
    print(f"  Overall: n={s_all['n']:>5d}  wins={s_all['wins']:>4d}  "
          f"ROI={s_all['roi']:+6.1f}%  P&L=€{s_all['pnl']:+7.0f}")

    buckets = analyze_bot(bot, bot_rows, min_bets)
    if not buckets:
        print("  No significant loss buckets found (all slices either profitable or too small).")
        return None

    print(f"\n  LOSS BUCKETS (≥{min_bets} bets, ROI < -5%):")
    print(f"  {'Slice':<30} {'n':>5} {'wins':>5} {'ROI':>8} {'P&L':>9}  Suggestion")
    print(f"  {'-'*30} {'-'*5} {'-'*5} {'-'*8} {'-'*9}  {'-'*30}")
    for b in buckets:
        print(f"  {b['label']:<30} {b['n']:>5d} {b['wins']:>5d} {b['roi']:>+7.1f}% €{b['pnl']:>+8.0f}  → {b['filter_suggestion']}")

    # Compute what "v2" bot would look like if we dropped ALL loss buckets
    # (simple exclusion of the worst single selection or tier slice)
    worst_sel = [b for b in buckets if b["slice_type"] == "selection"]
    worst_tier = [b for b in buckets if b["slice_type"] == "tier"]
    worst_odds = [b for b in buckets if b["slice_type"] == "odds_bucket"]

    sug_parts = []
    filter_out_sels = {b["label"] for b in worst_sel}
    filter_out_tiers = {b["label"].replace("tier ", "") for b in worst_tier}

    kept = bot_rows
    if filter_out_sels:
        kept = [r for r in kept if r["selection"] not in filter_out_sels]
        sug_parts.append(f"drop selections: {sorted(filter_out_sels)}")
    if filter_out_tiers:
        kept = [r for r in kept if r["tier_group"] not in filter_out_tiers]
        sug_parts.append(f"exclude tiers: {sorted(filter_out_tiers)}")

    # Odds bucket pruning: find the best odds_range by cutting losing extremes
    # Only suggest if the loss bucket is at the boundary (< 1.50 or 3.50+)
    boundary_cuts = []
    for b in worst_odds:
        if b["label"] in ("odds <1.50", "odds 3.50+"):
            boundary_cuts.append(b["label"])
    if boundary_cuts:
        sug_parts.append(f"tighten odds_range to exclude: {boundary_cuts}")
        # Apply rough cut (simple version: exclude those boundary rows)
        if "odds <1.50" in boundary_cuts:
            kept = [r for r in kept if r["odds"] >= 1.50]
        if "odds 3.50+" in boundary_cuts:
            kept = [r for r in kept if r["odds"] < 3.50]

    if kept and len(kept) < len(bot_rows):
        s_kept = slice_stats(kept)
        improvement = s_kept["roi"] - s_all["roi"]
        print(f"\n  PROJECTED v2 ({len(kept)}/{len(bot_rows)} bets kept, applying all suggestions):")
        print(f"  ROI={s_kept['roi']:+6.1f}%  P&L=€{s_kept['pnl']:+7.0f}  ({improvement:+.1f}pp improvement)")
        if sug_parts:
            for sp in sug_parts:
                print(f"    → {sp}")

    return buckets


def all_slices_for_bot(bot: str, bot_rows: list[dict]) -> list[dict]:
    """Return stats for every slice (not just loss buckets) for CSV export."""
    out = []

    def emit(slice_type: str, label: str, rows: list[dict]):
        s = slice_stats(rows)
        out.append({
            "bot": bot,
            "slice_type": slice_type,
            "label": label,
            "backtest_n": s["n"],
            "backtest_wins": s["wins"],
            "backtest_roi": round(s["roi"], 2),
            "backtest_pnl": round(s["pnl"], 2),
        })

    # Overall
    emit("overall", "all", bot_rows)

    # By selection
    by_sel = defaultdict(list)
    for r in bot_rows:
        by_sel[r["selection"]].append(r)
    for sel, rows in by_sel.items():
        emit("selection", sel, rows)

    # By odds bucket
    by_odds = defaultdict(list)
    for r in bot_rows:
        by_odds[r["odds_bucket"]].append(r)
    for bucket, rows in by_odds.items():
        emit("odds_bucket", bucket, rows)

    # By tier group
    by_tier = defaultdict(list)
    for r in bot_rows:
        by_tier[r["tier_group"]].append(r)
    for tg, rows in by_tier.items():
        emit("tier", tg, rows)

    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-bets", type=int, default=30,
                   help="Min bets in a slice to flag as a loss bucket (default 30)")
    p.add_argument("--bot", default=None, help="Filter to a single bot")
    p.add_argument("--show-all", action="store_true",
                   help="Show all bots including those with no loss buckets")
    p.add_argument("--csv-out", default=None, metavar="PATH",
                   help="Write full slice table (all bots × all slices) to this CSV path "
                        "for use by slice_live_validate.py")
    args = p.parse_args()

    rows = load_rows()
    print(f"Loaded {len(rows):,} backtest rows")
    print(f"Loss bucket threshold: ≥{args.min_bets} bets AND ROI < -5%\n")

    by_bot = defaultdict(list)
    for r in rows:
        by_bot[r["bot"]].append(r)

    bots = [args.bot] if args.bot else sorted(by_bot.keys(), key=lambda b: -len(by_bot[b]))

    summary: list[dict] = []
    all_slice_rows: list[dict] = []

    for bot in bots:
        bot_rows = by_bot.get(bot, [])
        if not bot_rows:
            print(f"Bot '{bot}' not found in backtest data.")
            continue
        s = slice_stats(bot_rows)
        buckets = print_bot_report(bot, bot_rows, args.min_bets)
        summary.append({
            "bot": bot,
            "n": s["n"],
            "roi": s["roi"],
            "pnl": s["pnl"],
            "loss_buckets": len(buckets) if buckets else 0,
        })
        all_slice_rows.extend(all_slices_for_bot(bot, bot_rows))

    if not args.bot:
        print(f"\n{'━'*80}")
        print("SUMMARY — bots with loss buckets (worst first by P&L)")
        print(f"{'━'*80}")
        print(f"{'bot':<30} {'n':>6} {'ROI':>8} {'P&L':>9} {'loss_buckets':>13}")
        for s in sorted(summary, key=lambda x: x["pnl"]):
            bucket_flag = f"  ← {s['loss_buckets']} bucket(s)" if s["loss_buckets"] > 0 else ""
            print(f"{s['bot']:<30} {s['n']:>6d} {s['roi']:>+7.1f}% €{s['pnl']:>+8.0f}{bucket_flag}")

    if args.csv_out:
        import csv as _csv
        out_path = Path(args.csv_out)
        fieldnames = ["bot", "slice_type", "label", "backtest_n", "backtest_wins",
                      "backtest_roi", "backtest_pnl"]
        with out_path.open("w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(all_slice_rows)
        print(f"\nSlice table written → {out_path}  ({len(all_slice_rows)} rows)")


if __name__ == "__main__":
    main()
