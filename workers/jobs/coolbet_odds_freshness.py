"""
COOLBET-ODDS-FRESHNESS-WATCHDOG (2026-07-03) — DB-side watchdog for the
Coolbet odds snapshot pipeline (now Mac-launchd-hosted post COOLBET-
SCRAPERS-MOVED-TO-MAC).

Why this exists, in one sentence: between 2026-06-26 08:43 UTC and
2026-07-03 07:31 UTC, `coolbet_odds_snapshot` on the VPS silently wrote
zero rows into `odds_snapshots(bookmaker='Coolbet')` — 7 days of Imperva
403's while pipeline_runs.status stayed 'completed'. Same silent-failure
class as the InplayBot UUID incident. Now that the scraper is on Mac
launchd (residential IP + real Chrome fingerprint), launchd's exit
codes aren't visible to the scheduler-side alerter — so we need a
DB-side data-freshness check that fires regardless of where the writer
runs.

Alert condition:
  • MAX(odds_snapshots.timestamp WHERE bookmaker='Coolbet') is older
    than COOLBET_ODDS_STALE_HOURS (default 2h — cadence is 30 min so
    2h = 4 consecutive misses, well past a transient Imperva blip).

Mirrors CS2-PIPELINE-HEALTHCHECK pattern. DB-backed dedup via
pipeline_health_state (pipeline_name='coolbet_odds'). Alerts at most
once per 12h per incident. Recovery message on first heal.

Runs on the VPS scheduler even though the writer is on Mac — the
watchdog is authoritative about DATA freshness, not process liveness.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

PIPELINE_NAME = "coolbet_odds"

# Writer cron fires every 30 min (:03/:33) on Mac launchd. 2h = 4
# consecutive misses before alerting — well past transient Imperva
# blips or Mac sleep/wake gaps, well before the 7-day silent outage class.
STALE_HOURS = float(os.getenv("COOLBET_ODDS_STALE_HOURS", "2"))

ALERT_DEDUP_HOURS = int(os.getenv("COOLBET_ODDS_ALERT_DEDUP_HOURS", "12"))


def _read_freshness() -> dict:
    """Snapshot of Coolbet odds table recency + per-window row counts."""
    from workers.api_clients.db import execute_query
    row = execute_query(
        """SELECT
              MAX(timestamp) FILTER (WHERE bookmaker = 'Coolbet') AS last_write,
              COUNT(*) FILTER (WHERE bookmaker = 'Coolbet'
                               AND timestamp > NOW() - INTERVAL '30 minutes') AS last_30m,
              COUNT(*) FILTER (WHERE bookmaker = 'Coolbet'
                               AND timestamp > NOW() - INTERVAL '2 hours') AS last_2h
           FROM odds_snapshots""",
        None,
    )
    return row[0] if row else {"last_write": None, "last_30m": 0, "last_2h": 0}


def _read_dedup_row() -> dict | None:
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT * FROM pipeline_health_state WHERE pipeline_name = %s LIMIT 1",
        (PIPELINE_NAME,),
    )
    return rows[0] if rows else None


def _set_dedup_row(*, ts: datetime | None, reason: str | None) -> None:
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


def _evaluate(state: dict, now: datetime) -> tuple[str, str]:
    """Classify freshness. Returns (status, reason)."""
    last_write = state.get("last_write")
    if last_write is None:
        return ("stale",
                "odds_snapshots(bookmaker='Coolbet') has zero rows — writer has never landed data")

    age_h = (now - last_write).total_seconds() / 3600.0
    if age_h > STALE_HOURS:
        return ("stale",
                f"odds_snapshots(bookmaker='Coolbet') last write {age_h:.1f}h ago "
                f"(> {STALE_HOURS}h threshold, cadence 30min so {int(age_h * 2)}+ misses)")

    return ("healthy",
            f"last write {age_h:.2f}h ago; {state.get('last_30m') or 0} rows in last 30min, "
            f"{state.get('last_2h') or 0} rows in last 2h")


def _format_alert(reason: str, state: dict, now: datetime) -> str:
    import html as _html
    lines = [
        "🚨 <b>Coolbet odds freshness alert</b>",
        "",
        f"Reason: {_html.escape(reason, quote=False)}",
        "",
        "<b>Current state</b>:",
    ]
    last_write = state.get("last_write")
    if last_write is None:
        lines.append("  • last write: <b>never</b>")
    else:
        age_h = (now - last_write).total_seconds() / 3600.0
        lines.append(f"  • last write: {age_h:.1f}h ago ({last_write.isoformat()})")
    lines.append(f"  • rows in last 30min: {state.get('last_30m') or 0}")
    lines.append(f"  • rows in last 2h:   {state.get('last_2h') or 0}")
    lines.append("")
    lines.append(
        "➡ Writer is <code>com.oddsintel.coolbet-odds-snapshot</code> on the "
        "operator's Mac (launchd, :03/:33 UTC). Check: "
        "<code>launchctl list | grep coolbet-odds-snapshot</code>, "
        "<code>tail -50 dev/active/coolbet-odds-snapshot.log</code>. "
        "If Imperva 403 is back — Mac IP / Chrome fingerprint may have been flagged. "
        "If launchd shows the job unloaded — Mac was rebooted; re-load the plist."
    )
    return "\n".join(lines)


def _format_recovery(state: dict, now: datetime) -> str:
    import html as _html
    last_write = state.get("last_write")
    age_h = (now - last_write).total_seconds() / 3600.0 if last_write else None
    age_str = f"{age_h:.2f}h ago" if age_h is not None else "fresh"
    return (f"✅ <b>Coolbet odds freshness recovered</b>\n"
            f"Last write {_html.escape(age_str, quote=False)} · "
            f"{state.get('last_30m') or 0} rows in last 30min")


def run_coolbet_odds_freshness_check(*, dry_run: bool = False) -> dict:
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
        state = _read_freshness()
        status, reason = _evaluate(state, now)
        counters["status"] = status
        counters["reason"] = reason

        dedup_row = _read_dedup_row()
        last_alert = (dedup_row or {}).get("last_alert_at")

        if status == "stale":
            if last_alert is not None and (now - last_alert) < timedelta(hours=ALERT_DEDUP_HOURS):
                counters["dedup_skipped"] = True
                return counters

            msg = _format_alert(reason, state, now)
            if dry_run:
                counters["alert_sent"] = True
                return counters

            tg_id = send_telegram(
                msg,
                dedup_key="coolbet-odds-freshness",
                dedup_window_s=ALERT_DEDUP_HOURS * 3600,
            )
            if tg_id is not None:
                counters["alert_sent"] = True
                try:
                    _set_dedup_row(ts=now, reason=reason)
                except Exception as e:
                    log.warning("dedup-stamp write failed (alert sent anyway): %s", e)

        elif status == "healthy" and last_alert is not None:
            if dry_run:
                counters["recovery_sent"] = True
                return counters

            msg = _format_recovery(state, now)
            send_telegram(
                msg,
                dedup_key="coolbet-odds-freshness-recovery",
                dedup_window_s=300,
            )
            counters["recovery_sent"] = True
            try:
                _set_dedup_row(ts=None, reason=None)
            except Exception as e:
                log.warning("dedup-stamp clear failed: %s", e)

    except Exception as e:
        log.warning("coolbet odds freshness check raised (non-fatal): %s", e)

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
    c = run_coolbet_odds_freshness_check(dry_run=args.dry_run)
    log.info("coolbet odds freshness: status=%s reason=%s alert_sent=%s "
             "recovery_sent=%s dedup_skipped=%s",
             c["status"], c["reason"], c["alert_sent"],
             c["recovery_sent"], c["dedup_skipped"])
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
