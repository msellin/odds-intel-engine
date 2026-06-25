"""
PIPELINE-FAILURE-ALERTER (2026-06-25) — fast Telegram alert for stuck crons.

Closes the 32h-lag gap surfaced by the FS outage on 2026-06-23:
the daily PIPELINE-RUNS-FAILURE-DIGEST email correctly identified
the 3 broken scrapers (cs2_hltv_upcoming, cs2_coolbet_scanner,
cs2_hltv_match_odds) at 06-24 08:00 UTC, but a daily-cadence email
isn't enough — the operator only noticed mid-day on 06-25 ("no bets
today"). The digest stays for forensics; this alerter is the
fire-detection layer.

Strategy: every cron tick, ask "which jobs have failed N times in a
row (excluding transient Railway redeploy kills)?" Fire a Telegram
once per stuck job; dedup via pipeline_health_state so the same
stuck job doesn't buzz the phone every hour for a week. On recovery
(next successful run after an alert was issued), clear the dedup
marker — next outage of that same job re-fires.

What's "transient" — the same 'killed — scheduler restarted' /
'killed — orphaned' patterns the digest filters out. Railway
redeploys can produce 1-2 in a row when a deploy + a long-running
job race; we don't want to wake the operator for that.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# How many consecutive non-transient failures count as "stuck."
# 3 is high enough that a transient infra blip + a real fail won't
# fire, low enough that a 30-min cron fires the alert within ~90 min.
CONSECUTIVE_FAILURE_THRESHOLD = 3

# Suppress repeat-alert spam: don't re-fire on the same stuck job
# more often than this. Long because the operator already knows.
ALERT_DEDUP_WINDOW_S = 4 * 3600   # 4h

# Health-state pipeline_name prefix so alerter rows don't collide
# with other consumers of pipeline_health_state.
PIPELINE_NAME_PREFIX = "failure_alerter:"


def _stuck_jobs() -> list[dict]:
    """For every cron that ran in the last 24h, look at its N latest runs
    (excluding transient kills). If all N are 'failed', the job is stuck.

    Returns: [{job_name, consecutive_failed, last_failure_at, last_success_at,
               sample_error}]
    """
    from workers.api_clients.db import execute_query
    n = CONSECUTIVE_FAILURE_THRESHOLD
    rows = execute_query(
        """
        WITH non_transient AS (
            SELECT job_name, status, started_at, error_message,
                   ROW_NUMBER() OVER (PARTITION BY job_name ORDER BY started_at DESC) AS rn
              FROM pipeline_runs
             WHERE started_at >= NOW() - INTERVAL '24 hours'
               AND status IN ('failed', 'completed')
               AND NOT (status = 'failed'
                        AND error_message IS NOT NULL
                        AND error_message LIKE 'killed — %%')
        ),
        latest_n AS (
            SELECT * FROM non_transient WHERE rn <= %s
        ),
        per_job AS (
            SELECT job_name,
                   COUNT(*) AS n_recent,
                   COUNT(*) FILTER (WHERE status = 'failed') AS n_failed,
                   MAX(started_at) FILTER (WHERE status = 'failed') AS last_failure_at,
                   (SELECT LEFT(error_message, 200)
                      FROM latest_n e
                     WHERE e.job_name = latest_n.job_name
                       AND e.status = 'failed'
                     ORDER BY e.started_at DESC LIMIT 1) AS sample_error
              FROM latest_n
             GROUP BY job_name
        ),
        any_recent_ok AS (
            SELECT job_name, MAX(started_at) AS last_success_at
              FROM non_transient
             WHERE status = 'completed'
             GROUP BY job_name
        )
        SELECT pj.job_name,
               pj.n_recent,
               pj.n_failed AS consecutive_failed,
               pj.last_failure_at,
               ao.last_success_at,
               pj.sample_error
          FROM per_job pj
          LEFT JOIN any_recent_ok ao ON ao.job_name = pj.job_name
         WHERE pj.n_recent = %s
           AND pj.n_failed = %s
         ORDER BY pj.last_failure_at DESC
        """,
        (n, n, n),
    )
    return rows or []


def _alert_state(pipeline_name: str) -> dict | None:
    """Fetch this pipeline's row from pipeline_health_state (or None)."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT last_alert_at, last_alert_reason FROM pipeline_health_state "
        "WHERE pipeline_name = %s",
        (pipeline_name,),
    )
    return rows[0] if rows else None


def _record_alert(pipeline_name: str, reason: str) -> None:
    from workers.api_clients.db import execute_write
    execute_write(
        """INSERT INTO pipeline_health_state (pipeline_name, last_alert_at, last_alert_reason)
           VALUES (%s, NOW(), %s)
           ON CONFLICT (pipeline_name) DO UPDATE
              SET last_alert_at = NOW(), last_alert_reason = EXCLUDED.last_alert_reason,
                  updated_at = NOW()""",
        (pipeline_name, reason),
    )


