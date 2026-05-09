"""
Finish the historical backfill in one shot.

Loops phase 1 → 2 → 3 (skipping any already-complete phase) and keeps calling
`run_backfill` until every league/season is at-tolerance. Stops early on
shutdown signal, budget exhaustion, or when no phase has incomplete work.

Usage:
    python3 scripts/finish_backfill.py
    python3 scripts/finish_backfill.py --batch-size 1000 --max-requests 3000
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from scripts.backfill_historical import (
    detect_next_phase,
    get_remaining_requests,
    run_backfill,
    MIN_BUDGET_TO_START,
)
import scripts.backfill_historical as bh

console = Console()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=1000,
                        help="Per-L/S fixture cap (raise for huge leagues like Indian I-League).")
    parser.add_argument("--max-requests", type=int, default=2000,
                        help="AF call budget per inner pass.")
    parser.add_argument("--max-passes", type=int, default=10,
                        help="Hard cap on outer-loop iterations (safety belt).")
    args = parser.parse_args()

    pass_no = 0
    while pass_no < args.max_passes:
        if bh._shutdown_requested:
            console.print("[yellow]Shutdown requested — stopping outer loop[/yellow]")
            break

        phase = detect_next_phase()
        if phase is None:
            console.print("\n[bold green]All phases complete — backfill finished ✓[/bold green]")
            return

        budget = get_remaining_requests()
        console.print(
            f"\n[cyan]Pass {pass_no + 1}: phase {phase} — "
            f"AF budget {budget['remaining']:,} remaining[/cyan]"
        )
        if budget["remaining"] < MIN_BUDGET_TO_START:
            console.print(
                f"[red]Budget {budget['remaining']:,} < min {MIN_BUDGET_TO_START:,} — "
                "stop, retry tomorrow.[/red]"
            )
            return

        run_backfill(
            phase=phase,
            batch_size=args.batch_size,
            max_requests=args.max_requests,
            skip_existing=True,
            dry_run=False,
        )
        pass_no += 1
        time.sleep(2)  # gentle gap so AF rate-limit headers settle

    console.print(
        f"[yellow]Hit max-passes={args.max_passes} — re-run if more work remains.[/yellow]"
    )


if __name__ == "__main__":
    main()
