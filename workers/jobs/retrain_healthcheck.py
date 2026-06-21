"""
RETRAIN-HEALTHCHECK — Railway-side alert for stale weekly_retrain (2026-06-21).

Mirrors COOLBET-DAEMON-HEALTHCHECK for a different silent-failure class.

2026-06-07 + 2026-06-14: both Sunday weekly_retrain jobs exited 1, no
Telegram, no email. Discovered only when manually inspecting pipeline_runs
on 2026-06-21 while diagnosing why bets still tagged the 2-week-old model
version. Cost: a full week of v20260621's BETTER-on-8-markets gains.

Conditions (any one fires an alert):
  • stale:   latest weekly_retrain WITH status='completed' is >9 days old
             (Sunday-cron + slack — a missed Sunday surfaces by Tuesday)
  • failing: 2+ consecutive failures since last successful run
             (transient flakes don't alert; sustained pattern does)

DB-backed dedup via pipeline_health_state (migration 258) — survives
Railway redeploys. Alert at most once per ALERT_DEDUP_HOURS per incident.
Recovery message when a successful retrain lands after a prior alert
clears last_alert_at so the next outage gets fresh alerting.

Cadence: Mon + Tue 09:00 UTC. Sunday runs at 03:00 UTC, so:
  - by Mon 09:00 a Sunday failure is ~30h old → first alert opportunity
  - by Tue 09:00 a Sunday failure is ~54h old → still in dedup window;
    but a Mon-to-Tue regression (rare) would re-alert under the 48h dedup
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

PIPELINE_NAME = "weekly_retrain"

STALE_THRESHOLD_DAYS = int(os.getenv("RETRAIN_STALE_DAYS", "9"))
FAILING_THRESHOLD_CONSEC = int(os.getenv("RETRAIN_FAILING_CONSEC", "2"))
ALERT_DEDUP_HOURS = int(os.getenv("RETRAIN_ALERT_DEDUP_HOURS", "48"))


def _latest_runs(limit: int = 8) -> list[dict]:
    """Most recent N weekly_retrain runs (any status). Ordered DESC by
    started_at so [0] is the latest attempt."""
    from workers.api_clients.db import execute_query
    return execute_query(
        """SELECT job_name, status, started_at, completed_at,
                  error_message, metadata
             FROM pipeline_runs
            WHERE job_name = %s
            ORDER BY started_at DESC
            LIMIT %s""",
        (PIPELINE_NAME, limit),
    )


def _read_dedup_row() -> dict | None:
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT * FROM pipeline_health_state WHERE pipeline_name = %s LIMIT 1",
        (PIPELINE_NAME,),
    )
    return rows[0] if rows else None


def _set_dedup_row(*, ts: datetime | None, reason: str | None) -> None:
    """UPSERT the dedup row. ts=None clears the incident (recovery path)."""
    from workers.api_clients.db import execute_write
    execute_write(
        """INSERT INTO pipeline_health_state (pipeline_name, last_alert_at,
                                                 last_alert_reason, updated_at)
           VALUES (%s, %s, %s, NOW())
           ON CONFLICT (pipeline_name) DO UPDATE
              SET last_alert_at     = EXCLUDED.last_alert_at,
                  last_alert_reason = EXCLUDED.last_alert_reason,
                  updated_at        = NOW()""",
        (PIPELINE_NAME, ts, reason),
    )


def _evaluate_health(runs: list[dict], now: datetime) -> tuple[str, str]:
    """Classify retrain health. Returns (status, reason) where status is
    'stale' / 'failing' / 'healthy'."""
    if not runs:
        # No retrain history at all — could be a fresh deploy or the table
        # was truncated. Either way, alert so the operator investigates.
        return ("stale", "no weekly_retrain runs recorded in pipeline_runs")

    last_completed = next((r for r in runs if r["status"] == "completed"), None)
    if last_completed is None:
        # Every run in the visible history failed. Definitely worth alerting.
        return ("failing",
                f"latest {len(runs)} weekly_retrain runs all non-completed "
                f"(visible window)")

    age_days = (now - last_completed["started_at"]).total_seconds() / 86400.0
    if age_days > STALE_THRESHOLD_DAYS:
        return ("stale",
                f"last successful retrain {int(age_days)}d ago "
                f"(> {STALE_THRESHOLD_DAYS}d threshold)")

    # Count consecutive failures since last_completed.
    consec_failures = 0
    for r in runs:
        if r["status"] == "completed":
            break
        consec_failures += 1
    if consec_failures >= FAILING_THRESHOLD_CONSEC:
        return ("failing",
                f"{consec_failures} consecutive non-completed runs since "
                f"last success on {last_completed['started_at'].date()}")

    return ("healthy",
            f"last success {int(age_days*24)}h ago, "
            f"{consec_failures} failure(s) in current streak")


def _format_alert(status: str, reason: str, runs: list[dict], now: datetime) -> str:
    """Build the Telegram body. HTML-escaped because send_telegram is HTML."""
    import html as _html

    lines = [
        "🚨 <b>weekly_retrain health alert</b>",
        "",
        f"Status: <b>{_html.escape(status, quote=False)}</b>",
        f"Reason: {_html.escape(reason, quote=False)}",
    ]

    if runs:
        lines.append("")
        lines.append("<b>Last 5 runs</b>:")
        for r in runs[:5]:
            age = (now - r["started_at"]).total_seconds() / 86400.0
            age_str = f"{age:.1f}d ago"
            err = (r.get("error_message") or "")[:80]
            err_str = f" — {_html.escape(err, quote=False)}" if err else ""
            lines.append(
                f"  • {_html.escape(age_str, quote=False)}: "
                f"<b>{_html.escape(r['status'], quote=False)}</b>{err_str}"
            )

    lines.append("")
    if status == "stale":
        lines.append("➡ Check Railway logs for the most recent Sunday cron. "
                     "Model is stuck on the last successful version.")
    elif status == "failing":
        lines.append("➡ Re-run manually: "
                     "<code>python3 -m workers.model.train --version vYYYYMMDD "
                     "--include-pinnacle --include-ou-market</code>")
    return "\n".join(lines)


def _format_recovery(latest: dict, now: datetime) -> str:
    import html as _html
    age_h = (now - latest["started_at"]).total_seconds() / 3600.0
    return (f"✅ <b>weekly_retrain recovered</b>\n"
            f"Last success: {_html.escape(f'{age_h:.1f}h ago', quote=False)}")


def run_retrain_healthcheck(*, dry_run: bool = False) -> dict:
    """Main entry point. Returns counters for the scheduler wrapper.
    Never raises — the alerter must not break the alerter."""
    counters: dict = {
        "status": "unknown",
        "reason": "",
        "alert_sent": False,
        "recovery_sent": False,
        "dedup_skipped": False,
    }

    try:
        from workers.notify.telegram import send_telegram

        now = datetime.now(timezone.utc)
        runs = _latest_runs(limit=8)
        status, reason = _evaluate_health(runs, now)
        counters["status"] = status
        counters["reason"] = reason

        dedup_row = _read_dedup_row()
        last_alert = (dedup_row or {}).get("last_alert_at")

        if status in ("stale", "failing"):
            if last_alert is not None and (now - last_alert) < timedelta(hours=ALERT_DEDUP_HOURS):
                counters["dedup_skipped"] = True
                return counters

            msg = _format_alert(status, reason, runs, now)
            if dry_run:
                counters["alert_sent"] = True
                return counters

            tg_id = send_telegram(
                msg,
                dedup_key=f"retrain-health-{status}",
                dedup_window_s=ALERT_DEDUP_HOURS * 3600,
            )
            if tg_id is not None:
                counters["alert_sent"] = True
                try:
                    _set_dedup_row(ts=now, reason=reason)
                except Exception as e:
                    log.warning("dedup-stamp write failed (alert sent anyway): %s", e)

        elif status == "healthy" and last_alert is not None:
            # Recovery from a prior alerted state.
            if dry_run:
                counters["recovery_sent"] = True
                return counters

            latest_completed = next((r for r in runs if r["status"] == "completed"), runs[0])
            msg = _format_recovery(latest_completed, now)
            send_telegram(
                msg,
                dedup_key="retrain-health-recovery",
                dedup_window_s=300,
            )
            counters["recovery_sent"] = True
            try:
                _set_dedup_row(ts=None, reason=None)
            except Exception as e:
                log.warning("dedup-stamp clear failed: %s", e)

    except Exception as e:
        log.warning("retrain healthcheck raised (non-fatal): %s", e)

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
    c = run_retrain_healthcheck(dry_run=args.dry_run)
    log.info("retrain healthcheck: status=%s reason=%s alert_sent=%s "
             "recovery_sent=%s dedup_skipped=%s",
             c["status"], c["reason"], c["alert_sent"],
             c["recovery_sent"], c["dedup_skipped"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