def _clear_alert(pipeline_name: str) -> None:
    from workers.api_clients.db import execute_write
    execute_write(
        """UPDATE pipeline_health_state
              SET last_alert_at = NULL, last_alert_reason = NULL,
                  updated_at = NOW()
            WHERE pipeline_name = %s""",
        (pipeline_name,),
    )


def _previously_alerted() -> list[str]:
    """Pipelines with a non-NULL alert marker — candidates for recovery."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT pipeline_name FROM pipeline_health_state
           WHERE pipeline_name LIKE %s AND last_alert_at IS NOT NULL""",
        (PIPELINE_NAME_PREFIX + "%",),
    )
    return [r["pipeline_name"] for r in rows]


def _format_alert(job: dict) -> str:
    job_name = job["job_name"]
    n = job["consecutive_failed"]
    last_fail = job["last_failure_at"]
    last_ok = job.get("last_success_at")
    sample = (job.get("sample_error") or "").strip()
    if last_ok:
        ok_str = f"last ok {last_ok:%Y-%m-%d %H:%M} UTC"
    else:
        ok_str = "no recent success"
    lines = [
        f"🔴 {job_name} stuck",
        f"  {n}+ consecutive failures (excl. Railway redeploy kills)",
        f"  last fail: {last_fail:%Y-%m-%d %H:%M} UTC",
        f"  {ok_str}",
    ]
    if sample:
        lines.append(f"  err: {sample[:160]}")
    return "\n".join(lines)


def run_alerter(*, dry_run: bool = False) -> dict:
    """Main entry point. Returns counters.

    counters = {
        "stuck_count": int,          # jobs currently stuck (3+ consecutive failures)
        "alerted_now": int,          # alerts sent on this run
        "skipped_dedup": int,        # stuck but suppressed by 4h dedup window
        "recovered": int,            # previously-alerted jobs that recovered
    }
    """
    counters = {"stuck_count": 0, "alerted_now": 0,
                "skipped_dedup": 0, "recovered": 0}
    try:
        stuck = _stuck_jobs()
    except Exception as e:
        log.warning("alerter _stuck_jobs failed: %s", e)
        return counters
    counters["stuck_count"] = len(stuck)
    stuck_names = {j["job_name"] for j in stuck}

    # Lazy import — only needed when we actually have something to send.
    send_telegram = None
    if stuck and not dry_run:
        try:
            from workers.notify.telegram import send_telegram as _st
            send_telegram = _st
        except Exception as e:
            log.warning("telegram import failed: %s", e)

    now = datetime.now(timezone.utc)
    for job in stuck:
        pname = PIPELINE_NAME_PREFIX + job["job_name"]
        state = _alert_state(pname)
        if state and state.get("last_alert_at"):
            age = (now - state["last_alert_at"]).total_seconds()
            if age < ALERT_DEDUP_WINDOW_S:
                counters["skipped_dedup"] += 1
                continue
        reason = f"consecutive_failed>={CONSECUTIVE_FAILURE_THRESHOLD}"
        msg = _format_alert(job)
        if dry_run:
            log.info("[dry-run] would alert: %s", msg.replace("\n", " | "))
            counters["alerted_now"] += 1
            continue
        if send_telegram:
            try:
                send_telegram(msg, dedup_key=f"failure_alerter:{job['job_name']}",
                              dedup_window_s=ALERT_DEDUP_WINDOW_S)
            except Exception as e:
                log.warning("telegram send failed for %s: %s", job["job_name"], e)
        try:
            _record_alert(pname, reason)
            counters["alerted_now"] += 1
        except Exception as e:
            log.warning("_record_alert failed for %s: %s", pname, e)

    # Recovery sweep: any previously-alerted pipeline that's no longer in
    # the stuck list has recovered. Clear its marker so a future re-stuck
    # condition re-fires immediately instead of waiting out the 4h window.
    try:
        for pname in _previously_alerted():
            job_name = pname[len(PIPELINE_NAME_PREFIX):]
            if job_name in stuck_names:
                continue
            if dry_run:
                log.info("[dry-run] would clear recovered: %s", pname)
            else:
                try:
                    _clear_alert(pname)
                    if send_telegram:
                        send_telegram(f"✓ {job_name} recovered", silent=True,
                                      dedup_key=f"failure_alerter_recovered:{job_name}",
                                      dedup_window_s=1800)
                except Exception as e:
                    log.warning("_clear_alert failed for %s: %s", pname, e)
            counters["recovered"] += 1
    except Exception as e:
        log.warning("alerter recovery sweep failed: %s", e)

    return counters


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    c = run_alerter(dry_run=args.dry_run)
    log.info("failure alerter: stuck=%d alerted=%d skipped_dedup=%d recovered=%d",
             c["stuck_count"], c["alerted_now"], c["skipped_dedup"], c["recovered"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
