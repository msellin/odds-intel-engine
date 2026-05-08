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
        console.print("\n[bold green]Morning pipeline complete — all 5 steps succeeded.[/bold green]")


def settlement_pipeline():
    """
    21:00 UTC — Settlement chain: results → ML ETL → prune → Platt (Wed+Sun) → DC rho (Sun).
    """
    import traceback
    from workers.jobs.settlement import run_settlement, run_ml_etl

    steps = [
        ("1/3", "Core settlement", lambda: run_settlement()),
        ("2/3", "ML ETL",          lambda: run_ml_etl()),
        ("3/3", "Prune odds",      lambda: __import__('scripts.prune_odds_snapshots', fromlist=['prune']).prune(dry_run=False)),
    ]

    is_refit_day = date.today().weekday() in (2, 6)  # Wednesday + Sunday
    is_sunday    = date.today().weekday() == 6        # Sunday only

    # Platt recalibration + blend weight refit: Wednesday + Sunday
    if is_refit_day:
        steps.append(("4+", "Platt recalibration", lambda: __import__('scripts.fit_platt', fromlist=['fit_and_store']).fit_and_store()))
        steps.append(("5+", "Blend weight refit",  lambda: __import__('scripts.fit_blend_weights', fromlist=['run']).run()))
    # DC rho refit: Sunday only (more data-intensive)
    if is_sunday:
        steps.append(("6+", "DC rho per tier",     lambda: __import__('scripts.fit_league_rho', fromlist=['run']).run()))
    if not is_refit_day:
        console.print("[dim]Settlement steps 4-6: Platt + blend weight + DC rho — skipped (not Wednesday or Sunday)[/dim]")

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
        console.print("\n[bold green]Settlement complete — all steps succeeded.[/bold green]")


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


def job_enrichment_full():
    """13:00 UTC full enrichment — all 4 components (standings, H2H, team_stats, injuries).
    Ensures H2H + team_stats are fresh for afternoon/evening betting refreshes (N7 fix).
    """
    from workers.jobs.fetch_enrichment import run_enrichment
    _run_job("enrichment_full", run_enrichment)  # no components= filter → all 4


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


def job_weekly_digest():
    from workers.jobs.weekly_digest import run_weekly_digest
    _run_job("weekly_digest", run_weekly_digest)


def job_watchlist_alerts():
    from workers.jobs.watchlist_alerts import run_watchlist_alerts
    _run_job("watchlist_alerts", run_watchlist_alerts)


def job_value_bet_alert_afternoon():
    from workers.jobs.email_digest import run_value_bet_alert
    _run_job("value_bet_alert_afternoon", lambda: run_value_bet_alert("afternoon"))


def job_value_bet_alert_evening():
    from workers.jobs.email_digest import run_value_bet_alert
    _run_job("value_bet_alert_evening", lambda: run_value_bet_alert("evening"))


def job_settlement():
    _run_job("settlement", settlement_pipeline)


def job_settle_ready():
    """15-min sweep: settle any finished match not yet marked done."""
    from workers.jobs.settlement import settle_ready_matches
    _run_job("settle_ready", settle_ready_matches)


def job_fixture_refresh():
    """Mid-day fixture status refresh — catches postponements/cancellations/time changes.

    Runs 4× daily, 15 min before each betting window. Re-fetches today's fixtures
    from AF and updates any status changes (PST/CANC → 'postponed') in the DB.
    Prevents the betting pipeline from placing bets on postponed matches.
    """
    from workers.jobs.fetch_fixtures import run_fixtures
    _run_job("fixture_refresh", run_fixtures)


def job_backfill():
    """Historical backfill — every 2h, 500 requests/run (~3 min each), self-terminates once done.
    75K AF requests/day available; 12 runs × 500 = 6K/day leaves 67K headroom for live ops."""
    from scripts.backfill_historical import run_backfill
    _run_job("hist_backfill", run_backfill, max_requests=500)  # phase=None → auto-detect next phase


def job_live_tracker():
    from workers.jobs.live_tracker import run_live_tracker
    _run_job("live_tracker", run_live_tracker)


def job_budget_sync():
    """Hourly budget sync with AF /status endpoint."""
    from workers.api_clients.api_football import budget
    _run_job("budget_sync", budget.sync_with_server)


def job_ops_snapshot():
    """Hourly fallback ops snapshot — captures state if no pipeline ran this hour.

    Wrapped in _run_job so failures surface on the /health endpoint and console
    instead of being lost. write_ops_snapshot also logs its own pipeline_runs row.
    """
    from workers.api_clients.supabase_client import write_ops_snapshot
    _run_job("ops_snapshot_fallback", write_ops_snapshot)


