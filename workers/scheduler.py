"""
OddsIntel — Railway Scheduler

Long-running process that replaces GitHub Actions cron scheduling.
Uses APScheduler for timed jobs + a health endpoint for Railway.

Run: python -m workers.scheduler
"""

import os
import sys
import json
import signal
import threading
import time
from datetime import date, datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from rich.console import Console

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

console = Console()

# ── Globals ────────────────────────────────────────────────────────────────
_shutdown_requested = False
_start_time = time.time()
_last_job: dict = {"name": None, "completed_at": None, "status": None}
_last_job_lock = threading.Lock()
_recent_errors: list[dict] = []  # Last N job errors for health endpoint
_MAX_RECENT_ERRORS = 20

SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"
HEALTH_PORT = int(os.getenv("PORT", "8080"))


# ── Job wrapper ────────────────────────────────────────────────────────────

def _job_prefix() -> str:
    return "railway_" if SHADOW_MODE else ""


def _run_job(name: str, fn, *args, **kwargs):
    """Wrapper that runs a job function with error isolation and logging."""
    import traceback
    full_name = f"{_job_prefix()}{name}"
    started = datetime.now(timezone.utc)
    console.print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
    console.print(f"[bold cyan]Job: {full_name} @ {started.strftime('%H:%M:%S UTC')}[/bold cyan]")
    console.print(f"[bold cyan]{'─' * 60}[/bold cyan]\n")

    error_msg = None
    try:
        fn(*args, **kwargs)
        status = "completed"
    except Exception as e:
        status = "failed"
        error_msg = str(e)
        tb = traceback.format_exc()
        console.print(f"\n[red]{'═' * 60}[/red]")
        console.print(f"[red bold]JOB FAILED: {full_name}[/red bold]")
        console.print(f"[red]Error: {e}[/red]")
        console.print(f"[red dim]{tb}[/red dim]")
        console.print(f"[red]{'═' * 60}[/red]")

        # Track recent errors for health endpoint
        _recent_errors.append({
            "job": full_name,
            "error": error_msg[:500],
            "at": datetime.now(timezone.utc).isoformat(),
        })
        if len(_recent_errors) > _MAX_RECENT_ERRORS:
            _recent_errors.pop(0)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    with _last_job_lock:
        _last_job["name"] = full_name
        _last_job["completed_at"] = datetime.now(timezone.utc).isoformat()
        _last_job["status"] = status
        _last_job["elapsed_seconds"] = round(elapsed, 1)
        if error_msg:
            _last_job["error"] = error_msg[:500]
        else:
            _last_job.pop("error", None)

    status_color = "green" if status == "completed" else "red"
    console.print(f"\n[{status_color}]Job {full_name} {status} in {elapsed:.1f}s[/{status_color}]")


# ── Pipeline chains ────────────────────────────────────────────────────────

def morning_pipeline():
    """
    04:00 UTC — Sequential chain replacing GH Actions timing gaps.
    Each step has error isolation so one failure doesn't block the rest.
    """
    from workers.jobs.fetch_fixtures import run_fixtures
    from workers.jobs.fetch_enrichment import run_enrichment
    from workers.jobs.fetch_odds import run_odds
    from workers.jobs.fetch_predictions import run_predictions
    from workers.jobs.betting_pipeline import run_betting

    today = date.today().isoformat()
    is_monday = date.today().weekday() == 0

    console.print(f"[bold green]═══ Morning Pipeline: {today} ═══[/bold green]\n")

    import traceback
    steps = [
        ("1/5", "Fixtures",    lambda: run_fixtures(target_date=today, refresh_leagues=is_monday)),
        ("2/5", "Enrichment",  lambda: run_enrichment(target_date=today)),
        ("3/5", "Odds",        lambda: run_odds(target_date=today)),
        ("4/5", "Predictions", lambda: run_predictions(target_date=today)),
        ("5/5", "Betting",     lambda: run_betting()),
    ]

    failed_steps = []
    for step_num, step_name, step_fn in steps:
        console.print(f"\n[cyan]Step {step_num}: {step_name}[/cyan]")
        step_start = datetime.now(timezone.utc)
        try:
            step_fn()
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            console.print(f"[green]  ✓ {step_name} completed in {elapsed:.1f}s[/green]")
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            failed_steps.append(step_name)
            console.print(f"[red]  ✗ {step_name} FAILED after {elapsed:.1f}s: {e}[/red]")
            console.print(f"[red dim]{traceback.format_exc()}[/red dim]")

    if failed_steps:
        console.print(f"\n[red bold]Morning pipeline finished with {len(failed_steps)} failure(s): {', '.join(failed_steps)}[/red bold]")
    else:
        console.print(f"\n[bold green]Morning pipeline complete — all 5 steps succeeded.[/bold green]")


