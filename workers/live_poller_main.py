"""WORKER-SPLIT-LIVEPOLLER (2026-05-25) — standalone LivePoller entrypoint.

Currently LivePoller runs as a daemon thread inside `workers.scheduler.main()`,
so a crash in the poller takes the whole scheduler with it. To split it into
a separate systemd service:

  1. Add a second systemd service pointing at this entrypoint:
        python3 -m workers.live_poller_main
  2. On the SCHEDULER service, set `LIVE_POLLER_IN_SCHEDULER=false` so it
     doesn't double-run the poller.
  3. The new service gets its own crash-restart cycle; scheduler stays up.

This file is intentionally minimal — all the work is in `workers/live_poller.py`.
Run-forever loop + graceful shutdown via SIGTERM/SIGINT.

Tested locally via `python3 -m workers.live_poller_main` (Ctrl-C to stop).
"""
from __future__ import annotations

import os
import signal
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()
_shutdown_requested = False


def _handle_signal(signum, _frame):
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    console.print(f"\n[yellow]Received {sig_name} — shutting down LivePoller...[/yellow]")
    _shutdown_requested = True


def main():
    console.print(f"[bold green]LivePoller standalone — started {datetime.now(timezone.utc).isoformat()}[/bold green]")
    console.print(f"  PYTHONPATH={os.getenv('PYTHONPATH', '<unset>')}")
    console.print(f"  PID={os.getpid()}")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    from workers.live_poller import LivePoller
    from workers.api_clients.api_football import budget

    poller = LivePoller(
        budget_tracker=budget,
        shutdown_flag_fn=lambda: _shutdown_requested,
    )
    console.print(f"  Live={poller.FAST_INTERVAL}s · Idle={poller.IDLE_INTERVAL}s · "
                  f"Stats={poller.FAST_INTERVAL * poller.MEDIUM_MULTIPLIER}s · "
                  f"Lineups={poller.FAST_INTERVAL * poller.SLOW_MULTIPLIER}s\n")
    poller.run_forever()
    console.print("[green]LivePoller stopped cleanly.[/green]")
    sys.exit(0)


if __name__ == "__main__":
    main()