def job_stripe_reconcile():
    """Daily Stripe event reconciliation — checks yesterday's events vs processed_events table."""
    from scripts.stripe_reconcile import run as stripe_reconcile_run
    from datetime import date, timedelta
    _run_job("stripe_reconcile", lambda: stripe_reconcile_run(date.today() - timedelta(days=1)))


def job_settle_reconcile():
    """MONEY-SETTLE-RECON: check finished matches have no stuck pending bets after settlement."""
    from scripts.settle_reconcile import run as settle_reconcile_run
    _run_job("settle_reconcile", settle_reconcile_run)


def job_health_alerts_morning():
    from workers.jobs.health_alerts import run_morning_checks
    _run_job("health_alerts_morning", run_morning_checks)


def job_health_alerts_snapshot():
    from workers.jobs.health_alerts import run_snapshot_check
    # Not via _run_job — this runs every hour and is very lightweight.
    # Errors are caught inside run_snapshot_check already.
    try:
        run_snapshot_check()
    except Exception as e:
        console.print(f"[yellow]health_alerts snapshot check error: {e}[/yellow]")


def job_health_alerts_settlement():
    from workers.jobs.health_alerts import run_settlement_check
    _run_job("health_alerts_settlement", run_settlement_check)


def job_cleanup_orphaned_runs():
    """Every 30 min: mark pipeline_runs stuck in 'running' >60 min as failed.

    Catches records that were <10 min old at scheduler restart and slipped past
    the startup cleanup. 60-min threshold is generous enough to allow legitimate
    long-running jobs (enrichment, settlement) to finish.
    """
    _cleanup_stale_runs(threshold_minutes=60, label="orphaned (periodic cleanup)")


def job_healthcheck_ping():
    """OBS-HEARTBEAT: Ping healthchecks.io every 5 min to confirm scheduler is alive.
    Set HEALTHCHECKS_IO_PING_URL in Railway env vars after creating a check at healthchecks.io.
    No-op if the env var is not set.
    """
    ping_url = os.getenv("HEALTHCHECKS_IO_PING_URL", "")
    if not ping_url:
        return
    try:
        import urllib.request
        urllib.request.urlopen(ping_url, timeout=10)
    except Exception as e:
        console.print(f"[yellow]Healthcheck ping failed: {e}[/yellow]")


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

