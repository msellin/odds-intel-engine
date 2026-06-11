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


def _run_coolbet_record() -> None:
    """After the pipeline stores bets to simulated_bets, auto-run the Coolbet
    placer. Defaults to --record (paper). Set COOLBET_AUTO_EXECUTE=true in env
    to flip on real-money placement (first proven 2026-06-11 18:50 UTC with
    Mexico vs South Africa — ticket 26061118-8cbc-45c0-a728-9a99fe9c0d35,
    €2.64 stake, the chain works end-to-end). The placer's existing
    _MIN_REMAINING_EDGE gate (live-edge ≥ 3% at placement price) is the
    real safety here — combined with per-market edge floors set in code
    (_MIN_EDGE_BY_MARKET).

    Edits each per-bet alert in place with the outcome (✓ recorded /
    ✗ no_event / etc.) so the admin scrolling the chat sees status per
    bet at a glance (ADMIN-TG-CLARITY 2026-05-29). Summary collapses to
    a single counter line — silent when everything placed cleanly, loud
    only on search_blocked."""
    from workers.automation.coolbet_placer import place_all_bets
    from workers.notify.telegram import send_telegram, edit_bet_alert_outcome

    # Env-driven execute mode. Default false (record-only) until operator
    # explicitly enables real-money placement on the Railway instance.
    # When true, the Coolbet API receives the actual POST /s/bets/bets.
    execute_mode = os.getenv("COOLBET_AUTO_EXECUTE", "false").lower() in ("true", "1", "yes")
    mode_label = "EXECUTE" if execute_mode else "record"
    console.print(f"[bold cyan]Coolbet auto-placer ({mode_label} mode)[/bold cyan]")

    try:
        # record=True always (so paper rows land in real_bets either way).
        # execute=True is the real-money switch.
        results = place_all_bets(record=True, execute=execute_mode)
    except Exception as e:
        send_telegram(f"⚠️ Coolbet --{mode_label.lower()} auto-run failed: {e}")
        console.print(f"[red]Coolbet auto-placer ({mode_label}) failed: {e}[/red]")
        return

    if not results:
        return  # nothing qualified — pipeline already sent "0 new value bets"

    placed    = [r for r in results if r["outcome"] == "placed"]
    no_event  = [r for r in results if r["outcome"] == "no_event"]
    no_market = [r for r in results if r["outcome"] == "no_market"]
    blocked   = [r for r in results if r["outcome"] == "search_blocked"]
    other     = [r for r in results if r["outcome"] not in ("placed", "no_event", "no_market", "search_blocked")]

    # ADMIN-TG-CLARITY: edit each per-bet alert with its outcome. Done per
    # result, not per (match,market,selection), so combos + singles both flow
    # through naturally.
    for r in results:
        sim_id = str(r.get("simulated_bet_id") or "")
        if not sim_id:
            continue
        outcome = r.get("outcome") or "error"
        if outcome == "placed":
            stake = float(r.get("stake") or 0)
            odds = float(r.get("live_odds") or r.get("model_odds") or 0)
            # Distinguish paper-trade from real-money in the Telegram status
            # line so the operator can see at a glance which mode it was.
            verb = "Placed" if execute_mode else "Auto-recorded"
            status = f"✓ {verb} €{stake:.2f} @ {odds:.2f}"
        elif outcome == "no_event":
            status = "✗ no_event (Coolbet didn't list this match)"
        elif outcome == "no_market":
            reason = r.get("reason") or ""
            status = f"✗ no_market{f' — {reason}' if reason else ''}"[:200]
        elif outcome == "search_blocked":
            status = "⚠️ search_blocked (Imperva — cookies?)"
        elif outcome == "edge_eroded":
            live_odds = float(r.get("live_odds") or 0)
            status = f"✗ edge_eroded (live odds {live_odds:.2f})"
        elif outcome == "guard_skip":
            status = f"✗ guard_skip — {r.get('reason') or ''}"[:200]
        elif outcome == "dry_run":
            continue  # nothing to update in dry mode
        else:
            status = f"✗ {outcome}"
        edit_bet_alert_outcome(sim_id, status)

    # Compact admin summary — one line of counters. Silent unless something's blocked.
    parts = [f"🤖 Coolbet: {len(placed)} placed"]
    if no_event:  parts.append(f"{len(no_event)} no_event")
    if no_market: parts.append(f"{len(no_market)} no_market")
    if other:     parts.append(f"{len(other)} skipped")
    if blocked:   parts.append(f"⚠️ {len(blocked)} search_blocked")
    send_telegram(" · ".join(parts), silent=not blocked)


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

        _run_coolbet_record()

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        console.print(f"\n[red]Betting pipeline failed: {e}[/red]")
        console.print(f"[red dim]{tb}[/red dim]")
        if run_id:
            # Store full traceback (not just str(e)) to help diagnose Railway failures
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
