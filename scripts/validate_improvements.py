"""
OddsIntel — Validation Script for Model Improvements (P1-P4)

Run this after accumulating settled bets to validate whether the
improvements are working. Produces:

  1. Calibration curve by tier (predicted vs actual, 5% bins)
  2. ROI by alignment class (HIGH/MEDIUM/LOW)
  3. CLV tracking (placed odds vs closing odds)
  4. Kelly vs flat-stake comparison
  5. Odds movement penalty impact

Usage:
  python scripts/validate_improvements.py             # Full report
  python scripts/validate_improvements.py --min-bets 50   # Only if enough data

Checkpoints from MODEL_ANALYSIS.md Section 7:
  - Calibration: predicted prob vs actual win rate should be near-diagonal
  - Alignment: ROI must increase monotonically with alignment class
  - Kelly: should show higher Sharpe ratio than flat stake
  - CLV: should be consistently positive if model has real edge
"""

import sys
import os
import argparse
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

console = Console()


def get_settled_bets() -> list[dict]:
    """Fetch all settled bets with improvement fields."""
    from workers.api_clients.supabase_client import get_client
    client = get_client()

    result = client.table("simulated_bets").select(
        "*, matches(date, league_id, leagues(tier, name, country))"
    ).neq("result", "pending").neq("result", "void").execute()

    return result.data


def report_calibration(bets: list[dict]):
    """
    Checkpoint: Plot predicted probability vs actual win rate in 5% bins.
    Should be near-diagonal after calibration fix.
    """
    console.print("\n[bold cyan]═══ 1. Calibration Analysis ═══[/bold cyan]\n")

    # Group bets by calibrated_prob bins (5% each)
    bins = defaultdict(lambda: {"total": 0, "won": 0})

    for bet in bets:
        prob = bet.get("calibrated_prob") or bet.get("model_probability")
        if prob is None:
            continue

        bin_key = round(prob * 20) / 20  # 5% bins
        bins[bin_key]["total"] += 1
        if bet["result"] == "won":
            bins[bin_key]["won"] += 1

    if not bins:
        console.print("[yellow]No bets with probability data.[/yellow]")
        return

    t = Table(title="Calibration: Predicted vs Actual Win Rate")
    t.add_column("Predicted", justify="right")
    t.add_column("Actual", justify="right")
    t.add_column("Gap", justify="right")
    t.add_column("Bets", justify="right")
    t.add_column("Visual")

    total_ece = 0.0
    total_bets = 0

    for bin_key in sorted(bins.keys()):
        data = bins[bin_key]
        if data["total"] < 3:
            continue

        actual = data["won"] / data["total"]
        gap = bin_key - actual
        total_ece += abs(gap) * data["total"]
        total_bets += data["total"]

        # Visual: bar showing gap
        bar_len = int(abs(gap) * 50)
        if gap > 0.02:
            bar = "[red]" + "█" * bar_len + f" overconfident[/red]"
        elif gap < -0.02:
            bar = "[green]" + "█" * bar_len + f" underconfident[/green]"
        else:
            bar = "[white]≈ calibrated[/white]"

        t.add_row(
            f"{bin_key:.0%}",
            f"{actual:.0%}",
            f"{gap:+.1%}",
            str(data["total"]),
            bar,
        )

    console.print(t)

    if total_bets > 0:
        ece = total_ece / total_bets
        console.print(f"\n  Expected Calibration Error (ECE): [bold]{ece:.3f}[/bold]")
        console.print(f"  (Lower is better. Target: < 0.05. Pre-fix baseline: ~0.12)")

    # Per-tier calibration
    tier_bins = defaultdict(lambda: defaultdict(lambda: {"total": 0, "won": 0}))
    for bet in bets:
        prob = bet.get("calibrated_prob") or bet.get("model_probability")
        tier = _get_tier(bet)
        if prob is None or tier is None:
            continue
        bin_key = round(prob * 10) / 10  # 10% bins for per-tier (less data)
        tier_bins[tier][bin_key]["total"] += 1
        if bet["result"] == "won":
            tier_bins[tier][bin_key]["won"] += 1

    if tier_bins:
        console.print("\n[bold]Per-Tier ECE:[/bold]")
        for tier in sorted(tier_bins.keys()):
            t_ece = 0.0
            t_total = 0
            for bk, data in tier_bins[tier].items():
                if data["total"] >= 3:
                    actual = data["won"] / data["total"]
                    t_ece += abs(bk - actual) * data["total"]
                    t_total += data["total"]
            if t_total > 0:
                console.print(f"  Tier {tier}: ECE = {t_ece / t_total:.3f} ({t_total} bets)")


