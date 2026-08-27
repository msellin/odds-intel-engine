"""
OddsIntel — Betting Pipeline (Phase 2)

Pure model + betting logic. Reads ALL data from DB — no external API calls.
Upstream jobs store everything before this runs at 06:00 UTC.

BOT-TIMING: Bots are split into 3 time-window cohorts to find the edge-maximizing
window. The cohort is determined by the current UTC hour:
  - morning  (06:00-10:59 UTC): early odds, full match slate
  - midday   (11:00-14:59 UTC): post-injury-news refresh
  - pre_ko   (15:00+     UTC): confirmed lineups, pre-kickoff
Each scheduler run (06:00, 11:00, 15:00, 19:00) only places bets for the
bots assigned to that cohort. See BOT_TIMING_COHORTS in daily_pipeline_v2.py.

Upstream jobs (must complete before this runs):
  - fetch_fixtures.py   (04:00 UTC) — stores matches
  - fetch_enrichment.py (04:15 UTC) — stores standings, H2H, injuries
  - fetch_odds.py       (05:00 UTC) — stores odds_snapshots
  - fetch_predictions.py(05:30 UTC) — stores predictions (source='af')

Schedule: 06:00 UTC daily (morning cohort)
Workflow: .github/workflows/betting.yml

Usage:
  python -m workers.jobs.betting_pipeline
  python -m workers.jobs.betting_pipeline report
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from workers.jobs.daily_pipeline_v2 import run_morning, run_report
from workers.utils.pipeline_utils import (
    log_pipeline_start, log_pipeline_complete,
    log_pipeline_failed,
)

console = Console()


def _current_cohort() -> str:
    """Determine which bot timing cohort is active based on current UTC hour."""
    hour = datetime.now(timezone.utc).hour
    if hour < 11:
        return "morning"
    elif hour < 15:
        return "midday"
    else:
        return "pre_ko"


def _run_coolbet_signal() -> None:
    """COOLBET-SIGNALER-A (2026-06-12): replaces the previous auto-placer
    call. The auto-place chain (Imperva 403 from the VPS IPs → FlareSolverr
    Chrome tab → 30-min JWT → SMS-2FA on re-login) burned the operator with
    100+ SMS overnight 2026-06-11 when the Chrome tab crashed. The signaler
    bypasses ALL of that — pure DB read + Telegram send, no Coolbet API.

    Operator gets a Telegram message per qualified pick with everything
    needed to place manually from their phone (~15 sec per bet). A future
    Mac-at-home daemon (option B) will consume the same qualified-bets
    queue and place from a residential IP; the signal remains as a safety
    net — even when auto-placement works, the operator still sees what
    fired.

    What this is NOT: it does not write to real_bets. Placement (manual
    or auto-via-Mac-daemon) is what creates the real_bets row. The signal
    is purely an outbound notification with dedup via simulated_bets.signaled_at."""
    from workers.automation.coolbet_signaler import signal_all_bets
    from workers.notify.telegram import send_telegram

    # Operator kill switch — same DB flag the old auto-placer respected.
    # /pause sets it; /resume clears. An OPERATOR-set pause silences
    # signaling too, so the operator can fully mute Coolbet output during
    # e.g. a personal break without env changes.
    #
    # SIGNAL-PAUSE-DECOUPLE (2026-08-27): a *daemon self-pause* is NOT an
    # operator decision — it means the Mac daemon hit a sustained Coolbet
    # outage and stopped placing. That is a placement-side problem, and it
    # must not mute notification. Before this fix the two shared one flag:
    # a daemon self-pause on 2026-08-23 03:53 UTC silenced every Telegram
    # signal — operator chat AND the public @oddsintelpicks channel, which
    # doesn't touch Coolbet at all — for 4 days and 12 picks. Nothing
    # errored; "0 signals" is indistinguishable from "no qualifying picks".
    # Signals are notification-only (no API calls, no real_bets writes), so
    # they are always safe to send while placement is down.
    try:
        from workers.automation.coolbet_state import (
            is_daemon_self_pause, is_placement_paused,
        )
        paused, reason = is_placement_paused()
    except Exception:
        paused, reason = (False, None)
        is_daemon_self_pause = lambda _r: False  # noqa: E731
    if paused and is_daemon_self_pause(reason):
        console.print(
            f"[yellow]Placement paused by daemon self-pause (reason: {reason}) "
            f"— signaling CONTINUES (notification-only, no Coolbet calls)[/yellow]"
        )
    elif paused:
        console.print(f"[yellow]Coolbet signaler SKIPPED — operator paused (reason: {reason or 'no reason given'})[/yellow]")
        return

    console.print("[bold cyan]Coolbet signaler (Telegram-only, no API calls)[/bold cyan]")

    try:
        results = signal_all_bets()
    except Exception as e:
        send_telegram(f"⚠️ Coolbet signaler failed: {e}", dedup_key="signaler-error")
        console.print(f"[red]Coolbet signaler failed: {e}[/red]")
        return

    if not results:
        return  # nothing qualified — pipeline already sent its summary

    sent = [r for r in results if r["outcome"] == "signaled"]
    skipped = [r for r in results if r["outcome"] == "skipped"]
    # Silent summary unless something didn't get through. Per-bet signals
    # already landed in the chat — no need to repeat the count loudly.
    if skipped:
        send_telegram(
            f"🤖 Coolbet signals: {len(sent)} sent · {len(skipped)} skipped (dedup or no TG creds)",
            silent=True,
        )


def run_betting(cohort: str | None = None):
    """
    Run the betting pipeline (Phase 2 — DB-only, no API calls).
    Reads matches, odds, and predictions stored by upstream jobs.

    cohort: 'morning', 'midday', or 'pre_ko'. Defaults to current time window.
    """
    from workers.utils.kill_switches import is_disabled
    if is_disabled("paper_betting"):
        return
    from datetime import date
    today_str = date.today().isoformat()

    active_cohort = cohort or _current_cohort()
    console.print(
        f"[bold green]═══ OddsIntel Betting Pipeline: {today_str} "
        f"[{active_cohort} cohort] ═══[/bold green]"
    )

    run_id = log_pipeline_start("betting_pipeline", today_str)

    try:
        # Phase 2: skip_fetch=True — upstream jobs already stored everything in DB
        run_morning(skip_fetch=True, cohort=active_cohort)

        log_pipeline_complete(run_id, metadata={"phase": 2, "skip_fetch": True, "cohort": active_cohort})
        console.print("\n[bold green]Betting pipeline complete.[/bold green]")

        _run_coolbet_signal()

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        console.print(f"\n[red]Betting pipeline failed: {e}[/red]")
        console.print(f"[red dim]{tb}[/red dim]")
        if run_id:
            # Store full traceback (not just str(e)) to help diagnose the VPS failures
            full_error = f"{type(e).__name__}: {e}\n\nTraceback:\n{tb}"
            log_pipeline_failed(run_id, full_error[:2000])
        raise


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        run_report()
    else:
        run_betting()


if __name__ == "__main__":
    main()
