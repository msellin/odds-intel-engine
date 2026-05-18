"""
SLICE-LIVE-VALIDATE — compare stored backtest slice baselines against live
simulated_bets data to see whether the tightening suggestions hold up.

Reads `dev/active/backtest-slice-baseline.csv` (written by
`per_bot_slice_analysis.py --csv-out`) and queries `simulated_bets` for the
same (bot, slice_type, label) combinations, then shows:

  backtest ROI  |  live ROI  |  verdict (confirms / contradicts / too-few)

Designed to be re-run periodically as live data accumulates. The signal
becomes meaningful once each slice has ≥20 settled live bets.

Usage:
  python scripts/slice_live_validate.py
  python scripts/slice_live_validate.py --bot bot_ou25_global
  python scripts/slice_live_validate.py --min-live 10   # lower threshold
  python scripts/slice_live_validate.py --slice-type odds_bucket
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import execute_query

BASELINE_CSV = Path(__file__).parent.parent / "dev" / "active" / "backtest-slice-baseline.csv"

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


def load_baseline() -> dict[tuple, dict]:
    """Returns {(bot, slice_type, label): backtest_row}"""
    if not BASELINE_CSV.exists():
        print(f"ERROR: {BASELINE_CSV} not found.")
        print("Run: python scripts/per_bot_slice_analysis.py --csv-out dev/active/backtest-slice-baseline.csv")
        sys.exit(1)
    result = {}
    with BASELINE_CSV.open() as f:
        for r in csv.DictReader(f):
            key = (r["bot"], r["slice_type"], r["label"])
            result[key] = {
                "backtest_n": int(r["backtest_n"]),
                "backtest_roi": float(r["backtest_roi"]),
                "backtest_pnl": float(r["backtest_pnl"]),
            }
    return result


def fetch_live_bets() -> list[dict]:
    """Fetch all settled simulated_bets with bot name, odds, selection, tier, pnl."""
    rows = execute_query("""
        SELECT
            b.name AS bot,
            sb.odds_at_pick AS odds,
            sb.market,
            sb.selection,
            sb.stake,
            sb.pnl,
            sb.result,
            l.tier
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        JOIN leagues l ON l.id = m.league_id
        WHERE sb.result IN ('won', 'lost')
          AND sb.combo_size IS NULL
        ORDER BY sb.created_at
    """, [])
    out = []
    for r in rows:
        odds = float(r["odds"])
        tier = int(r["tier"]) if r["tier"] else 0
        out.append({
            "bot": r["bot"],
            "odds": odds,
            "selection": (r["selection"] or "").lower(),
            "stake": float(r["stake"]),
            "pnl": float(r["pnl"]),
            "won": r["result"] == "won",
            "odds_bucket": odds_bucket(odds),
            "tier_group": tier_group(tier),
        })
    return out


def slice_live(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0, "roi": None, "pnl": 0.0}
    stake = sum(r["stake"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    roi = pnl / stake * 100 if stake else 0.0
    return {"n": n, "roi": roi, "pnl": pnl}


def verdict(bt_roi: float, live_roi: float | None, live_n: int, min_live: int) -> str:
    if live_n < min_live:
        return f"too few ({live_n}<{min_live})"
    if live_roi is None:
        return "no live data"
    bt_neg = bt_roi < -5.0
    live_neg = live_roi < -5.0
    if bt_neg and live_neg:
        return "✅ confirms (both negative)"
    if bt_neg and not live_neg:
        return "❌ contradicts (live positive)"
    if not bt_neg and live_neg:
        return "⚠️  live worse than backtest"
    return "✓ both positive"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bot", default=None, help="Filter to a single bot")
    p.add_argument("--slice-type", default=None,
                   choices=["overall", "selection", "odds_bucket", "tier"],
                   help="Filter to one slice type")
    p.add_argument("--min-live", type=int, default=20,
                   help="Min live settled bets to show a verdict (default 20)")
    p.add_argument("--show-all", action="store_true",
                   help="Show slices with too few live bets too")
    args = p.parse_args()

    baseline = load_baseline()
    print(f"Loaded {len(baseline)} backtest slice baselines from {BASELINE_CSV.name}")

    live_rows = fetch_live_bets()
    print(f"Loaded {len(live_rows)} settled live bets\n")

    # Build live lookup: {(bot, slice_type, label): [rows]}
    live_by_key: dict[tuple, list] = defaultdict(list)
    for r in live_rows:
        bot = r["bot"]
        live_by_key[(bot, "overall", "all")].append(r)
        live_by_key[(bot, "selection", r["selection"])].append(r)
        live_by_key[(bot, "odds_bucket", r["odds_bucket"])].append(r)
        live_by_key[(bot, "tier", r["tier_group"])].append(r)

    # Filter keys
    keys = sorted(baseline.keys())
    if args.bot:
        keys = [k for k in keys if k[0] == args.bot]
    if args.slice_type:
        keys = [k for k in keys if k[1] == args.slice_type]

    # Group by bot for display
    by_bot: dict[str, list] = defaultdict(list)
    for k in keys:
        by_bot[k[0]].append(k)

    contradictions: list[tuple] = []
    confirmations: list[tuple] = []

    for bot in sorted(by_bot.keys()):
        bot_keys = by_bot[bot]
        bt_overall = baseline.get((bot, "overall", "all"))
        live_overall = slice_live(live_by_key.get((bot, "overall", "all"), []))

        print(f"{'━'*90}")
        print(f"BOT: {bot}")
        if bt_overall:
            live_roi_str = f"{live_overall['roi']:+.1f}%" if live_overall["n"] > 0 else "—"
            print(f"  Overall  backtest={bt_overall['backtest_roi']:+.1f}% (n={bt_overall['backtest_n']})   "
                  f"live={live_roi_str} (n={live_overall['n']}  P&L=€{live_overall['pnl']:+.1f})")

        print(f"\n  {'Slice':<22} {'BT n':>6} {'BT ROI':>8} {'Live n':>7} {'Live ROI':>9} {'Live P&L':>10}  Verdict")
        print(f"  {'-'*22} {'-'*6} {'-'*8} {'-'*7} {'-'*9} {'-'*10}  {'-'*30}")

        for k in sorted(bot_keys, key=lambda x: x[1:]):
            _, slice_type, label = k
            if slice_type == "overall":
                continue
            bt = baseline[k]
            live_data = live_by_key.get(k, [])
            ls = slice_live(live_data)
            v = verdict(bt["backtest_roi"], ls["roi"], ls["n"], args.min_live)

            if not args.show_all and ls["n"] < args.min_live and "contradicts" not in v and "confirms" not in v:
                continue

            live_roi_str = f"{ls['roi']:+.1f}%" if ls["n"] > 0 else "—"
            live_pnl_str = f"€{ls['pnl']:+.1f}" if ls["n"] > 0 else "—"
            tag = f"{slice_type}:{label}"
            print(f"  {tag:<22} {bt['backtest_n']:>6} {bt['backtest_roi']:>+7.1f}% {ls['n']:>7} {live_roi_str:>9} {live_pnl_str:>10}  {v}")

            if "❌" in v:
                contradictions.append((bot, slice_type, label, bt["backtest_roi"], ls["roi"], ls["n"]))
            if "✅" in v:
                confirmations.append((bot, slice_type, label, bt["backtest_roi"], ls["roi"], ls["n"]))
        print()

    # Summary
    print(f"{'━'*90}")
    print(f"SUMMARY  (min_live={args.min_live} bets to count as verdict)")
    print(f"  Confirmations (backtest negative, live also negative): {len(confirmations)}")
    for c in confirmations:
        print(f"    {c[0]}.{c[1]}:{c[2]}  BT={c[3]:+.1f}%  live={c[4]:+.1f}%  n={c[5]}")
    print(f"  Contradictions (backtest negative, live positive):     {len(contradictions)}")
    for c in contradictions:
        print(f"    {c[0]}.{c[1]}:{c[2]}  BT={c[3]:+.1f}%  live={c[4]:+.1f}%  n={c[5]}")

    print(f"\nNote: backtest uses Poisson-only predictions (no v14 XGBoost, no Pinnacle veto).")
    print(f"      Live ROI reflects the full filter stack. Contradictions = live model corrects backtest.")
    print(f"      Re-run when any slice reaches {args.min_live}+ settled bets for a reliable verdict.")


if __name__ == "__main__":
    main()
