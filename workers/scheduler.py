"""
OddsIntel — Pipeline Scheduler

Long-running process that replaces GitHub Actions cron scheduling.
Uses APScheduler for timed jobs + a health endpoint on :8080.

Run: python -m workers.scheduler
"""

import logging
import os
import sys
import json
import signal
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from rich.console import Console

from workers.utils.kuma import push as _kuma_push

# APScheduler logs every job-fire at INFO — suppress to keep logs readable at 50+ jobs.
logging.getLogger("apscheduler").setLevel(logging.WARNING)

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

# ── SCHEDULER-STALL-RCA / SCHEDULER-AF-429-DEADLOCK watchdog (2026-08-24) ──
# Registry of jobs currently occupying an APScheduler worker thread, keyed by
# thread ident. The 2026-08-22 stall took hours of journalctl archaeology to
# pin down because the only evidence was "max_instances blocked" — we knew a
# job was stuck but not WHERE. The watchdog below turns that into a stack trace
# in the log the first time it happens.
_inflight: dict[int, dict] = {}
_inflight_lock = threading.Lock()
# Longest any single job legitimately runs today is weekly_retrain (~21 min) and
# settlement (~30 min). 45 min is comfortably past both, so a trip means a hang.
JOB_STALL_WARN_S = float(os.getenv("JOB_STALL_WARN_S", "2700"))
_stall_reported: set[tuple[int, str]] = set()

SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"
HEALTH_PORT = int(os.getenv("PORT", "8080"))


# ── Job wrapper ────────────────────────────────────────────────────────────

def _job_prefix() -> str:
    return "railway_" if SHADOW_MODE else ""


def _run_job(name: str, fn, *args, _log_run: bool = True, **kwargs):
    """Wrapper that runs a job function with error isolation and logging.

    OBS-LOG-ALL-JOBS — every wrapped job auto-logs to ``pipeline_runs`` so the
    ops dashboard sees all 25 jobs, not just the 14 whose body happens to call
    ``log_pipeline_*`` themselves. Logging exceptions are swallowed: a failure
    in the logger must not kill the actual job.

    ``_log_run=False`` suppresses pipeline_runs writes when the wrapped fn
    already logs the same job_name itself (currently ``settlement_pipeline``
    whose first sub-step logs as ``settlement``, and ``run_backfill`` which
    logs as ``hist_backfill``). Without the flag those rows would double up.
    """
    import traceback
    from datetime import date as _date
    full_name = f"{_job_prefix()}{name}"
    started = datetime.now(timezone.utc)
    console.print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
    console.print(f"[bold cyan]Job: {full_name} @ {started.strftime('%H:%M:%S UTC')}[/bold cyan]")
    console.print(f"[bold cyan]{'─' * 60}[/bold cyan]\n")

    run_id = None
    if _log_run:
        try:
            from workers.utils.pipeline_utils import log_pipeline_start
            run_id = log_pipeline_start(name, _date.today().isoformat())
        except Exception:
            # Silent — logging failure must not interfere with the job and
            # must not add journal volume. _recent_errors will surface
            # any underlying DB problem on the next genuine job error.
            run_id = None

    error_msg = None
    _tid = threading.get_ident()
    with _inflight_lock:
        _inflight[_tid] = {"job": full_name, "started_monotonic": time.monotonic(),
                           "started_at": started.isoformat()}
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

    with _inflight_lock:
        _inflight.pop(_tid, None)
        _stall_reported.discard((_tid, full_name))

    if _log_run and run_id:
        try:
            if status == "completed":
                from workers.utils.pipeline_utils import log_pipeline_complete
                log_pipeline_complete(run_id)
            else:
                from workers.utils.pipeline_utils import log_pipeline_failed
                log_pipeline_failed(run_id, error_msg or "unknown error")
        except Exception:
            # Silent — see start-log note. cleanup_orphaned_runs sweeps any
            # row left in 'running' status after >60 min.
            pass

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

    # Uptime Kuma push — silent no-op unless KUMA_URL_BASE + KUMA_TOKENS[name]
    # are set. Uses the pipeline-runs `name` (not full_name) as the key so a
    # PIPELINE_JOB_PREFIX doesn't need mirroring in Kuma config. Failures in
    # the push itself never crash the job (kuma.push swallows exceptions).
    _kuma_push(
        name,
        status="up" if status == "completed" else "down",
        msg="OK" if status == "completed" else (error_msg or "failed")[:120],
        ping_ms=int(elapsed * 1000),
    )


def _run_subprocess_job(
    name: str,
    cmd: list[str],
    *,
    timeout: int,
    summary_keywords: list[str],
    require_output_marker: str | None = None,
    skip_if: "callable | None" = None,
) -> None:
    """CS2-PIPELINE-TRUTHFUL-LOGGING (2026-06-21) — subprocess job wrapper.

    The legacy pattern wrapped subprocess calls in a body that called
    `_run_job(name, lambda: None)` after the subprocess. A non-zero subprocess
    exit only printed `[red]error[/red]` to stdout — the no-op lambda always
    succeeded, so `pipeline_runs.status` was always 'completed'. Result: the
    cs2_scanner stopped writing on 2026-06-14 and every fire for 9 days still
    logged 'completed' while the DB stayed empty.

    This helper runs the subprocess INSIDE `_run_job` so non-zero exit and
    missing-output markers raise — `pipeline_runs.error_message` carries the
    stderr tail and a real Telegram-able failure signal exists.

    skip_if: optional callable returning (skip: bool, reason: str). When True,
      logs a yellow notice and returns without running (e.g., env var missing).
    require_output_marker: substring that MUST appear in stdout. If missing,
      raise — catches subprocess exit-0 with empty work output.
    """
    def _impl():
        import subprocess as _sp
        if skip_if is not None:
            skip, reason = skip_if()
            if skip:
                console.print(f"[yellow]Skipped — {reason}[/yellow]")
                return
        result = _sp.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[-1500:]
            raise RuntimeError(
                f"{Path(cmd[1]).name if len(cmd) > 1 else cmd[0]} exited "
                f"{result.returncode}: {stderr or '(empty stderr)'}"
            )
        if require_output_marker and require_output_marker not in result.stdout:
            # CS2-PIPELINE-TRUTHFUL-LOGGING-FOLLOWUP (2026-06-22): include
            # stderr too — the bo3.gg client catches request errors with
            # `print(..., file=sys.stderr)` and returns {} so the subprocess
            # exits 0. Without stderr we only see "0 matches" on stdout, not
            # WHY (the network error / timeout / 403 / etc.).
            stdout_tail = (result.stdout or "")[-600:]
            stderr_tail = (result.stderr or "").strip()[-600:]
            stderr_part = f"\nstderr tail: {stderr_tail}" if stderr_tail else ""
            raise RuntimeError(
                f"missing expected output marker {require_output_marker!r} — "
                f"subprocess returned 0 but produced no work output. "
                f"stdout tail: {stdout_tail}{stderr_part}"
            )
        for line in result.stdout.splitlines():
            if any(k in line for k in summary_keywords):
                console.print(f"[dim]{line}[/dim]")

    _run_job(name, _impl)


# ── Pipeline chains ────────────────────────────────────────────────────────