def report_alignment(bets: list[dict]):
    """
    Checkpoint: ROI must increase monotonically with alignment class.
    If it doesn't, alignment filter isn't adding value.
    """
    console.print("\n[bold cyan]═══ 2. Alignment Analysis (LOG-ONLY) ═══[/bold cyan]\n")

    classes = defaultdict(lambda: {"bets": 0, "pnl": 0.0, "staked": 0.0})

    for bet in bets:
        ac = bet.get("alignment_class")
        if not ac:
            ac = "NONE"

        classes[ac]["bets"] += 1
        classes[ac]["pnl"] += float(bet.get("pnl") or 0)
        classes[ac]["staked"] += float(bet.get("stake") or 10)

    if not classes:
        console.print("[yellow]No alignment data yet.[/yellow]")
        return

    t = Table(title="ROI by Alignment Class")
    t.add_column("Class")
    t.add_column("Bets", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Signal?")

    for ac in ["HIGH", "MEDIUM", "LOW", "NONE"]:
        if ac not in classes:
            continue
        data = classes[ac]
        roi = (data["pnl"] / data["staked"] * 100) if data["staked"] > 0 else 0

        signal = ""
        if ac == "HIGH" and roi > 0:
            signal = "[green]✓ positive[/green]"
        elif ac == "LOW" and roi < -5:
            signal = "[green]✓ filter would help[/green]"
        elif ac != "NONE":
            signal = "[yellow]? inconclusive[/yellow]"

        t.add_row(ac, str(data["bets"]), f"{roi:+.1f}%", f"€{data['pnl']:+.2f}", signal)

    console.print(t)
    console.print("\n  [dim]Alignment is LOG-ONLY. Activate as filter when HIGH > MEDIUM > LOW ROI consistently.[/dim]")


def report_clv(bets: list[dict]):
    """
    Checkpoint: CLV should be consistently positive if model has edge.
    CLV = (odds_at_pick / odds_at_close) - 1
    """
    console.print("\n[bold cyan]═══ 3. Closing Line Value (CLV) ═══[/bold cyan]\n")

    clv_values = []
    for bet in bets:
        clv = bet.get("clv")
        if clv is not None:
            clv_values.append(float(clv))

    if not clv_values:
        console.print("[yellow]No CLV data yet. CLV requires closing odds (settlement pipeline).[/yellow]")
        return

    import statistics
    avg_clv = statistics.mean(clv_values)
    med_clv = statistics.median(clv_values)
    pos_pct = sum(1 for c in clv_values if c > 0) / len(clv_values) * 100

    console.print(f"  Bets with CLV: {len(clv_values)}")
    console.print(f"  Average CLV: [bold]{avg_clv:+.3f}[/bold] ({'[green]positive ✓[/green]' if avg_clv > 0 else '[red]negative ✗[/red]'})")
    console.print(f"  Median CLV: {med_clv:+.3f}")
    console.print(f"  % beating closing line: {pos_pct:.0f}%")
    console.print(f"\n  [dim]Target: avg CLV > +0.015. If consistently negative, model has no real edge.[/dim]")


def report_kelly_vs_flat(bets: list[dict]):
    """
    Checkpoint: Kelly should show higher Sharpe ratio than flat stake.
    Simulates both on the same bets.
    """
    console.print("\n[bold cyan]═══ 4. Kelly vs Flat Stake Comparison ═══[/bold cyan]\n")

    kelly_bankroll = 1000.0
    flat_bankroll = 1000.0
    flat_stake = 10.0

    kelly_returns = []
    flat_returns = []

    for bet in bets:
        odds = float(bet.get("odds_at_pick", 0))
        kelly_stake = float(bet.get("stake") or 10)
        won = bet["result"] == "won"

        # Kelly path
        k_pnl = kelly_stake * (odds - 1) if won else -kelly_stake
        kelly_bankroll += k_pnl
        kelly_returns.append(k_pnl / max(kelly_bankroll - k_pnl, 1))

        # Flat path
        f_pnl = flat_stake * (odds - 1) if won else -flat_stake
        flat_bankroll += f_pnl
        flat_returns.append(f_pnl / max(flat_bankroll - f_pnl, 1))

    if not kelly_returns:
        console.print("[yellow]No settled bets yet.[/yellow]")
        return

    import statistics
    k_mean = statistics.mean(kelly_returns)
    f_mean = statistics.mean(flat_returns)
    k_std = statistics.stdev(kelly_returns) if len(kelly_returns) > 1 else 1
    f_std = statistics.stdev(flat_returns) if len(flat_returns) > 1 else 1
    k_sharpe = k_mean / k_std if k_std > 0 else 0
    f_sharpe = f_mean / f_std if f_std > 0 else 0

    t = Table(title="Kelly vs Flat Stake")
    t.add_column("Metric")
    t.add_column("Kelly", justify="right")
    t.add_column("Flat €10", justify="right")
    t.add_row("Final Bankroll", f"€{kelly_bankroll:.2f}", f"€{flat_bankroll:.2f}")
    t.add_row("ROI", f"{(kelly_bankroll - 1000) / 10:.1f}%", f"{(flat_bankroll - 1000) / 10:.1f}%")
    t.add_row("Sharpe Ratio", f"{k_sharpe:.3f}", f"{f_sharpe:.3f}")
    t.add_row("Total Bets", str(len(kelly_returns)), str(len(flat_returns)))

    console.print(t)

    if k_sharpe > f_sharpe:
        console.print("\n  [green]Kelly has higher Sharpe ratio ✓[/green]")
    else:
        console.print("\n  [yellow]Flat stake has higher Sharpe. Kelly sizing may not be ready.[/yellow]")


def report_odds_movement(bets: list[dict]):
    """
    Checkpoint: Bets with favorable odds movement should have better ROI.
    """
    console.print("\n[bold cyan]═══ 5. Odds Movement Impact ═══[/bold cyan]\n")

    buckets = defaultdict(lambda: {"bets": 0, "pnl": 0.0, "staked": 0.0})

    for bet in bets:
        drift = bet.get("odds_drift")
        if drift is None:
            buckets["no_data"]["bets"] += 1
            buckets["no_data"]["pnl"] += float(bet.get("pnl") or 0)
            buckets["no_data"]["staked"] += float(bet.get("stake") or 10)
            continue

        drift = float(drift)
        if drift > 0.01:
            bucket = "favorable"
        elif drift < -0.01:
            bucket = "adverse"
        else:
            bucket = "stable"

        buckets[bucket]["bets"] += 1
        buckets[bucket]["pnl"] += float(bet.get("pnl") or 0)
        buckets[bucket]["staked"] += float(bet.get("stake") or 10)

    t = Table(title="ROI by Odds Movement Direction")
    t.add_column("Direction")
    t.add_column("Bets", justify="right")
    t.add_column("ROI", justify="right")

    for bucket in ["favorable", "stable", "adverse", "no_data"]:
        if bucket not in buckets:
            continue
        data = buckets[bucket]
        roi = (data["pnl"] / data["staked"] * 100) if data["staked"] > 0 else 0
        t.add_row(bucket, str(data["bets"]), f"{roi:+.1f}%")

    console.print(t)


def _get_tier(bet: dict) -> int | None:
    """Extract league tier from nested bet data."""
    try:
        return bet["matches"]["leagues"]["tier"]
    except (KeyError, TypeError):
        return None


def main():
    parser = argparse.ArgumentParser(description="Validate model improvements")
    parser.add_argument("--min-bets", type=int, default=20,
                        help="Minimum settled bets required to run (default: 20)")
    args = parser.parse_args()

    console.print("[bold green]═══ OddsIntel — Model Improvement Validation ═══[/bold green]\n")

    bets = get_settled_bets()
    console.print(f"  Total settled bets: {len(bets)}")

    if len(bets) < args.min_bets:
        console.print(f"[yellow]Need at least {args.min_bets} settled bets. Currently: {len(bets)}. Run again later.[/yellow]")
        return

    # Count bets with improvement fields
    with_cal = sum(1 for b in bets if b.get("calibrated_prob") is not None)
    with_align = sum(1 for b in bets if b.get("alignment_class") is not None)
    with_kelly = sum(1 for b in bets if b.get("kelly_fraction") is not None)
    with_drift = sum(1 for b in bets if b.get("odds_drift") is not None)

    console.print(f"  With calibrated_prob: {with_cal}")
    console.print(f"  With alignment_class: {with_align}")
    console.print(f"  With kelly_fraction: {with_kelly}")
    console.print(f"  With odds_drift: {with_drift}")

    report_calibration(bets)
    report_alignment(bets)
    report_clv(bets)
    report_kelly_vs_flat(bets)
    report_odds_movement(bets)

    # Summary
    console.print("\n[bold green]═══ Validation Thresholds ═══[/bold green]")
    console.print("  P1 Calibration: ECE < 0.05 → ready")
    console.print("  P3 Alignment:   HIGH ROI > MED ROI > LOW ROI → activate filter")
    console.print("  P4 Kelly:       Kelly Sharpe > Flat Sharpe → keep Kelly sizing")
    console.print("  CLV:            Avg CLV > +0.015 → model has real edge")
    console.print(f"\n  [dim]Next milestone: 100 settled bets (currently {len(bets)})[/dim]")


if __name__ == "__main__":
    main()
