#!/usr/bin/env python3
"""
T1 Evaluation: Does API-Football prediction agreement predict better outcomes?

Splits settled bets into:
  - AF agrees  (af_agrees = true)
  - AF disagrees (af_agrees = false)
  - No AF data  (af_agrees = null)

Reports ROI, win rate, avg edge, and CLV for each group.

Usage:
  python scripts/evaluate_af_predictions.py
  python scripts/evaluate_af_predictions.py --bot bot_v10_all
  python scripts/evaluate_af_predictions.py --min-bets 20  # only show groups with 20+ bets
"""

import sys
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import get_client
from rich.console import Console
from rich.table import Table

console = Console()


def compute_roi_stats(bets: list[dict]) -> dict:
    if not bets:
        return {"count": 0, "win_rate": None, "roi": None, "avg_edge": None, "avg_clv": None, "total_stake": 0}

    won = [b for b in bets if b["result"] == "won"]
    lost = [b for b in bets if b["result"] == "lost"]
    settled = won + lost

    if not settled:
        return {"count": len(bets), "win_rate": None, "roi": None, "avg_edge": None, "avg_clv": None, "total_stake": 0}

    total_stake = sum(float(b["stake"]) for b in settled)
    total_pnl = sum(float(b["pnl"] or 0) for b in settled)
    roi = total_pnl / total_stake if total_stake > 0 else 0
    win_rate = len(won) / len(settled)

    # Average edge at time of bet
    edges = [float(b["edge"]) for b in settled if b.get("edge") is not None]
    avg_edge = sum(edges) / len(edges) if edges else None

    # CLV: compare bet odds vs closing odds (if available)
    clv_values = []
    for b in settled:
        if b.get("odds") and b.get("closing_odds") and float(b["closing_odds"]) > 0:
            clv = (float(b["odds"]) - float(b["closing_odds"])) / float(b["closing_odds"])
            clv_values.append(clv)
    avg_clv = sum(clv_values) / len(clv_values) if clv_values else None

    return {
        "count": len(settled),
        "pending": len(bets) - len(settled),
        "win_rate": win_rate,
        "roi": roi,
        "avg_edge": avg_edge,
        "avg_clv": avg_clv,
        "total_stake": total_stake,
        "total_pnl": total_pnl,
    }


def _fmt(val, fmt=".1%", none_str="—") -> str:
    if val is None:
        return none_str
    return format(val, fmt)


