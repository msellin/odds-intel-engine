"""
OddsIntel — Pipeline Health Alerts (PIPE-ALERT / SYNTHETIC-LIVENESS)

Sends an email alert to ADMIN_ALERT_EMAIL when the pipeline is silently broken.
The /admin/ops dashboard is post-mortem — you have to open it to see problems.
This job is proactive: it fires if something looks wrong.

Conditions checked:
  1. Morning bet check (09:30): 0 bets placed today with ≥10 scheduled matches
  2. Odds coverage (09:15): Pinnacle odds missing for >10 of today's scheduled matches
  3. Snapshot staleness (hourly 10-23 UTC): no live snapshot in last 25 min during active window
  4. Settlement check (21:30): 0 results settled when >5 bets were pending before settlement

Each condition logs to console always. Email fires only when the condition is true.
One alert per condition per UTC day (deduped in memory via a simple set — process-level only).

Requires: RESEND_API_KEY + ADMIN_ALERT_EMAIL in .env.
"""

import os
from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from workers.api_clients.db import execute_query

console = Console()

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("DIGEST_FROM_EMAIL", "OddsIntel <digest@oddsintel.app>")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL", "")

# In-memory dedup: set of "YYYY-MM-DD:condition_name" strings already alerted today.
# Resets on process restart (fine — restart is rare, and a re-alert on restart is OK).
_alerted_today: set[str] = set()


def _dedup_key(condition: str) -> str:
    return f"{date.today().isoformat()}:{condition}"


def _send_alert(subject: str, body_html: str) -> None:
    if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
        console.print(f"[yellow]Alert (no email configured): {subject}[/yellow]")
        return

    import httpx
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL], "subject": subject, "html": body_html},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            console.print(f"[green]Alert sent: {subject}[/green]")
        else:
            console.print(f"[yellow]Alert send failed ({resp.status_code}): {subject}[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Alert send error: {e}[/yellow]")


def _alert_once(condition: str, subject: str, body_html: str) -> None:
    key = _dedup_key(condition)
    if key in _alerted_today:
        return
    _alerted_today.add(key)
    console.print(f"[red bold]PIPELINE ALERT: {subject}[/red bold]")
    _send_alert(f"[OddsIntel Alert] {subject}", body_html)


def check_morning_bets() -> None:
    """No bets placed today despite ≥10 scheduled matches — morning pipeline may have failed."""
    today = date.today().isoformat()

    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM matches WHERE date::date = %s AND status = 'scheduled'",
        (today,)
    )
    match_count = (rows[0]["cnt"] if rows else 0) or 0

    if match_count < 10:
        console.print(f"[dim]health_alerts: {match_count} scheduled matches today — skipping bet check[/dim]")
        return

    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM simulated_bets WHERE created_at::date = %s",
        (today,)
    )
    bet_count = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: {bet_count} bets placed today ({match_count} matches)[/dim]")

    if bet_count == 0:
        _alert_once(
            "zero_bets",
            f"0 bets placed — {match_count} matches scheduled",
            f"<p>Today ({today}) has {match_count} scheduled matches but 0 simulated bets were placed.</p>"
            f"<p>The morning betting pipeline likely failed. Check Railway logs.</p>",
        )