def _cleanup_stale_runs(threshold_minutes: int = 10, label: str = "scheduler restarted"):
    """Mark orphaned 'running' records as failed — called on startup and periodically."""
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            f"""UPDATE pipeline_runs
               SET status = 'failed',
                   completed_at = NOW(),
                   error_message = 'killed — {label}'
               WHERE status = 'running'
                 AND started_at < NOW() - INTERVAL '{threshold_minutes} minutes'""",
            []
        )
        console.print(f"[cyan]Orphan cleanup ({label}): marked stale running jobs as failed[/cyan]")
    except Exception as e:
        console.print(f"[yellow]Orphan cleanup failed (non-fatal): {e}[/yellow]")


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

    # Clean up orphaned "running" records from previous process (Railway kill/restart)
    # 10-min threshold catches jobs that were <30 min old under the old logic
    _cleanup_stale_runs(threshold_minutes=10, label="scheduler restarted")

    # Sync budget in background (API call can take 2-5s, don't block startup)
    def _initial_budget_sync():
        try:
            from workers.api_clients.api_football import budget
            budget.sync_with_server(source="startup")
        except Exception as e:
            console.print(f"[yellow]Initial budget sync failed: {e}[/yellow]")

    threading.Thread(target=_initial_budget_sync, daemon=True).start()

    # Create scheduler — coalesce + max_instances=1 prevent overlapping/stacked runs
    scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    # ── Register all jobs ──────────────────────────────────────────────

    # Historical backfill: every 2h, 500 requests/run (~3 min, 12 runs/day)
    # AF budget: 75K/day, live ops ~8K, backfill uses 6K — leaves 61K headroom.
    for _bh_hour in range(0, 24, 2):
        scheduler.add_job(job_backfill, CronTrigger(hour=_bh_hour, minute=30),
                          id=f"hist_backfill_{_bh_hour:02d}",
                          name=f"Historical Backfill {_bh_hour:02d}:30")

    # Fixture status refresh: 4× daily, 15 min before each betting window
    # Re-fetches today's fixtures to catch postponements/cancellations/time changes.
    # store_match() now updates status → 'postponed' for PST/CANC matches.
    for hour, minute in [(9, 15), (10, 45), (14, 45), (18, 45)]:
        scheduler.add_job(job_fixture_refresh, CronTrigger(hour=hour, minute=minute),
                          id=f"fixture_refresh_{hour:02d}{minute:02d}",
                          name=f"Fixture Refresh {hour:02d}:{minute:02d}")

    # Morning pipeline: 04:00 UTC
    scheduler.add_job(job_morning, CronTrigger(hour=4, minute=0),
                      id="morning_pipeline", name="Morning Pipeline")

    # Odds refresh: every 30min during 07-22 UTC
    # 20:00 replaced by pre-KO mark_closing run (marks closing odds for evening KOs)
    for hour in range(7, 23):
        for minute in [0, 30]:
            if hour == 20 and minute == 0:
                continue  # 20:00 is handled by pre-KO mark_closing below
            scheduler.add_job(job_odds_refresh, CronTrigger(hour=hour, minute=minute),
                              id=f"odds_{hour:02d}{minute:02d}", name=f"Odds {hour:02d}:{minute:02d}")

    # Odds pre-kickoff (mark_closing): 13:30, 17:30, 20:00 UTC
    # 20:00 covers 19:00-21:00 KO window (replaces regular 20:00 refresh — marks CLV closing line)
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=13, minute=30),
                      id="odds_prekick_1330", name="Odds Pre-KO 13:30")
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=17, minute=30),
                      id="odds_prekick_1730", name="Odds Pre-KO 17:30")
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=20, minute=0),
                      id="odds_prekick_2000", name="Odds Pre-KO 20:00")

    # Enrichment refresh: 10:30, 16:00 UTC (injuries + standings)
    # 10:30 moved from 12:00 so injury data is fresh before the 11:00 betting refresh
    scheduler.add_job(job_enrichment_refresh, CronTrigger(hour=10, minute=30),
                      id="enrichment_1030", name="Enrichment 10:30")
    scheduler.add_job(job_enrichment_refresh, CronTrigger(hour=16, minute=0),
                      id="enrichment_16", name="Enrichment 16:00")

    # Full enrichment: 13:00 UTC — all 4 components (standings, H2H, team_stats, injuries)
    # N7 fix: H2H + team_stats were only fetched in morning pipeline; this refresh
    # ensures afternoon/evening betting runs have up-to-date context.
    scheduler.add_job(job_enrichment_full, CronTrigger(hour=13, minute=0),
                      id="enrichment_full_13", name="Enrichment Full 13:00")

    # Betting refreshes: 09:30, 11:00, 15:00, 19:00, 20:30 UTC
    # 09:30 — acts on 08:00 odds + 09:00 news; catches Asian KO window
    # 11:00 — European morning KOs; uses fresh 10:30 enrichment
    # 15:00 — European afternoon KOs
    # 19:00 — European early evening KOs; uses fresh 18:30 news
    # 20:30 — European prime-time KOs (19:00-21:00); uses 20:00 closing odds
    for hour, minute in [(9, 30), (11, 0), (15, 0), (19, 0), (20, 30)]:
        scheduler.add_job(job_betting_refresh_wrapper, CronTrigger(hour=hour, minute=minute),
                          id=f"betting_refresh_{hour:02d}{minute:02d}",
                          name=f"Betting Refresh {hour:02d}:{minute:02d}")

    # News checker: 09:00, 12:30, 14:30, 16:30, 18:30 UTC
    # 14:30 added — feeds 15:00 betting (was 2.5h stale)
    # 18:30 replaces 19:30 — now feeds 19:00 + 20:30 betting instead of neither
    for hour, minute in [(9, 0), (12, 30), (14, 30), (16, 30), (18, 30)]:
        scheduler.add_job(job_news_checker, CronTrigger(hour=hour, minute=minute),
                          id=f"news_{hour:02d}{minute:02d}",
                          name=f"News {hour:02d}:{minute:02d}")

    # ENG-3: AI match previews — 07:15 UTC (after morning pipeline + 07:00 odds refresh settle)
    scheduler.add_job(job_match_previews, CronTrigger(hour=7, minute=15),
                      id="match_previews", name="Match Previews 07:15")

    # ENG-4: Email digest — 07:30 UTC (after previews are generated)
    scheduler.add_job(job_email_digest, CronTrigger(hour=7, minute=30),
                      id="email_digest", name="Email Digest 07:30")

    # N5: Value bet alerts — 16:00 (afternoon) + 20:45 (evening) UTC — Pro/Elite only
    # Afternoon: catches 11:00 + 15:00 betting refresh bets (since 10:00 UTC)
    # Evening:   catches 19:00 + 20:30 betting refresh bets (since 17:00 UTC)
    # No-op if no new bets exist. Deduped per slot via value_bet_alert_log.
    scheduler.add_job(job_value_bet_alert_afternoon, CronTrigger(hour=16, minute=0),
                      id="value_bet_alert_afternoon", name="Value Bet Alert Afternoon 16:00")
    scheduler.add_job(job_value_bet_alert_evening, CronTrigger(hour=20, minute=45),
                      id="value_bet_alert_evening", name="Value Bet Alert Evening 20:45")

    # ENG-10: Weekly performance email — Monday 08:00 UTC
    scheduler.add_job(job_weekly_digest, CronTrigger(day_of_week="mon", hour=8, minute=0),
                      id="weekly_digest", name="Weekly Digest Monday 08:00")

    # ENG-8: Watchlist alerts — 08:30, 14:30, 20:35 UTC
    # 20:35 staggered 5 min after 20:30 betting refresh (N9 fix — avoids simultaneous heavy jobs)
    for hour, minute in [(8, 30), (14, 30), (20, 35)]:
        scheduler.add_job(job_watchlist_alerts, CronTrigger(hour=hour, minute=minute),
                          id=f"watchlist_alerts_{hour:02d}",
                          name=f"Watchlist Alerts {hour:02d}:{minute:02d}")

    # Settlement: 21:00 + 23:30 + 01:00 UTC
    # 01:00 added (N4 fix) — catches 21:30+ KO matches finishing with extra time after 23:30
    scheduler.add_job(job_settlement, CronTrigger(hour=21, minute=0),
                      id="settlement", name="Settlement 21:00")
    scheduler.add_job(job_settlement, CronTrigger(hour=23, minute=30),
                      id="settlement_late", name="Settlement 23:30")
    scheduler.add_job(job_settlement, CronTrigger(hour=1, minute=0),
                      id="settlement_overnight", name="Settlement 01:00")

    # Settle-ready sweep: every 15 min, all day.
    # Catches matches the live poller missed (outside 10-23 UTC window, or if it errored).
    # Idempotent — skips matches already marked 'done'.
    scheduler.add_job(job_settle_ready, CronTrigger(minute="*/15"),
                      id="settle_ready", name="Settle-Ready Sweep (15min)")

    # Budget sync: hourly
    scheduler.add_job(job_budget_sync, CronTrigger(minute=0),
                      id="budget_sync", name="Budget Sync")

    # Ops snapshot fallback: every hour at :30 — captures state if no pipeline ran
    scheduler.add_job(job_ops_snapshot, CronTrigger(minute=30),
                      id="ops_snapshot", name="Ops Snapshot :30")

    # Orphan cleanup: every 30 min — marks pipeline_runs stuck >60 min as failed
    # Catches records that slipped past startup cleanup (were <10 min old at restart time)
    scheduler.add_job(job_cleanup_orphaned_runs, CronTrigger(minute="*/30"),
                      id="cleanup_orphaned_runs", name="Orphan Cleanup (30min)")

    # OBS-HEARTBEAT: ping healthchecks.io every 5 min — external liveness signal
    # Set HEALTHCHECKS_IO_PING_URL env var to activate (no-op if unset)
    scheduler.add_job(job_healthcheck_ping, CronTrigger(minute="*/5"),
                      id="healthcheck_ping", name="Healthcheck Ping (5min)")

    # STRIPE-RECONCILE: daily drift check — Stripe events vs processed_events (09:00 UTC)
    # Runs after Stripe's 24h retry window closes so all retries have been attempted.
    scheduler.add_job(job_stripe_reconcile, CronTrigger(hour=9, minute=0),
                      id="stripe_reconcile", name="Stripe Reconcile 09:00")

    # PIPE-ALERT: proactive pipeline anomaly alerts via email
    # Morning check at 09:35 (after 09:30 betting refresh settles)
    scheduler.add_job(job_health_alerts_morning, CronTrigger(hour=9, minute=35),
                      id="health_alerts_morning", name="Health Alerts Morning 09:35")
    # Snapshot staleness: every hour 10-22 UTC
    for _ha_hour in range(10, 23):
        scheduler.add_job(job_health_alerts_snapshot, CronTrigger(hour=_ha_hour, minute=45),
                          id=f"health_alerts_snapshot_{_ha_hour:02d}",
                          name=f"Health Alerts Snapshot {_ha_hour:02d}:45")
    # Settlement check at 21:30 (after 21:00 settlement job has had 30 min to run)
    scheduler.add_job(job_health_alerts_settlement, CronTrigger(hour=21, minute=30),
                      id="health_alerts_settlement", name="Health Alerts Settlement 21:30")

    # MONEY-SETTLE-RECON: verify no stuck pending bets after settlement (21:30 UTC)
    scheduler.add_job(job_settle_reconcile, CronTrigger(hour=21, minute=30),
                      id="settle_reconcile", name="Settlement Reconcile 21:30")

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

    console.print(f"\n[bold green]Scheduler + LivePoller running 24/7. "
                  f"Live={poller.FAST_INTERVAL}s, "
                  f"Idle={poller.IDLE_INTERVAL}s, "
                  f"Stats={poller.FAST_INTERVAL * poller.MEDIUM_MULTIPLIER}s, "
                  f"Lineups={poller.FAST_INTERVAL * poller.SLOW_MULTIPLIER}s[/bold green]\n")

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
