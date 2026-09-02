"""ODDS-FRESHNESS — generic DB-side staleness watchdog for any bookmaker feed.

Why this exists: EPICBET-403-FROM-VPS-2026-08-29. The Epicbet ingest wrote
zero rows for six days — 277 consecutive scheduler runs, every one recording
`status='completed'` — because Cloudflare 403'd the VPS and the job caught the
exception and returned normally. `pipeline_runs` said healthy the whole time.

`coolbet_odds_freshness.py` exists for exactly this failure class and caught a
7-day Coolbet outage, but it is hardcoded to `bookmaker='Coolbet'`, so Epicbet
had no equivalent. This module is the same idea with the bookmaker as a
parameter, so adding a third book is one call rather than another 230-line copy.

Deliberately NOT a refactor of coolbet_odds_freshness.py. That file is live
alerting code whose whole job is catching silent failures; rewriting it to add
a second caller would put the working watchdog at risk to save duplication.
It should migrate here once this has run clean for a while.

Process-liveness is not the signal — DATA freshness is. The writer may live on
the VPS scheduler, Mac launchd, or behind FlareSolverr; this asks only whether
rows are landing.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# Per-bookmaker tuning. `stale_hours` should be several missed cycles, not one:
# alerting on a single transient blip trains the operator to ignore it.
FEEDS: dict[str, dict] = {
    # Ingest fires :02/:32, so 2h = 4 consecutive misses.
    "Epicbet": {"stale_hours": 2.0, "dedup_hours": 12},
}


def _read_freshness(bookmaker: str) -> dict:
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT MAX(timestamp)                        AS last_write,
                  COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '30 minutes') AS last_30m,
                  COUNT(*) FILTER (WHERE timestamp > NOW() - INTERVAL '2 hours')    AS last_2h
             FROM odds_snapshots
            WHERE bookmaker = %s""",
        (bookmaker,),
    )
    return rows[0] if rows else {"last_write": None, "last_30m": 0, "last_2h": 0}


def _pipeline_name(bookmaker: str) -> str:
    return f"{bookmaker.lower()}_odds"


def _read_dedup_row(bookmaker: str) -> dict | None:
    from workers.api_clients.db import execute_query
    rows = execute_query(
        "SELECT * FROM pipeline_health_state WHERE pipeline_name = %s LIMIT 1",
        (_pipeline_name(bookmaker),),
    )
    return rows[0] if rows else None


def _set_dedup_row(bookmaker: str, *, ts: datetime | None, reason: str | None) -> None:
    from workers.api_clients.db import execute_write
    execute_write(
        """INSERT INTO pipeline_health_state (pipeline_name, last_alert_at, last_reason)
           VALUES (%s, %s, %s)
           ON CONFLICT (pipeline_name)
           DO UPDATE SET last_alert_at = EXCLUDED.last_alert_at,
                         last_reason   = EXCLUDED.last_reason""",
        (_pipeline_name(bookmaker), ts, reason),
    )


def _evaluate(bookmaker: str, state: dict, now: datetime, stale_hours: float) -> tuple[str, str]:
    last = state.get("last_write")
    if last is None:
        return "stale", f"no {bookmaker} rows in odds_snapshots at all"
    age_h = (now - last).total_seconds() / 3600.0
    if age_h > stale_hours:
        return "stale", (
            f"last {bookmaker} row is {age_h:.1f}h old "
            f"(threshold {stale_hours:.0f}h; {state.get('last_2h') or 0} rows in the last 2h)"
        )
    return "ok", f"last {bookmaker} row {age_h:.1f}h old"


def check_feed(bookmaker: str, *, dry_run: bool = False) -> dict:
    """Alert if `bookmaker` has stopped writing to odds_snapshots.

    Never raises — an alerter that dies takes the alerting with it.
    """
    cfg = FEEDS.get(bookmaker, {})
    stale_hours = float(os.getenv(f"{bookmaker.upper()}_ODDS_STALE_HOURS",
                                  cfg.get("stale_hours", 2.0)))
    dedup_hours = int(os.getenv(f"{bookmaker.upper()}_ODDS_ALERT_DEDUP_HOURS",
                                cfg.get("dedup_hours", 12)))
    counters = {"bookmaker": bookmaker, "status": "unknown", "reason": "",
                "alert_sent": False, "recovery_sent": False, "dedup_skipped": False}
    try:
        from workers.notify.telegram import send_telegram

        now = datetime.now(timezone.utc)
        state = _read_freshness(bookmaker)
        status, reason = _evaluate(bookmaker, state, now, stale_hours)
        counters["status"], counters["reason"] = status, reason

        row = _read_dedup_row(bookmaker) or {}
        last_alert = row.get("last_alert_at")

        if status == "stale":
            if last_alert is not None and (now - last_alert) < timedelta(hours=dedup_hours):
                counters["dedup_skipped"] = True
                return counters
            if not dry_run:
                send_telegram(
                    f"🔴 {bookmaker} odds feed STALE\n\n{reason}\n\n"
                    f"The job can report 'completed' while writing nothing — "
                    f"check the ingest log before trusting pipeline_runs.",
                    dedup_key=f"{bookmaker.lower()}-odds-freshness",
                    dedup_window_s=dedup_hours * 3600,
                )
                _set_dedup_row(bookmaker, ts=now, reason=reason)
            counters["alert_sent"] = True
            return counters

        # Recovered: clear the marker and say so once.
        if last_alert is not None:
            if not dry_run:
                send_telegram(f"✅ {bookmaker} odds feed recovered — {reason}")
                _set_dedup_row(bookmaker, ts=None, reason=None)
            counters["recovery_sent"] = True
        return counters
    except Exception as e:                       # noqa: BLE001 - see docstring
        log.warning("odds freshness check for %s failed: %s", bookmaker, e)
        counters["status"] = "check_failed"
        counters["reason"] = f"{type(e).__name__}: {e}"
        return counters
