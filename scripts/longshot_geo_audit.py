"""LONGSHOT-GEO-AUDIT — is 0.30-0.40 calibrated_prob bin failure geographically concentrated?

Hypothesis: the model over-predicts in some regions (typically high-home-advantage
regions like South America) and under-predicts in others. If the 30-40% calibrated-
prob bucket is miscalibrated globally but the bias is concentrated in a few
country clusters, we should add a per-country shrinkage factor on top of the
tier-level Platt.

Method:
  1. Bin all settled bets by calibrated_prob in 5pp steps
  2. For each bin, compute actual win rate
  3. The "gap" = actual − predicted at bin midpoint = miscalibration
  4. GROUP BY country (matches.league_id → leagues.country)
  5. Report countries where the 30-40% bin gap exceeds the global average by ≥5pp
     in either direction

Run:
  python3 scripts/longshot_geo_audit.py             # report only
  python3 scripts/longshot_geo_audit.py --write     # also store as match_signal
"""
from __future__ import annotations
import argparse
import sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, get_conn

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    console.print("[bold]LONGSHOT-GEO-AUDIT — per-country calibration gap in the 30-40% bucket[/bold]")

    rows = execute_query("""
        SELECT sb.calibrated_prob,
               sb.result,
               sb.pnl, sb.stake,
               l.country, l.tier
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        JOIN leagues l ON l.id = m.league_id
        WHERE sb.calibrated_prob IS NOT NULL
          AND sb.result IN ('won','lost','void')
          AND sb.market = '1x2'
          AND sb.pick_time >= NOW() - INTERVAL '60 days'
    """)
    if not rows:
        console.print("[yellow]No settled bets in last 60 days.[/yellow]")
        return
    console.print(f"  Loaded {len(rows):,} settled 1x2 bets from last 60 days")

    # GLOBAL calibration curve
    bins = [(i / 20, (i + 1) / 20) for i in range(0, 20)]  # 5pp bins
    global_bin: dict[tuple[float, float], dict] = {b: {"n": 0, "wins": 0, "p_sum": 0.0} for b in bins}
    country_bin: dict[str, dict[tuple, dict]] = defaultdict(lambda: {b: {"n": 0, "wins": 0, "p_sum": 0.0} for b in bins})

    for r in rows:
        p = float(r["calibrated_prob"])
        won = r["pnl"] is not None and float(r["pnl"]) > 0
        country = r["country"] or "Unknown"
        for b in bins:
            if b[0] <= p < b[1]:
                global_bin[b]["n"] += 1
                global_bin[b]["p_sum"] += p
                if won:
                    global_bin[b]["wins"] += 1
                country_bin[country][b]["n"] += 1
                country_bin[country][b]["p_sum"] += p
                if won:
                    country_bin[country][b]["wins"] += 1
                break

    # Global calibration table
    t = Table(title="GLOBAL calibration curve (last 60d, 1x2 bets)")
    for c in ("p bin", "n", "avg_pred", "actual_win%", "gap (act-pred)"):
        t.add_column(c)
    for b, stats in global_bin.items():
        if stats["n"] < 30:
            continue
        avg_pred = stats["p_sum"] / stats["n"]
        actual = stats["wins"] / stats["n"]
        gap = (actual - avg_pred) * 100
        t.add_row(
            f"{b[0]:.2f}-{b[1]:.2f}",
            str(stats["n"]),
            f"{avg_pred*100:.1f}%",
            f"{actual*100:.1f}%",
            f"{gap:+.1f}pp",
        )
    console.print(t)

    # Focus bin: 0.30-0.40 (was flagged as 42% predicted, 13% actual in the original task description)
    focus_low, focus_high = 0.30, 0.40
    focus_bins = [(focus_low, focus_low + 0.05), (focus_low + 0.05, focus_high)]
    # Aggregate the focus bins per country
    country_summary = []
    for country, cb in country_bin.items():
        n = sum(cb[b]["n"] for b in focus_bins)
        if n < 20:
            continue
        wins = sum(cb[b]["wins"] for b in focus_bins)
        p_sum = sum(cb[b]["p_sum"] for b in focus_bins)
        avg_pred = p_sum / n
        actual = wins / n
        gap = (actual - avg_pred) * 100
        country_summary.append({"country": country, "n": n, "avg_pred": avg_pred,
                                "actual": actual, "gap": gap})
    country_summary.sort(key=lambda x: x["gap"])
    n_total = sum(c["n"] for c in country_summary)
    if n_total == 0:
        console.print("[yellow]No matches in 0.30-0.40 bin.[/yellow]")
        return
    global_focus_gap = sum(c["gap"] * c["n"] for c in country_summary) / n_total

    console.print(f"\n[bold]Per-country gap in 0.30-0.40 calibrated bin (last 60d):[/bold]")
    console.print(f"  Global average gap = {global_focus_gap:+.1f}pp (across {n_total:,} bets)")
    t = Table(title="Countries where the 0.30-0.40 bin diverges from the global average by ≥5pp")
    for c in ("country", "n", "actual %", "predicted %", "gap", "vs global"):
        t.add_column(c)
    for cs in country_summary:
        if abs(cs["gap"] - global_focus_gap) < 5.0:
            continue
        t.add_row(
            cs["country"], str(cs["n"]),
            f"{cs['actual']*100:.1f}%",
            f"{cs['avg_pred']*100:.1f}%",
            f"{cs['gap']:+.1f}pp",
            f"{(cs['gap'] - global_focus_gap):+.1f}pp",
        )
    console.print(t)

    # Verdict
    extreme = [c for c in country_summary if abs(c["gap"] - global_focus_gap) >= 5.0]
    if extreme:
        worst = min(extreme, key=lambda x: x["gap"])
        best = max(extreme, key=lambda x: x["gap"])
        console.print(f"\n[bold]Findings[/bold]")
        console.print(f"  Worst calibrated: {worst['country']} (n={worst['n']}, gap {worst['gap']:+.1f}pp — "
                      f"{(worst['gap'] - global_focus_gap):+.1f}pp vs global)")
        console.print(f"  Best calibrated:  {best['country']} (n={best['n']}, gap {best['gap']:+.1f}pp)")
        console.print(f"  → Consider per-country shrinkage on top of tier-level Platt for the outliers.")
    else:
        console.print("[yellow]No countries diverge by ≥5pp from global average — bias appears uniform.[/yellow]")


if __name__ == "__main__":
    main()