def morning_pipeline():
    """
    04:00 UTC — Sequential chain replacing GH Actions timing gaps.
    Each step has error isolation so one failure doesn't block the rest.
    """
    from workers.jobs.fetch_fixtures import run_fixtures, fetch_and_store_fixtures
    from workers.jobs.fetch_enrichment import run_enrichment
    from workers.jobs.fetch_odds import run_odds
    from workers.jobs.fetch_predictions import run_predictions
    from workers.jobs.betting_pipeline import run_betting
    # National-team predictor: 1X2/OU/BTTS for INTERNATIONAL fixtures, after
    # fixtures land + AF predictions run. Source = 'national_team_v1'.
    # WC-RETIRED-2026-09-01 kept this step: it is NOT World Cup-specific —
    # over the last 60 days it priced ASEAN Championship and Friendlies as
    # well, and last ran 2026-08-25. Only the WC-only *blended* variant
    # (national_team_v1_blended, which needed wc_market_consensus and last
    # produced anything 2026-07-18) was removed with the rest of the surface.
    from scripts.write_national_team_predictions import run_predictions as run_national_team_predictions

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    is_monday = date.today().weekday() == 0

    console.print(f"[bold green]═══ Morning Pipeline: {today} ═══[/bold green]\n")

    import traceback
    steps = [
        ("1/7", "Fixtures (today)",        lambda: run_fixtures(target_date=today, refresh_leagues=is_monday)),
        ("2/7", "Fixtures (tomorrow rows)", lambda: fetch_and_store_fixtures(tomorrow)),
        ("3/7", "Enrichment",              lambda: run_enrichment(target_date=today)),
        ("4/7", "Odds",                    lambda: run_odds(target_date=today)),
        ("5/7", "Predictions (club)",      lambda: run_predictions(target_date=today)),
        ("6/7", "Predictions (national)",  lambda: run_national_team_predictions(days=30)),
        ("7/7", "Betting",                 lambda: run_betting()),
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
        console.print("\n[bold green]Morning pipeline complete — all 8 steps succeeded.[/bold green]")


def settlement_pipeline():
    """
    21:00 UTC — Settlement chain: results → ML ETL → prune → Platt (Wed+Sun) → DC rho (Sun).
    Each step is logged to pipeline_runs so the ops dashboard shows status and row counts.
    """
    import traceback
    from workers.jobs.settlement import run_settlement, run_ml_etl
    from workers.utils.pipeline_utils import log_pipeline_start, log_pipeline_complete, log_pipeline_failed

    today = date.today().isoformat()

    # (job_name, display_label, fn)
    steps = [
        ("settlement",        "Core settlement", lambda: run_settlement()),
        ("settlement_ml_etl", "ML ETL",          lambda: run_ml_etl()),
        ("settlement_prune",  "Prune odds",      lambda: __import__('scripts.prune_odds_snapshots', fromlist=['prune']).prune(dry_run=False)),
    ]

    is_refit_day = date.today().weekday() in (2, 6)  # Wednesday + Sunday
    is_sunday    = date.today().weekday() == 6        # Sunday only

    if is_refit_day:
        steps.append(("settlement_platt", "Platt recalibration", lambda: __import__('scripts.fit_platt', fromlist=['fit_and_store']).fit_and_store(model_version=os.getenv("MODEL_VERSION"))))
        steps.append(("settlement_blend", "Blend weight refit",  lambda: __import__('scripts.fit_blend_weights', fromlist=['run']).run()))
    if is_sunday:
        steps.append(("settlement_dc_rho", "DC rho per tier",    lambda: __import__('scripts.fit_league_rho', fromlist=['run']).run()))
    if not is_refit_day:
        console.print("[dim]Settlement steps 4-6: Platt + blend weight + DC rho — skipped (not Wednesday or Sunday)[/dim]")

    failed_steps = []
    for i, (job_name, step_label, step_fn) in enumerate(steps, 1):
        console.print(f"\n[cyan]Settlement step {i}/{len(steps)}: {step_label}[/cyan]")
        step_start = datetime.now(timezone.utc)
        run_id = None
        try:
            run_id = log_pipeline_start(job_name, today)
        except Exception as _e:
            console.print(f"[yellow]  ⚠ log_pipeline_start failed (non-critical): {_e}[/yellow]")
        try:
            result = step_fn()
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            console.print(f"[green]  ✓ {step_label} completed in {elapsed:.1f}s[/green]")
            try:
                # prune returns rows deleted; other steps return None
                rows = result if isinstance(result, int) else None
                log_pipeline_complete(run_id, records_count=rows)
            except Exception as _e:
                console.print(f"[yellow]  ⚠ log_pipeline_complete failed (non-critical): {_e}[/yellow]")
        except Exception as e:
            elapsed = (datetime.now(timezone.utc) - step_start).total_seconds()
            failed_steps.append(step_label)
            console.print(f"[red]  ✗ {step_label} FAILED after {elapsed:.1f}s: {e}[/red]")
            console.print(f"[red dim]{traceback.format_exc()}[/red dim]")
            try:
                log_pipeline_failed(run_id, str(e))
            except Exception as _e:
                console.print(f"[yellow]  ⚠ log_pipeline_failed failed (non-critical): {_e}[/yellow]")

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


def job_closing_snap():
    """CLOSING-LINE-COVERAGE: every 5 min, snap odds for matches in T-15→T+5.
    Stored with is_closing=TRUE so get_closing_odds() and clv_pinnacle resolve
    against the actual closing line rather than the latest stale pre-match snap."""
    from workers.jobs.closing_snap import run_closing_snap
    _run_job("closing_snap", run_closing_snap)


def job_odds_tomorrow():
    """OPENING-LINE-MOVE-CAPTURE (2026-05-25): fetch odds for TOMORROW's
    matches at 22:00 UTC. The match-day morning fetch at 04:00 UTC then
    produces a 'yesterday → today' delta in odds_snapshots that
    batch_write_morning_signals translates into overnight_line_move.

    Pre-fix coverage: 0.2% (48 / 13,500 MFV rows since 2026-05-01).
    The earlier OVERNIGHT-ODDS-CAPTURE at 02:00/04:00 UTC fetched
    today's matches but they had no prior snapshot to diff against.
    """
    from workers.jobs.fetch_odds import run_odds
    from datetime import date, timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    _run_job("odds_tomorrow", lambda: run_odds(target_date=tomorrow))


def job_injuries_morning():
    """AF-INJURIES-LATE (2026-06-01): single morning injury fetch at 08:00 UTC.
    Replaces the previous 10:30 + 13:00 + 16:00 schedule. Most injury news
    breaks overnight / pre-match; the 08:00 fetch keeps the morning betting
    pipeline at 11:00 within 3h of the last injury data, which is plenty for
    football's injury news cadence. Saves ~30 AF calls/day. News-event-
    triggered refresh path is filed as a follow-up but not wired today.
    """
    from workers.jobs.fetch_enrichment import run_enrichment
    _run_job("injuries_morning", run_enrichment,
             components={"injuries"})


def job_enrichment_full():
    """13:00 UTC full enrichment — H2H + team_stats only.
    AF-INJURIES-LATE (2026-06-01): injuries dropped from this slot; they're
    now fetched once at 08:00 UTC by job_injuries_morning. H2H + team_stats
    still refreshed here for afternoon/evening betting refreshes (N7 fix).
    Standings moved to job_standings_nightly (23:30 UTC, AF-STANDINGS-DAILY).
    """
    from workers.jobs.fetch_enrichment import run_enrichment
    _run_job("enrichment_full", run_enrichment,
             components={"h2h", "team_stats"})


def job_standings_nightly():
    """AF-STANDINGS-DAILY: standings once at 23:30 UTC, not intraday.
    Standings change ~1x/week; fetching them at 10:30/13:00/16:00 wasted ~40 AF calls/day.
    """
    from workers.jobs.fetch_enrichment import run_enrichment
    _run_job("standings_nightly", run_enrichment,
             components={"standings"})


def job_betting_refresh_wrapper():
    _run_job("betting_refresh", job_betting_refresh)


def job_betting_refresh():
    """Pre-kickoff betting re-evaluation — re-run betting with fresh odds + signals.

    P-PRED-1 (2026-05-10): /predictions is no longer refetched here. AF
    documents the predictions endpoint as updating at most hourly, and in
    practice the values barely move once the morning fetch is in. Re-pulling
    ~3,000 fixtures × 5 betting_refresh slots was burning ~10K AF calls/day
    for data identical to what's already on `matches.af_prediction`. Morning
    pipeline still calls `run_predictions` once at 05:30 UTC; the cached
    JSONB feeds every betting_refresh below.
    """
    from workers.jobs.betting_pipeline import run_betting
    from workers.jobs.settlement import write_dashboard_cache
    import traceback

    today = date.today().isoformat()
    console.print(f"[bold cyan]Pre-KO Betting Refresh: {today}[/bold cyan]")

    try:
        run_betting()
    except Exception as e:
        console.print(f"[red]Betting refresh failed: {e}[/red]")
        console.print(f"[red dim]{traceback.format_exc()}[/red dim]")

    try:
        write_dashboard_cache()
    except Exception as e:
        console.print(f"[red]Dashboard cache refresh failed: {e}[/red]")
        console.print(f"[red dim]{traceback.format_exc()}[/red dim]")


def job_shadow_run_interval():
    """BET-TIMING-MONITOR — 30-min shadow run, fires at :05/:35 past each hour 07–22 UTC.

    Cohort label = 'HHMM' UTC string (e.g. '0705', '1435') so each run is
    independently analysable. All bots are evaluated regardless of cohort —
    shadow mode has no bankroll impact. Writes to shadow_bets only.
    """
    from datetime import datetime, timezone
    cohort = datetime.now(timezone.utc).strftime("%H%M")
    _run_job(f"shadow_{cohort}", _shadow_run, cohort)


def job_coolbet_odds_snapshot():
    """COOLBET-ODDS-SNAPSHOT — 30-min Coolbet odds ingest, fires at :03/:33 UTC.

    Matches upcoming matches in our DB (next 2 days) against Coolbet's fo-category
    tree, then pulls sidebets per match to get full market depth (1X2, OU 0.5–4.5,
    BTTS, double_chance, asian_handicap). Stores in odds_snapshots with
    bookmaker='Coolbet'. Lands before the :05/:35 betting refresh so the same
    cycle's edge math sees the latest Coolbet prices.

    Error-isolated — a Coolbet auth/Imperva blowup never blocks other jobs.
    """
    from workers.automation.coolbet_explorer import run_bulk
    import traceback
    try:
        run_bulk(days=2, dry_run=False, sleep_s=0.25, limit=None)
    except Exception as e:
        console.print(f"[red]Coolbet odds snapshot failed: {e}[/red]")
        console.print(f"[red dim]{traceback.format_exc()}[/red dim]")


def _coolbet_odds_snapshot_wrapper():
    _run_job("coolbet_odds_snapshot", job_coolbet_odds_snapshot)


def job_epicbet_odds_snapshot():
    """EPICBET-ODDS-INGEST-2026-08-27 — 30-min Epicbet odds ingest at :02/:32 UTC.

    Second EMTA-licensed, operator-reachable book alongside Coolbet, so value
    bots stop being priced against a single venue. Unlike Coolbet this needs no
    auth and hits no bot-protection, so it runs on the VPS rather than the Mac.

    Sweeps Epicbet's football league listings in bulk, fuzzy-matches against DB
    fixtures in the next 2 days, and stores 1X2 / OU / BTTS / AH into
    odds_snapshots with bookmaker='Epicbet'. Fires 3 min before the :05/:35
    betting refresh so the same cycle sees fresh prices.

    EPICBET-403-FROM-VPS-2026-08-29: the original docstring claimed Epicbet
    "needs no auth and hits no bot-protection, so it runs on the VPS rather
    than the Mac". That was only ever true from the operator's residential IP.
    From this host Cloudflare answers every call with a 403 interstitial, so
    epicbet_explorer now falls back to FlareSolverr. The claim is corrected
    here rather than deleted, because it is what made the job look safe to run
    unattended on the VPS in the first place.

    RAISES on failure. It previously caught everything and returned normally,
    so _run_job recorded status='completed' and pinged Kuma "up" — 277
    consecutive runs over six days reported success while writing zero rows.
    _run_job already isolates each job from the others, so re-raising costs no
    isolation and is the only thing that makes the failure visible
    ([[feedback_silent_failures]]).
    """
    from workers.automation.epicbet_explorer import run_bulk
    res = run_bulk(days=2, dry_run=False)
    # A run that stores nothing is not necessarily broken (quiet fixture
    # window), so this is not an error — but it must be visible, because
    # "completed" with zero rows is exactly what six days of failure looked
    # like. The freshness watchdog is what actually alarms.
    if res and not res.get("stored"):
        console.print(
            f"[yellow]Epicbet: stored 0 rows "
            f"(db_matches={res.get('db_matches')} matched={res.get('matched')})[/yellow]"
        )
    return res


def _epicbet_odds_snapshot_wrapper():
    _run_job("epicbet_odds_snapshot", job_epicbet_odds_snapshot)



def _shadow_run(shadow_cohort: str):
    """Run run_morning(shadow_mode=True, shadow_cohort=...).

    RAISES on failure. It used to catch everything and return normally, so
    `_run_job` recorded status='completed' and the shadow cohorts looked
    healthy while writing nothing.

    SHADOW-SILENT-FAILURE-2026-09-03: that is not hypothetical. A per-cent sign
    in a SQL comment broke `_load_today_from_db` from 2026-09-02 14:57 UTC
    until 07:20 the next morning. `betting_pipeline` re-raises, so it showed
    four loud failures and got noticed. Every shadow cohort in the same ~16
    hours reported **completed** and wrote nothing — shadow_bets went from
    3,269 rows the day before to 7. The one that told the truth is the one
    that was fixed within the hour.

    `_run_job` already isolates each job from the others, so re-raising costs
    no isolation and is the only thing that makes the failure visible. Same
    change, same reasoning, as job_epicbet_odds_snapshot after
    EPICBET-403-FROM-VPS ([[feedback_silent_failures]]).
    """
    from workers.jobs.daily_pipeline_v2 import run_morning
    run_morning(skip_fetch=True, shadow_mode=True, shadow_cohort=shadow_cohort)


def job_dashboard_cache_refresh():
    """Refresh dashboard_cache snapshot independently of betting/settlement.

    Without this, the public /performance page can lag ~24h between the
    last betting_refresh / settlement run that wrote cache. Lightweight —
    just SQL aggregations, no external API calls."""
    from workers.jobs.settlement import write_dashboard_cache
    _run_job("dashboard_cache_refresh", write_dashboard_cache)


def job_news_checker():
    from workers.jobs.news_checker import run_news_checker
    _run_job("news_checker", run_news_checker)


def job_match_previews():
    from workers.jobs.match_previews import run_match_previews
    _run_job("match_previews", run_match_previews)


# WC-AI-PREVIEW (2026-06-02): tournament-window gate. Runs only between
# 7 days pre-tournament and the final inclusive (2026-06-04 → 2026-07-19).
# Outside that window the job exits immediately as a no-op so APScheduler






def job_publish_daily_picks():
    """GROWTH-ACCURACY-PICKS-LOG (2026-06-05): publish the top model pick per
    market for every match kicking off in the next 24h. Powers the public
    accuracy track-record at /accuracy (future). Idempotent — the UNIQUE
    constraint on (match_id, market, model_version) makes re-runs safe."""
    from workers.jobs.publish_daily_picks import run_publish_daily_picks
    _run_job("publish_daily_picks", run_publish_daily_picks)










def job_weekly_digest():
    from workers.jobs.weekly_digest import run_weekly_digest
    _run_job("weekly_digest", run_weekly_digest)


def job_prune_anon_users():
    """ANON-AUTH PHASE 4 — weekly prune of stale anonymous Supabase users
    (see workers/jobs/prune_anon_users.py for details + safety cap)."""
    from workers.jobs.prune_anon_users import run as run_prune
    _run_job("prune_anon_users", run_prune)


def job_weekly_retrain():
    """ML-PIPELINE-UNIFY Stage 5a — weekly retrain + auto-comparison.

    Trains a new model bundle tagged `v{YYYYMMDD}` from the current MFV, then
    runs `compare_models.py` against the production version. Promotion stays
    manual: the operator flips MODEL_VERSION env after reading the comparison.
    """
    from datetime import date as _date
    import subprocess
    import os

    def _retrain():
        version = f"v{_date.today().strftime('%Y%m%d')}"
        # WEEKLY-EVAL-BASELINE-2026-08-26: resolve the baseline the way the
        # RUNTIME does, not from the global env var alone.
        #
        # This read `os.getenv("MODEL_VERSION")` — the global — while inference
        # resolves per market via _resolve_version(), which checks
        # MODEL_VERSION_OU_T{tier} then MODEL_VERSION_{MARKET} then the global.
        # Since 2026-07-19 OU 2.5 has actually been served by v20260719 while
        # the global stayed v20260712, so every weekly email since has scored
        # the OU markets against a model that was NOT in production. The
        # 2026-08-23 email reported v20260823 as +5.42% worse on over25/under25
        # — measured against the wrong baseline entirely.
        #
        # Comparing markets served by different versions against one shared
        # baseline cannot be made correct, so report the per-market baselines
        # too and let the eval note record which was used where.
        from workers.model.xgboost_ensemble import _resolve_version as _rv
        production = os.getenv("MODEL_VERSION", "v14")
        _per_market = {k: _rv(k) for k in ("1x2", "ou", "goals")}
        if len(set(_per_market.values())) > 1:
            console.print(
                f"[yellow]weekly eval: production is SPLIT across versions "
                f"{_per_market} — the single-baseline comparison below is only "
                f"valid for markets served by {production}[/yellow]"
            )

        console.print(f"[bold cyan]Weekly retrain → {version}[/bold cyan]")
        # Subprocess so a hung XGBoost run can't block the scheduler thread
        # any longer than the 30-min wallclock here. Lives outside the cron
        # process so a crash bubbles up cleanly.
        # WEEKLY-RETRAIN-OU-FEATURES (2026-05-24): explicitly pass the
        # --include-pinnacle + --include-ou-market flags so the retrain
        # produces a v14-feature-equivalent bundle. The flags were missing
        # since this job first shipped, which silently dropped 14 market-data
        # columns (pinnacle_implied_*, ou25_bookmaker_disagreement,
        # market_implied_btts_yes) from every weekly bundle. The MARKET-EVAL
        # eval surfaced the cost: v20260517 and v20260524 are +9 to +13%
        # worse than v14 on the over_under XGBoost head despite being better
        # on every other market head. Root cause traced to this flag omission.
        # DRIFT-FEATURE flag intentionally OMITTED here (2026-06-04 revert).
        # The DRIFT-FEATURE backfill currently only covers 260/10K matches
        # (rest don't have MFV rows yet), and the June 8 / June 15 calibration
        # + bot-threshold work needs clean apples-to-apples comparison vs the
        # prior bundle. Re-enable via DRIFT-FEATURE-REENABLE in PRIORITY_QUEUE
        # once MFV coverage on drift columns is ≥30% and the Batch 2
        # calibration eval is baselined. Until then, drift remains available
        # for ad-hoc training runs via `--include-drift` but not on the
        # auto-Sunday-retrain.
        result = subprocess.run(
            [sys.executable, "-m", "workers.model.train", "--version", version,
             "--include-pinnacle", "--include-ou-market"],
            cwd=str(Path(__file__).parent.parent),
            timeout=1800,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]train.py exit {result.returncode}: {result.stderr[-2000:]}[/red]")
            raise RuntimeError(f"weekly retrain failed: exit {result.returncode}")
        console.print(result.stdout[-2000:])

        # Auto-comparison — best-effort.
        # WEEKLY-EVAL (2026-05-24): switched from compare_models.py to
        # weekly_eval_and_compare.py. The legacy script needed OVERLAPPING
        # predictions in the predictions table between candidate and prod,
        # but candidate bundles are never inferenced so it always returned
        # "0 overlap". The new script loads each bundle directly and scores
        # held-out settled MFV rows, then persists cv_metrics to model_versions
        # for audit. Output is parsed by the email digest below.
        try:
            cmp = subprocess.run(
                [sys.executable, "scripts/weekly_eval_and_compare.py", version, production],
                cwd=str(Path(__file__).parent.parent),
                timeout=900,
                capture_output=True,
                text=True,
            )
            console.print(cmp.stdout[-3000:])
            if cmp.returncode != 0:
                console.print(f"[yellow]weekly_eval_and_compare.py exit {cmp.returncode}: {cmp.stderr[-1000:]}[/yellow]")
            # WEEKLY-EVAL-EMAIL (2026-05-24): send Resend digest with the
            # SUMMARY_JSON the eval script prints on its final line.
            try:
                from workers.jobs.weekly_retrain_email import send_weekly_retrain_email
                send_weekly_retrain_email(version, production, cmp.stdout)
            except Exception as ee:
                console.print(f"[yellow]Email digest failed (non-blocking): {ee}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Auto-comparison skipped: {e}[/yellow]")

        # AH-PLATT-WIRE (2026-06-07): refit AH + other thin-sample Platt rows
        # using the blended-version fit script. Runs AFTER the retrain so it
        # operates on the freshest calibrated_prob data. Non-blocking — a fit
        # failure must not abort the retrain job or prevent the email.
        # Only the per-line AH rows that have crossed the 50-sample gate will
        # actually refit; the rest are skipped silently by the script.
        try:
            platt = subprocess.run(
                [sys.executable, "scripts/fit_platt_live.py"],
                cwd=str(Path(__file__).parent.parent),
                timeout=120,
                capture_output=True,
                text=True,
            )
            console.print(platt.stdout[-1000:])
            if platt.returncode != 0:
                console.print(f"[yellow]fit_platt_live.py exit {platt.returncode}: {platt.stderr[-500:]}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]AH Platt refit skipped: {e}[/yellow]")

    _run_job("weekly_retrain", _retrain)


def job_weekly_meta_retrain():
    """META-RETRAIN (2026-05-25): weekly retrain of the B-ML3 meta-model.
    Runs Sunday 04:00 UTC, AFTER the main XGBoost weekly_retrain at 03:00 UTC
    finishes (the meta-model consumes MFV features built/refreshed by the
    main retrain pipeline).

    Train script writes the bundle to data/models/meta/<version>/. No
    promotion — the operator inspects the new bundle's threshold.json and
    decides whether to flip META_B_ML3_VERSION in /opt/odds-intel-engine/.env (then restart oddsintel-scheduler).
    """
    import subprocess
    from datetime import date as _date

    def _meta_retrain():
        version = f"v_{_date.today().strftime('%Y%m%d')}_meta"
        console.print(f"[bold cyan]Weekly META retrain → {version}[/bold cyan]")
        result = subprocess.run(
            [sys.executable, "scripts/train_b_ml3.py", "--version", version],
            cwd=str(Path(__file__).parent.parent),
            timeout=900,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]meta retrain exit {result.returncode}: {result.stderr[-2000:]}[/red]")
            raise RuntimeError(f"weekly meta retrain failed: exit {result.returncode}")
        # Tail the output so the new bundle's CV AUC is visible in pipeline_runs metadata.
        console.print(result.stdout[-3000:])
        # BUNDLE-STORAGE-SYNC (2026-05-25): mirror the new bundle to Supabase
        # Storage so future scheduler restarts can hydrate it on cache miss.
        try:
            from workers.model.storage import upload_meta_bundle
            local_dir = Path(__file__).parent.parent / "data" / "models" / "meta" / version
            count = upload_meta_bundle(version, local_dir)
            console.print(f"[green]Uploaded {count} meta bundle files to Storage[/green]")
        except Exception as e:
            console.print(f"[yellow]Meta bundle Storage upload skipped: {e} — bundle remains on local disk only[/yellow]")
    _run_job("weekly_meta_retrain", _meta_retrain)


def job_weekly_meta_validate():
    """META-VALIDATE-WEEKLY (2026-06-01): weekly run of validate_meta_b_ml3.
    Runs Sunday 05:00 UTC, AFTER weekly_meta_retrain (04:00). Scores all
    available meta bundles on real settled bets and emails the verdict so
    the 2026-06-10 activation decision (and every future one) stops being
    a manual checkpoint.

    Output is the script's stdout — captured into pipeline_runs.metadata via
    the email helper, which parses the verdict table and renders an HTML
    summary. Even if no bundle passes the 5pp gate (the 2026-06-01 pre-flight
    verdict), the report still lands so we can track the delta over time.
    """
    import subprocess

    def _meta_validate():
        console.print("[bold cyan]Weekly meta validate — scoring bundles vs settled bets[/bold cyan]")
        result = subprocess.run(
            [sys.executable, "scripts/validate_meta_b_ml3.py", "--since", "2026-05-25"],
            cwd=str(Path(__file__).parent.parent),
            timeout=600,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]meta validate exit {result.returncode}: {result.stderr[-2000:]}[/red]")
            raise RuntimeError(f"weekly meta validate failed: exit {result.returncode}")
        console.print(result.stdout[-4000:])
        try:
            from workers.jobs.weekly_meta_validate_email import send_weekly_meta_validate_email
            send_weekly_meta_validate_email(result.stdout)
        except Exception as e:
            console.print(f"[yellow]Weekly meta validate email skipped: {e}[/yellow]")
    _run_job("weekly_meta_validate", _meta_validate)


def job_weekly_threshold_check():
    """THRESHOLD-CHECK-WEEKLY (2026-06-06): weekly run of threshold_check.py.
    Runs Sunday 06:00 UTC, AFTER weekly_retrain (03:00) / meta_retrain (04:00) /
    meta_validate (05:00) so the snapshot reflects this week's freshly-fit
    models. Output is the script's raw stdout, wrapped in <pre> and emailed
    to ADMIN_ALERT_EMAIL via Resend.

    Origin: the 2026-06-06 audit found threshold_check.py output was 13 days
    stale (last manual run 2026-05-24) AND had 3 silent bugs masking what was
    true. Hours of debugging would have been avoided if a fresh snapshot
    landed every week — this cron makes that automatic.
    """
    import subprocess
    from datetime import datetime, timezone

    def _threshold_check():
        console.print("[bold cyan]Weekly threshold check — refreshing key gate counts[/bold cyan]")
        result = subprocess.run(
            [sys.executable, "scripts/threshold_check.py"],
            cwd=str(Path(__file__).parent.parent),
            timeout=300,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]threshold check exit {result.returncode}: {result.stderr[-2000:]}[/red]")
            raise RuntimeError(f"weekly threshold check failed: exit {result.returncode}")
        console.print(result.stdout[-4000:])
        ran_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        try:
            from workers.jobs.weekly_threshold_check_email import send_weekly_threshold_check_email
            send_weekly_threshold_check_email(result.stdout, ran_at)
        except Exception as e:
            console.print(f"[yellow]Weekly threshold check email skipped: {e}[/yellow]")
    _run_job("weekly_threshold_check", _threshold_check)


def job_weekly_bot_review():
    """BOT-MATURITY-REVIEW-WEEKLY (2026-06-15): Sunday rollup of per-bot
    performance with PROMOTE / DEMOTE / HOLD verdicts.

    Runs Sunday 06:30 UTC, AFTER weekly_threshold_check (06:00) so the
    operator gets the gate counts and the bot review in two adjacent emails.

    Origin: 2026-06-13 audit found `bot_high_alignment` (maturity=beta,
    -€56 over 50 real bets) had been auto-placing real money for days
    because the Mac daemon lacked the maturity gate the pipeline had. The
    decision "which bots are trustworthy enough to spend real money on?"
    was manual and ad-hoc — promotions happened reactively when someone
    happened to notice. This cron makes it systematic.
    """
    import subprocess
    from datetime import datetime, timezone

    def _bot_review():
        console.print("[bold cyan]Weekly bot maturity review — per-bot 30/60/90d perf + verdict[/bold cyan]")
        result = subprocess.run(
            [sys.executable, "scripts/weekly_bot_review.py"],
            cwd=str(Path(__file__).parent.parent),
            timeout=300,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]bot review exit {result.returncode}: {result.stderr[-2000:]}[/red]")
            raise RuntimeError(f"weekly bot review failed: exit {result.returncode}")
        console.print(result.stdout[-4000:])
        ran_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        try:
            from workers.jobs.weekly_bot_review_email import send_weekly_bot_review_email
            send_weekly_bot_review_email(result.stdout, ran_at)
        except Exception as e:
            console.print(f"[yellow]Weekly bot review email skipped: {e}[/yellow]")
    _run_job("weekly_bot_review", _bot_review)








def job_coolbet_health_ping():
    """COOLBET-FS-SESSION-STABLE Step 1.5 (2026-06-11): every 5 min, probe
    the full Coolbet auth chain (FS reachable → session alive → JWT valid →
    /s/casino/fo/maintenance returns 200). Updates coolbet_session_state
    so /admin pages, Telegram /status, and the health-alert cron all see
    fresh truth.

    Cheap: 1 HTTPS GET per fire. Failure here surfaces hours BEFORE a
    pipeline run would notice (the previous pattern was 'wait until
    cs2_coolbet_scanner returns 0 matches and wonder why')."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "scripts/coolbet/health_ping.py", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    # exit code 0 = healthy, 1 = unhealthy, 2 = config error
    if result.returncode == 0:
        _run_job("coolbet_health_ping", lambda: None)
        return
    console.print(f"[yellow]Coolbet health-ping: exit {result.returncode}[/yellow]")
    console.print(result.stdout[-500:] or result.stderr[-500:])
    # Raise from the lambda so _run_job records status=failed in
    # pipeline_runs AND pushes status=down to Kuma. The old pattern
    # (lambda: None) logged 'completed' on failure — a silent-failure trap.
    exit_code = result.returncode
    def _fail():
        raise RuntimeError(f"coolbet health_ping exited {exit_code}")
    _run_job("coolbet_health_ping", _fail)


def job_coolbet_daily_summary():
    """COOLBET-DAILY-SUMMARY (2026-06-16, C1): one Telegram at 08:00 UTC
    summarising daemon health, JWT, catch-net liveness, 24h activity, and
    today's calibrated queue. Lets the operator confirm "everything is
    fine" without prompting the system. A missed daily summary = scheduler
    itself is down (a failure mode no other alert covers)."""
    from workers.jobs.coolbet_daily_summary import run_daily_summary
    r = run_daily_summary()
    if not r.get("sent"):
        console.print(f"[yellow]Coolbet daily summary: sent={r.get('sent')}[/yellow]")
    _run_job("coolbet_daily_summary", lambda: None)


def job_coolbet_prekickoff_alert():
    """COOLBET-DAEMON-ALERTS (2026-06-16): pre-kickoff catch-net. Runs every
    5 min on the VPS, independent of the Mac. When the Mac daemon's
    heartbeat is stale or its last tick errored AND a calibrated-bot pick
    is approaching KO unplaced — push an urgent Telegram so the operator
    can place from their phone.

    Quiet on success: returns silently when daemon is healthy. Logs a
    summary line when it fires."""
    from workers.jobs.coolbet_prekickoff_alert import run_prekickoff_alert
    counters = run_prekickoff_alert()
    if not counters.get("healthy") and counters.get("candidates"):
        console.print(
            f"[yellow]Coolbet prekickoff catch-net: healthy={counters['healthy']} "
            f"candidates={counters['candidates']} sent={counters['sent']} "
            f"skipped_dedup={counters['skipped_dedup']}[/yellow]"
        )
    _run_job("coolbet_prekickoff_alert", lambda: None)


def job_pipeline_runs_failure_digest():
    """PIPELINE-RUNS-FAILURE-DIGEST (2026-06-22): daily 08:00 UTC email
    digest of jobs that failed in the last 24h, grouped by job_name.

    Quiet on healthy — if zero failures, no email. The CS2-PIPELINE-
    TRUTHFUL-LOGGING fix (2026-06-21) made `pipeline_runs` record real
    failures; this surfaces them to the operator within 24h instead of
    waiting for a manual sweep.

    Metadata (added 2026-06-25 after a 32h FS outage went unnoticed):
    write the digest's counters (sent / job_count / failure_count /
    skipped_reason) into pipeline_runs.metadata so future audits can
    verify whether the email actually went out without grepping logs.
    """
    from workers.jobs.pipeline_runs_failure_digest import run_failure_digest
    from workers.utils.pipeline_utils import (
        log_pipeline_start, log_pipeline_complete, log_pipeline_failed,
    )
    from datetime import date as _date
    run_id = None
    try:
        run_id = log_pipeline_start("pipeline_runs_failure_digest",
                                    _date.today().isoformat())
    except Exception:
        run_id = None
    try:
        counters = run_failure_digest()
        if counters.get("sent"):
            console.print(
                f"[yellow]Pipeline failure digest: job_count={counters['job_count']} "
                f"failure_count={counters['failure_count']} sent=True[/yellow]"
            )
        elif counters.get("skipped_reason"):
            console.print(
                f"[dim]Pipeline failure digest skipped: {counters['skipped_reason']}[/dim]"
            )
        if run_id:
            try:
                log_pipeline_complete(run_id, metadata=counters)
            except Exception:
                pass
    except Exception as e:
        if run_id:
            try:
                log_pipeline_failed(run_id, str(e))
            except Exception:
                pass
        raise


def job_pipeline_failure_alerter():
    """PIPELINE-FAILURE-ALERTER (2026-06-25): hourly Telegram alert when
    any cron racks up 3+ consecutive non-transient failures. Closes the
    32h-lag gap surfaced by the 06-23 FlareSolverr outage — the daily
    digest stays for forensics; this is fire-detection.

    DB-backed dedup via pipeline_health_state (4h re-fire window).
    Recovery clears the marker on first successful run after an alert,
    so a re-stuck condition re-fires immediately.

    Metadata: counters (stuck/alerted/skipped_dedup/recovered) stamped
    into pipeline_runs.metadata for retroactive audits.
    """
    from workers.jobs.pipeline_failure_alerter import run_alerter
    from workers.utils.pipeline_utils import (
        log_pipeline_start, log_pipeline_complete, log_pipeline_failed,
    )
    from datetime import date as _date
    run_id = None
    try:
        run_id = log_pipeline_start("pipeline_failure_alerter",
                                    _date.today().isoformat())
    except Exception:
        run_id = None
    try:
        counters = run_alerter()
        if counters["alerted_now"] or counters["recovered"]:
            console.print(
                f"[yellow]Failure alerter: stuck={counters['stuck_count']} "
                f"alerted={counters['alerted_now']} skipped={counters['skipped_dedup']} "
                f"recovered={counters['recovered']}[/yellow]"
            )
        if run_id:
            try:
                log_pipeline_complete(run_id, metadata=counters)
            except Exception:
                pass
    except Exception as e:
        if run_id:
            try:
                log_pipeline_failed(run_id, str(e))
            except Exception:
                pass
        raise


def job_retrain_healthcheck():
    """RETRAIN-HEALTHCHECK (2026-06-21): Mon/Tue 09:00 UTC sentinel for the
    weekly Sunday retrain. Alerts when (a) latest successful retrain is
    >9 days old, or (b) 2+ consecutive non-completed runs since last
    success. DB-backed dedup via pipeline_health_state.

    Mon 09:00 catches a Sunday-03:00 failure ~30h after the fact (first
    business-day alert). Tue 09:00 covers a Mon recurrence + a Sunday
    failure that the Mon alert may have missed if the operator silenced
    Telegram. Quiet on healthy. Logs a one-liner when alert or recovery
    fires."""
    from workers.jobs.retrain_healthcheck import run_retrain_healthcheck
    counters = run_retrain_healthcheck()
    if counters.get("alert_sent") or counters.get("recovery_sent"):
        console.print(
            f"[yellow]Retrain healthcheck: status={counters['status']} "
            f"reason={counters['reason']} alert_sent={counters['alert_sent']} "
            f"recovery_sent={counters['recovery_sent']}[/yellow]"
        )
    _run_job("retrain_healthcheck", lambda: None)


def job_coolbet_daemon_healthcheck():
    """COOLBET-DAEMON-HEALTHCHECK (2026-06-21): VPS-side safety net for
    the Mac daemon's in-process alert path. Reads coolbet_session_state +
    coolbet_heal_log every 30 min and Telegrams when the daemon is silent
    (>90m since last tick) or sustainedly erroring (>2h without a
    successful auto-heal). DB-backed dedup survives scheduler restarts.

    Quiet on healthy. Logs a summary line when it fires (alert/recovery)."""
    from workers.jobs.coolbet_daemon_healthcheck import run_daemon_healthcheck
    counters = run_daemon_healthcheck()
    if counters.get("alert_sent") or counters.get("recovery_sent"):
        console.print(
            f"[yellow]Coolbet daemon healthcheck: status={counters['status']} "
            f"reason={counters['reason']} alert_sent={counters['alert_sent']} "
            f"recovery_sent={counters['recovery_sent']}[/yellow]"
        )
    _run_job("coolbet_daemon_healthcheck", lambda: None)


def job_epicbet_odds_freshness():
    """EPICBET-403-FROM-VPS-2026-08-29 — DB-side staleness watchdog for the
    Epicbet feed, the thing whose absence let a six-day outage pass unnoticed.

    Coolbet has had one since 2026-07-03 (it caught a 7-day silent outage);
    Epicbet did not, so 277 consecutive 'completed' runs writing zero rows
    raised nothing. Reads odds_snapshots directly, so it is indifferent to
    where the writer runs or whether the job reported success.
    """
    from workers.jobs.odds_freshness import check_feed
    c = check_feed("Epicbet")
    if c.get("alert_sent") or c.get("recovery_sent"):
        console.print(f"[yellow]Epicbet freshness: {c['status']} — {c['reason']}[/yellow]")
    _run_job("epicbet_odds_freshness", lambda: None)


def job_coolbet_odds_freshness():
    """COOLBET-ODDS-FRESHNESS-WATCHDOG (2026-07-03): DB-side freshness
    watchdog for Coolbet odds. The writer now runs on Mac launchd
    (post COOLBET-SCRAPERS-MOVED-TO-MAC), so its exit codes don't reach
    the scheduler-side alerter. This job reads odds_snapshots directly
    and Telegrams if MAX(timestamp) for bookmaker='Coolbet' goes stale,
    closing the class of failure that hid the 2026-06-26 → 2026-07-03
    seven-day silent outage. See workers/jobs/coolbet_odds_freshness.py.
    """
    from workers.jobs.coolbet_odds_freshness import run_coolbet_odds_freshness_check
    counters = run_coolbet_odds_freshness_check()
    if counters.get("alert_sent") or counters.get("recovery_sent"):
        console.print(
            f"[yellow]Coolbet odds freshness: status={counters['status']} "
            f"reason={counters['reason']} alert_sent={counters['alert_sent']} "
            f"recovery_sent={counters['recovery_sent']}[/yellow]"
        )
    _run_job("coolbet_odds_freshness", lambda: None)


def job_flaresolverr_sweep():
    """COOLBET-FS-SESSION-STABLE sweeper (2026-06-11): hourly destroys
    stale FlareSolverr sessions that aren't in the active whitelist.

    Root cause this fixes: every scraper that calls sessions.create without
    a matching sessions.destroy leaks a Chrome instance. the scheduler hit the
    slot limit on 2026-06-11 and sessions.create started hanging — the
    symptom that triggered the architectural rewrite. Hourly sweeping
    bounds future damage to ~1h of leaked sessions even if a scraper
    crashes mid-run."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "scripts/coolbet/sweep_stale_sessions.py"],
        capture_output=True, text=True, timeout=180,
    )
    for line in result.stdout.splitlines():
        if any(k in line for k in ("destroyed", "stale", "no stale")):
            console.print(f"[dim]{line}[/dim]")
    _run_job("flaresolverr_sweep", lambda: None)




