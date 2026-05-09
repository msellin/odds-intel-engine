"""
Probe: does AF /fixtures?ids= return embedded statistics/events/lineups/players
for HISTORICAL finished fixtures, or only fresh ones?

Settlement uses get_fixtures_batch on today's matches successfully — but the
historical backfill needs the embed to work for old (2023/2024/2025) fixtures.
This probe picks 5 known-finished AF fixture IDs from each of 2023, 2024, 2025,
2026 (20 total = one batch call), fires /fixtures?ids=, and reports per-id
which embedded keys came back populated.

Decides: Path A (full batch refactor) vs Path B (skip-fixture-refetch only).
"""

import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.api_football import get_fixtures_batch

console = Console()

# 5 IDs per year, drawn from `matches` where status=finished AND no match_stats row.
# Picked at runtime via the helper SQL on 2026-05-10.
SAMPLES = {
    "2023": [1063607, 1063606, 1063610, 1063604, 1063603],
    "2024": [1201220, 1201625, 1201624, 1201219, 1201079],
    "2025": [1375859, 1375842, 1375803, 1375840, 1375839],
    "2026": [1398257, 1537879, 1544391, 1398261, 1542470],
}


def main() -> int:
    flat_ids = [fid for ids in SAMPLES.values() for fid in ids]
    id_to_year = {fid: yr for yr, ids in SAMPLES.items() for fid in ids}

    console.print(f"[cyan]Probing /fixtures?ids= with {len(flat_ids)} IDs (5 per year, 2023-2026)[/cyan]\n")

    prefetched = get_fixtures_batch(flat_ids)

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Year")
    table.add_column("AF ID", justify="right")
    table.add_column("Returned?")
    table.add_column("statistics", justify="right")
    table.add_column("events", justify="right")
    table.add_column("lineups", justify="right")
    table.add_column("players", justify="right")

    summary = {yr: {"returned": 0, "stats_pop": 0, "events_pop": 0,
                    "lineups_pop": 0, "players_pop": 0} for yr in SAMPLES}

    for fid in flat_ids:
        yr = id_to_year[fid]
        f = prefetched.get(fid)
        if not f:
            table.add_row(yr, str(fid), "[red]NO[/red]", "-", "-", "-", "-")
            continue

        summary[yr]["returned"] += 1
        stats = f.get("statistics") or []
        events = f.get("events") or []
        lineups = f.get("lineups") or []
        players = f.get("players") or []

        if stats: summary[yr]["stats_pop"] += 1
        if events: summary[yr]["events_pop"] += 1
        if lineups: summary[yr]["lineups_pop"] += 1
        if players: summary[yr]["players_pop"] += 1

        table.add_row(
            yr, str(fid), "[green]yes[/green]",
            f"{len(stats)}", f"{len(events)}",
            f"{len(lineups)}", f"{len(players)}",
        )

    console.print(table)
    console.print()

    summary_table = Table(title="Per-year summary", show_header=True, header_style="bold cyan")
    summary_table.add_column("Year")
    summary_table.add_column("Returned", justify="right")
    summary_table.add_column("With statistics", justify="right")
    summary_table.add_column("With events", justify="right")
    summary_table.add_column("With lineups", justify="right")
    summary_table.add_column("With players", justify="right")

    for yr in SAMPLES:
        s = summary[yr]
        summary_table.add_row(
            yr,
            f"{s['returned']}/5",
            f"{s['stats_pop']}/5",
            f"{s['events_pop']}/5",
            f"{s['lineups_pop']}/5",
            f"{s['players_pop']}/5",
        )

    console.print(summary_table)
    console.print()

    # Verdict — Path A is viable iff stats AND events come back populated for 2023+
    old_stats = summary["2023"]["stats_pop"] + summary["2024"]["stats_pop"]
    old_events = summary["2023"]["events_pop"] + summary["2024"]["events_pop"]
    if old_stats >= 6 and old_events >= 6:
        console.print("[bold green]→ PATH A viable: ?ids= embeds historical stats+events. "
                      "Refactor backfill_historical to batch-fetch via ids=.[/bold green]")
    elif old_stats >= 6 or old_events >= 6:
        console.print(f"[bold yellow]→ PARTIAL: stats_pop_old={old_stats}/10, events_pop_old={old_events}/10. "
                      "One endpoint embeds, the other doesn't. Hybrid path needed.[/bold yellow]")
    else:
        console.print(f"[bold red]→ PATH B only: ?ids= does NOT embed for old fixtures "
                      f"(stats_pop_old={old_stats}/10, events_pop_old={old_events}/10). "
                      "Keep per-fixture stats/events calls; just remove the league/season refetch.[/bold red]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
