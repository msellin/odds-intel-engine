"""
OddsIntel — Railway Scheduler

Long-running process that replaces GitHub Actions cron scheduling.
Uses APScheduler for timed jobs + a health endpoint for Railway.

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

# APScheduler logs every job-fire at INFO — with 50+ jobs this hits Railway's 500/sec limit.
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
            # must not add Railway stdout volume. _recent_errors will surface
            # any underlying DB problem on the next genuine job error.
            run_id = None

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

    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    is_monday = date.today().weekday() == 0

    console.print(f"[bold green]═══ Morning Pipeline: {today} ═══[/bold green]\n")

    import traceback
    steps = [
        ("1/6", "Fixtures (today)",        lambda: run_fixtures(target_date=today, refresh_leagues=is_monday)),
        ("2/6", "Fixtures (tomorrow rows)", lambda: fetch_and_store_fixtures(tomorrow)),
        ("3/6", "Enrichment",              lambda: run_enrichment(target_date=today)),
        ("4/6", "Odds",                    lambda: run_odds(target_date=today)),
        ("5/6", "Predictions",             lambda: run_predictions(target_date=today)),
        ("6/6", "Betting",                 lambda: run_betting()),
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


def job_enrichment_refresh():
    from workers.jobs.fetch_enrichment import run_enrichment
    _run_job("enrichment_refresh", run_enrichment,
             components={"injuries"})


def job_enrichment_full():
    """13:00 UTC full enrichment — injuries, H2H, team_stats.
    Standings moved to job_standings_nightly (23:30 UTC, AF-STANDINGS-DAILY).
    Ensures H2H + team_stats are fresh for afternoon/evening betting refreshes (N7 fix).
    """
    from workers.jobs.fetch_enrichment import run_enrichment
    _run_job("enrichment_full", run_enrichment,
             components={"injuries", "h2h", "team_stats"})


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


def job_coolbet_keepalive():
    """COOLBET-KEEPALIVE — every 20 min, ping Coolbet to prevent the
    server-side idle-logout (~30 min). Coolbet JWT TTL = 1820s, renewal_date
    sits at the 20-min mark; a heartbeat well inside that window keeps the
    session indefinitely warm so manual / placer work doesn't hit a re-login
    prompt mid-flow. Error-isolated.
    """
    from workers.automation.coolbet_session import CoolbetSession
    import traceback
    try:
        ok = CoolbetSession().keep_alive()
        if not ok:
            console.print("[yellow]Coolbet keepalive call returned non-200[/yellow]")
    except Exception as e:
        console.print(f"[red]Coolbet keepalive failed: {e}[/red]")
        console.print(f"[red dim]{traceback.format_exc()}[/red dim]")


def _coolbet_keepalive_wrapper():
    _run_job("coolbet_keepalive", job_coolbet_keepalive)


def _shadow_run(shadow_cohort: str):
    """Run run_morning(shadow_mode=True, shadow_cohort=...) with error isolation."""
    from workers.jobs.daily_pipeline_v2 import run_morning
    import traceback
    try:
        run_morning(skip_fetch=True, shadow_mode=True, shadow_cohort=shadow_cohort)
    except Exception as e:
        console.print(f"[red]Shadow run ({shadow_cohort}) failed: {e}[/red]")
        console.print(f"[red dim]{traceback.format_exc()}[/red dim]")


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


def job_email_digest():
    from workers.jobs.email_digest import run_email_digest
    _run_job("email_digest", run_email_digest)


def job_weekly_digest():
    from workers.jobs.weekly_digest import run_weekly_digest
    _run_job("weekly_digest", run_weekly_digest)


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
        production = os.getenv("MODEL_VERSION", "v14")

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

    _run_job("weekly_retrain", _retrain)


def job_weekly_meta_retrain():
    """META-RETRAIN (2026-05-25): weekly retrain of the B-ML3 meta-model.
    Runs Sunday 04:00 UTC, AFTER the main XGBoost weekly_retrain at 03:00 UTC
    finishes (the meta-model consumes MFV features built/refreshed by the
    main retrain pipeline).

    Train script writes the bundle to data/models/meta/<version>/. No
    promotion — the operator inspects the new bundle's threshold.json and
    decides whether to flip META_B_ML3_VERSION on Railway.
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
        # Storage so future Railway redeploys can hydrate it on cache miss.
        try:
            from workers.model.storage import upload_meta_bundle
            local_dir = Path(__file__).parent.parent / "data" / "models" / "meta" / version
            count = upload_meta_bundle(version, local_dir)
            console.print(f"[green]Uploaded {count} meta bundle files to Storage[/green]")
        except Exception as e:
            console.print(f"[yellow]Meta bundle Storage upload skipped: {e} — bundle remains on local disk only[/yellow]")
    _run_job("weekly_meta_retrain", _meta_retrain)


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