def job_league_clv_efficiency():
    """LEAGUE-CLV-EFFICIENCY (2026-05-25): weekly compute of per-league CLV
    beatability index. Runs Sunday 02:30 UTC, before the weekly_retrain at
    03:00 so the freshly-computed league_clv_efficiency signal is in
    match_signals before MFV gets rebuilt. Persists to match_signals as
    a per-match league-inherited signal.
    """
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/compute_league_clv_efficiency.py", "--days", "60", "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=600,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]league_clv_efficiency exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"league_clv_efficiency failed: exit {result.returncode}")
        console.print(result.stdout[-2000:])
    _run_job("league_clv_efficiency", _run)


def job_league_season_phase():
    """LEAGUE-SEASON-PHASE (2026-05-25): per-match season-progress signal.
    Backtest: late-vs-early shifts +7.7pp Over 2.5, +6.0pp BTTS, +6.7pp home
    — materially affects OU + BTTS + 1X2 markets.
    """
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/compute_league_season_phase.py", "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=300,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]league_season_phase exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"league_season_phase failed: exit {result.returncode}")
        console.print(result.stdout[-1500:])
    _run_job("league_season_phase", _run)


def job_line_velocity():
    """LINE-VELOCITY (2026-05-25): nightly Pinnacle line slope.
    Backtest: Q4 |v| vs Q1 → -6.6pp CLV-beat — REVERSE signal. Meta-model
    should down-weight high-|v| bets (we end up wrong side at close).
    """
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/compute_line_velocity.py", "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=600,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]line_velocity exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"line_velocity failed: exit {result.returncode}")
        console.print(result.stdout[-1500:])
    _run_job("line_velocity", _run)


