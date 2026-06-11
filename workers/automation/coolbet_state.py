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
