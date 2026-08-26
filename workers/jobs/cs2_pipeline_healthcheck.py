"""
CS2-PIPELINE-HEALTHCHECK (2026-06-21) — DB-side watchdog for the CS2 paper-bet
pipeline. Independent of the scheduler wrapper so it catches the case where
the wrapper itself silently lies about success.

Why this exists, in one sentence: between 2026-06-14 and 2026-06-21, every
cs2_* job on the VPS logged status='completed' in pipeline_runs while
cs2_upcoming_matches received zero new rows — 9 days of silent outage.
The scheduler-side fix (CS2-PIPELINE-TRUTHFUL-LOGGING, same commit) makes
non-zero subprocess exits propagate as RuntimeError so pipeline_runs reflects
truth. This module is the DB-side safety net for the OTHER failure mode:
subprocess exits 0 but writes nothing.

Mirrors the RETRAIN-HEALTHCHECK pattern (workers/jobs/retrain_healthcheck.py).

Alert conditions (any one fires):
  • stale_scanner: MAX(cs2_upcoming_matches.scanned_at) is > 6 hours old
  • stale_bot:     MAX(cs2_simulated_bets.placed_at) is > 24 hours old
                   AND there are >0 future-kickoff rows in cs2_upcoming_matches
                   (otherwise it's a quiet day, not a failure)

DB-backed dedup via pipeline_health_state (migration 258, pipeline_name=
'cs2_pipeline'). Alerts at most once per 12h per incident. Recovery message
on first heal after a prior alert.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

PIPELINE_NAME = "cs2_pipeline"

# Scanner cron fires every 30 min 10-23 UTC. 6h gives 12 consecutive misses
# before alerting — well past transient bo3.gg / HLTV blips, well before the
# 9-day silent-outage class. Configurable via env for tuning.
SCANNER_STALE_HOURS = int(os.getenv("CS2_HEALTH_SCANNER_HOURS", "6"))

# Bot runs every 30 min 10-23 UTC. 24h means we tolerate overnight quiet
# windows; alerts only when the scanner is producing matches but no bot
# picks land — the symptom that surfaced in this incident (cs2_upcoming
# repopulated but no new cs2_simulated_bets).
BOT_STALE_HOURS = int(os.getenv("CS2_HEALTH_BOT_HOURS", "24"))

ALERT_DEDUP_HOURS = int(os.getenv("CS2_HEALTH_ALERT_DEDUP_HOURS", "12"))


def _read_pipeline_state() -> dict:
    """Snapshot of the most recent writes across the CS2 paper-bet path."""
    from workers.api_clients.db import execute_query
    row = execute_query(
        """SELECT
              (SELECT MAX(scanned_at) FROM cs2_upcoming_matches)   AS last_scan,
              (SELECT COUNT(*) FROM cs2_upcoming_matches
                WHERE kickoff_time >= NOW())                       AS upcoming_future,
              (SELECT MAX(placed_at) FROM cs2_simulated_bets)      AS last_bet,
              (SELECT COUNT(*) FROM cs2_simulated_bets
                WHERE result IS NULL
                  AND kickoff_time < NOW() - INTERVAL '6 hours')   AS stale_open_bets""",
        None,
    )
    return row[0] if row else {
        "last_scan": None, "upcoming_future": 0,
        "last_bet": None, "stale_open_bets": 0,
    }


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


def _evaluate_health(state: dict, now: datetime) -> tuple[str, str]:
    """Classify pipeline health. Returns (status, reason)."""
    last_scan = state.get("last_scan")
    if last_scan is None:
        return ("stale_scanner",
                "cs2_upcoming_matches has zero rows — scanner has never written")

    scan_age_h = (now - last_scan).total_seconds() / 3600.0
    if scan_age_h > SCANNER_STALE_HOURS:
        return ("stale_scanner",
                f"cs2_upcoming_matches last scanned {scan_age_h:.1f}h ago "
                f"(> {SCANNER_STALE_HOURS}h threshold)")

    # Scanner is fresh; check bot. Bot only matters if there ARE future matches
    # to score — empty future cohort = quiet day, not failure.
    if (state.get("upcoming_future") or 0) > 0:
        last_bet = state.get("last_bet")
        if last_bet is None:
            return ("stale_bot",
                    f"{state['upcoming_future']} future matches scored, but "
                    f"cs2_simulated_bets has zero rows — bot has never fired")

        bet_age_h = (now - last_bet).total_seconds() / 3600.0
        if bet_age_h > BOT_STALE_HOURS:
            return ("stale_bot",
                    f"bot last fired {bet_age_h:.1f}h ago despite "
                    f"{state['upcoming_future']} future matches available")

    return ("healthy",
            f"scanner {scan_age_h:.1f}h ago, "
            f"{state.get('upcoming_future') or 0} future matches, "
            f"{state.get('stale_open_bets') or 0} bets stuck open >6h post-KO")


def _format_alert(status: str, reason: str, state: dict, now: datetime) -> str:
    import html as _html

    lines = [
        "🚨 <b>cs2_pipeline health alert</b>",
        "",
        f"Status: <b>{_html.escape(status, quote=False)}</b>",
        f"Reason: {_html.escape(reason, quote=False)}",
        "",
        "<b>Current state</b>:",
    ]

    def _fmt_age(ts):
        if ts is None:
            return "never"
        h = (now - ts).total_seconds() / 3600.0
        return f"{h:.1f}h ago"

    lines.append(f"  • cs2_upcoming_matches last scan: {_html.escape(_fmt_age(state.get('last_scan')), quote=False)}")
    lines.append(f"  • cs2_upcoming_matches future-KO rows: {state.get('upcoming_future') or 0}")
    lines.append(f"  • cs2_simulated_bets last placed: {_html.escape(_fmt_age(state.get('last_bet')), quote=False)}")
    lines.append(f"  • bets stuck open >6h post-KO: {state.get('stale_open_bets') or 0}")

    lines.append("")
    if status == "stale_scanner":
        lines.append("➡ Check the VPS logs for the most recent <code>cs2_scanner</code> + "
                     "<code>cs2_hltv_upcoming</code> + <code>cs2_pinnacle_scanner</code> runs. "
                     "Pipeline_runs.error_message now carries stderr after the "
                     "CS2-PIPELINE-TRUTHFUL-LOGGING fix.")
    else:  # stale_bot
        lines.append("➡ Scanner is writing but bot isn't firing. Likely causes: "
                     "(a) odds tables empty (no <code>bookie_odds1</code> / "
                     "<code>pinnacle_odds1</code> / <code>coolbet_odds1</code>); "
                     "(b) calibration/threshold change rejected every pick. "
                     "Check <code>cs2_bot</code> stdout in pipeline_runs.")
    return "\n".join(lines)


def _format_recovery(state: dict, now: datetime) -> str:
    import html as _html
    last_scan = state.get("last_scan")
    age_h = (now - last_scan).total_seconds() / 3600.0 if last_scan else None
    age_str = f"{age_h:.1f}h ago" if age_h is not None else "fresh"
    return (f"✅ <b>cs2_pipeline recovered</b>\n"
            f"Scanner active: {_html.escape(age_str, quote=False)} · "
            f"{state.get('upcoming_future') or 0} future matches scored")


def run_pipeline_healthcheck(*, dry_run: bool = False) -> dict:
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
        state = _read_pipeline_state()
        status, reason = _evaluate_health(state, now)
        counters["status"] = status
        counters["reason"] = reason

        dedup_row = _read_dedup_row()
        last_alert = (dedup_row or {}).get("last_alert_at")

        if status.startswith("stale"):
            if last_alert is not None and (now - last_alert) < timedelta(hours=ALERT_DEDUP_HOURS):
                counters["dedup_skipped"] = True
                return counters

            msg = _format_alert(status, reason, state, now)
            if dry_run:
                counters["alert_sent"] = True
                return counters

            tg_id = send_telegram(
                msg,
                dedup_key=f"cs2-pipeline-{status}",
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
                dedup_key="cs2-pipeline-recovery",
                dedup_window_s=300,
            )
            counters["recovery_sent"] = True
            try:
                _set_dedup_row(ts=None, reason=None)
            except Exception as e:
                log.warning("dedup-stamp clear failed: %s", e)

    except Exception as e:
        log.warning("cs2 pipeline healthcheck raised (non-fatal): %s", e)

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
    c = run_pipeline_healthcheck(dry_run=args.dry_run)
    log.info("cs2 pipeline healthcheck: status=%s reason=%s alert_sent=%s "
             "recovery_sent=%s dedup_skipped=%s",
             c["status"], c["reason"], c["alert_sent"],
             c["recovery_sent"], c["dedup_skipped"])
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