def settlement_pipeline():
    """
    21:00 UTC — Settlement chain: results → ML ETL → prune → Platt (Sundays).
    """
    import traceback
    from workers.jobs.settlement import run_settlement, run_ml_etl

    steps = [
        ("1/4", "Core settlement", lambda: run_settlement()),
        ("2/4", "ML ETL",          lambda: run_ml_etl()),
        ("3/4", "Prune odds",      lambda: __import__('scripts.prune_odds_snapshots', fromlist=['prune']).prune(dry_run=False)),
    ]

    # Step 4 only on Sundays
    if date.today().weekday() == 6:
        steps.append(("4/4", "Platt recalibration", lambda: __import__('scripts.fit_platt', fromlist=['fit_and_store']).fit_and_store()))
    else:
        console.print(f"[dim]Settlement step 4/4: Platt — skipped (not Sunday)[/dim]")

    failed_steps = []
    for step_num, step_name, step_fn in steps:
        console.print(f"\n[cyan]Settlement step {step_num}: {step_name}[/cyan]")
        step_start = datetime.now(timezone.utc)
        try:
            step_fn()
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            console.print(f"[green]  ✓ {step_name} completed in {elapsed:.1f}s[/green]")
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            failed_steps.append(step_name)
            console.print(f"[red]  ✗ {step_name} FAILED after {elapsed:.1f}s: {e}[/red]")
            console.print(f"[red dim]{traceback.format_exc()}[/red dim]")

    if failed_steps:
        console.print(f"\n[red bold]Settlement finished with {len(failed_steps)} failure(s): {', '.join(failed_steps)}[/red bold]")
    else:
        console.print(f"\n[bold green]Settlement complete — all steps succeeded.[/bold green]")


# ── Individual job wrappers ────────────────────────────────────────────────

def job_morning():
    _run_job("morning_pipeline", morning_pipeline)


def job_odds_refresh():
    from workers.jobs.fetch_odds import run_odds
    _run_job("odds_refresh", run_odds)


def job_odds_pre_kickoff():
    from workers.jobs.fetch_odds import run_odds
    _run_job("odds_pre_kickoff", run_odds, mark_closing=True)


def job_enrichment_refresh():
    from workers.jobs.fetch_enrichment import run_enrichment
    _run_job("enrichment_refresh", run_enrichment,
             components={"injuries", "standings"})


def job_betting_refresh_wrapper():
    _run_job("betting_refresh", job_betting_refresh)


def job_betting_refresh():
    """Pre-kickoff betting re-evaluation — re-run predictions + betting with fresher data."""
    from workers.jobs.fetch_predictions import run_predictions
    from workers.jobs.betting_pipeline import run_betting
    import traceback

    today = date.today().isoformat()
    console.print(f"[bold cyan]Pre-KO Betting Refresh: {today}[/bold cyan]")

    try:
        run_predictions(target_date=today)
    except Exception as e:
        console.print(f"[red]Predictions refresh failed: {e}[/red]")
        console.print(f"[red dim]{traceback.format_exc()}[/red dim]")

    try:
        run_betting()
    except Exception as e:
        console.print(f"[red]Betting refresh failed: {e}[/red]")
        console.print(f"[red dim]{traceback.format_exc()}[/red dim]")


