"""
PIPELINE-RUNS-FAILURE-DIGEST (2026-06-22) — daily 08:00 UTC email summary.

Closes the visibility gap that surfaced this morning. After the
CS2-PIPELINE-TRUTHFUL-LOGGING fix (2026-06-21), `pipeline_runs` now
records `status='failed'` for jobs that used to silently log
'completed' — so we get a real per-job failure stream. But nothing
SURFACES it: discovery still requires a manual `SELECT ... WHERE
status='failed'` sweep, exactly what found this morning's WC-BRACKET-
SCORING-PARTIAL-INDEX + CS2-scrapers-broken state.

This job runs once daily at 08:00 UTC (post-Sunday-cron, before any
European morning kickoffs) and emails a digest of every job that
failed in the last 24h, grouped by job_name with failure-ratio and a
sample stderr tail. Resend, same template as weekly_retrain_email.

QUIET on healthy: if no failures, no email — operator gets a positive
silence signal, not an empty-digest notification.

DESIGN CHOICES:
- Group by job_name, not by individual run, so spam at 100% failure
  rate becomes one line not 48.
- Total/failed counts let the reader spot intermittent vs constant.
- 200-char sample of error_message — long enough to identify the
  bug class, short enough to fit in a digest.
- DB-backed dedup is unnecessary here: this job is once-daily by
  construction. The dedup window for repeat-of-yesterday's-failures
  is "day boundary" — the 24h window naturally cycles.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
ADMIN_ALERT_EMAIL = os.getenv("ADMIN_ALERT_EMAIL") or os.getenv("DIGEST_TO_EMAIL", "")
FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL") or os.getenv("DIGEST_FROM_EMAIL", "alerts@oddsintel.app")

# Sample length for the stderr tail in the digest. Longer hurts
# readability; shorter loses the diagnostic signature.
ERROR_SAMPLE_LEN = 200


# Transient kill messages — these are Railway redeploy noise, not real
# bugs. Filtered out of the failure count so the digest surfaces only
# actionable signal. (The rows are still in pipeline_runs for audits.)
# Added 2026-06-25 after a 32h FS outage stayed hidden behind 13 daily
# "killed — scheduler restarted" rows that yellow-shaded the table.
_TRANSIENT_PATTERNS = (
    "killed — scheduler restarted",
    "killed — orphaned",
)


def _is_transient(err: str | None) -> bool:
    if not err:
        return False
    e = err.strip()
    return any(p in e for p in _TRANSIENT_PATTERNS)


def _collect_failures() -> list[dict]:
    """One row per job_name with at least one *real* failure in the last 24h.

    Excludes 'killed — scheduler restarted' / 'killed — orphaned' kills
    from the failed count (Railway redeploy noise). Jobs whose ONLY 24h
    failures are transient kills don't appear in the digest at all.

    Returns: [{job_name, failed, total, transient_failed, sample_error,
               last_failure_at, last_success_at}]
    """
    from workers.api_clients.db import execute_query
    # `not_transient` boolean carries the classification — TRUE when the
    # row's error_message doesn't match any transient pattern. We aggregate
    # 'real failed' as COUNT FILTER (status=failed AND not_transient).
    rows = execute_query(
        """
        WITH last24 AS (
            SELECT job_name, status, started_at, error_message,
                   (status = 'failed' AND error_message IS NOT NULL
                    AND error_message LIKE 'killed — %%') AS is_transient
              FROM pipeline_runs
             WHERE started_at >= NOW() - INTERVAL '24 hours'
        )
        SELECT
          job_name,
          COUNT(*) FILTER (WHERE status = 'failed' AND NOT is_transient) AS failed,
          COUNT(*) FILTER (WHERE status = 'failed' AND is_transient)     AS transient_failed,
          COUNT(*)                                                       AS total,
          MAX(started_at) FILTER (WHERE status = 'failed' AND NOT is_transient) AS last_failure_at,
          MAX(started_at) FILTER (WHERE status = 'completed') AS last_success_at,
          (
              SELECT LEFT(error_message, %s)
                FROM last24 e
               WHERE e.job_name = last24.job_name
                 AND e.status = 'failed'
                 AND NOT e.is_transient
                 AND e.error_message IS NOT NULL
               ORDER BY e.started_at DESC LIMIT 1
          ) AS sample_error
          FROM last24
         GROUP BY job_name
        HAVING COUNT(*) FILTER (WHERE status = 'failed' AND NOT is_transient) > 0
         ORDER BY COUNT(*) FILTER (WHERE status = 'failed' AND NOT is_transient) DESC,
                  job_name ASC
        """,
        (ERROR_SAMPLE_LEN,),
    )
    return rows or []


def _render_html(failures: list[dict], now: datetime) -> str:
    import html as _h
    rows_html = []
    for f in failures:
        ratio = (f["failed"] / f["total"]) if f["total"] else 0
        ratio_pct = f"{ratio*100:.0f}%"
        sample = (f.get("sample_error") or "").strip()
        sample_html = _h.escape(sample) if sample else "<em>(no error_message)</em>"
        last_ok = f.get("last_success_at")
        last_ok_str = (
            f"{int((now - last_ok).total_seconds()/3600)}h ago"
            if last_ok else "<em>never in 24h</em>"
        )
        # Highlight 100%-failure rows (every run failed — fully broken job).
        row_bg = "#ffe5e5" if ratio >= 0.99 else "#fff7e0" if ratio >= 0.5 else "#ffffff"
        rows_html.append(
            f"<tr style='background:{row_bg};'>"
            f"<td style='padding:6px 10px;font-family:monospace;'>{_h.escape(f['job_name'])}</td>"
            f"<td style='padding:6px 10px;text-align:right;'><b>{f['failed']}</b>/{f['total']}</td>"
            f"<td style='padding:6px 10px;text-align:right;'>{ratio_pct}</td>"
            f"<td style='padding:6px 10px;font-family:monospace;font-size:11px;'>{sample_html}</td>"
            f"<td style='padding:6px 10px;font-size:11px;color:#666;'>last ok: {last_ok_str}</td>"
            f"</tr>"
        )
    table = (
        "<table style='border-collapse:collapse;border:1px solid #ccc;font-size:13px;'>"
        "<thead><tr style='background:#f0f0f0;'>"
        "<th style='padding:6px 10px;text-align:left;'>job_name</th>"
        "<th style='padding:6px 10px;text-align:right;'>failed/total</th>"
        "<th style='padding:6px 10px;text-align:right;'>ratio</th>"
        "<th style='padding:6px 10px;text-align:left;'>sample error</th>"
        "<th style='padding:6px 10px;text-align:left;'>recovery signal</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )
    return f"""
    <div style="font-family:system-ui,sans-serif;font-size:14px;max-width:900px;">
      <h2 style="font-size:18px;">Pipeline failures last 24h</h2>
      <p>Window: {now - timedelta(hours=24):%Y-%m-%d %H:%M} → {now:%Y-%m-%d %H:%M} UTC.</p>
      <p>Red = 100% failure rate (job fully broken). Yellow = ≥50% (intermittent). White = single/few failures (likely transient).</p>
      {table}
      <p style="margin-top:24px;font-size:11px;color:#888;">
        Inspect a specific failure: <code>SELECT started_at, error_message FROM pipeline_runs WHERE job_name='&lt;name&gt;' AND status='failed' ORDER BY started_at DESC LIMIT 5;</code>
      </p>
    </div>
    """


def run_failure_digest(*, dry_run: bool = False) -> dict:
    """Main entry point. Returns counters for the scheduler wrapper.
    Never raises — alerter must not break the alerter."""
    counters = {"job_count": 0, "failure_count": 0, "sent": False, "skipped_reason": ""}
    try:
        now = datetime.now(timezone.utc)
        failures = _collect_failures()
        counters["job_count"] = len(failures)
        counters["failure_count"] = sum(f["failed"] for f in failures)

        if not failures:
            counters["skipped_reason"] = "no failures in 24h"
            return counters

        subject = (
            f"Pipeline failures · {counters['failure_count']} fail across "
            f"{counters['job_count']} job{'s' if counters['job_count'] != 1 else ''} (24h)"
        )

        if dry_run:
            log.info("[dry-run] would send: %s", subject)
            for f in failures:
                log.info("  %s: %d/%d fail (%s)", f["job_name"], f["failed"], f["total"],
                         (f.get("sample_error") or "")[:80])
            counters["sent"] = True
            return counters

        if not RESEND_API_KEY or not ADMIN_ALERT_EMAIL:
            counters["skipped_reason"] = "no Resend creds"
            return counters

        body = _render_html(failures, now)
        import httpx
        try:
            resp = httpx.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                          "Content-Type": "application/json"},
                json={"from": FROM_EMAIL, "to": [ADMIN_ALERT_EMAIL],
                       "subject": subject, "html": body},
                timeout=15,
            )
            if resp.status_code in (200, 201):
                counters["sent"] = True
                log.info("failure digest email sent: %s", subject)
            else:
                counters["skipped_reason"] = f"resend {resp.status_code}"
                log.warning("failure digest email failed (%s): %s",
                             resp.status_code, resp.text[:200])
        except Exception as e:
            counters["skipped_reason"] = f"resend exception: {e}"
            log.warning("failure digest email error: %s", e)

    except Exception as e:
        log.warning("failure digest raised (non-fatal): %s", e)
        counters["skipped_reason"] = f"internal exception: {e}"

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
    c = run_failure_digest(dry_run=args.dry_run)
    log.info("failure digest: job_count=%d failure_count=%d sent=%s skipped=%s",
             c["job_count"], c["failure_count"], c["sent"], c["skipped_reason"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