def job_prune_live_snapshots():
    """LIVE-SNAPSHOTS-PRUNE (2026-05-25): weekly prune of live_match_snapshots.
    Keeps 5-min boundaries + event-adjacent rows for matches finished ≥48h ago.
    Expected ~50% reduction. Runs Sunday 01:00 UTC, before weekly_retrain at 03:00.
    """
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/prune_live_snapshots.py", "--apply"],
            cwd=str(Path(__file__).parent.parent),
            timeout=900,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]prune_live_snapshots exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"prune_live_snapshots failed: exit {result.returncode}")
        console.print(result.stdout[-1500:])
    _run_job("prune_live_snapshots", _run)


def _drain_manual_placement_queue():
    """MANUAL-PLACE (2026-05-29): drain admin "Record at Coolbet" requests.

    The Vercel webhook (Telegram callback_query handler) inserts a row into
    manual_placement_queue when the admin taps the inline button on a value-bet
    alert. This job runs every 10s, claims pending rows, calls the placer
    with --bet-id filter, and edits the original Telegram message with the
    outcome.

    Stays silent when there's nothing pending (no pipeline_runs entries, no
    console output). Idempotency: place_bet_by_id() short-circuits to
    `already_recorded` when a real_bet row already exists for the
    simulated_bet_id, so double-taps or auto-record racing the button are
    both safe.
    """
    from workers.api_clients.db import execute_write, execute_write_returning
    from workers.automation.coolbet_placer import place_bet_by_id
    from workers.notify.telegram import edit_telegram_message

    # Claim up to 3 pending rows in one tick — keeps Telegram round-trips
    # serialised but lets a burst of taps drain quickly.
    claimed = execute_write_returning(
        """
        UPDATE manual_placement_queue
           SET status = 'processing'
         WHERE id IN (
             SELECT id FROM manual_placement_queue
              WHERE status = 'pending'
              ORDER BY requested_at
              LIMIT 3
         )
        RETURNING id, simulated_bet_id, telegram_chat_id, telegram_message_id
        """,
    )
    if not claimed:
        return

    # SCHEDULER-DRAIN-TIMEOUT-2026-08-16 — the historical hang pattern
    # (SCHEDULER-AF-429-DEADLOCK, 4 occurrences in 5 weeks) traces back to
    # place_bet_by_id blocking indefinitely when Coolbet auth is broken
    # (CDP-Chrome down / JWT expired / FS session hung). Because APScheduler
    # runs each job in a worker thread and max_instances=1 blocks future
    # ticks, one stuck drain freezes this whole 10s-interval job forever.
    # Wrap the call in a ThreadPoolExecutor.submit(...).result(timeout=90)
    # so a hung placer surfaces as a TimeoutError instead of a permanent
    # worker lock. 90s ceiling — a real Coolbet placement takes ~5-15s.
    import concurrent.futures as _cf
    _drain_executor = _cf.ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="drain-place")

    for row in claimed:
        queue_id = row["id"]
        sim_id = str(row["simulated_bet_id"])
        chat_id = row.get("telegram_chat_id")
        message_id = row.get("telegram_message_id")
        try:
            future = _drain_executor.submit(place_bet_by_id, sim_id)
            result = future.result(timeout=90)
        except _cf.TimeoutError:
            console.print(f"[red]manual_placement_drain {sim_id} TIMEOUT after 90s — "
                          "likely Coolbet auth or FS session down[/red]")
            result = {"outcome": "error",
                       "reason": "timeout_90s_likely_coolbet_auth_or_fs_down"}
        except Exception as e:
            console.print(f"[red]manual_placement_drain {sim_id} failed: {e}[/red]")
            result = {"outcome": "error", "reason": str(e)[:300]}

        outcome = result.get("outcome") or "error"
        # Build status line for Telegram edit. Keep it short — fits as a tail line.
        if outcome == "placed":
            stake = float(result.get("stake") or 0)
            odds = float(result.get("live_odds") or result.get("model_odds") or 0)
            status_line = f"✓ Recorded €{stake:.2f} @ {odds:.2f}"
        elif outcome == "already_recorded":
            status_line = "✓ Already recorded"
        elif outcome == "no_event":
            status_line = "✗ no_event (Coolbet doesn't list this match)"
        elif outcome == "no_market":
            reason = result.get("reason") or ""
            status_line = f"✗ no_market{f' — {reason}' if reason else ''}"[:200]
        elif outcome == "search_blocked":
            status_line = "✗ search_blocked (refresh Imperva cookies)"
        elif outcome == "edge_eroded":
            status_line = "✗ edge_eroded (odds moved against us)"
        elif outcome == "guard_skip":
            status_line = f"✗ guard_skip — {result.get('reason') or ''}"[:200]
        elif outcome == "not_found":
            status_line = "✗ bet not in DB (settled or deleted?)"
        else:
            status_line = f"✗ {outcome}"

        # Edit the original message: append status, remove the button
        if chat_id and message_id:
            edited = edit_telegram_message(
                chat_id, int(message_id),
                f"<b>{status_line}</b>\n\n<i>(original alert via MANUAL-PLACE)</i>",
                remove_buttons=True,
            )
            if not edited:
                console.print(f"[yellow]Telegram edit failed for queue={queue_id}[/yellow]")

        execute_write(
            """
            UPDATE manual_placement_queue
               SET status = 'done',
                   result = %s,
                   result_detail = %s,
                   processed_at = NOW()
             WHERE id = %s
            """,
            (outcome, status_line[:500], queue_id),
        )

    # Release the per-tick executor. wait=False so a still-hung placement
    # doesn't block the drain from returning — the worker thread dies with
    # the executor and we'll re-instantiate next tick. Hung threads are
    # daemon and get reaped on scheduler exit.
    _drain_executor.shutdown(wait=False)