def run(bot_filter: str | None = None, min_bets: int = 5):
    client = get_client()

    # Fetch all settled + pending bets with AF agreement columns
    query = client.table("simulated_bets").select(
        "id, result, pnl, stake, odds, closing_odds, edge, "
        "af_home_prob, af_draw_prob, af_away_prob, af_agrees, "
        "market, selection, calibrated_prob, model_disagreement, "
        "bots(name)"
    ).in_("result", ["won", "lost", "pending"])

    if bot_filter:
        # Filter by bot name via join
        bets_raw = query.execute().data
        bets_raw = [b for b in bets_raw if b.get("bots", {}).get("name") == bot_filter]
    else:
        bets_raw = query.execute().data

    total = len(bets_raw)
    settled_count = sum(1 for b in bets_raw if b["result"] in ("won", "lost"))
    af_covered = sum(1 for b in bets_raw if b.get("af_agrees") is not None)

    console.print(f"\n[bold cyan]═══ API-Football Prediction Evaluation (T1) ═══[/bold cyan]")
    console.print(f"  Total bets: {total} ({settled_count} settled, {total - settled_count} pending)")
    console.print(f"  With AF prediction: {af_covered} ({af_covered/total:.0%} coverage)\n")

    if settled_count < min_bets:
        console.print(f"[yellow]Only {settled_count} settled bets — need {min_bets}+ for reliable stats.[/yellow]")
        console.print("[dim]Check back after more matches are settled.[/dim]\n")

    # Split by AF agreement
    groups = {
        "AF Agrees": [b for b in bets_raw if b.get("af_agrees") is True],
        "AF Disagrees": [b for b in bets_raw if b.get("af_agrees") is False],
        "No AF Data": [b for b in bets_raw if b.get("af_agrees") is None],
        "ALL Bets": bets_raw,
    }

    t = Table(title="ROI by AF Agreement")
    t.add_column("Group", style="cyan")
    t.add_column("Bets", justify="right")
    t.add_column("Win Rate", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("Avg Edge", justify="right")
    t.add_column("Avg CLV", justify="right")
    t.add_column("Total P&L", justify="right")

    for group_name, group_bets in groups.items():
        stats = compute_roi_stats(group_bets)
        if stats["count"] == 0 and not group_bets:
            continue

        roi_color = "green" if (stats.get("roi") or 0) > 0 else "red"
        t.add_row(
            group_name,
            f"{stats['count']} ({stats.get('pending', 0)} pend)",
            _fmt(stats.get("win_rate")),
            f"[{roi_color}]{_fmt(stats.get('roi'))}[/{roi_color}]",
            _fmt(stats.get("avg_edge")),
            _fmt(stats.get("avg_clv")),
            f"[{roi_color}]{stats.get('total_pnl', 0):+.2f}[/{roi_color}]" if stats.get("total_pnl") is not None else "—",
        )

    console.print(t)

    # Per-market breakdown for AF agrees vs disagrees
    console.print("\n[bold]Breakdown by market:[/bold]")
    markets = set(b["market"] for b in bets_raw if b.get("market"))

    t2 = Table()
    t2.add_column("Market", style="cyan")
    t2.add_column("AF Agrees — ROI", justify="right")
    t2.add_column("AF Agrees — N", justify="right")
    t2.add_column("AF Disagrees — ROI", justify="right")
    t2.add_column("AF Disagrees — N", justify="right")

    for mkt in sorted(markets):
        agrees_mkt = [b for b in groups["AF Agrees"] if b.get("market") == mkt]
        disagrees_mkt = [b for b in groups["AF Disagrees"] if b.get("market") == mkt]
        ag_s = compute_roi_stats(agrees_mkt)
        di_s = compute_roi_stats(disagrees_mkt)

        if ag_s["count"] + di_s["count"] < min_bets:
            continue

        ag_roi = _fmt(ag_s.get("roi"))
        di_roi = _fmt(di_s.get("roi"))

        t2.add_row(
            mkt,
            f"[green]{ag_roi}[/green]" if ag_s.get("roi", 0) > 0 else f"[red]{ag_roi}[/red]",
            str(ag_s["count"]),
            f"[green]{di_roi}[/green]" if di_s.get("roi", 0) > 0 else f"[red]{di_roi}[/red]",
            str(di_s["count"]),
        )

    console.print(t2)

    # Per-bot breakdown
    console.print("\n[bold]Breakdown by bot:[/bold]")
    bot_names = set(b.get("bots", {}).get("name") for b in bets_raw if b.get("bots"))

    t3 = Table()
    t3.add_column("Bot", style="cyan")
    t3.add_column("AF Agrees — ROI", justify="right")
    t3.add_column("AF Agrees — N", justify="right")
    t3.add_column("AF Disagrees — ROI", justify="right")
    t3.add_column("AF Disagrees — N", justify="right")

    for bot in sorted(bot_names or []):
        if not bot:
            continue
        bot_bets = [b for b in bets_raw if b.get("bots", {}).get("name") == bot]
        ag_b = [b for b in bot_bets if b.get("af_agrees") is True]
        di_b = [b for b in bot_bets if b.get("af_agrees") is False]
        ag_s = compute_roi_stats(ag_b)
        di_s = compute_roi_stats(di_b)

        if ag_s["count"] + di_s["count"] < 2:
            continue

        ag_roi = _fmt(ag_s.get("roi"))
        di_roi = _fmt(di_s.get("roi"))

        t3.add_row(
            bot,
            f"[green]{ag_roi}[/green]" if ag_s.get("roi", 0) > 0 else f"[red]{ag_roi}[/red]",
            str(ag_s["count"]),
            f"[green]{di_roi}[/green]" if di_s.get("roi", 0) > 0 else f"[red]{di_roi}[/red]",
            str(di_s["count"]),
        )

    console.print(t3)

    # Summary insight
    ag_stats = compute_roi_stats(groups["AF Agrees"])
    di_stats = compute_roi_stats(groups["AF Disagrees"])

    if ag_stats["count"] >= min_bets and di_stats["count"] >= min_bets:
        console.print("\n[bold]Insight:[/bold]")
        ag_roi = ag_stats.get("roi") or 0
        di_roi = di_stats.get("roi") or 0
        diff = ag_roi - di_roi
        if diff > 0.03:
            console.print(f"  [green]✓ AF agreement is a USEFUL signal: +{diff:.1%} ROI lift when AF agrees[/green]")
            console.print(f"  → Consider using af_agrees as an alignment dimension (P3)")
        elif diff > 0:
            console.print(f"  [yellow]Weak positive signal: +{diff:.1%} ROI lift when AF agrees (need more data)[/yellow]")
        else:
            console.print(f"  [red]No signal yet: {diff:.1%} difference (AF agreement not predictive so far)[/red]")
    else:
        console.print(f"\n[dim]Need {min_bets}+ settled bets in each group for reliable conclusion.[/dim]")
        console.print(f"[dim]Currently: {ag_stats['count']} AF-agrees settled, {di_stats['count']} AF-disagrees settled.[/dim]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate AF prediction agreement vs our ROI")
    parser.add_argument("--bot", help="Filter to specific bot name")
    parser.add_argument("--min-bets", type=int, default=5, help="Minimum settled bets to show group (default: 5)")
    args = parser.parse_args()

    run(bot_filter=args.bot, min_bets=args.min_bets)