def job_news_checker():
    from workers.jobs.news_checker import run_news_checker
    _run_job("news_checker", run_news_checker)


def job_match_previews():
    from workers.jobs.match_previews import run_match_previews
    _run_job("match_previews", run_match_previews)


def job_email_digest():
    from workers.jobs.email_digest import run_email_digest
    _run_job("email_digest", run_email_digest)


def job_settlement():
    _run_job("settlement", settlement_pipeline)


def job_settle_ready():
    """15-min sweep: settle any finished match not yet marked done."""
    from workers.jobs.settlement import settle_ready_matches
    _run_job("settle_ready", settle_ready_matches)


def job_backfill():
    """Daily historical backfill — 02:00 UTC, skips once flag file exists."""
    from scripts.backfill_historical import run_backfill
    _run_job("hist_backfill", run_backfill, phase=1)


def job_live_tracker():
    from workers.jobs.live_tracker import run_live_tracker
    _run_job("live_tracker", run_live_tracker)


def job_budget_sync():
    """Hourly budget sync with AF /status endpoint."""
    from workers.api_clients.api_football import budget
    budget.sync_with_server()


# ── Health endpoint ────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            from workers.api_clients.api_football import budget

            with _last_job_lock:
                last = dict(_last_job)

            body = json.dumps({
                "status": "ok",
                "uptime_seconds": int(time.time() - _start_time),
                "shadow_mode": SHADOW_MODE,
                "api_budget": budget.status(),
                "last_job": last,
                "recent_errors": list(_recent_errors),
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress access logs


def _start_health_server():
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    console.print(f"[dim]Health endpoint listening on :{HEALTH_PORT}/health[/dim]")
    return server


# ── Signal handling ────────────────────────────────────────────────────────

def _handle_signal(signum, frame):
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    console.print(f"\n[yellow]{sig_name} received — shutting down gracefully...[/yellow]")
    _shutdown_requested = True


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    global _shutdown_requested

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    console.print("[bold green]═══════════════════════════════════════════════[/bold green]")
    console.print("[bold green]   OddsIntel Railway Scheduler starting...    [/bold green]")
    console.print("[bold green]═══════════════════════════════════════════════[/bold green]")
    if SHADOW_MODE:
        console.print("[yellow]SHADOW MODE: job names prefixed with 'railway_'[/yellow]")

    # Start health endpoint FIRST — must respond before Railway's health check window
    _start_health_server()

    # Sync budget in background (API call can take 2-5s, don't block startup)
    def _initial_budget_sync():
        try:
            from workers.api_clients.api_football import budget
            budget.sync_with_server()
        except Exception as e:
            console.print(f"[yellow]Initial budget sync failed: {e}[/yellow]")

    threading.Thread(target=_initial_budget_sync, daemon=True).start()

    # Create scheduler
    scheduler = BackgroundScheduler(timezone="UTC")

    # ── Register all jobs ──────────────────────────────────────────────

    # Historical backfill: 02:00 UTC daily (self-terminates once complete)
    scheduler.add_job(job_backfill, CronTrigger(hour=2, minute=0),
                      id="hist_backfill", name="Historical Backfill 02:00")

    # Morning pipeline: 04:00 UTC
    scheduler.add_job(job_morning, CronTrigger(hour=4, minute=0),
                      id="morning_pipeline", name="Morning Pipeline")

    # Odds refresh: every 2h during 07-22 UTC
    for hour in [7, 8, 10, 12, 14, 16, 18, 20, 22]:
        scheduler.add_job(job_odds_refresh, CronTrigger(hour=hour, minute=0),
                          id=f"odds_{hour:02d}", name=f"Odds {hour:02d}:00")

    # Odds pre-kickoff: 13:30, 17:30 UTC
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=13, minute=30),
                      id="odds_prekick_1330", name="Odds Pre-KO 13:30")
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=17, minute=30),
                      id="odds_prekick_1730", name="Odds Pre-KO 17:30")

    # Enrichment refresh: 12:00, 16:00 UTC
    scheduler.add_job(job_enrichment_refresh, CronTrigger(hour=12, minute=0),
                      id="enrichment_12", name="Enrichment 12:00")
    scheduler.add_job(job_enrichment_refresh, CronTrigger(hour=16, minute=0),
                      id="enrichment_16", name="Enrichment 16:00")

    # Pre-kickoff betting refresh: 11:00, 15:00, 19:00 UTC
    # Re-evaluates predictions + bets with fresher odds, lineups, and news
    for hour in [11, 15, 19]:
        scheduler.add_job(job_betting_refresh_wrapper, CronTrigger(hour=hour, minute=0),
                          id=f"betting_refresh_{hour:02d}",
                          name=f"Betting Refresh {hour:02d}:00")

    # News checker: 09:00, 12:30, 16:30, 19:30 UTC
    for hour, minute in [(9, 0), (12, 30), (16, 30), (19, 30)]:
        scheduler.add_job(job_news_checker, CronTrigger(hour=hour, minute=minute),
                          id=f"news_{hour:02d}{minute:02d}",
                          name=f"News {hour:02d}:{minute:02d}")

    # ENG-3: AI match previews — 07:00 UTC (after morning pipeline settles)
    scheduler.add_job(job_match_previews, CronTrigger(hour=7, minute=0),
                      id="match_previews", name="Match Previews 07:00")

    # ENG-4: Email digest — 07:30 UTC (after previews are generated)
    scheduler.add_job(job_email_digest, CronTrigger(hour=7, minute=30),
                      id="email_digest", name="Email Digest 07:30")

    # Settlement: 21:00 + 23:30 UTC (late-finishing European matches)
    scheduler.add_job(job_settlement, CronTrigger(hour=21, minute=0),
                      id="settlement", name="Settlement 21:00")
    scheduler.add_job(job_settlement, CronTrigger(hour=23, minute=30),
                      id="settlement_late", name="Settlement 23:30")

    # Settle-ready sweep: every 15 min, all day.
    # Catches matches the live poller missed (outside 10-23 UTC window, or if it errored).
    # Idempotent — skips matches already marked 'done'.
    scheduler.add_job(job_settle_ready, CronTrigger(minute="*/15"),
                      id="settle_ready", name="Settle-Ready Sweep (15min)")

    # Budget sync: hourly
    scheduler.add_job(job_budget_sync, CronTrigger(minute=0),
                      id="budget_sync", name="Budget Sync")

    # ── Start scheduler ────────────────────────────────────────────────
    scheduler.start()

    jobs = scheduler.get_jobs()
    console.print(f"\n[green]{len(jobs)} scheduled jobs registered:[/green]")
    for job in sorted(jobs, key=lambda j: str(j.next_run_time)):
        next_run = job.next_run_time.strftime("%H:%M UTC") if job.next_run_time else "—"
        console.print(f"  [dim]{next_run}[/dim]  {job.name}")

    # ── Start LivePoller in background thread ──────────────────────────
    from workers.live_poller import LivePoller
    from workers.api_clients.api_football import budget

    poller = LivePoller(
        budget_tracker=budget,
        shutdown_flag_fn=lambda: _shutdown_requested,
    )
    poller_thread = threading.Thread(target=poller.run_forever, daemon=True, name="live-poller")
    poller_thread.start()

    console.print(f"\n[bold green]Scheduler + LivePoller running. "
                  f"Fast={poller.FAST_INTERVAL}s, "
                  f"Medium={poller.FAST_INTERVAL * poller.MEDIUM_MULTIPLIER}s, "
                  f"Slow={poller.FAST_INTERVAL * poller.SLOW_MULTIPLIER}s[/bold green]\n")

    # Keep alive until shutdown
    try:
        while not _shutdown_requested:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass

    console.print("[yellow]Shutting down scheduler + poller...[/yellow]")
    scheduler.shutdown(wait=True)
    console.print("[green]Scheduler stopped cleanly.[/green]")


if __name__ == "__main__":
    main()