def job_league_draw_rate():
    """LEAGUE-DRAW-YTD (2026-05-25): nightly recompute of per-league season-to-date
    draw rate. Backtest: Q4 vs Q1 actual-draw gap +11.6pp on 11,875 historical
    matches — real signal. Feeds next MFV rebuild + retrain.
    """
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/compute_league_draw_rate.py", "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=300,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]league_draw_rate exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"league_draw_rate failed: exit {result.returncode}")
        console.print(result.stdout[-1500:])
    _run_job("league_draw_rate", _run)


def job_xg_overperformance():
    """SIG-12 (2026-05-25): nightly rolling team xG-overperformance signal.
    Reads last live snapshot per settled match (minute ≥80, xG present),
    computes per-team (goals - xG) rolling 10-match mean, writes to
    match_signals as xg_overperf_home/away. Feeds next MFV rebuild +
    next B-ML3 retrain."""
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/compute_xg_overperformance.py", "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=600,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]xg_overperformance exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"xg_overperformance failed: exit {result.returncode}")
        console.print(result.stdout[-1500:])
    _run_job("xg_overperformance", _run)


def job_injury_severity():
    """INJURY-SEVERITY (2026-05-25): nightly bucketing of match_injuries by
    severity (SEVERE 3× / MODERATE 1.5× / MINOR 0.5× / UNKNOWN 1×). Writes
    weighted score per (match, team_side) to match_signals
    (injury_severity_score_home/away). Feeds next MFV rebuild + B-ML3 v3+.
    """
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/compute_injury_severity.py", "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=300,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]injury_severity exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"injury_severity failed: exit {result.returncode}")
        console.print(result.stdout[-1500:])
    _run_job("injury_severity", _run)


def job_team_avg_player_rating():
    """AF-PLAYER-RATINGS (2026-05-25): nightly rolling-team-rating refresh.
    Computes per-team last-10-match average AF player rating from
    match_player_stats and stores as match_signals rows
    (team_avg_player_rating_home/away). Feeds the next MFV rebuild / B-ML3
    v3+ training cohort.
    """
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/compute_team_avg_player_rating.py", "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=900,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]team_avg_player_rating exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(f"team_avg_player_rating failed: exit {result.returncode}")
        console.print(result.stdout[-1500:])
    _run_job("team_avg_player_rating", _run)


def job_daily_real_perf_email():
    """DAILY-REAL-PERF-EMAIL (2026-05-25): captures yesterday + 7d real-bet
    performance split (placer vs manual) and emails the summary via Resend.
    Runs after settlement so yesterday's results are final."""
    from workers.jobs.daily_real_perf_email import send_daily_real_perf
    _run_job("daily_real_perf_email", send_daily_real_perf)


def job_aln_auto_tune():
    """ALN-AUTO (2026-05-25): monthly alignment bump re-tune. Runs aln1_tune
    analysis on a 60d window; emails a diff if any class needs |Δ| ≥ 0.005
    with n ≥ 100. Never auto-applies — human approves the bump."""
    from workers.jobs.aln_auto_tune import run_aln_auto_tune
    _run_job("aln_auto_tune", run_aln_auto_tune)


def job_mfv_v3_signals_propagate():
    """MFV-V3-SIGNALS-NIGHTLY-PROPAGATE (2026-06-22): copy match_signals
    rows for the v3 signal set (season_progress, line_velocity, xg_overperf,
    league_clv_efficiency, injury_severity, team_avg_player_rating,
    league_draw_rate_ytd) into the corresponding MFV columns.

    Without this, the nightly signal writers populate `match_signals` but
    MFV columns stay NULL — the bug surfaced 2026-06-22 when MFV.season_progress
    was 0% covered for 90 days because this script was never on cron.

    Idempotent (COALESCE keeps existing non-NULL). Rolling window covers
    last 60d so cron misfires self-heal."""
    import subprocess
    from datetime import date as _date, timedelta as _td
    def _run():
        since = (_date.today() - _td(days=60)).isoformat()
        result = subprocess.run(
            [sys.executable, "scripts/backfill_mfv_v3_signals.py",
             "--since", since, "--write"],
            cwd=str(Path(__file__).parent.parent),
            timeout=900,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]mfv_v3_signals_propagate exit "
                          f"{result.returncode}: {result.stderr[-1000:]}[/yellow]")
            raise RuntimeError(
                f"mfv_v3_signals_propagate failed: exit {result.returncode}"
            )
        console.print(result.stdout[-1500:])
    _run_job("mfv_v3_signals_propagate", _run)


def job_nightly_mfv_b_ml3_refresh():
    """MFV-B-ML3-V2-NIGHTLY-REFRESH (2026-05-25): re-runs the B-ML3 v2 feature
    backfill nightly so MFV rows for matches that just finished settle into
    the new columns. Cheaper than modifying the live MFV builder which has
    ordering issues with T-6h snapshot availability at build time. Idempotent
    via WHERE …_at_t6h IS NULL guard in the backfill script.
    """
    import subprocess
    def _run():
        # Roll a 7-day window so we backfill recent settled matches + any
        # earlier ones still NULL (covers cron misfires).
        result = subprocess.run(
            [sys.executable, "scripts/backfill_mfv_b_ml3_v2_features.py",
             "--since", "2026-05-06"],
            cwd=str(Path(__file__).parent.parent),
            timeout=1200,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]mfv_b_ml3_refresh exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
        console.print(result.stdout[-1500:])
    _run_job("mfv_b_ml3_nightly_refresh", _run)


def job_nightly_mfv_form_momentum_refresh():
    """MFV-FORM-MOMENTUM-NIGHTLY-REFRESH (2026-05-25): same pattern for the
    form_momentum_home/away columns. Idempotent via NULL guards."""
    import subprocess
    def _run():
        result = subprocess.run(
            [sys.executable, "scripts/backfill_mfv_form_momentum.py",
             "--since", "2026-05-06"],
            cwd=str(Path(__file__).parent.parent),
            timeout=600,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"[yellow]mfv_form_momentum_refresh exit {result.returncode}: {result.stderr[-1000:]}[/yellow]")
        console.print(result.stdout[-1500:])
    _run_job("mfv_form_momentum_nightly_refresh", _run)


def job_watchlist_alerts():
    from workers.jobs.watchlist_alerts import run_watchlist_alerts
    _run_job("watchlist_alerts", run_watchlist_alerts)






# EMAIL-DIGEST-RETIRED-2026-09-03. The subscriber-facing email jobs
# (job_email_digest, job_value_bet_alert_afternoon/evening) lived here as
# wrappers long after SCHEDULER-CLEANUP (da9bf97, 2026-07-07) removed their
# add_job() registrations, so they had not run for two months while still
# reading as live code. Removed rather than left orphaned: with 52 registered
# users (51 free) there is no audience for a digest, and a wrapper nobody calls
# is a standing invitation to assume the feature works.
#
# workers/jobs/email_digest.py is deliberately KEPT — the prestige filter and
# qualifies_today gate are still covered by smoke tests, and the module is what
# a future paid tier would build on.
#
# OPERATOR email is unaffected and must stay: job_pipeline_runs_failure_digest,
# job_weekly_threshold_check, job_daily_real_perf_email, retrain and meta
# reports. That is the monitoring that surfaces silent failures.

def job_settlement():
    # _log_run=False — settlement_pipeline's first sub-step already logs to
    # pipeline_runs as job_name='settlement'. Letting the wrapper log too
    # would write a duplicate row per run.
    _run_job("settlement", settlement_pipeline, _log_run=False)


def job_stall_watchdog():
    """Detect a job that has occupied its worker thread past JOB_STALL_WARN_S and
    dump every thread's stack so the hang is diagnosable from the log alone.

    SCHEDULER-STALL-RCA (2026-08-24). On 2026-08-22 settle_ready started at 06:15
    and never returned; max_instances=1 then silently skipped every run until
    after 11:45, leaving 439 matches unsettled and 1,760 zombie shadow bets. The
    only trace in the journal was a repeating "max_instances blocked" line, which
    says a job is stuck but not where — pinning it down needed hours of log
    archaeology plus a py-spy install after the fact.

    Uses sys._current_frames() rather than py-spy so it has zero external deps and
    works identically on the VPS and locally. Reports once per (thread, job) so a
    long hang produces one dump, not one every tick.
    """
    import traceback as _tb
    now = time.monotonic()
    with _inflight_lock:
        snapshot = {tid: dict(meta) for tid, meta in _inflight.items()}
    stalled = [
        (tid, meta) for tid, meta in snapshot.items()
        if now - meta["started_monotonic"] >= JOB_STALL_WARN_S
    ]
    if not stalled:
        return

    frames = sys._current_frames()
    for tid, meta in stalled:
        key = (tid, meta["job"])
        with _inflight_lock:
            if key in _stall_reported:
                continue
            _stall_reported.add(key)
        elapsed_min = (now - meta["started_monotonic"]) / 60
        frame = frames.get(tid)
        stack = "".join(_tb.format_stack(frame)) if frame else "<thread gone>"
        console.print(f"\n[red]{'═' * 60}[/red]")
        console.print(
            f"[red bold]JOB STALLED: {meta['job']} — running {elapsed_min:.0f} min "
            f"(started {meta['started_at']}, thread {tid})[/red bold]"
        )
        console.print(f"[red dim]{stack}[/red dim]")
        console.print(f"[red]{'═' * 60}[/red]")
        _recent_errors.append({
            "job": meta["job"],
            "error": (f"STALLED {elapsed_min:.0f}min — stack tail: "
                      f"{stack.strip().splitlines()[-1][:300] if stack.strip() else 'n/a'}"),
            "at": datetime.now(timezone.utc).isoformat(),
        })
        if len(_recent_errors) > _MAX_RECENT_ERRORS:
            _recent_errors.pop(0)
        try:
            from workers.notify.telegram import send_telegram
            send_telegram(
                f"\u26a0\ufe0f Scheduler job stalled: {meta['job']} running "
                f"{elapsed_min:.0f} min. Stack dumped to journalctl.",
                dedup_key=f"job-stall:{meta['job']}",
                dedup_window_s=3600,
            )
        except Exception:
            pass


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
    """Match stats/events backfill — micro-batch every 5min, 30 req/run.
    One run ≈ 10-15 fixtures. Picks up where it left off via backfill_progress table."""
    from scripts.backfill_historical import run_backfill
    # _log_run=False — run_backfill internally logs job_name='hist_backfill'.
    _run_job("hist_backfill", run_backfill, max_requests=30, _log_run=False)


def job_backfill_coaches():
    """Coaches backfill — micro-batch every 25min, 10 teams/run.
    Fetches /coachs for teams never seen before. Self-skips when all caught up."""
    from scripts.backfill_coaches import run_batch
    _run_job("backfill_coaches", run_batch, batch_size=10)


def job_backfill_transfers():
    """Transfers backfill — 25 teams/run every 25min.
    Fetches /transfers for teams not in team_transfer_cache. Self-skips when done."""
    from scripts.backfill_transfers import run_batch
    _run_job("backfill_transfers", run_batch, batch_size=25)


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


