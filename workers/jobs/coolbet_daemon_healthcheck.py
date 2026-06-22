"""
COOLBET-DAEMON-HEALTHCHECK — Railway-side daemon health alert (2026-06-21).

Closes the third leg of the alerting story. The 2026-06-18 → 2026-06-21
outage went silent for 3 days because:

  1. coolbet_mac_daemon's alert path has `alert_fired_this_burst` — once
     the first alert fires in a streak of failures, no more alerts (or
     auto-heals) happen until a *clean tick*. There were zero clean ticks
     during the outage, so only the very first alert ever fired.
  2. The daemon's Telegram dedup is in-process — dies with the process.
  3. The Mac daemon IS the alerter. Mac sleep / daemon crash kills
     monitoring AND placement together.

This job runs every 30 min on Railway, independent of the Mac. It reads
coolbet_session_state directly from the DB and decides whether to alert.
DB-backed dedup via last_health_alert_at (migration 256) survives Railway
redeploys and bounds alert rate to once per ALERT_DEDUP_HOURS.

Conditions (any one fires an alert):
  • silent:  mac_daemon_last_tick_at IS NULL OR > 90 min old
             (daemon crashed, Mac asleep, launchd job broken)
  • erroring: last tick result.errors > 0 AND no successful auto_self_heal
             in the last 2 hours (sustained, not a transient blip)

On healthy state AFTER a previous alert: send a recovery Telegram once
and clear last_health_alert_at so the next outage gets fresh alerting.

Complements (does NOT replace) the Mac daemon's in-process alert. The
Mac alert is fast on the first failure inside one process lifetime; this
job is the safety net that survives the things the Mac can't.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

# How fresh the Mac daemon's last tick must be. Daemon ticks every 30 min;
# allow 90 min before we conclude it's stuck or asleep.
SILENT_THRESHOLD_MIN = int(os.getenv("COOLBET_HEALTH_SILENT_MIN", "90"))

# Errors are noisy in short windows (transient network blips, single
# stuck request). Only alert when the daemon has been failing long enough
# that auto_self_heal hasn't recovered it within this window.
SUSTAINED_ERROR_THRESHOLD_MIN = int(os.getenv("COOLBET_HEALTH_ERROR_SUSTAINED_MIN", "120"))

# Dedup window — fire at most once per this many hours per incident. The
# operator only needs to hear about the same outage so often.
ALERT_DEDUP_HOURS = int(os.getenv("COOLBET_HEALTH_DEDUP_HOURS", "4"))

# JWT-staleness threshold. JWT-stale-with-pending-picks alerts only when
# the JWT is BOTH expired (or about to expire) AND there's a calibrated
# pick whose kickoff is within JWT_STALE_KO_WINDOW_HOURS — without the
# pending-picks gate, an overnight idle daemon (no work) would alert
# every dedup cycle even though nothing's actually wrong.
JWT_STALE_GRACE_MIN = int(os.getenv("COOLBET_HEALTH_JWT_GRACE_MIN", "30"))
JWT_STALE_KO_WINDOW_HOURS = int(os.getenv("COOLBET_HEALTH_JWT_KO_WINDOW_HOURS", "6"))


def _read_state_row() -> dict | None:
    """Load the singleton coolbet_session_state row (id=1) with the columns
    we need. Returns None if the row is missing (which is itself an alert
    condition the caller handles)."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT mac_daemon_last_tick_at,
                  mac_daemon_last_tick_result,
                  last_health_alert_at,
                  jwt_exp_at
             FROM coolbet_session_state
            WHERE id = 1
            LIMIT 1"""
    )
    if not rows:
        return None
    return rows[0]


def _last_successful_heal_at() -> datetime | None:
    """Most recent `coolbet_heal_log` row where the heal recovered. Used to
    decide whether a currently-errored tick is sustained or transient.
    Returns None if no recovery in the last 7 days (which means we treat
    any current error as sustained)."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT MAX(triggered_at) AS t
             FROM coolbet_heal_log
            WHERE recovered = TRUE
              AND triggered_at >= NOW() - INTERVAL '7 days'"""
    )
    if not rows or not rows[0].get("t"):
        return None
    return rows[0]["t"]


def _last_heal_attempt() -> dict | None:
    """Most recent coolbet_heal_log row (any outcome) for context in the
    alert payload. Includes state_after, message, actions so the Telegram
    tells the operator what the last self-heal tried."""
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """SELECT triggered_at, triggered_by, state_before, state_after,
                  recovered, message, actions
             FROM coolbet_heal_log
            ORDER BY triggered_at DESC
            LIMIT 1"""
    )
    if not rows:
        return None
    return rows[0]


def _set_last_health_alert_at(ts: datetime | None) -> None:
    """Write the dedup timestamp. Pass None to clear (used on recovery)."""
    from workers.api_clients.db import execute_write
    execute_write(
        "UPDATE coolbet_session_state SET last_health_alert_at = %s WHERE id = 1",
        (ts,),
    )


def _pending_calibrated_picks_in_ko_window(hours: int) -> int:
    """Count calibrated-bot picks that are signaled, NOT placed, NOT skipped,
    AND have a kickoff within [now-5min, now+hours]. Used by the JWT-stale
    branch to gate the alert — without pending picks, a stale JWT doesn't
    matter (the daemon will refresh on the next placement attempt anyway).
    """
    from workers.api_clients.db import execute_query
    rows = execute_query(
        """
        SELECT COUNT(*) AS n
          FROM simulated_bets sb
          JOIN bots b ON b.id = sb.bot_id
          JOIN matches m ON m.id = sb.match_id
         WHERE b.maturity_label = 'calibrated' AND b.is_active = TRUE
           AND sb.signaled_at IS NOT NULL
           AND sb.user_placed_at IS NULL
           AND sb.user_skipped_at IS NULL
           AND m.date BETWEEN NOW() - INTERVAL '5 min'
                          AND NOW() + (%s || ' hours')::interval
           AND NOT EXISTS (
               SELECT 1 FROM real_bets rb WHERE rb.simulated_bet_id = sb.id
           )
        """,
        (hours,),
    )
    return int(rows[0]["n"]) if rows else 0


def _normalise_tick_result(raw) -> dict:
    """mac_daemon_last_tick_result is JSONB; psycopg2 may return str or dict
    depending on the cursor. Normalise to dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _evaluate_health(state: dict | None, now: datetime) -> tuple[str, str]:
    """Decide current daemon health. Returns (status, reason) where status
    is one of 'silent' / 'erroring' / 'jwt_stale' / 'healthy'. `reason`
    is a short string the alert body uses verbatim."""
    if state is None:
        return ("silent", "coolbet_session_state row missing")

    last_tick = state.get("mac_daemon_last_tick_at")
    if last_tick is None:
        return ("silent", "Mac daemon has never reported a tick")

    age_min = (now - last_tick).total_seconds() / 60.0
    if age_min > SILENT_THRESHOLD_MIN:
        return ("silent",
                f"last tick {int(age_min)}m ago "
                f"(> {SILENT_THRESHOLD_MIN}m threshold)")

    result = _normalise_tick_result(state.get("mac_daemon_last_tick_result"))
    if int(result.get("errors") or 0) > 0:
        last_recovered = _last_successful_heal_at()
        if last_recovered is None:
            return ("erroring",
                    "last tick errors > 0; no successful auto-heal in 7 days")
        recovered_age_min = (now - last_recovered).total_seconds() / 60.0
        if recovered_age_min > SUSTAINED_ERROR_THRESHOLD_MIN:
            return ("erroring",
                    f"last tick errors > 0; sustained {int(recovered_age_min)}m "
                    f"without a successful auto-heal")
        # Recent recovery — likely a transient blip
        return ("healthy",
                f"errored tick but auto-heal recovered "
                f"{int(recovered_age_min)}m ago")

    # JWT-stale branch (COOLBET-HEALTHCHECK-JWT-AWARE 2026-06-22): the
    # daemon's _tick() is SILENT-WHEN-EMPTY — when there are no qualified
    # bets, it returns counters with errors=0 without touching CDP/JWT.
    # An overnight gap can therefore leave the JWT expired (Coolbet
    # invalidates after ~10h idle) while every healthcheck sees clean
    # ticks. When picks finally arrive later, the first placement attempt
    # fails on the expired JWT and we lose the bet to slippage / KO drift.
    # Surface this BEFORE the picks arrive so the operator can heal pre-
    # emptively. Gate on pending-calibrated-picks-within-KO-window so an
    # idle Sunday→Monday morning doesn't alert when there's truly no work
    # to do.
    jwt_exp = state.get("jwt_exp_at")
    jwt_age_min = None
    if jwt_exp is not None:
        # Negative = expired N minutes ago; positive = N minutes until expiry.
        jwt_ttl_min = (jwt_exp - now).total_seconds() / 60.0
        jwt_age_min = -jwt_ttl_min  # positive when expired
    if jwt_exp is None or jwt_age_min is not None and jwt_age_min > JWT_STALE_GRACE_MIN:
        try:
            pending = _pending_calibrated_picks_in_ko_window(JWT_STALE_KO_WINDOW_HOURS)
        except Exception:
            # Don't break the healthcheck on a DB hiccup — fall through
            # to healthy if we can't verify pending picks. The silent
            # branch already covers the catastrophic case.
            pending = 0
        if pending > 0:
            if jwt_age_min is not None:
                preamble = f"JWT expired {int(jwt_age_min)}m ago"
            else:
                preamble = "JWT never set"
            return ("jwt_stale",
                    f"{preamble} and {pending} pending calibrated "
                    f"pick(s) kickoff within {JWT_STALE_KO_WINDOW_HOURS}h — "
                    f"first placement will fail")

    return ("healthy", "last tick clean")


