"""Analyze a captured DeepBetting /results payload.

Usage: python3 scripts/analyze_deepbetting_results.py path/to/results.json

Computes:
  - Overall ROI (settled = Won+Lost; Push/Postp refund stake → neutral)
  - ROI per confidence tier (1, 2, 3)
  - ROI per market type (Moneyline / Spread / Over-Under / BTTS / Draw No Bet)
  - ROI per sport block (football vs multi-sport)
  - Free vs paid pick comparison (free_flag="1" vs free_flag=null)

Why this exists: DeepBetting's `/results` endpoint exposes paid pick metadata via
`free_flag: null`. If we can scrape the full history, we can publish their
real ROI (not just the cherry-picked free-pick subset) on /vs/deepbetting.

Pick schema fields used:
  - forecast_status: "Won" | "Lost" | "Push" | "Postp."
  - forecast_profit: odds (Won), "0" (Lost), "1" (Push/Postp = stake refund)
  - odds: stake-equivalent decimal odds
  - confidence: "1" | "2" | "3"
  - forecast_type: market label
  - free_flag: "1" (free pick) or null (paid)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def parse_payload(path: Path) -> list[dict]:
    with path.open() as f:
        data = json.load(f)
    rows: list[dict] = []
    blocks = data.get("data", {})
    for sport_block, picks in blocks.items():
        for p in picks:
            p["_block"] = sport_block
            rows.append(p)
    return rows


def settle_pl(pick: dict) -> tuple[float, float, bool]:
    """Returns (stake, pl, counts_as_settled).

    Push/Postp refund stake (forecast_profit=1, but no risk taken) → not counted
    as a "settled bet" for ROI denominator, but P&L is 0.
    """
    status = pick.get("forecast_status")
    profit = float(pick.get("forecast_profit", 0))
    if status == "Won":
        return 1.0, profit - 1.0, True
    if status == "Lost":
        return 1.0, -1.0, True
    if status in ("Push", "Postp."):
        return 0.0, 0.0, False
    return 0.0, 0.0, False


def summarize(rows: list[dict], label: str) -> None:
    n = len(rows)
    settled = 0
    won = 0
    lost = 0
    push_postp = 0
    stake = 0.0
    pl = 0.0
    odds_sum = 0.0
    for p in rows:
        s, profit, is_settled = settle_pl(p)
        stake += s
        pl += profit
        status = p.get("forecast_status")
        if status == "Won":
            won += 1
            odds_sum += float(p.get("odds", 0))
        elif status == "Lost":
            lost += 1
            odds_sum += float(p.get("odds", 0))
        elif status in ("Push", "Postp."):
            push_postp += 1
        if is_settled:
            settled += 1
    roi = (pl / stake * 100) if stake > 0 else 0.0
    hit_rate = (won / settled * 100) if settled > 0 else 0.0
    avg_odds = (odds_sum / settled) if settled > 0 else 0.0
    print(f"\n=== {label} ===")
    print(f"  Total picks: {n}")
    print(f"  Settled (Won+Lost): {settled}  | Push/Postp refunds: {push_postp}")
    print(f"  Won: {won} ({hit_rate:.1f}%)  Lost: {lost}")
    print(f"  Avg odds (settled): {avg_odds:.3f}")
    print(f"  Total stake: {stake:.2f}u  Net P&L: {pl:+.2f}u")
    print(f"  ROI: {roi:+.2f}%")


def by_field(rows: list[dict], field: str, label: str) -> None:
    print(f"\n--- ROI by {label} ---")
    buckets: dict[str, list[dict]] = defaultdict(list)
    for p in rows:
        key = str(p.get(field, "?"))
        buckets[key].append(p)
    for key in sorted(buckets.keys()):
        summarize(buckets[key], f"{label}={key}")


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    rows = parse_payload(Path(sys.argv[1]))
    summarize(rows, "ALL")
    summarize([p for p in rows if p.get("free_flag") == "1"], "FREE picks only")
    summarize([p for p in rows if p.get("free_flag") is None], "PAID picks only")
    by_field(rows, "confidence", "confidence tier")
    by_field(rows, "forecast_type", "market type")
    by_field(rows, "_block", "sport block")


if __name__ == "__main__":
    main()