# WC-BRACKET-SCORING (2026-06-02): tournament-window gate. Runs only between
# the first kickoff date (2026-06-11) and the final date inclusive (2026-07-19).
# Outside that window the job exits immediately as a no-op so APScheduler isn't
# generating useless pipeline_runs rows for the other ~340 days of the year.










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
    """OBS-HEARTBEAT: Ping healthchecks.io + Uptime Kuma every 5 min to
    confirm scheduler is alive. Both external heartbeats no-op if their
    respective env vars aren't set (HEALTHCHECKS_IO_PING_URL for the
    healthchecks.io one, KUMA_URL_BASE + KUMA_TOKENS['healthcheck_ping']
    for Kuma). No pipeline_runs row on purpose — 288 rows/day for a
    liveness check would swamp the ops dashboard.
    """
    ping_url = os.getenv("HEALTHCHECKS_IO_PING_URL", "")
    if not ping_url:
        # Still push Kuma even if healthchecks.io isn't configured —
        # Kuma is the new preferred heartbeat.
        _kuma_push("healthcheck_ping", status="up")
        return
    t0 = time.time()
    try:
        import urllib.request
        urllib.request.urlopen(ping_url, timeout=10)
        _kuma_push("healthcheck_ping", status="up",
                   ping_ms=int((time.time() - t0) * 1000))
    except Exception as e:
        console.print(f"[yellow]Healthcheck ping failed: {e}[/yellow]")
        _kuma_push("healthcheck_ping", status="down", msg=str(e)[:120])