def check_track_record_continuity(low_threshold: int = 5) -> None:
    """GROWTH-TRACK-RECORD-CONTINUITY (2026-06-05) — alert if the paper-bet
    chain went thin or broke yesterday.

    Distinct from `check_morning_bets()` which fires only on TODAY=0 picks
    with ≥10 scheduled matches. This check looks back at YESTERDAY (a fully
    settled UTC day) and fires if the daily count fell below `low_threshold`
    (default 5). Rationale: the chain's marketing value (e.g. "tracked
    across 21,831+ matches since 2023" on the landing) compounds with time
    — every gap weakens the story permanently. A day with <5 picks is
    overwhelmingly likely to be a silent scheduler failure rather than a
    quiet match calendar (see 60-day audit at scripts/audit_track_record_chain.py).

    Fires two distinct alerts:
      - `chain_broken_yesterday`: 0 picks yesterday → catastrophic
      - `chain_weak_yesterday`:   1 ≤ picks < low_threshold → anomaly
    """
    from datetime import timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    rows = execute_query(
        "SELECT COUNT(*) AS cnt FROM simulated_bets WHERE pick_time::date = %s",
        (yesterday,)
    )
    count = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: yesterday ({yesterday}) had {count} paper bets[/dim]")

    if count == 0:
        _alert_once(
            "chain_broken_yesterday",
            f"PAPER-BET CHAIN BROKE — 0 picks yesterday ({yesterday})",
            f"<p><b>The bot chain produced 0 paper bets on {yesterday}.</b></p>"
            f"<p>Every day with 0 picks is permanently lost from the public "
            f"track-record. Investigate today before the next morning pipeline "
            f"so we don't break the chain a second time.</p>"
            f"<p>Likely causes: scheduler crashed, RAILWAY restart wiped state, "
            f"AF Pinnacle outage, all bots silent (check <code>silent_bots</code> "
            f"in ops_snapshots).</p>"
            f"<p>Audit history: <code>python scripts/audit_track_record_chain.py --days 14</code></p>",
        )
    elif count < low_threshold:
        _alert_once(
            "chain_weak_yesterday",
            f"Chain anomaly — only {count} picks yesterday ({yesterday})",
            f"<p>The bot chain produced just {count} paper bets on {yesterday} "
            f"(below threshold {low_threshold}). Recent daily counts are "
            f"typically 15-300+.</p>"
            f"<p>Probable causes: most bots went silent, AF odds incomplete, "
            f"edge thresholds tightened too hard, or league filters dropping "
            f"matches. Check <code>silent_bots</code> + per-bot stats.</p>"
            f"<p>Audit: <code>python scripts/audit_track_record_chain.py --days 14</code></p>",
        )


def check_pinnacle_coverage() -> None:
    """More than 10 scheduled matches today have no Pinnacle odds — odds fetch may have failed."""
    today = date.today().isoformat()

    rows = execute_query(
        """
        SELECT COUNT(*) AS cnt
        FROM matches m
        WHERE m.date::date = %s
          AND m.status = 'scheduled'
          AND NOT EXISTS (
              SELECT 1 FROM match_signals ms
              WHERE ms.match_id = m.id
                AND ms.signal_name = 'pinnacle_implied_home'
          )
        """,
        (today,)
    )
    missing = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: {missing} scheduled matches missing Pinnacle odds[/dim]")

    if missing > 10:
        _alert_once(
            "pinnacle_missing",
            f"{missing} matches missing Pinnacle odds",
            f"<p>Today ({today}) has {missing} scheduled matches without Pinnacle implied odds.</p>"
            f"<p>The odds fetch job may have failed or the AF Pinnacle bookmaker is unavailable.</p>",
        )


def check_snapshot_staleness() -> None:
    """No live snapshot in last 25 min during 10-23 UTC — LivePoller may be down."""
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    if hour < 10 or hour >= 23:
        return  # Outside live window — no matches expected

    rows = execute_query(
        "SELECT MAX(created_at) AS last_snap FROM live_match_snapshots"
    )
    last_snap = rows[0]["last_snap"] if rows else None

    if last_snap is None:
        console.print("[dim]health_alerts: no live snapshots in DB yet[/dim]")
        return

    if last_snap.tzinfo is None:
        from datetime import timezone as tz
        last_snap = last_snap.replace(tzinfo=tz.utc)

    age_minutes = (now_utc - last_snap).total_seconds() / 60
    console.print(f"[dim]health_alerts: last live snapshot {age_minutes:.1f} min ago[/dim]")

    if age_minutes > 25:
        _alert_once(
            "snapshot_stale",
            f"LivePoller stale — last snapshot {age_minutes:.0f} min ago",
            f"<p>It is {now_utc.strftime('%H:%M UTC')} and the last live match snapshot was "
            f"{age_minutes:.0f} minutes ago.</p>"
            f"<p>LivePoller may be down or stuck. Check Railway logs for the live-poller thread.</p>",
        )


