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

SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"
HEALTH_PORT = int(os.getenv("PORT", "8080"))


# ── Job wrapper ────────────────────────────────────────────────────────────

def _job_prefix() -> str:
    return "railway_" if SHADOW_MODE else ""


def _run_job(name: str, fn, *args, **kwargs):
    """Wrapper that runs a job function with error isolation and logging."""
    full_name = f"{_job_prefix()}{name}"
    started = datetime.now(timezone.utc)
    console.print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
    console.print(f"[bold cyan]Job: {full_name} @ {started.strftime('%H:%M:%S UTC')}[/bold cyan]")
    console.print(f"[bold cyan]{'─' * 60}[/bold cyan]\n")

    try:
        fn(*args, **kwargs)
        status = "completed"
    except Exception as e:
        status = "failed"
        console.print(f"\n[red]Job {full_name} failed: {e}[/red]")

    with _last_job_lock:
        _last_job["name"] = full_name
        _last_job["completed_at"] = datetime.now(timezone.utc).isoformat()
        _last_job["status"] = status

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    console.print(f"\n[dim]Job {full_name} {status} in {elapsed:.1f}s[/dim]")


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

    # Step 1: Fixtures
    console.print("[cyan]Step 1/5: Fixtures[/cyan]")
    try:
        run_fixtures(target_date=today, refresh_leagues=is_monday)
    except Exception as e:
        console.print(f"[red]Fixtures failed: {e}[/red]")

    # Step 2: Enrichment (full)
    console.print("\n[cyan]Step 2/5: Enrichment (full)[/cyan]")
    try:
        run_enrichment(target_date=today)
    except Exception as e:
        console.print(f"[red]Enrichment failed: {e}[/red]")

    # Step 3: Odds
    console.print("\n[cyan]Step 3/5: Odds[/cyan]")
    try:
        run_odds(target_date=today)
    except Exception as e:
        console.print(f"[red]Odds failed: {e}[/red]")

    # Step 4: Predictions
    console.print("\n[cyan]Step 4/5: Predictions[/cyan]")
    try:
        run_predictions(target_date=today)
    except Exception as e:
        console.print(f"[red]Predictions failed: {e}[/red]")

    # Step 5: Betting
    console.print("\n[cyan]Step 5/5: Betting pipeline[/cyan]")
    try:
        run_betting()
    except Exception as e:
        console.print(f"[red]Betting failed: {e}[/red]")

    console.print(f"\n[bold green]Morning pipeline complete.[/bold green]")


def settlement_pipeline():
    """
    21:00 UTC — Settlement chain: results → ML ETL → prune → Platt (Sundays).
    """
    from workers.jobs.settlement import run_settlement, run_ml_etl

    # Step 1: Core settlement
    console.print("[cyan]Settlement step 1/4: Core settlement[/cyan]")
    try:
        run_settlement()
    except Exception as e:
        console.print(f"[red]Settlement failed: {e}[/red]")

    # Step 2: ML ETL
    console.print("\n[cyan]Settlement step 2/4: ML ETL[/cyan]")
    try:
        run_ml_etl()
    except Exception as e:
        console.print(f"[red]ML ETL failed: {e}[/red]")

    # Step 3: Prune odds snapshots
    console.print("\n[cyan]Settlement step 3/4: Prune odds snapshots[/cyan]")
    try:
        from scripts.prune_odds_snapshots import prune
        prune(dry_run=False)
    except Exception as e:
        console.print(f"[red]Prune failed: {e}[/red]")

    # Step 4: Platt recalibration (Sundays only)
    if date.today().weekday() == 6:  # Sunday
        console.print("\n[cyan]Settlement step 4/4: Platt recalibration (Sunday)[/cyan]")
        try:
            from scripts.fit_platt import fit_and_store
            fit_and_store()
        except Exception as e:
            console.print(f"[red]Platt recalibration failed: {e}[/red]")
    else:
        console.print(f"\n[dim]Settlement step 4/4: Platt — skipped (not Sunday)[/dim]")


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


def job_news_checker():
    from workers.jobs.news_checker import run_news_checker
    _run_job("news_checker", run_news_checker)


def job_settlement():
    _run_job("settlement", settlement_pipeline)


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

    # Start health endpoint
    _start_health_server()

    # Sync budget at startup
    try:
        from workers.api_clients.api_football import budget
        budget.sync_with_server()
    except Exception as e:
        console.print(f"[yellow]Initial budget sync failed: {e}[/yellow]")

    # Create scheduler
    scheduler = BackgroundScheduler(timezone="UTC")

    # ── Register all jobs ──────────────────────────────────────────────

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

    # News checker: 09:00, 12:30, 16:30, 19:30 UTC
    for hour, minute in [(9, 0), (12, 30), (16, 30), (19, 30)]:
        scheduler.add_job(job_news_checker, CronTrigger(hour=hour, minute=minute),
                          id=f"news_{hour:02d}{minute:02d}",
                          name=f"News {hour:02d}:{minute:02d}")

    # Settlement: 21:00 UTC
    scheduler.add_job(job_settlement, CronTrigger(hour=21, minute=0),
                      id="settlement", name="Settlement")

    # Live tracker: every 5 min, 12-22 UTC (Phase 1 — will be replaced by LivePoller)
    scheduler.add_job(job_live_tracker, CronTrigger(minute="*/5", hour="12-22"),
                      id="live_tracker", name="Live Tracker")

    # Budget sync: hourly
    scheduler.add_job(job_budget_sync, CronTrigger(minute=0),
                      id="budget_sync", name="Budget Sync")

    # ── Start scheduler ────────────────────────────────────────────────
    scheduler.start()

    jobs = scheduler.get_jobs()
    console.print(f"\n[green]{len(jobs)} jobs registered:[/green]")
    for job in sorted(jobs, key=lambda j: str(j.next_run_time)):
        next_run = job.next_run_time.strftime("%H:%M UTC") if job.next_run_time else "—"
        console.print(f"  [dim]{next_run}[/dim]  {job.name}")

    console.print(f"\n[bold green]Scheduler running. Waiting for jobs...[/bold green]\n")

    # Keep alive until shutdown
    try:
        while not _shutdown_requested:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass

    console.print("[yellow]Shutting down scheduler...[/yellow]")
    scheduler.shutdown(wait=True)
    console.print("[green]Scheduler stopped cleanly.[/green]")


if __name__ == "__main__":
    main()