# ── Health endpoint ────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            from workers.api_clients.api_football import budget
            from workers.api_clients.db import get_pool_status

            with _last_job_lock:
                last = dict(_last_job)

            pool = get_pool_status()
            body = json.dumps({
                "status": "ok",
                "uptime_seconds": int(time.time() - _start_time),
                "shadow_mode": SHADOW_MODE,
                "api_budget": budget.status(),
                "pool": pool,
                "pool_alert": pool["pct"] >= 80,
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

def _maybe_catchup_missed_settlement():
    """If the last successful 'settlement' run was >25h ago, a daily settlement
    is missing. Fire one in a background thread so startup isn't blocked.

    Background: every git push triggers a a scheduler restart, which kills any
    in-flight job. With heavy dev cadence the 21:00/23:30/01:00 settlement
    triples can all be killed mid-run. Without this catch-up, finished matches
    sit unsettled until the next 21:00 window.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT MAX(completed_at) AS last_ok
               FROM pipeline_runs
               WHERE job_name = 'settlement' AND status = 'completed'""",
            [],
        )
        last_ok = rows[0]["last_ok"] if rows else None
        if last_ok is None:
            console.print("[yellow]Settlement catch-up: no prior successful run on record — skipping[/yellow]")
            return
        from datetime import datetime, timezone, timedelta
        age = datetime.now(timezone.utc) - last_ok
        if age < timedelta(hours=25):
            return
        console.print(f"[yellow]Settlement catch-up: last successful run was {age} ago — firing now[/yellow]")

        def _run_catchup():
            time.sleep(60)  # let scheduler + health endpoint settle first
            try:
                _run_job("settlement", settlement_pipeline, _log_run=False)
            except Exception as e:
                console.print(f"[red]Settlement catch-up failed: {e}[/red]")

        threading.Thread(target=_run_catchup, daemon=True).start()
    except Exception as e:
        console.print(f"[yellow]Settlement catch-up check errored (non-fatal): {e}[/yellow]")


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
    console.print("[bold green]   OddsIntel Pipeline Scheduler starting...   [/bold green]")
    console.print("[bold green]═══════════════════════════════════════════════[/bold green]")

    if SHADOW_MODE:
        console.print("[yellow]SHADOW MODE: job names prefixed with 'railway_'[/yellow]")

    # Start health endpoint FIRST — responds on :8080/health for external monitors.
    _start_health_server()

    # Clean up orphaned "running" records from previous process (restart/kill).
    # 10-min threshold catches jobs that were <30 min old under the old logic.
    _cleanup_stale_runs(threshold_minutes=10, label="scheduler restarted")

    # SETTLEMENT-CATCHUP: if last night's daily settlement got killed mid-run
    # (process restart, host reboot), the 21:00 / 23:30 / 01:00 redundant runs
    # may all have been wiped. Detect that and fire one settlement run shortly
    # after startup.
    _maybe_catchup_missed_settlement()

    # Sync budget in background (API call can take 2-5s, don't block startup)
    def _initial_budget_sync():
        try:
            from workers.api_clients.api_football import budget
            budget.sync_with_server(source="startup")
        except Exception as e:
            console.print(f"[yellow]Initial budget sync failed: {e}[/yellow]")

    threading.Thread(target=_initial_budget_sync, daemon=True).start()

    # Create scheduler — coalesce + max_instances=1 prevent same-job stacking;
    # executor cap of 4 bounds cross-job concurrency. APScheduler's default is
    # 10 threads, which combined with LivePoller + Flask + InplayBot can fan out
    # to 15+ simultaneous DB conns at startup when missed jobs catch up. 4 is
    # plenty for the actual workload (most jobs are short, scheduled minutes apart).
    # misfire_grace_time=300: APScheduler's default is 1s, which causes
    # once-a-day jobs (Watchlist 08:30, Stripe Reconcile 09:00, Odds 11:00…) to be
    # silently skipped when GIL contention from LivePoller/Flask/InplayBot delays
    # the scheduler thread by 2-3s at fire time. 5 min is wider than any normal
    # jitter and shorter than the smallest job interval (5min healthcheck), so
    # late runs still execute promptly. coalesce=True keeps stale bursts to one run.
    # SCHEDULER-AF-429-DEADLOCK mitigation 2026-07-18: max_workers 4→12.
    # Two multi-hour hangs (Jul 12, Jul 15) both happened when 5 concurrent
    # AF-touching jobs (budget_sync, fetch_odds, odds_refresh, settle_ready,
    # bracket slot-sync) blocked on AF 429s and drained the 4-worker
    # pool. 12 gives headroom so a stuck cluster can't lock the scheduler.
    # Real fix is finite timeouts + rate limiter in api_football.py — P0.
    scheduler = BackgroundScheduler(
        timezone="UTC",
        executors={"default": APSThreadPoolExecutor(max_workers=12)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )

    # SCHEDULER-HANG-MITIGATION (2026-06-01) — listener fires whenever a job
    # is blocked from starting because the previous instance is still
    # "running" (i.e. consuming a worker thread). On the 2026-06-01 hang at
    # 14:35 UTC, three jobs occupied 3 of 4 threads and downstream jobs were
    # silently dropped. With this listener, the next scheduler hang surfaces
    # in stdout + _recent_errors immediately rather than going unnoticed
    # until /performance numbers stay frozen.
    from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_ERROR
    def _on_max_instances_blocked(event):
        msg = (
            f"max_instances blocked: job_id={event.job_id} — a previous run "
            f"is still occupying its worker. Likely a hung job or a slow "
            f"shared lock (Coolbet session / AF semaphore / DB pool). "
            f"Check pipeline_runs for rows in 'running' state >5 min."
        )
        console.print(f"[red bold]SCHEDULER WARNING[/red bold] {msg}")
        _recent_errors.append({
            "job": event.job_id,
            "error": msg[:500],
            "at": datetime.now(timezone.utc).isoformat(),
        })
        if len(_recent_errors) > _MAX_RECENT_ERRORS:
            _recent_errors.pop(0)
    scheduler.add_listener(_on_max_instances_blocked, EVENT_JOB_MAX_INSTANCES)

    # ── Register all jobs ──────────────────────────────────────────────

    # MANUAL-PLACE drain: 10s tick to consume admin "Record at Coolbet" taps.
    # Stays silent when empty — only logs when there's work. Skips _run_job
    # so the pipeline_runs table isn't flooded with 8640 entries/day.
    scheduler.add_job(_drain_manual_placement_queue, IntervalTrigger(seconds=10),
                      id="manual_placement_drain", name="Manual Placement Drain [10s]")

    # Backfill jobs — micro-batch, runs every 25min.
    # If a run fails, only ~25-30 API calls are lost. Progress tracked in DB.
    # AF budget: 75K/day. At 25min intervals: hist=30×57=1,710, coaches=10×57=570,
    # transfers=25×57=1,425 → ~3,705/day total, well within headroom.
    # 25min interval: worst case = 15s timeout × 3 retries × 30 requests = 22 min max,
    # giving 3 min buffer. At 25 transfers/run: 7,400 teams ÷ 57 runs/day ≈ 5 days to complete.
    scheduler.add_job(job_backfill, IntervalTrigger(minutes=25),
                      id="hist_backfill", name="Match Stats/Events Backfill")
    scheduler.add_job(job_backfill_coaches, IntervalTrigger(minutes=25),
                      id="backfill_coaches", name="Coaches Backfill")
    scheduler.add_job(job_backfill_transfers, IntervalTrigger(minutes=25),
                      id="backfill_transfers", name="Transfers Backfill")

    # Fixture status refresh: 6× daily, 15 min before each betting window
    # Re-fetches today's fixtures to catch postponements/cancellations/time changes.
    # store_match() now updates status → 'postponed' for PST/CANC matches.
    # 12:45 + 17:15 added alongside new 13:30 + 17:30 betting slots (BET-TIMING-ANALYSIS).
    for hour, minute in [(9, 15), (10, 45), (12, 45), (14, 45), (17, 15), (18, 45)]:
        scheduler.add_job(job_fixture_refresh, CronTrigger(hour=hour, minute=minute),
                          id=f"fixture_refresh_{hour:02d}{minute:02d}",
                          name=f"Fixture Refresh {hour:02d}:{minute:02d}")

    # Morning pipeline: 04:00 UTC
    scheduler.add_job(job_morning, CronTrigger(hour=4, minute=0),
                      id="morning_pipeline", name="Morning Pipeline")

    # Odds refresh: every 30min, 24/7 (WC-OVERNIGHT-COVERAGE 2026-06-12).
    # Previously windowed 07-22 UTC, which left a 9-hour dead zone covering
    # overnight WC kickoffs (e.g. Korea-Czech 02:00 UTC on 2026-06-12 — odds
    # last refreshed at 22:30 the prior day, betting refresh never ran on
    # near-KO odds). Now runs hourly through the night so the betting refresh
    # (also expanded to 24/7 below) has fresh odds for any near-KO match.
    # 20:00 still replaced by pre-KO mark_closing run (marks closing odds).
    for hour in range(0, 24):
        for minute in [0, 30]:
            if hour == 20 and minute == 0:
                continue  # 20:00 is handled by pre-KO mark_closing below
            scheduler.add_job(job_odds_refresh, CronTrigger(hour=hour, minute=minute),
                              id=f"odds_{hour:02d}{minute:02d}", name=f"Odds {hour:02d}:{minute:02d}")

    # OPENING-LINE-MOVE-CAPTURE (2026-05-25, rev 2): the earlier overnight slots
    # at 02:00 + 04:00 UTC fetched TODAY's matches — but today's matches had no
    # prior snapshot to diff against (~0% MFV.overnight_line_move coverage). Fix:
    # at 22:00 UTC fetch TOMORROW's odds (using the fixture rows just stored by
    # job_morning step 2 of the SAME day). The next morning's 04:00 fetch then
    # produces the 'yesterday → today' delta that batch_write_morning_signals
    # converts into match_signals.overnight_line_move. Removed the redundant
    # 02:00 + 04:00 odds_refresh slots — morning_pipeline already includes
    # run_odds for today at 04:00 (step 4/6).
    scheduler.add_job(job_odds_tomorrow, CronTrigger(hour=22, minute=0),
                      id="odds_tomorrow_2200",
                      name="Odds (tomorrow) 22:00 — OPENING-LINE-MOVE-CAPTURE")

    # T24H-COVERAGE (2026-06-24): the day-ahead backtest landed today
    # (dev/active/day_ahead_backtest_results.json) found that picks with a
    # T-24h max-odds snap return +19.76% ROI vs the production baseline of
    # +9.80% — BUT only 15% of picks currently have a T-24h snap because
    # the single 22:00 UTC fetch only covers ~22:00-next-day kickoffs at
    # T-24h. Add four more fetches of tomorrow's odds (04, 10, 16, 22)
    # spaced every 6h so every kickoff hour gets a fetch within ±3h of
    # T-24h. Target: coverage 15% → 60-70% on the high-ROI early-fire
    # cohort. Cost: ~50 AF calls/day (4 × ~10 paginated) on a 7500/day
    # budget — cheap.
    scheduler.add_job(job_odds_tomorrow, CronTrigger(hour=4, minute=0),
                      id="odds_tomorrow_0400",
                      name="Odds (tomorrow) 04:00 — T24H-COVERAGE")
    scheduler.add_job(job_odds_tomorrow, CronTrigger(hour=10, minute=0),
                      id="odds_tomorrow_1000",
                      name="Odds (tomorrow) 10:00 — T24H-COVERAGE")
    scheduler.add_job(job_odds_tomorrow, CronTrigger(hour=16, minute=0),
                      id="odds_tomorrow_1600",
                      name="Odds (tomorrow) 16:00 — T24H-COVERAGE")

    # Odds pre-kickoff (mark_closing): 13:30, 17:30, 20:00 UTC
    # 20:00 covers 19:00-21:00 KO window (replaces regular 20:00 refresh — marks CLV closing line)
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=13, minute=30),
                      id="odds_prekick_1330", name="Odds Pre-KO 13:30")
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=17, minute=30),
                      id="odds_prekick_1730", name="Odds Pre-KO 17:30")
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=20, minute=0),
                      id="odds_prekick_2000", name="Odds Pre-KO 20:00")

    # CLOSING-LINE-COVERAGE: per-fixture closing snap every 5 min, peak hours.
    # Catches matches with kickoff in T-15→T+5 — without this only ~25% of
    # bets had a Pinnacle pre-KO snap, hurting CLV measurement.
    scheduler.add_job(job_closing_snap, CronTrigger(hour="12-23", minute="*/5"),
                      id="closing_snap_5min", name="Closing snap (5min)")

    # AF-INJURIES-LATE (2026-06-01): single 08:00 UTC injury fetch replaces
    # the previous 10:30 + 13:00 + 16:00 schedule. ~30 AF calls/day saved.
    # News-event-triggered refresh path filed as follow-up.
    scheduler.add_job(job_injuries_morning, CronTrigger(hour=8, minute=0),
                      id="injuries_morning", name="Injuries (morning) 08:00")

    # Full enrichment: 13:00 UTC — injuries, H2H, team_stats (standings moved to 23:30)
    # N7 fix: H2H + team_stats were only fetched in morning pipeline; this refresh
    # ensures afternoon/evening betting runs have up-to-date context.
    scheduler.add_job(job_enrichment_full, CronTrigger(hour=13, minute=0),
                      id="enrichment_full_13", name="Enrichment Full 13:00")

    # Standings nightly: 23:30 UTC — AF-STANDINGS-DAILY
    # Standings update ~1x/week; daily once-at-night is sufficient.
    scheduler.add_job(job_standings_nightly, CronTrigger(hour=23, minute=30),
                      id="standings_nightly", name="Standings Nightly 23:30")

    # Betting refresh: every 30 min, 5 min after each odds refresh, 24/7
    # (WC-OVERNIGHT-COVERAGE 2026-06-12 — was windowed 07:05–22:35 UTC, which
    # missed overnight WC kickoffs entirely). run_betting() is DB-only
    # (skip_fetch=True), zero AF calls. Hard dedup on (bot_id, match_id,
    # market, selection) means no duplicate bets ever written. New bets only
    # appear when fresh odds create a new edge or a new match is priced.
    # Cohort (morning/midday/pre_ko) is auto-detected from UTC hour by
    # _current_cohort(); overnight runs land in whichever cohort the hour maps to.
    scheduler.add_job(job_betting_refresh_wrapper, CronTrigger(hour="*", minute="5,35"),
                      id="betting_refresh_interval", name="Betting Refresh [30min]")

    # BET-TIMING-MONITOR: shadow runs every 30 min, 5 min after each odds refresh.
    # Cohort label = 'HHMM' UTC — each run is a snapshot of the full bot universe
    # at that moment. Settlement runs nightly to compute per-hour ROI.
    # Replaces the old 3-slot (06:30/11:30/15:30) design — now 32 snapshots/day.
    # Coolbet odds snapshot runs on the operator's Mac via launchd
    # (local/launchd/com.oddsintel.coolbet-odds-snapshot.plist) — Imperva
    # 403s the VPS. `_coolbet_odds_snapshot_wrapper` above is kept for
    # manual runs (`python -m workers.automation.coolbet_explorer`).

    # EPICBET-ODDS-INGEST-2026-08-27: Epicbet, by contrast, serves its prematch
    # feed anonymously with no bot-protection, so it runs here on the VPS.
    # :02/:32 — 3 min ahead of betting_refresh_interval (:05/:35) so the same
    # cycle prices against fresh Epicbet quotes, and clear of the :10/:40
    # shadow slot (see SCHEDULER-HANG-MITIGATION below).
    scheduler.add_job(_epicbet_odds_snapshot_wrapper,
                      CronTrigger(hour="*", minute="2,32"),
                      id="epicbet_odds_snapshot", name="Epicbet Odds [30min]")

    # SCHEDULER-HANG-MITIGATION (2026-06-01) — staggered :10/:40 instead of
    # :05/:35 so it doesn't share a firing minute with betting_refresh_interval.
    # On 2026-06-01 at 14:35 UTC, betting_pipeline + betting_refresh + shadow_1435
    # all hung simultaneously, consuming 3 of 4 executor threads; after that
    # the scheduler stopped accepting jobs entirely. Three jobs sharing a
    # 30-min firing minute under max_workers=4 is too tight when any one of
    # them takes >5 min on a shared lock (Coolbet session / AF semaphore / DB
    # pool). Staggering doesn't eliminate the underlying shared-resource bug
    # but reduces the worst-case overlap window from 0s to 5min.
    # SHADOW-24H-COVERAGE-2026-08-21: previously "hour=7-22" — created an
    # 8.5h overnight gap (22:40 UTC → 07:10 UTC) where shadow bots didn't
    # fire. That missed MLS matches (kick 00:00-06:00 UTC), Australian
    # A-League (04:00-08:00 UTC), and early Asian kickoffs. Since
    # BOT-PIN-OU-SHADOW-2026-08-21 our line-shopping shadow bots are pure
    # Pinnacle-vs-soft-book comparisons (no v10 dependency), so they can
    # cover ALL continents — but the cron gate was clipping them.
    #
    # Cost: minimal. Shadow runners read existing predictions + odds
    # snapshots from DB, no fresh AF fetches. +16 runs/day (32 → 48).
    scheduler.add_job(job_shadow_run_interval, CronTrigger(hour="*", minute="10,40"),
                      id="shadow_interval", name="Shadow Run [30min · 24/7]")

    # News checker: 09:00, 12:30, 14:30, 16:30, 18:30 UTC
    # 14:30 added — feeds 15:00 betting (was 2.5h stale)
    # 18:30 replaces 19:30 — now feeds 19:00 + 20:30 betting instead of neither
    for hour, minute in [(9, 0), (12, 30), (14, 30), (16, 30), (18, 30)]:
        scheduler.add_job(job_news_checker, CronTrigger(hour=hour, minute=minute),
                          id=f"news_{hour:02d}{minute:02d}",
                          name=f"News {hour:02d}:{minute:02d}")



    # GROWTH-ACCURACY-PICKS-LOG (2026-06-05): 06:45 UTC daily — after morning
    # predictions (04:00) and before the morning betting pipeline (~06:30 but
    # safe at :45). Publishes one pick per market per next-24h match into
    # published_picks. Idempotent via UNIQUE(match_id, market, model_version).
    scheduler.add_job(job_publish_daily_picks, CronTrigger(hour=6, minute=45),
                      id="publish_daily_picks",
                      name="Accuracy: Publish Daily Picks (06:45 UTC)")


    # ANON-AUTH PHASE 4 — prune anonymous users idle >90 days, Sunday 02:00 UTC.
    # Cascade removes their profile + favorites + picks. Hard cap of 10k rows
    # per run as a safety guard.
    scheduler.add_job(job_prune_anon_users, CronTrigger(day_of_week="sun", hour=2, minute=0),
                      id="prune_anon_users", name="Prune Anonymous Users Sunday 02:00")

    # ODDS-BACKLOG-PRUNE — drain historical odds_snapshots for finished matches >30d.
    # Simple DELETE (no window functions): keeps only is_closing + is_opening.
    # Runs nightly at 03:00 UTC when IO budget is fresh. 5k matches per run
    # clears the ~45k-match backlog in ~9 nights automatically.
    scheduler.add_job(
        lambda: __import__('scripts.prune_odds_snapshots', fromlist=['prune_old_simple']).prune_old_simple(max_matches=5000),
        CronTrigger(hour=3, minute=0),
        id="odds_backlog_prune", name="Odds Backlog Prune 03:00"
    )

    # ML-PIPELINE-UNIFY Stage 5a — weekly retrain Sunday 03:00 UTC, runs train.py +
    # compare_models.py. Promotion stays manual (operator flips MODEL_VERSION).
    scheduler.add_job(job_weekly_retrain, CronTrigger(day_of_week="sun", hour=3, minute=0),
                      id="weekly_retrain", name="Weekly Retrain Sunday 03:00",
                      max_instances=1, misfire_grace_time=3600)

    # META-RETRAIN (2026-05-25) — weekly B-ML3 meta-model retrain Sunday 04:00 UTC,
    # an hour after the main retrain (which refreshes MFV features the meta
    # model consumes). Promotion stays manual (flip META_B_ML3_VERSION in /opt/odds-intel-engine/.env (then restart oddsintel-scheduler)).
    # META-VALIDATE-WEEKLY (2026-06-01) — runs Sunday 05:00 UTC after
    # weekly_meta_retrain finishes, scores all bundles on real settled bets
    # and emails the verdict. Replaces the 2026-06-10 manual checkpoint.
    # DISABLED 2026-07-18 (SCHEDULER-META-VALIDATE-SEGFAULT): job has been
    # failing with `exit -11` (SIGSEGV) for at least 2 weeks — suspected
    # XGBoost lib / bundle mismatch. Quarantined during 2026-07-18 → -08-01
    # vacation window so it doesn't spam alerts. Re-enable + fix on return.
    # scheduler.add_job(job_weekly_meta_validate, CronTrigger(day_of_week="sun", hour=5, minute=0),
    #                   id="weekly_meta_validate", name="Weekly META Validate Sunday 05:00",
    #                   max_instances=1, misfire_grace_time=1800)

    # THRESHOLD-CHECK-WEEKLY (2026-06-06) — Sunday 06:00 UTC, after the
    # retrain/meta_retrain/meta_validate chain finishes. Runs threshold_check.py
    # so the "Key Thresholds to Watch" counts in PRIORITY_QUEUE.md stay live
    # instead of going 13 days stale (which is what triggered today's audit).
    scheduler.add_job(job_weekly_threshold_check,
                      CronTrigger(day_of_week="sun", hour=6, minute=0),
                      id="weekly_threshold_check",
                      name="Weekly Threshold Check Sunday 06:00",
                      max_instances=1, misfire_grace_time=1800)

    # BOT-MATURITY-REVIEW-WEEKLY (2026-06-15) — Sunday 06:30 UTC, after the
    # 06:00 threshold check. Origin: the 2026-06-13 audit found
    # bot_high_alignment had been auto-placing real money for days because the
    # maturity gate wasn't enforced everywhere — manual promotion/demotion was
    # the only feedback loop.
    scheduler.add_job(job_weekly_bot_review,
                      CronTrigger(day_of_week="sun", hour=6, minute=30),
                      id="weekly_bot_review",
                      name="Weekly Bot Maturity Review Sunday 06:30",
                      max_instances=1, misfire_grace_time=1800)

    # TENNIS-RETIRED-2026-09-01: the tennis pipeline was paused 2026-07-02
    # (focusing on soccer) and its cron registrations removed then. The four
    # wrapper functions were kept "for manual invocation" but were never
    # invoked again — `tennis_scanner` last succeeded 2026-07-04, i.e. 59 days
    # before removal, while `check_tennis_scanner_silent()` kept emailing about
    # it every day. Wrappers, scanner scripts, health tripwires and smoke tests
    # all removed together; git history has the lot if tennis comes back.

    # COOLBET-FS-SESSION-STABLE Step 1.5 — heartbeat every 5 min.
    # Updates coolbet_session_state.last_heartbeat_at + session_healthy.
    # Side benefit: keeps Coolbet's server-side session marked active.
    scheduler.add_job(job_coolbet_health_ping,
                      CronTrigger(minute="*/5"),
                      id="coolbet_health_ping",
                      name="Coolbet Session Health Ping [5min]",
                      max_instances=1, misfire_grace_time=60)

    # COOLBET-DAILY-SUMMARY (2026-06-16) — once at 08:00 UTC. Proactive
    # "everything's fine" Telegram so the operator can confirm health
    # without prompting. Missed summary = scheduler itself is down.
    scheduler.add_job(job_coolbet_daily_summary,
                      CronTrigger(hour=8, minute=0),
                      id="coolbet_daily_summary",
                      name="Coolbet Daily Summary [08:00 UTC]",
                      max_instances=1, misfire_grace_time=3600)

    # COOLBET-DAEMON-ALERTS (2026-06-16) — pre-kickoff catch-net, every 5 min.
    # Independent of the Mac so it survives a dead daemon. Quiet on healthy.
    scheduler.add_job(job_coolbet_prekickoff_alert,
                      CronTrigger(minute="*/5"),
                      id="coolbet_prekickoff_alert",
                      name="Coolbet Pre-KO Catch-Net [5min]",
                      max_instances=1, misfire_grace_time=60)

    # PIPELINE-RUNS-FAILURE-DIGEST (2026-06-22) — daily 08:00 UTC email
    # digest of jobs that failed in the last 24h. The CS2-PIPELINE-
    # TRUTHFUL-LOGGING fix made pipeline_runs record real failures; this
    # surfaces them within 24h instead of via manual sweep. Quiet on
    # healthy (no email when zero failures).
    scheduler.add_job(job_pipeline_runs_failure_digest,
                      CronTrigger(hour=8, minute=0),
                      id="pipeline_runs_failure_digest",
                      name="Pipeline Failure Digest [daily 08:00 UTC]",
                      max_instances=1, misfire_grace_time=3600)

    # PIPELINE-FAILURE-ALERTER (2026-06-25) — hourly fast-cycle alerter.
    # Closes the 32h-lag gap surfaced by the 06-23 FS outage: the daily
    # digest correctly identified the broken scrapers at 06-24 08:00 UTC,
    # but a daily-cadence email is too slow. This fires Telegram within
    # ~1h of any cron racking up 3+ consecutive non-transient failures.
    # DB-backed dedup via pipeline_health_state (4h window), recovery
    # auto-clears on next successful run. Fires at :23 to offset from
    # the bulk of cron firing at :00/:05/:12/:15/:17/:30.
    # Stall watchdog: every 5 min, off-the-hour. Deliberately NOT wrapped in
    # _run_job — it must keep working when every other worker thread is wedged,
    # and it must not appear in its own _inflight registry. SCHEDULER-STALL-RCA.
    scheduler.add_job(job_stall_watchdog, IntervalTrigger(minutes=5),
                      id="job_stall_watchdog", name="Job Stall Watchdog [5min]")

    scheduler.add_job(job_pipeline_failure_alerter,
                      CronTrigger(minute=23),
                      id="pipeline_failure_alerter",
                      name="Pipeline Failure Alerter [hourly]",
                      max_instances=1, misfire_grace_time=900)

    # RETRAIN-HEALTHCHECK (2026-06-21) — Mon + Tue 09:00 UTC, alerts when
    # the Sunday weekly_retrain has been silently failing.
    # RETRAIN-HEALTHCHECK-CADENCE-2026-08-16: bumped to Mon-Sat 09:00 UTC
    # (skip Sunday because the actual retrain fires at 03:00 UTC and a
    # 09:00 healthcheck could race a slow retrain still finishing). Jul 26
    # weekly_retrain never ran (pipeline_runs has zero rows for that
    # Sunday — silent skip). Mon Jul 27 age was 8.25d, under the 9d
    # threshold, so passed as healthy. Tue Jul 28 age was 9.25d and DID
    # exceed threshold, but the alert was either dedup-swallowed by the
    # 48h window or the send failed silently — nothing surfaced. Adding
    # Wed–Sat as extra alert chances catches the case even when Mon dedup
    # or Tue send silently fails. Weekly job = ok to alert 5×/week if
    # something's actually wrong; the operator can tune dedup down.
    scheduler.add_job(job_retrain_healthcheck,
                      CronTrigger(day_of_week="mon,tue,wed,thu,fri,sat",
                                  hour=9, minute=0),
                      id="retrain_healthcheck",
                      name="Retrain Healthcheck [Mon-Sat 09:00 UTC]",
                      max_instances=1, misfire_grace_time=3600)

    # COOLBET-DAEMON-HEALTHCHECK (2026-06-21) — every 30 min, VPS-side
    # safety net. Independent of the Mac daemon's in-process alert (which
    # left a 3-day outage silent on 2026-06-18 → 21).
    scheduler.add_job(job_coolbet_daemon_healthcheck,
                      CronTrigger(minute="3,33"),  # offset off the half-hour to avoid pileups
                      id="coolbet_daemon_healthcheck",
                      name="Coolbet Daemon Healthcheck [30min]",
                      max_instances=1, misfire_grace_time=600)

    # COOLBET-ODDS-FRESHNESS-WATCHDOG (2026-07-03) — 30-min freshness
    # check on odds_snapshots(bookmaker='Coolbet'). :13/:43 lands ~10 min
    # after the Mac launchd writer fires at :03/:33, so we check AFTER
    # the write has a chance to land. Closes the class of failure that
    # hid the 06-26 → 07-03 seven-day silent outage.
    scheduler.add_job(job_coolbet_odds_freshness,
                      CronTrigger(minute="13,43"),
                      id="coolbet_odds_freshness",
                      name="Coolbet Odds Freshness Watchdog [30min]",
                      max_instances=1, misfire_grace_time=600)

    # EPICBET-403-FROM-VPS: same watchdog for the second book. :18/:48 sits
    # well after the :02/:32 ingest so a slow run is not reported as stale,
    # and clear of the Coolbet check at :13/:43.
    scheduler.add_job(job_epicbet_odds_freshness,
                      CronTrigger(minute="18,48"),
                      id="epicbet_odds_freshness",
                      name="Epicbet Odds Freshness Watchdog [30min]",
                      max_instances=1, misfire_grace_time=600)

    # COOLBET-FS-SESSION-STABLE sweeper — hourly, destroys stale FS sessions
    # not in the active whitelist (coolbet_prod, hltv_*, coolbet_dev). Bounds
    # the damage from any future session-leak bug to ≤1 hour.
    scheduler.add_job(job_flaresolverr_sweep,
                      CronTrigger(minute=37),  # off-the-hour to avoid pileups
                      id="flaresolverr_sweep",
                      name="FlareSolverr Stale Session Sweep [hourly]",
                      max_instances=1, misfire_grace_time=600)

    scheduler.add_job(job_weekly_meta_retrain, CronTrigger(day_of_week="sun", hour=4, minute=0),
                      id="weekly_meta_retrain", name="Weekly META Retrain Sunday 04:00",
                      max_instances=1, misfire_grace_time=3600)

    # DAILY-REAL-PERF-EMAIL (2026-05-25) — 23:30 UTC after settlement so
    # yesterday's bets are final. Captures placer-vs-manual split + 7d rollup.
    scheduler.add_job(job_daily_real_perf_email, CronTrigger(hour=23, minute=30),
                      id="daily_real_perf_email", name="Daily Real-Perf Email 23:30")

    # LEAGUE-CLV-EFFICIENCY (2026-05-25) — Sunday 02:30 UTC, before weekly_retrain
    # at 03:00 so the per-league CLV index is in match_signals before MFV rebuild.
    scheduler.add_job(job_league_clv_efficiency,
                      CronTrigger(day_of_week="sun", hour=2, minute=30),
                      id="league_clv_efficiency", name="League CLV Efficiency Sun 02:30")

    # MFV-B-ML3-V2-NIGHTLY-REFRESH (2026-05-25): re-runs the B-ML3 v2 feature
    # backfill nightly at 22:30 UTC so MFV rows for matches that finished today
    # get the new _at_t6h columns populated. Cheaper than modifying the live MFV
    # builder which has ordering issues with T-6h snapshot availability.
    scheduler.add_job(job_nightly_mfv_b_ml3_refresh, CronTrigger(hour=22, minute=30),
                      id="mfv_b_ml3_refresh", name="MFV B-ML3 v2 Refresh 22:30")

    # MFV-FORM-MOMENTUM-NIGHTLY-REFRESH (2026-05-25): same pattern for form_momentum.
    scheduler.add_job(job_nightly_mfv_form_momentum_refresh,
                      CronTrigger(hour=22, minute=45),
                      id="mfv_form_momentum_refresh", name="MFV Form Momentum Refresh 22:45")

    # AF-PLAYER-RATINGS (2026-05-25): rolling team rating refresh 22:50 UTC.
    # Reads match_player_stats (AF /fixtures/players already ingested for
    # every ~191/280 matches/day) and writes team_avg_player_rating_home/away
    # to match_signals. Slot between the two MFV refreshes to keep nightly
    # data-pipeline jobs ordered.
    scheduler.add_job(job_team_avg_player_rating,
                      CronTrigger(hour=22, minute=50),
                      id="team_avg_player_rating",
                      name="AF Player Ratings Rolling 22:50")

    # INJURY-SEVERITY (2026-05-25): nightly bucketing of match_injuries.
    # 22:55 slot keeps the nightly-feature jobs strictly ordered before any
    # downstream MFV consumer.
    scheduler.add_job(job_injury_severity,
                      CronTrigger(hour=22, minute=55),
                      id="injury_severity",
                      name="Injury Severity Bucketing 22:55")

    # SIG-12 (2026-05-25): rolling xG-overperformance signal.
    # Runs at 23:00 UTC nightly — final slot in the signal-builder chain.
    scheduler.add_job(job_xg_overperformance,
                      CronTrigger(hour=23, minute=0),
                      id="xg_overperformance",
                      name="xG Overperformance Rolling 23:00")

    # LIVE-SNAPSHOTS-PRUNE (2026-05-25): weekly prune.
    # Sunday 01:00 UTC — before weekly_retrain at 03:00 so MFV builder
    # reads from the already-trimmed table (faster queries).
    scheduler.add_job(job_prune_live_snapshots,
                      CronTrigger(day_of_week="sun", hour=1, minute=0),
                      id="prune_live_snapshots",
                      name="Prune live_match_snapshots Sun 01:00")

    # LEAGUE-DRAW-YTD (2026-05-25): per-league season-to-date draw rate.
    # Backtest +11.6pp Q4 vs Q1. 23:05 UTC.
    scheduler.add_job(job_league_draw_rate,
                      CronTrigger(hour=23, minute=5),
                      id="league_draw_rate",
                      name="League Draw Rate YTD 23:05")

    # LINE-VELOCITY (2026-05-25): Pinnacle home implied-prob slope T-12h..T-2h.
    # Backtest: Q4 |v| → -6.6pp CLV-beat (REVERSE signal). 23:10 UTC.
    scheduler.add_job(job_line_velocity,
                      CronTrigger(hour=23, minute=10),
                      id="line_velocity",
                      name="Line Velocity 23:10")

    # LEAGUE-SEASON-PHASE (2026-05-25): per-match season_progress [0..1].
    # Backtest: late vs early +7.7pp Over 2.5, +6.0pp BTTS, +6.7pp home win.
    scheduler.add_job(job_league_season_phase,
                      CronTrigger(hour=23, minute=15),
                      id="league_season_phase",
                      name="League Season Phase 23:15")

    # MFV-V3-SIGNALS-NIGHTLY-PROPAGATE (2026-06-22): propagate
    # match_signals → match_feature_vectors columns for the v3 signals
    # (season_progress, line_velocity, xg_overperf_*, league_clv_efficiency,
    # injury_severity_*, team_avg_player_rating_*, league_draw_rate_ytd).
    # Without this, the signal writers (league_season_phase 23:15,
    # line_velocity 23:10, etc.) populate match_signals nightly but MFV
    # columns stay NULL — the audit on 2026-06-22 found MFV.season_progress
    # at 0% coverage for 90 days because backfill_mfv_v3_signals.py was
    # never scheduled. Runs at 23:30 — after all v3 signal writers complete.
    scheduler.add_job(job_mfv_v3_signals_propagate,
                      CronTrigger(hour=23, minute=30),
                      id="mfv_v3_signals_propagate",
                      name="MFV v3 Signals → MFV Propagate 23:30",
                      max_instances=1, misfire_grace_time=1800)

    # ALN-AUTO (2026-05-25): 1st of each month at 03:30 UTC. Runs the
    # alignment-bump tuner over a 60d window; emails a diff via Resend
    # if any class needs |Δ| ≥ 0.005 with n ≥ 100. Never auto-applies
    # — human approves the bump because alignment directly affects
    # bet placement.
    scheduler.add_job(job_aln_auto_tune,
                      CronTrigger(day="1", hour=3, minute=30),
                      id="aln_auto_tune",
                      name="ALN-AUTO Monthly 1st 03:30")

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

    # Extra settle_ready at 09:00 UTC — catches early Asian/Australian matches
    # (05:00-08:00 UTC kickoffs) that finish before the 21:00 batch run.
    # The live poller probe handles these in real-time, but this is a backup.
    scheduler.add_job(job_settle_ready, CronTrigger(hour=9, minute=0),
                      id="settle_ready_09", name="Settle-Ready 09:00 (early matches)")

    # Budget sync: hourly
    scheduler.add_job(job_budget_sync, CronTrigger(minute=0),
                      id="budget_sync", name="Budget Sync")

    # Ops snapshot fallback: every hour at :30 — captures state if no pipeline ran
    scheduler.add_job(job_ops_snapshot, CronTrigger(minute=30),
                      id="ops_snapshot", name="Ops Snapshot :30")

    # Dashboard cache refresh: every 30 min — keeps public /performance page fresh
    # between betting_refresh / settlement runs that already write cache. Offset to :15/:45
    # to avoid colliding with budget_sync (:00) and ops_snapshot (:30).
    scheduler.add_job(job_dashboard_cache_refresh, CronTrigger(minute="15,45"),
                      id="dashboard_cache_refresh", name="Dashboard Cache Refresh (30min)")

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
    # WORKER-SPLIT-LIVEPOLLER (2026-05-25): env-gate so this scheduler
    # service can be deployed without the in-process poller. Default ON
    # (matches pre-split behaviour); set LIVE_POLLER_IN_SCHEDULER=false
    # on the scheduler service when running `workers/live_poller_main.py`
    # as a separate systemd service.
    if os.getenv("LIVE_POLLER_IN_SCHEDULER", "true").lower() in ("true", "1", "yes"):
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
    else:
        console.print("\n[bold green]Scheduler running 24/7 (LivePoller disabled — running in separate service)[/bold green]\n")

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