def check_tennis_scanner_silent() -> None:
    """TENNIS-PAPER-BETS Phase 3 (2026-06-25): tennis_scanner has not run
    successfully in > 12 hours. The scanner is scheduled at 06:00 + 14:00 UTC
    so a healthy state means last success ≤ 12h ago in steady state. Catches:

    - OA_KEY/ODDS_API_KEY missing/expired on Railway
    - Scheduler crash that skipped the tennis_scanner cron specifically
    - subprocess timeout / The Odds API outage
    - Quota exhaustion (the failure mode that bit OddsPapi at 250 req/mo;
      The Odds API is 500 cred/mo so far more headroom, but still worth
      monitoring — the alert subject covers it generically)

    Why this matters: this is the exact silent-failure class the
    `feedback_silent_failures` memory warns about — the OddsPapi scanner
    had been writing 0 rows for ~a week before we noticed (table empty,
    no alert). Don't let it happen again.
    """
    from datetime import timedelta
    rows = execute_query(
        """
        SELECT MAX(started_at) AS last_success
          FROM pipeline_runs
         WHERE job_name = 'tennis_scanner'
           AND status   = 'completed'
        """
    )
    last_success = rows[0]["last_success"] if rows else None

    if last_success is None:
        # Never succeeded — could be brand-new deploy, hold off alerting until
        # first 24h elapse to avoid spam on fresh installs.
        console.print("[dim]health_alerts: tennis_scanner has no successful runs yet[/dim]")
        return

    now_utc = datetime.now(timezone.utc)
    age_hours = (now_utc - last_success).total_seconds() / 3600.0
    console.print(f"[dim]health_alerts: tennis_scanner last success {age_hours:.1f}h ago[/dim]")

    if age_hours > 12:
        _alert_once(
            "tennis_scanner_silent",
            f"Tennis scanner silent — last success {age_hours:.1f}h ago",
            f"<p>The tennis scanner has not completed successfully in "
            f"<b>{age_hours:.1f} hours</b> (last success "
            f"{last_success.strftime('%Y-%m-%d %H:%M UTC')}).</p>"
            f"<p>Scheduled at 06:00 + 14:00 UTC. Likely causes:</p>"
            f"<ul>"
            f"<li>OA_KEY / ODDS_API_KEY missing or invalid on Railway</li>"
            f"<li>The Odds API credit quota exhausted (500/mo free tier)</li>"
            f"<li>Scheduler crashed or specific cron skipped</li>"
            f"<li>subprocess timeout (>300s) — check logs</li>"
            f"</ul>"
            f"<p>Check: <code>SELECT * FROM pipeline_runs WHERE job_name='tennis_scanner' "
            f"ORDER BY started_at DESC LIMIT 5</code></p>",
        )


