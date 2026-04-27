"""
OddsIntel — Prediction Audit Trail Report
Compares model performance across information stages to measure
the value-add of each data source.

Usage:
  python scripts/audit_trail_report.py           # Full report
  python scripts/audit_trail_report.py --bot bot_aggressive  # Single bot
"""

import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.supabase_client import get_client

console = Console()


def run_report(bot_filter: str = None):
    client = get_client()
    console.print("[bold]OddsIntel — Prediction Audit Trail Report[/bold]\n")

    # Get all settled bets with snapshots
    query = client.table("simulated_bets").select(
        "id, bot_id, market, selection, odds_at_pick, model_probability, "
        "edge_percent, result, pnl, news_triggered, reasoning, "
        "bots(name)"
    ).neq("result", "pending")

    if bot_filter:
        # Need to filter by bot name via join
        pass  # handled below

    bets = query.execute().data

    if not bets:
        console.print("[yellow]No settled bets yet. Run settlement first.[/yellow]")
        console.print("[dim]The audit trail compares performance across stages once bets are settled.[/dim]")

        # Show what snapshots we have so far
        snapshots = client.table("prediction_snapshots").select(
            "stage, model_probability, edge_percent, metadata", count="exact"
        ).execute()
        console.print(f"\n[cyan]Prediction snapshots collected: {snapshots.count}[/cyan]")

        if snapshots.data:
            by_stage = {}
            for s in snapshots.data:
                stage = s["stage"]
                by_stage.setdefault(stage, []).append(s)

            t = Table(title="Snapshots by Stage")
            t.add_column("Stage")
            t.add_column("Count", justify="right")
            t.add_column("Avg Probability", justify="right")
            t.add_column("Avg Edge", justify="right")

            for stage in ["stats_only", "post_ai", "pre_kickoff", "closing"]:
                if stage in by_stage:
                    items = by_stage[stage]
                    avg_prob = sum(s["model_probability"] for s in items) / len(items)
                    edges = [s["edge_percent"] for s in items if s.get("edge_percent")]
                    avg_edge = sum(edges) / len(edges) if edges else 0
                    t.add_row(stage, str(len(items)), f"{avg_prob:.1%}", f"{avg_edge:.1%}")

            console.print(t)

            # Show AI impact preview
            if "post_ai" in by_stage:
                ai_snaps = by_stage["post_ai"]
                flags = {}
                for s in ai_snaps:
                    meta = s.get("metadata", {}) or {}
                    flag = meta.get("ai_flag", "unknown")
                    flags[flag] = flags.get(flag, 0) + 1
                console.print(f"\nAI flags: {flags}")
                adjustments = [
                    s.get("metadata", {}).get("confidence_adjustment", 0)
                    for s in ai_snaps
                    if s.get("metadata", {}).get("confidence_adjustment")
                ]
                if adjustments:
                    console.print(
                        f"AI adjustments: min={min(adjustments):.1%}, "
                        f"max={max(adjustments):.1%}, "
                        f"avg={sum(adjustments)/len(adjustments):.1%}"
                    )

        console.print("\n[dim]Once bets settle, this report will show:[/dim]")
        console.print("[dim]  - ROI at each stage (did AI improve it?)[/dim]")
        console.print("[dim]  - Bets AI flagged as 'skip' — did they actually lose?[/dim]")
        console.print("[dim]  - Edge accuracy: stats-only edge vs actual outcome[/dim]")
        return

    # === Full report with settled bets ===

    # Get all snapshots
    bet_ids = [b["id"] for b in bets]
    all_snapshots = client.table("prediction_snapshots").select("*").in_("bet_id", bet_ids).execute().data

    # Index snapshots by bet_id → stage
    snap_index = {}
    for s in all_snapshots:
        snap_index.setdefault(s["bet_id"], {})[s["stage"]] = s

    # Compare stages
    stages = ["stats_only", "post_ai", "closing"]
    stage_stats = {s: {"bets": 0, "won": 0, "pnl": 0, "edge_sum": 0} for s in stages}

    for bet in bets:
        bot_name = bet.get("bots", {}).get("name", "") if isinstance(bet.get("bots"), dict) else ""
        if bot_filter and bot_filter != bot_name:
            continue

        is_won = bet["result"] == "won"
        pnl = bet.get("pnl", 0) or 0

        for stage in stages:
            snap = snap_index.get(bet["id"], {}).get(stage)
            if snap:
                stage_stats[stage]["bets"] += 1
                if is_won:
                    stage_stats[stage]["won"] += 1
                stage_stats[stage]["pnl"] += pnl
                stage_stats[stage]["edge_sum"] += snap.get("edge_percent", 0) or 0

    # Display
    t = Table(title="Performance by Information Stage")
    t.add_column("Stage")
    t.add_column("Bets", justify="right")
    t.add_column("Win %", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg Edge", justify="right")

    for stage in stages:
        s = stage_stats[stage]
        if s["bets"] == 0:
            continue
        win_pct = s["won"] / s["bets"] * 100
        avg_edge = s["edge_sum"] / s["bets"] * 100
        t.add_row(
            stage,
            str(s["bets"]),
            f"{win_pct:.1f}%",
            f"€{s['pnl']:.2f}",
            f"{avg_edge:.1f}%",
        )

    console.print(t)

    # AI skip analysis
    ai_skips = [
        b for b in bets
        if snap_index.get(b["id"], {}).get("post_ai", {}).get("metadata", {}).get("ai_flag") == "skip"
    ]
    if ai_skips:
        skip_won = sum(1 for b in ai_skips if b["result"] == "won")
        skip_pnl = sum(b.get("pnl", 0) or 0 for b in ai_skips)
        console.print(f"\n[bold]AI 'skip' bets:[/bold] {len(ai_skips)} total")
        console.print(f"  Won: {skip_won}/{len(ai_skips)} ({skip_won/len(ai_skips)*100:.0f}%)")
        console.print(f"  P&L: €{skip_pnl:.2f}")
        console.print(
            f"  → {'AI was right to skip (net loss)' if skip_pnl < 0 else 'AI was wrong to skip (net gain)'}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", help="Filter by bot name")
    args = parser.parse_args()
    run_report(bot_filter=args.bot)