def _format_alert(status: str, reason: str, state: dict,
                   heal_attempt: dict | None, now: datetime) -> str:
    """Build the Telegram message body. HTML-escaped because send_telegram
    hardcodes parse_mode=HTML."""
    import html as _html

    last_tick = state.get("mac_daemon_last_tick_at")
    tick_str = (f"{int((now - last_tick).total_seconds()/60)}m ago"
                if last_tick else "never")

    jwt_exp = state.get("jwt_exp_at")
    jwt_str = "?"
    if jwt_exp is not None:
        ttl_min = (jwt_exp - now).total_seconds() / 60.0
        jwt_str = f"{int(ttl_min):+d}m" if ttl_min > -10000 else "stale"

    lines = [
        f"🚨 <b>Coolbet daemon health alert</b>",
        "",
        f"Status: <b>{_html.escape(status, quote=False)}</b>",
        f"Reason: {_html.escape(reason, quote=False)}",
        f"Last tick: {_html.escape(tick_str, quote=False)}",
        f"JWT expiry: {_html.escape(jwt_str, quote=False)}",
    ]

    if heal_attempt:
        heal_when = heal_attempt.get("triggered_at")
        heal_age = ("?"
                    if not heal_when
                    else f"{int((now - heal_when).total_seconds()/60)}m ago")
        state_after = heal_attempt.get("state_after") or "?"
        msg = (heal_attempt.get("message") or "")[:140]
        lines.append("")
        lines.append(f"<b>Last self-heal</b> ({_html.escape(heal_age, quote=False)}):")
        lines.append(f"state_after: {_html.escape(str(state_after), quote=False)}")
        lines.append(f"{_html.escape(msg, quote=False)}")

    # Action hint — keyed off the recent self-heal state when present,
    # otherwise generic.
    state_after = (heal_attempt or {}).get("state_after") or "unknown"
    hint_by_state = {
        "chrome_down":              "Run ./local/launch_chrome_for_sync.sh on the Mac.",
        "chrome_at_profile_picker": "Click your profile in the running CDP-Chrome window, then open coolbet.com.",
        "no_coolbet_tab":           "Open a coolbet.com tab in CDP-Chrome.",
        "logged_out":               "Log into coolbet.com in CDP-Chrome.",
        "jwt_expired":              "Refresh the coolbet.com tab (or log in again).",
    }
    if status == "silent":
        hint = ("Check the Mac — daemon may be dead or Mac asleep. "
                "`launchctl list | grep coolbet` to see launchd status.")
    elif status == "jwt_stale":
        # JWT expired pre-emptively (before any tick had a chance to error).
        # The recovery action is identical to the logged_out / jwt_expired
        # hint — just heal proactively before picks arrive.
        hint = ("JWT expired during the daemon's idle window. Run "
                "`python3 -m workers.automation.coolbet_browser_sync "
                "--full-heal` on the Mac OR open Coolbet in CDP-Chrome "
                "to refresh — picks have KO within the next 6h.")
    else:
        hint = hint_by_state.get(state_after,
                                  "Run python3 -m workers.automation.coolbet_browser_sync --full-heal on the Mac.")
    lines.append("")
    lines.append(f"➡ {_html.escape(hint, quote=False)}")
    return "\n".join(lines)


