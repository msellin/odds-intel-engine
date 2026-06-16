"""
coolbet_session_state writer — thin helper for the singleton observability row.

Why a separate module: CoolbetSession itself should stay focused on auth +
transport. The state-table writes are observability, used by admin pages and
the Telegram /status command. Putting them in their own file makes it easy
to grep "everywhere we update session state" and keeps the session class
small.

Every write is best-effort: a failed state UPDATE must NEVER bring down the
session. We log the failure and move on. The state is for ops visibility,
not for transactional correctness.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)


def _safe_write(sql: str, params: tuple) -> None:
    """Best-effort UPDATE — silently swallows DB errors so session ops aren't
    blocked by an observability hiccup. Logs at warning level so the gap
    surfaces in standard log scraping without aborting the caller."""
    try:
        # Local import — avoids a circular dep with workers.api_clients.db
        # when this module is imported from coolbet_session.py at module load.
        from workers.api_clients.db import execute_write
        execute_write(sql, params)
    except Exception as e:
        log.warning("coolbet_session_state write failed (non-fatal): %s", e)


def mark_login_success(*, method: str, user_id: str | None,
                        jwt_exp_at: datetime | None,
                        fs_url: str | None = None,
                        fs_session_name: str | None = None) -> None:
    """Successful login — clears last_error and stamps last_login_at."""
    _safe_write(
        """UPDATE coolbet_session_state
           SET last_login_at = NOW(),
               last_login_method = %s,
               jwt_user_id = %s,
               jwt_exp_at = %s,
               session_healthy = TRUE,
               last_error = NULL,
               last_error_at = NULL,
               fs_url = COALESCE(%s, fs_url),
               fs_session_name = COALESCE(%s, fs_session_name)
           WHERE id = 1""",
        (method, user_id, jwt_exp_at, fs_url, fs_session_name),
    )


def mark_error(error_text: str) -> None:
    """Login/refresh/heartbeat failure — flips session_healthy false and
    records the error string. Trimmed to 1000 chars so a runaway stack trace
    doesn't bloat the row."""
    _safe_write(
        """UPDATE coolbet_session_state
           SET last_error = %s,
               last_error_at = NOW(),
               session_healthy = FALSE
           WHERE id = 1""",
        (str(error_text)[:1000],),
    )


def mark_heartbeat(ok: bool, *, note: str | None = None) -> None:
    """Heartbeat ping result — updates last_heartbeat_at and propagates
    healthy state. A failed heartbeat sets last_error to the note so
    /admin pages can show 'heartbeat failed: <reason>' without a join."""
    if ok:
        _safe_write(
            """UPDATE coolbet_session_state
               SET last_heartbeat_at = NOW(),
                   last_heartbeat_ok = TRUE,
                   session_healthy = TRUE,
                   last_error = NULL,
                   last_error_at = NULL
               WHERE id = 1""",
            (),
        )
    else:
        _safe_write(
            """UPDATE coolbet_session_state
               SET last_heartbeat_at = NOW(),
                   last_heartbeat_ok = FALSE,
                   session_healthy = FALSE,
                   last_error = %s,
                   last_error_at = NOW()
               WHERE id = 1""",
            (f"heartbeat: {note}" if note else "heartbeat failed",),
        )


def mark_mac_daemon_tick(result: dict) -> None:
    """Write the Mac daemon's per-tick heartbeat so the Telegram /status
    command can answer 'is the daemon actually running?'. Called at the
    end of every _tick() in coolbet_mac_daemon, success OR failure —
    a "dead" tick (errors=1) still bumps the timestamp so a stale
    `mac_daemon_last_tick_at` always means the process itself is
    dead/asleep/unloaded, not just failing.

    Stored as compact JSON: {qualified, placed, skipped, errors,
    synced_from_coolbet, elapsed_s} — same dict the daemon already
    builds for its log line, so no extra computation."""
    import json as _json
    _safe_write(
        """UPDATE coolbet_session_state
           SET mac_daemon_last_tick_at = NOW(),
               mac_daemon_last_tick_result = %s::jsonb
           WHERE id = 1""",
        (_json.dumps(result, default=str),),
    )


def mark_prekickoff_run(result: dict) -> None:
    """Write the pre-kickoff catch-net's per-fire heartbeat so /admin pages
    and ad-hoc probes can verify Railway's */5 cron actually ran without
    tailing Railway logs. Called from
    `workers.jobs.coolbet_prekickoff_alert.run_prekickoff_alert` at the end
    of every invocation, success OR no-op — a "healthy daemon, no
    candidates" run still bumps the timestamp so a stale
    `prekickoff_last_run_at` means the cron itself isn't firing.

    Stored as compact JSON: {healthy, candidates, sent, skipped_dedup} —
    same dict the job already returns to its caller, so no extra
    computation."""
    import json as _json
    _safe_write(
        """UPDATE coolbet_session_state
           SET prekickoff_last_run_at = NOW(),
               prekickoff_last_run_result = %s::jsonb
           WHERE id = 1""",
        (_json.dumps(result, default=str),),
    )


def mark_cookies_refreshed(count: int) -> None:
    """FS cookie harvest succeeded — tracks last_refresh + count so /status
    can show 'cookies refreshed 3 min ago (5 cookies)'."""
    _safe_write(
        """UPDATE coolbet_session_state
           SET cookies_last_refresh_at = NOW(),
               cookies_count_last = %s
           WHERE id = 1""",
        (count,),
    )


