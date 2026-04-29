"""
Diagnose match data quality to find the clean cutoff timestamp.

Uses COUNT queries via RPC/aggregation to avoid PostgREST 1000-row cap.

Run: python scripts/diagnose_match_quality.py
"""

import os
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from workers.api_clients.supabase_client import get_client
from rich.console import Console
from rich.table import Table

console = Console()
client = get_client()


def run():
    console.print("\n[bold cyan]═══ Match Data Quality Diagnostic ═══[/bold cyan]\n")

    # 1. Matches grouped by date
    console.print("[cyan]Loading matches by date...[/cyan]")
    matches_r = client.table("matches").select(
        "id, date, status"
    ).order("date", desc=False).execute()

    matches = matches_r.data or []
    console.print(f"  {len(matches)} total matches in DB\n")

    by_date: dict[str, list[str]] = defaultdict(list)
    status_by_date: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for m in matches:
        day = (m["date"] or "")[:10]
        by_date[day].append(m["id"])
        status_by_date[day][m.get("status", "unknown")] += 1

    # 2. Ensemble predictions — use match_id grouping
    console.print("[cyan]Loading ensemble predictions...[/cyan]")
    preds_r = client.table("predictions").select(
        "match_id"
    ).eq("source", "ensemble").execute()
    preds_set = {p["match_id"] for p in (preds_r.data or [])}

    # 3. Draw predictions specifically
    draw_r = client.table("predictions").select(
        "match_id"
    ).eq("source", "ensemble").eq("market", "1x2_draw").execute()
    draw_set = {p["match_id"] for p in (draw_r.data or [])}

    # 4. Match signals
    console.print("[cyan]Loading signals...[/cyan]")
    signals_r = client.table("match_signals").select(
        "match_id"
    ).execute()
    signals_set = {s["match_id"] for s in (signals_r.data or [])}

    # 5. Odds coverage — use the historical RPC per-date (avoids row cap)
    console.print("[cyan]Loading odds coverage per date via RPC...[/cyan]")
    odds_by_match: dict[str, int] = {}  # match_id → bookmaker count
    sorted_days = sorted(by_date.keys())
    for day in sorted_days:
        ids = by_date[day]
        if not ids:
            continue
        try:
            r = client.rpc("get_historical_match_odds", {"p_match_ids": ids}).execute()
            for row in (r.data or []):
                mid = row["match_id"]
                odds_by_match[mid] = max(
                    odds_by_match.get(mid, 0),
                    int(row.get("bookmaker_count", 0))
                )
        except Exception as e:
            console.print(f"  [yellow]RPC failed for {day}: {e}[/yellow]")

    # 6. Print per-day table
    console.print()
    table = Table(title="Match Quality by Date", show_lines=True)
    table.add_column("Date", style="cyan", width=12)
    table.add_column("Matches", justify="right", width=8)
    table.add_column("Finished", justify="right", width=9)
    table.add_column("w/ Odds", justify="right", width=8)
    table.add_column("Avg BMs", justify="right", width=8)
    table.add_column("w/ Ensemble", justify="right", width=12)
    table.add_column("w/ Draw", justify="right", width=8)
    table.add_column("w/ Signals", justify="right", width=10)
    table.add_column("Quality", width=10)

    for day in sorted_days:
        ids = by_date[day]
        n = len(ids)
        finished = status_by_date[day].get("finished", 0) + status_by_date[day].get("Match Finished", 0)
        with_odds = sum(1 for mid in ids if mid in odds_by_match)
        with_preds = sum(1 for mid in ids if mid in preds_set)
        with_draw = sum(1 for mid in ids if mid in draw_set)
        with_signals = sum(1 for mid in ids if mid in signals_set)

        total_bms = sum(odds_by_match.get(mid, 0) for mid in ids)
        avg_bms = (total_bms / with_odds) if with_odds > 0 else 0

        pred_pct = (with_preds / n * 100) if n > 0 else 0
        odds_pct = (with_odds / n * 100) if n > 0 else 0

        if avg_bms >= 3 and pred_pct >= 30:
            quality = "[green]GOOD[/green]"
        elif avg_bms >= 1 or pred_pct >= 10:
            quality = "[yellow]PARTIAL[/yellow]"
        else:
            quality = "[red]POOR[/red]"

        table.add_row(
            day,
            str(n),
            str(finished),
            f"{with_odds} ({odds_pct:.0f}%)",
            f"{avg_bms:.1f}",
            f"{with_preds} ({pred_pct:.0f}%)",
            f"{with_draw}",
            f"{with_signals}",
            quality,
        )

    console.print(table)

    # 7. Status breakdown
    console.print("\n[bold]Status breakdown per day:[/bold]")
    for day in sorted_days:
        statuses = dict(status_by_date[day])
        console.print(f"  {day}: {statuses}")

    # 8. Summary
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Total matches:         {len(matches)}")
    console.print(f"  With odds (any):       {len(odds_by_match)}")
    console.print(f"  With ensemble pred:    {len(preds_set)}")
    console.print(f"  With draw pred:        {len(draw_set)}  ← should equal ensemble count after fix")
    console.print(f"  With signals:          {len(signals_set)}")

    # 9. Suggest cutoff
    first_good = None
    for day in sorted_days:
        ids = by_date[day]
        n = len(ids)
        with_odds = sum(1 for mid in ids if mid in odds_by_match)
        with_preds = sum(1 for mid in ids if mid in preds_set)
        total_bms = sum(odds_by_match.get(mid, 0) for mid in ids)
        avg_bms = (total_bms / with_odds) if with_odds > 0 else 0
        pred_pct = (with_preds / n * 100) if n > 0 else 0
        if avg_bms >= 3 and pred_pct >= 30:
            first_good = day
            break

    if first_good:
        bad_days = [d for d in sorted_days if d < first_good]
        bad_count = sum(len(by_date[d]) for d in bad_days)
        console.print(f"\n[bold yellow]Suggested cutoff: keep from {first_good} onwards[/bold yellow]")
        console.print(f"  Delete matches dated before {first_good} (~{bad_count} matches)")
        console.print(f"  This also cleans: odds_snapshots, predictions, match_signals,")
        console.print(f"  simulated_bets, live_match_snapshots for those match IDs")
        console.print(f"\n  To proceed: python scripts/cleanup_before_date.py {first_good}")
    else:
        console.print(f"\n[yellow]No day clearly qualifies as GOOD — review the table above manually.[/yellow]")
        console.print(f"The issue may be that odds data is sparse across all days (RPC only returns")
        console.print(f"settled/finished matches). Predictions and signals are the better quality signal.")


if __name__ == "__main__":
    run()