def check_tennis_settlement_stale() -> None:
    """TENNIS-PAPER-BETS Phase 3 (2026-06-25): N tennis_value_bets rows are
    past kickoff + 6h with result still NULL. Settlement runs 02:00 + 14:15
    UTC; a row >6h past kickoff should have been picked up. Catches:

    - The Odds API /scores endpoint not returning the event (sport deactivated
      after tournament ended, fixture postponed, lower-tier match never gets
      a result in their feed)
    - Settlement job crashed
    - sport_title → sport_key resolution failed (rare — would need /sports
      response to change format)

    Threshold 5 — small enough that one stuck tournament triggers it, big
    enough that the occasional walkover/retirement we couldn't settle doesn't.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    rows = execute_query(
        """
        SELECT COUNT(*) AS cnt
          FROM tennis_value_bets
         WHERE result IS NULL
           AND kickoff_time < %s
        """,
        (cutoff,)
    )
    stale = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: tennis_value_bets stale past KO+6h: {stale}[/dim]")

    STALE_THRESHOLD = 5
    if stale > STALE_THRESHOLD:
        _alert_once(
            "tennis_settlement_stale",
            f"Tennis settlement gap — {stale} rows past KO+6h with NULL result",
            f"<p>There are <b>{stale} rows</b> in tennis_value_bets with "
            f"kickoff > 6 hours ago but <code>result IS NULL</code> "
            f"(threshold: {STALE_THRESHOLD}).</p>"
            f"<p>Settlement runs 02:00 + 14:15 UTC. If rows linger past 24h, "
            f"The Odds API /scores likely didn't return those events (sport "
            f"deactivated after tournament ended, fixture postponed/walkover, "
            f"or lower-tier match never gets a feed result).</p>"
            f"<p>Check the pending-settlement table on /admin/tennis or:</p>"
            f"<p><code>SELECT fixture_id, tournament_name, player_home, player_away, kickoff_time "
            f"FROM tennis_value_bets WHERE result IS NULL AND kickoff_time &lt; now() - interval '6 hours' "
            f"ORDER BY kickoff_time DESC LIMIT 20</code></p>",
        )


def check_settlement() -> None:
    """Bets still pending on finished matches after settlement window — settlement may have failed."""
    rows = execute_query(
        """
        SELECT COUNT(*) AS cnt FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.result = 'pending'
          AND m.status = 'finished'
        """,
    )
    stale_pending = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: {stale_pending} pending bets on finished matches[/dim]")

    if stale_pending > 5:
        today = date.today().isoformat()
        _alert_once(
            "settlement_stale",
            f"Settlement gap — {stale_pending} pending bets on finished matches",
            f"<p>There are {stale_pending} simulated bets still marked 'pending' on matches "
            f"that have finished.</p>"
            f"<p>Settlement job may have failed on {today}. "
            f"Check Railway logs for the 21:00 UTC settlement job and pipeline_runs table.</p>",
        )


def check_odds_bloat() -> None:
    """odds_snapshots total row count > 1.5M after the nightly prune window — prune may have failed."""
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour < 22:
        return  # Only meaningful after the 21:00 UTC prune should have run

    rows = execute_query("SELECT COUNT(*) AS cnt FROM odds_snapshots")
    total = (rows[0]["cnt"] if rows else 0) or 0
    console.print(f"[dim]health_alerts: odds_snapshots total rows: {total:,}[/dim]")

    BLOAT_THRESHOLD = 1_500_000
    if total > BLOAT_THRESHOLD:
        today = date.today().isoformat()
        _alert_once(
            "odds_bloat",
            f"odds_snapshots bloat — {total:,} rows (threshold {BLOAT_THRESHOLD:,})",
            f"<p>odds_snapshots has {total:,} rows as of {now_utc.strftime('%H:%M UTC')} on {today}.</p>"
            f"<p>The nightly prune (settlement step 3/3 at 21:00 UTC) should have run by now. "
            f"Check <code>pipeline_runs WHERE job_name = 'settlement_prune'</code> for failure details.</p>"
            f"<p>Manual fix: <code>venv/bin/python scripts/prune_odds_snapshots.py --apply</code></p>",
        )


def check_memory_usage() -> None:
    """MEMORY-MONITORING (2026-05-25): OOM is silent — process gets killed without
    emitting a Python exception, the pipeline just stops. This check reads the current
    process RSS via psutil and alerts at >85% of the configured limit. The check runs
    every 30 min via scheduler so an OOM trajectory can be caught before the kill.
    Set SCHEDULER_PROCESS_MB_LIMIT to the host's available RAM (default 4096 MB).
    """
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        rss_mb = proc.memory_info().rss / 1024 / 1024
    except Exception:
        # psutil may not be installed; fall back to /proc/self/status (Linux only)
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        rss_mb = rss_kb / 1024
                        break
                else:
                    return  # No VmRSS field — skip
        except FileNotFoundError:
            return  # Not on Linux — skip silently

    limit_mb = int(os.getenv("SCHEDULER_PROCESS_MB_LIMIT", "4096"))
    pct = rss_mb / limit_mb * 100
    console.print(f"[dim]health_alerts: process RSS = {rss_mb:.0f} MB / {limit_mb} MB ({pct:.0f}%)[/dim]")

    if pct >= 85:
        _alert_once(
            "memory_high",
            f"Memory pressure — {rss_mb:.0f} MB / {limit_mb} MB ({pct:.0f}%)",
            f"<p>The engine process is using <b>{rss_mb:.0f} MB</b> of memory ({pct:.0f}% of "
            f"the {limit_mb} MB cap).</p>"
            f"<p>Likely cause: a backfill or batch job is loading large in-memory dicts. "
            f"Check the most recent pipeline_runs entries for any memory-heavy job.</p>"
            f"<p>Quick mitigation: restart the scheduler (systemctl restart oddsintel-scheduler "
            f"or launchctl kickstart on Mac).</p>",
        )


def check_betting_refresh_stale() -> None:
    """PIPELINE-DEAD-MAN'S-SWITCH (2026-05-25): the betting_refresh cron runs
    every 30 min between 07-22 UTC. If two cycles miss (60+ min gap), the
    pipeline is silently broken — bets aren't being placed, edges aren't being
    re-evaluated, the engine is dark even though OBS-HEARTBEAT is still pinging.
    This check fires only during the active 07-22 UTC window.
    """
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour < 7 or now_utc.hour >= 22:
        return  # Outside active window — refreshes paused legitimately

    rows = execute_query("""
        SELECT MAX(completed_at) AS last_done
        FROM pipeline_runs
        WHERE job_name = 'betting_refresh'
          AND status = 'completed'
          AND completed_at >= NOW() - INTERVAL '4 hours'
    """)
    last = rows[0]["last_done"] if rows else None
    if last is None:
        # No completed run in 4h is itself the alert
        _alert_once(
            "betting_refresh_dead",
            "betting_refresh has not completed in 4+ hours during active window",
            f"<p>At {now_utc.strftime('%H:%M UTC')} no betting_refresh job has completed in the "
            f"last 4 hours.</p>"
            f"<p>Either APScheduler is stuck, the betting_pipeline is erroring out before commit, "
            f"or the engine is OOMed. Check Railway logs.</p>",
        )
        return

    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age_min = (now_utc - last).total_seconds() / 60
    console.print(f"[dim]health_alerts: last betting_refresh = {age_min:.0f} min ago[/dim]")
    if age_min > 60:
        _alert_once(
            "betting_refresh_stale",
            f"betting_refresh stale — last run {age_min:.0f} min ago",
            f"<p>It is {now_utc.strftime('%H:%M UTC')} and the last successful betting_refresh "
            f"completed {age_min:.0f} minutes ago (expected every 30 min in the active window).</p>"
            f"<p>2 consecutive cycles have been missed. Pipeline likely degraded — check Railway logs "
            f"for stack traces in betting_pipeline or shadow_* jobs.</p>",
        )


def check_af_quota() -> None:
    """OBS-BUDGET-ALERT (2026-05-25): API-Football quota is 150K calls/day. The
    pipeline burns ~30-60K on a calm day, ~80-100K on busy Saturdays. If usage
    exceeds 80% of the cap with >6h left in the UTC day, alert — the rest of
    the day's jobs may halt mid-stride. Reads from api_budget_log.
    """
    now_utc = datetime.now(timezone.utc)
    hours_remaining = 24 - now_utc.hour
    if hours_remaining < 6:
        return  # Late in the day — too late to throttle, alert would be noise

    rows = execute_query("""
        SELECT COALESCE(SUM(calls_made), 0) AS used
        FROM api_budget_log
        WHERE log_date = CURRENT_DATE
    """)
    used = (rows[0]["used"] if rows else 0) or 0
    cap = int(os.getenv("AF_DAILY_QUOTA", "150000"))
    pct = used / cap * 100
    console.print(f"[dim]health_alerts: AF quota = {used:,} / {cap:,} ({pct:.0f}%), {hours_remaining}h remaining[/dim]")
    if pct >= 80:
        _alert_once(
            "af_quota_high",
            f"AF quota at {pct:.0f}% with {hours_remaining}h remaining",
            f"<p>API-Football usage is <b>{used:,} / {cap:,}</b> ({pct:.0f}%) at "
            f"{now_utc.strftime('%H:%M UTC')}. {hours_remaining}h remain in the UTC day.</p>"
            f"<p>If usage trends linear, today's odds-refresh + settlement-enrichment jobs may "
            f"exhaust the budget before completing. Consider pausing non-critical fetches.</p>",
        )


def check_model_drift() -> None:
    """MODEL-DRIFT-ALERT (2026-05-25): catch broken feature pipelines BEFORE
    bots drain bankroll. Compares the mean P(home win) on today's predictions
    to the rolling 7-day mean. A 2-sigma shift signals the model has changed
    inputs unexpectedly — exactly the kind of regression that the dropped-OU-
    features bug went 13 weeks unnoticed (WEEKLY-RETRAIN-OU-FEATURES, 2026-05-24).
    """
    rows = execute_query("""
        WITH daily AS (
            SELECT created_at::date AS d, AVG(probability) AS mean_prob
            FROM predictions
            WHERE market = '1x2_home'
              AND created_at >= NOW() - INTERVAL '14 days'
            GROUP BY d
        ),
        stats AS (
            SELECT AVG(mean_prob) AS mu, STDDEV(mean_prob) AS sigma
            FROM daily WHERE d < CURRENT_DATE AND d >= CURRENT_DATE - INTERVAL '7 days'
        )
        SELECT
            (SELECT mean_prob FROM daily WHERE d = CURRENT_DATE) AS today,
            mu, sigma
        FROM stats
    """)
    if not rows:
        return
    today = rows[0].get("today")
    mu = rows[0].get("mu")
    sigma = rows[0].get("sigma")
    if today is None or mu is None or sigma is None or sigma < 1e-6:
        return
    today, mu, sigma = float(today), float(mu), float(sigma)
    z = (today - mu) / sigma
    console.print(f"[dim]health_alerts: 1x2_home mean prob today={today:.4f}, 7d mu={mu:.4f}, sigma={sigma:.4f}, z={z:+.2f}[/dim]")
    if abs(z) >= 2.0:
        _alert_once(
            "model_drift",
            f"Model drift — 1x2_home mean shifted {z:+.2f} sigma vs 7d baseline",
            f"<p>Today's mean P(home win) on 1X2 predictions is <b>{today:.3f}</b>, vs the "
            f"7-day baseline of {mu:.3f} ± {sigma:.3f} (z={z:+.2f}).</p>"
            f"<p>This is the kind of distribution shift that signals a broken feature pipeline "
            f"or a silent retrain regression. Investigate the most recent model_version + MFV "
            f"builder changes.</p>"
            f"<p>Reference: WEEKLY-RETRAIN-OU-FEATURES (2026-05-24) — dropped-features bug went "
            f"13 weeks unnoticed before this alert existed.</p>",
        )


def check_meta_score_drift() -> None:
    """B-ML3 score-distribution drift alert (2026-05-25). Compares the mean
    meta_clv_score on today's bets vs the 7-day rolling baseline. If the
    distribution shifts >2 sigma, the meta-model's feature inputs likely
    changed — possibly because MFV got new columns or live builder changed.
    """
    rows = execute_query("""
        WITH daily AS (
            SELECT pick_time::date AS d, AVG(meta_clv_score) AS mean_score, COUNT(*) AS n
            FROM simulated_bets
            WHERE pick_time >= NOW() - INTERVAL '14 days'
              AND meta_clv_score IS NOT NULL
            GROUP BY d
        ),
        stats AS (
            SELECT AVG(mean_score) AS mu, STDDEV(mean_score) AS sigma,
                   COUNT(*) AS baseline_days
            FROM daily WHERE d < CURRENT_DATE AND d >= CURRENT_DATE - INTERVAL '7 days'
        )
        SELECT
            (SELECT mean_score FROM daily WHERE d = CURRENT_DATE) AS today,
            (SELECT n FROM daily WHERE d = CURRENT_DATE) AS today_n,
            mu, sigma, baseline_days
        FROM stats
    """)
    if not rows:
        return
    today = rows[0].get("today")
    today_n = rows[0].get("today_n") or 0
    mu = rows[0].get("mu")
    sigma = rows[0].get("sigma")
    baseline_days = rows[0].get("baseline_days") or 0
    # Require ≥5 baseline days — fewer days means σ is computed from too small
    # a sample and any minor shift produces an absurdly large z-score.
    if today is None or mu is None or sigma is None or sigma < 1e-6 or today_n < 20 or baseline_days < 5:
        return  # Not enough data
    today, mu, sigma = float(today), float(mu), float(sigma)
    z = (today - mu) / sigma
    console.print(f"[dim]health_alerts: meta_clv_score today={today:.4f}, 7d mu={mu:.4f}, sigma={sigma:.4f}, z={z:+.2f}[/dim]")
    if abs(z) >= 2.0:
        _alert_once(
            "meta_score_drift",
            f"B-ML3 meta score drifted {z:+.2f} sigma vs 7d baseline",
            f"<p>Today's mean meta_clv_score on {today_n} bets is <b>{today:.3f}</b>, vs the "
            f"7-day baseline of {mu:.3f} ± {sigma:.3f} (z={z:+.2f}).</p>"
            f"<p>This means either the meta-model's input features shifted (MFV column added, "
            f"upstream pipeline change) or the prediction distribution itself moved. If "
            f"META_B_ML3_ENABLED=true is also set, this directly affects which bets get filtered.</p>",
        )


def check_stale_retirement_flags() -> None:
    """STALE-FLAG-WATCHDOG 2026-06-01 — five bots in 24h had retired_reason
    populated but is_active=true (the migration prepared the reason text but
    never flipped the flag). Performance page kept counting them as active and
    /performance ROI math was wrong. This check catches the next occurrence
    automatically: any bot with a populated retired_reason that is still
    is_active=true with no retired_at stamp gets flagged.

    Note: clearing retired_reason on a recovered bot is a valid operator
    action — see migration 162 STALE-FLAG-AUDIT bot_conservative /
    bot_opt_home_lower. The check only triggers when the reason is set AND
    the bot is still considered active by the pipeline.
    """
    rows = execute_query("""
        SELECT name, maturity_label, LEFT(retired_reason, 120) AS reason
        FROM bots
        WHERE retired_reason IS NOT NULL
          AND is_active = true
          AND retired_at IS NULL
        ORDER BY name
    """)
    if not rows:
        console.print("[dim]health_alerts: no stale retirement flags[/dim]")
        return

    bot_list_html = "<ul>" + "".join(
        f"<li><b>{r['name']}</b> ({r['maturity_label']}) — {r['reason']}…</li>"
        for r in rows
    ) + "</ul>"

    _alert_once(
        "stale_retirement_flags",
        f"Stale retirement flags — {len(rows)} bot(s) need attention",
        f"<p>The following bots have <code>retired_reason</code> set but are "
        f"still <code>is_active=true</code> with no <code>retired_at</code> "
        f"stamp. They are still firing into the active /performance cohort.</p>"
        f"{bot_list_html}"
        f"<p>Action: either flip <code>is_active=false</code> + stamp "
        f"<code>retired_at=NOW()</code> (true retirement), or clear "
        f"<code>retired_reason = NULL</code> (bot recovered).</p>"
    )


def check_dashboard_cache_stale() -> None:
    """CACHE-FRESHNESS-WATCHDOG 2026-06-01 — Railway dashboard_cache_refresh
    job stopped running 14:35 UTC today; the staleness went unnoticed until a
    user spotted misleading numbers on /performance 3h later. This check fires
    a Telegram alert when the latest dashboard_cache row is > 60 min old —
    well outside the 30-min cron cadence, conservatively avoids false positives
    during scheduler restarts.
    """
    rows = execute_query("SELECT MAX(computed_at) AS t FROM dashboard_cache")
    last = rows[0]["t"] if rows else None
    if last is None:
        return  # No cache yet — separate problem, surfaced elsewhere

    now_utc = datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age_min = (now_utc - last).total_seconds() / 60
    console.print(f"[dim]health_alerts: dashboard_cache age {age_min:.1f} min[/dim]")

    if age_min > 60:
        _alert_once(
            "dashboard_cache_stale",
            f"Dashboard cache stale — {age_min:.0f} min old",
            f"<p>The <code>dashboard_cache</code> table's most recent row is "
            f"{age_min:.0f} minutes old (last write {last.strftime('%H:%M UTC')}). "
            f"The cron is supposed to run every 30 minutes via "
            f"<code>job_dashboard_cache_refresh</code>.</p>"
            f"<p>Likely causes: APScheduler hung (check <code>pipeline_runs</code> "
            f"for jobs stuck in 'running'), Railway service crash, or "
            f"<code>write_dashboard_cache()</code> raising silently.</p>"
            f"<p>The /performance page is currently showing the stale row, which "
            f"may confuse visitors. Restart Railway or investigate the scheduler "
            f"thread pool.</p>"
        )


def run_morning_checks() -> None:
    """09:30 UTC check — run after the morning betting pipeline."""
    console.print("[cyan]health_alerts: running morning checks[/cyan]")
    try:
        check_morning_bets()
    except Exception as e:
        console.print(f"[yellow]health_alerts morning bet check error: {e}[/yellow]")
    try:
        check_track_record_continuity()
    except Exception as e:
        console.print(f"[yellow]health_alerts continuity check error: {e}[/yellow]")
    try:
        check_pinnacle_coverage()
    except Exception as e:
        console.print(f"[yellow]health_alerts pinnacle check error: {e}[/yellow]")
    # TENNIS-PAPER-BETS Phase 3 — scanner silent-failure tripwire.
    # 09:30 UTC slot fires AFTER the 06:00 UTC tennis_scanner run, so a
    # successful 06:00 run keeps us inside the 12h window. Catches the
    # OddsPapi-era silent failure mode that left tennis_value_bets empty
    # for ~a week before we noticed.
    try:
        check_tennis_scanner_silent()
    except Exception as e:
        console.print(f"[yellow]health_alerts tennis scanner check error: {e}[/yellow]")


def run_snapshot_check() -> None:
    """Hourly 10-23 UTC — LivePoller staleness check + companion alerts.
    Extended 2026-05-25 to include the new monitoring checks (MEMORY, BETTING
    REFRESH DEAD-MAN'S, AF QUOTA, MODEL DRIFT, META SCORE DRIFT). All are
    quick reads + email-only on threshold breach; safe to bundle here so we
    don't add 5 new scheduler entries.
    """
    for fn_name, fn in [
        ("snapshot_staleness", check_snapshot_staleness),
        ("memory_usage", check_memory_usage),
        ("betting_refresh_stale", check_betting_refresh_stale),
        ("af_quota", check_af_quota),
        ("model_drift", check_model_drift),
        ("meta_score_drift", check_meta_score_drift),
        ("stale_retirement_flags", check_stale_retirement_flags),
        ("dashboard_cache_stale", check_dashboard_cache_stale),
    ]:
        try:
            fn()
        except Exception as e:
            console.print(f"[yellow]health_alerts {fn_name} error: {e}[/yellow]")


def run_settlement_check() -> None:
    """21:30 UTC — settlement completeness check + odds bloat check."""
    console.print("[cyan]health_alerts: running settlement check[/cyan]")
    try:
        check_settlement()
    except Exception as e:
        console.print(f"[yellow]health_alerts settlement check error: {e}[/yellow]")
    try:
        check_odds_bloat()
    except Exception as e:
        console.print(f"[yellow]health_alerts odds bloat check error: {e}[/yellow]")
    # TENNIS-PAPER-BETS Phase 3 — settlement staleness tripwire.
    # 21:30 UTC slot runs ~7h after the 14:15 tennis settlement; anything
    # past kickoff+6h still NULL means /scores didn't return the event.
    try:
        check_tennis_settlement_stale()
    except Exception as e:
        console.print(f"[yellow]health_alerts tennis settlement check error: {e}[/yellow]")