def is_placement_paused() -> tuple[bool, str | None]:
    """Returns (paused, reason). Placer calls this at the start of every
    run; short-circuits the whole placement loop when paused=True.

    Falls open (returns (False, None)) on DB error so an observability
    hiccup doesn't accidentally halt placements — the system is more
    useful running than paralysed by a transient lookup failure. The
    intentional-pause path is the one we care about; transient DB errors
    are caught elsewhere and surface in last_error."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT placement_paused, placement_paused_reason FROM coolbet_session_state WHERE id = 1"
        )
        if not rows:
            return (False, None)
        return (bool(rows[0].get("placement_paused")),
                rows[0].get("placement_paused_reason"))
    except Exception as e:
        log.warning("placement_paused read failed (defaulting to NOT paused): %s", e)
        return (False, None)


def set_placement_paused(paused: bool, *, reason: str | None = None) -> None:
    """Operator kill switch. Telegram /pause sets paused=True with a reason;
    /resume clears both. Plain UPDATE — no validation — operator owns this."""
    _safe_write(
        """UPDATE coolbet_session_state
           SET placement_paused = %s,
               placement_paused_at = CASE WHEN %s THEN NOW() ELSE NULL END,
               placement_paused_reason = %s
           WHERE id = 1""",
        (paused, paused, reason if paused else None),
    )


def get_or_create_device_id() -> str:
    """Return the bot's stable Coolbet deviceId. Auto-generates on first
    call and persists to coolbet_session_state.device_id so subsequent
    calls (and process restarts) read the same UUID.

    Coolbet's /s/bets/bets POST requires a non-empty deviceId. Browsers
    generate one client-side on first visit + store in localStorage —
    FS-routed scrapes don't have access to that localStorage, so we
    manage our own. Coolbet's server validates only that it's a valid
    UUID-shaped string and doesn't care about its origin.

    On DB error this falls through to a per-process random UUID so
    bet placement never blocks on observability — that's the same
    'best-effort' contract as the other state helpers."""
    import uuid as _uuid
    try:
        from workers.api_clients.db import execute_query, execute_write
        rows = execute_query(
            "SELECT device_id FROM coolbet_session_state WHERE id = 1"
        )
        if rows and rows[0].get("device_id"):
            return rows[0]["device_id"]
        # First-time generation. UUID4 matches the format a real browser
        # would write to localStorage on first visit.
        new_id = str(_uuid.uuid4())
        execute_write(
            "UPDATE coolbet_session_state SET device_id = %s WHERE id = 1",
            (new_id,),
        )
        log.info("Generated + persisted new Coolbet deviceId: %s", new_id)
        return new_id
    except Exception as e:
        log.warning("device_id read/persist failed (using ephemeral UUID): %s", e)
        return str(_uuid.uuid4())


def persist_jwt(jwt: str, *, login_session_id: str | None = None,
                 set_by: str | None = None) -> None:
    """Write the live Coolbet JWT to coolbet_session_state.jwt_current so
    any process starting up can bootstrap from DB instead of needing the
    env var to be in sync.

    Called after every successful adopt / api_login / renew_jwt_via_api.
    The JWT in env stays as a fallback for first-deploy bootstrap; DB is
    the freshest canonical source after that.

    Why this matters: Imperva 403's /s/auth/login from Railway IPs but
    accepts it from residential IPs. Without DB-backed JWT, Railway loses
    its session on every restart and can't re-login from there. With it,
    local enrollment writes a fresh JWT to DB → Railway reads from DB on
    next start → keeps it alive via /s/auth/renew-token (which IS
    accepted from Railway IP).

    set_by is a free-text process tag for debugging — e.g. "local_enroll",
    "local_renew", "railway_renew" — surfaces in /status."""
    _safe_write(
        """UPDATE coolbet_session_state
           SET jwt_current = %s,
               jwt_login_session_id = %s,
               jwt_current_set_at = NOW(),
               jwt_set_by = %s
           WHERE id = 1""",
        (jwt, login_session_id, set_by or _default_set_by()),
    )


def _default_set_by() -> str:
    """Best-effort process tag — 'railway_*' on Railway, 'local_*' otherwise.
    Used when a caller doesn't pass an explicit set_by tag."""
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"):
        return "railway"
    return "local"


def read_persisted_jwt() -> tuple[str | None, str | None]:
    """Return (jwt, login_session_id) from coolbet_session_state.

    Returns (None, None) if no JWT has been persisted yet OR on any DB
    error — caller should fall back to env COOLBET_MANUAL_JWT, then to
    API login.

    Called from CoolbetSession.__init__ to bootstrap from the freshest
    available JWT across the local + Railway pair, so neither side has
    to wait for an env-var push."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT jwt_current, jwt_login_session_id FROM coolbet_session_state WHERE id = 1"
        )
        if not rows:
            return (None, None)
        return (rows[0].get("jwt_current"), rows[0].get("jwt_login_session_id"))
    except Exception as e:
        log.warning("read_persisted_jwt failed: %s", e)
        return (None, None)


def read_state() -> dict | None:
    """SELECT the singleton row. Returns None on DB error (best-effort)."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT id, last_login_at, last_login_method, jwt_user_id,
                      jwt_exp_at, last_heartbeat_at, last_heartbeat_ok,
                      session_healthy, last_error, last_error_at,
                      fs_session_name, fs_url,
                      cookies_last_refresh_at, cookies_count_last,
                      updated_at
               FROM coolbet_session_state WHERE id = 1"""
        )
        return rows[0] if rows else None
    except Exception as e:
        log.warning("coolbet_session_state read failed: %s", e)
        return None