def job_value_bet_alert_afternoon():
    from workers.jobs.email_digest import run_value_bet_alert
    _run_job("value_bet_alert_afternoon", lambda: run_value_bet_alert("afternoon"))


def job_value_bet_alert_evening():
    from workers.jobs.email_digest import run_value_bet_alert
    _run_job("value_bet_alert_evening", lambda: run_value_bet_alert("evening"))


def job_settlement():
    # _log_run=False — settlement_pipeline's first sub-step already logs to
    # pipeline_runs as job_name='settlement'. Letting the wrapper log too
    # would write a duplicate row per run.
    _run_job("settlement", settlement_pipeline, _log_run=False)


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

    Background: every git push triggers a Railway redeploy, which kills any
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
    console.print("[bold green]   OddsIntel Railway Scheduler starting...    [/bold green]")
    console.print("[bold green]═══════════════════════════════════════════════[/bold green]")

    if SHADOW_MODE:
        console.print("[yellow]SHADOW MODE: job names prefixed with 'railway_'[/yellow]")

    # Start health endpoint FIRST — must respond before Railway's health check window
    _start_health_server()

    # Clean up orphaned "running" records from previous process (Railway kill/restart)
    # 10-min threshold catches jobs that were <30 min old under the old logic
    _cleanup_stale_runs(threshold_minutes=10, label="scheduler restarted")

    # SETTLEMENT-CATCHUP: if last night's daily settlement got killed by a deploy
    # (frequent during active dev — every git push restarts Railway), the 21:00 /
    # 23:30 / 01:00 redundant runs may all have been wiped. Detect that and
    # fire one settlement run shortly after startup.
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
    scheduler = BackgroundScheduler(
        timezone="UTC",
        executors={"default": APSThreadPoolExecutor(max_workers=4)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
    )

    # ── Register all jobs ──────────────────────────────────────────────

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

    # Odds refresh: every 30min during 07-22 UTC
    # 20:00 replaced by pre-KO mark_closing run (marks closing odds for evening KOs)
    for hour in range(7, 23):
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

    # Odds pre-kickoff (mark_closing): 13:30, 17:30, 20:00 UTC
    # 20:00 covers 19:00-21:00 KO window (replaces regular 20:00 refresh — marks CLV closing line)
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=13, minute=30),
                      id="odds_prekick_1330", name="Odds Pre-KO 13:30")
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=17, minute=30),
                      id="odds_prekick_1730", name="Odds Pre-KO 17:30")
    scheduler.add_job(job_odds_pre_kickoff, CronTrigger(hour=20, minute=0),
                      id="odds_prekick_2000", name="Odds Pre-KO 20:00")

    # Enrichment refresh: 10:30, 16:00 UTC (injuries only — AF-STANDINGS-DAILY)
    # 10:30 moved from 12:00 so injury data is fresh before the 11:00 betting refresh
    scheduler.add_job(job_enrichment_refresh, CronTrigger(hour=10, minute=30),
                      id="enrichment_1030", name="Enrichment 10:30")
    scheduler.add_job(job_enrichment_refresh, CronTrigger(hour=16, minute=0),
                      id="enrichment_16", name="Enrichment 16:00")

    # Full enrichment: 13:00 UTC — injuries, H2H, team_stats (standings moved to 23:30)
    # N7 fix: H2H + team_stats were only fetched in morning pipeline; this refresh
    # ensures afternoon/evening betting runs have up-to-date context.
    scheduler.add_job(job_enrichment_full, CronTrigger(hour=13, minute=0),
                      id="enrichment_full_13", name="Enrichment Full 13:00")

    # Standings nightly: 23:30 UTC — AF-STANDINGS-DAILY
    # Standings update ~1x/week; daily once-at-night is sufficient.
    scheduler.add_job(job_standings_nightly, CronTrigger(hour=23, minute=30),
                      id="standings_nightly", name="Standings Nightly 23:30")

    # Betting refresh: every 30 min, 5 min after each odds refresh (07:05–22:35 UTC).
    # run_betting() is DB-only (skip_fetch=True), zero AF calls. Hard dedup on
    # (bot_id, match_id, market, selection) means no duplicate bets ever written.
    # New bets only appear when fresh odds create a new edge or a new match is priced.
    # Cohort (morning/midday/pre_ko) is auto-detected from UTC hour by _current_cohort().
    scheduler.add_job(job_betting_refresh_wrapper, CronTrigger(hour="7-22", minute="5,35"),
                      id="betting_refresh_interval", name="Betting Refresh [30min]")

    # BET-TIMING-MONITOR: shadow runs every 30 min, 5 min after each odds refresh.
    # Cohort label = 'HHMM' UTC — each run is a snapshot of the full bot universe
    # at that moment. Settlement runs nightly to compute per-hour ROI.
    # Replaces the old 3-slot (06:30/11:30/15:30) design — now 32 snapshots/day.
    # Coolbet odds snapshot: every 30 min at :03/:33, between odds refresh (:00/:30)
    # and betting refresh (:05/:35). Builds a Coolbet odds time-series in
    # odds_snapshots — feeds the planned COOLBET-OR-PIN-REQUIRED data-quality
    # gate so OU/AH bots can lean on Coolbet (our actual placement venue)
    # instead of being Pinnacle-only.
    scheduler.add_job(_coolbet_odds_snapshot_wrapper, CronTrigger(hour="7-22", minute="3,33"),
                      id="coolbet_odds_interval", name="Coolbet Odds Snapshot [30min]")

    # Coolbet keepalive: every 20 min. JWT TTL is 1820s with renewal_date at
    # the 20-min mark — pinging inside that window keeps the server-side
    # session alive so manual / placer / explorer work never hits an
    # unexpected re-login. Round-the-clock so the session stays warm overnight.
    scheduler.add_job(_coolbet_keepalive_wrapper, CronTrigger(minute="*/20"),
                      id="coolbet_keepalive_interval", name="Coolbet Keepalive [20min]")

    scheduler.add_job(job_shadow_run_interval, CronTrigger(hour="7-22", minute="5,35"),
                      id="shadow_interval", name="Shadow Run [30min]")

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

    # EMAIL-DIGEST-SMART (ENG-4): four qualification slots, 10/12/14/16 UTC.
    # First slot whose pending-bet signal-strength score clears
    # EMAIL_DIGEST_MIN_SIGNAL (default 5.0) sends the digest. Later slots
    # see the per-user `email_digest_log` lock and skip — exactly one digest
    # per user per day. Replaces the old 07:30 send that routinely went out
    # with "0 value bets today" because evening markets weren't priced yet.
    for hour in (10, 12, 14, 16):
        scheduler.add_job(job_email_digest, CronTrigger(hour=hour, minute=0),
                          id=f"email_digest_{hour:02d}",
                          name=f"Email Digest Slot {hour:02d}:00")

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

    # ML-PIPELINE-UNIFY Stage 5a — weekly retrain Sunday 03:00 UTC, runs train.py +
    # compare_models.py. Promotion stays manual (operator flips MODEL_VERSION).
    scheduler.add_job(job_weekly_retrain, CronTrigger(day_of_week="sun", hour=3, minute=0),
                      id="weekly_retrain", name="Weekly Retrain Sunday 03:00",
                      max_instances=1, misfire_grace_time=3600)

    # META-RETRAIN (2026-05-25) — weekly B-ML3 meta-model retrain Sunday 04:00 UTC,
    # an hour after the main retrain (which refreshes MFV features the meta
    # model consumes). Promotion stays manual (flip META_B_ML3_VERSION on Railway).
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

    # LEAGUE-DRAW-YTD (2026-05-25): per-league season-to-date draw rate.
    # Backtest +11.6pp Q4 vs Q1. 23:05 UTC.
    scheduler.add_job(job_league_draw_rate,
                      CronTrigger(hour=23, minute=5),
                      id="league_draw_rate",
                      name="League Draw Rate YTD 23:05")

    # ALN-AUTO (2026-05-25): 1st of each month at 03:30 UTC. Runs the
    # alignment-bump tuner over a 60d window; emails a diff via Resend
    # if any class needs |Δ| ≥ 0.005 with n ≥ 100. Never auto-applies
    # — human approves the bump because alignment directly affects
    # bet placement.
    scheduler.add_job(job_aln_auto_tune,
                      CronTrigger(day="1", hour=3, minute=30),
                      id="aln_auto_tune",
                      name="ALN-AUTO Monthly 1st 03:30")

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