def _format_recovery(state: dict, now: datetime) -> str:
    """Recovery Telegram when daemon comes back online after a prior alert.
    Quiet, single line — the user just needs to know the incident closed."""
    import html as _html
    last_tick = state.get("mac_daemon_last_tick_at")
    tick_str = (f"{int((now - last_tick).total_seconds()/60)}m ago"
                if last_tick else "?")
    return (f"✅ <b>Coolbet daemon recovered</b>\n"
            f"Last tick: {_html.escape(tick_str, quote=False)} (clean)")


def run_daemon_healthcheck(*, dry_run: bool = False) -> dict:
    """Main entry point. Returns counters for the scheduler wrapper to log.
    Always returns; never raises (the alerter must not break the alerter)."""
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
        state = _read_state_row()
        status, reason = _evaluate_health(state, now)
        counters["status"] = status
        counters["reason"] = reason

        last_alert = (state or {}).get("last_health_alert_at")

        if status in ("silent", "erroring", "jwt_stale"):
            if last_alert is not None and (now - last_alert) < timedelta(hours=ALERT_DEDUP_HOURS):
                counters["dedup_skipped"] = True
                return counters

            heal_attempt = _last_heal_attempt()
            msg = _format_alert(status, reason, state or {}, heal_attempt, now)

            if dry_run:
                counters["alert_sent"] = True
                return counters

            tg_id = send_telegram(
                msg,
                dedup_key=f"daemon-health-{status}",
                dedup_window_s=ALERT_DEDUP_HOURS * 3600,
            )
            if tg_id is not None:
                counters["alert_sent"] = True
                try:
                    _set_last_health_alert_at(now)
                except Exception as e:
                    log.warning("dedup-stamp write failed (alert sent anyway): %s", e)

        elif status == "healthy" and last_alert is not None:
            # Recovery from a prior alerted state.
            if dry_run:
                counters["recovery_sent"] = True
                return counters

            msg = _format_recovery(state or {}, now)
            send_telegram(
                msg,
                dedup_key="daemon-health-recovery",
                dedup_window_s=300,  # short window — recovery msg should usually be one-shot
            )
            counters["recovery_sent"] = True
            try:
                _set_last_health_alert_at(None)
            except Exception as e:
                log.warning("dedup-stamp clear failed: %s", e)

    except Exception as e:
        # Never let the alerter break. Just log.
        log.warning("daemon healthcheck raised (non-fatal): %s", e)

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
    c = run_daemon_healthcheck(dry_run=args.dry_run)
    log.info("daemon healthcheck: status=%s reason=%s alert_sent=%s "
             "recovery_sent=%s dedup_skipped=%s",
             c["status"], c["reason"], c["alert_sent"],
             c["recovery_sent"], c["dedup_skipped"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
