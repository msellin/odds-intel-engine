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


def persist_imperva_cookies(cookies: dict[str, str], *, source: str = "cdp_chrome") -> None:
    """COOLBET-CDP-COOKIE-EXPORT (2026-07-08): stash the fresh Imperva
    cookies harvested from CDP-Chrome so the other Coolbet-HTTP jobs
    (coolbet-odds-snapshot, cs2-coolbet-scanner) can read them in
    COOLBET_NO_FS=true mode instead of hitting FS-Docker Chrome (which
    fails Imperva challenges — different fingerprint).

    Adds `_harvested_at` + `_source` metadata into the JSON payload so
    consumers can decide whether to trust the snapshot. Also stamps
    `imperva_cookies_refreshed_at` at the column level for quick freshness
    filters without JSON parsing.

    No-op silent-fail: cookie harvest is one of many things the daemon
    tick does; DB flakiness must not bring down placement.
    """
    if not cookies:
        return
    import json as _json
    from datetime import datetime, timezone
    payload = dict(cookies)
    payload["_harvested_at"] = datetime.now(timezone.utc).isoformat()
    payload["_source"] = source
    _safe_write(
        """UPDATE coolbet_session_state
              SET imperva_cookies_json = %s::jsonb,
                  imperva_cookies_refreshed_at = NOW()
            WHERE id = 1""",
        (_json.dumps(payload),),
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


def auto_login_recently_attempted(*, min_gap_min: int = 60) -> bool:
    """Returns True if auto_self_heal has tried cdp_auto_login within the
    last `min_gap_min` minutes — rate-limit gate for the logged_out
    auto-recovery branch. Bounds SMS exposure in the unlikely case Coolbet
    rotates device trust and starts requiring SMS again.

    Falls open (returns False) on any DB error so observability hiccups
    don't accidentally prevent recovery."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            """SELECT EXTRACT(EPOCH FROM (NOW() - last_auto_login_attempt_at))
                  AS age_s
                 FROM coolbet_session_state WHERE id = 1"""
        )
        if not rows or rows[0].get("age_s") is None:
            return False
        return float(rows[0]["age_s"]) < (min_gap_min * 60)
    except Exception as e:
        log.warning("auto_login_recently_attempted check failed: %s", e)
        return False


def record_auto_login_attempt(*, outcome: str) -> None:
    """Stamp the timestamp + outcome of an auto_self_heal-initiated
    cdp_auto_login. outcome ∈ {'success', 'sms_timeout', 'error',
    'rate_limited'}. Best-effort."""
    _safe_write(
        """UPDATE coolbet_session_state
              SET last_auto_login_attempt_at = NOW(),
                  last_auto_login_outcome = %s
            WHERE id = 1""",
        (outcome,),
    )


def claim_pending_daemon_command() -> dict | None:
    """Pull the OLDEST pending row from coolbet_daemon_commands (executed_at
    IS NULL) and mark it as in-flight by stamping executed_at = NOW()
    atomically. Returns the row dict or None if nothing pending.

    Atomic via UPDATE ... RETURNING — even if two daemon processes raced,
    only one would win the row. Caller MUST then run the actual command
    and call `finish_daemon_command()` with the result. If the caller
    crashes after claiming but before finishing, the row stays
    `executed_at IS NOT NULL AND result_status IS NULL` — the dashboard
    can surface that as 'in-flight, may be stale' for the operator."""
    try:
        # MUST use execute_write_returning (not execute_query) — UPDATE
        # ... RETURNING is a WRITE that needs an explicit commit, and
        # execute_query never commits. Without this the executed_at
        # stamp silently rolls back when the connection returns to the
        # pool, leaving rows that look "pending" forever even though
        # the daemon has already processed them.
        from workers.api_clients.db import execute_write_returning
        rows = execute_write_returning(
            """UPDATE coolbet_daemon_commands
                  SET executed_at = NOW()
                WHERE id = (
                    SELECT id FROM coolbet_daemon_commands
                     WHERE executed_at IS NULL
                     ORDER BY requested_at ASC
                     LIMIT 1
                     FOR UPDATE SKIP LOCKED
                )
            RETURNING id, command_type, requested_at, requested_by"""
        )
        return dict(rows[0]) if rows else None
    except Exception as e:
        log.warning("claim_pending_daemon_command failed: %s", e)
        return None


def finish_daemon_command(*, command_id, status: str, message: str,
                            actions: list | None = None) -> None:
    """Complete the lifecycle by writing result_status + result_message +
    result_actions. status ∈ {'recovered', 'stalled', 'error'}. Idempotent
    enough that re-calling won't crash, but the first call wins (UPDATE
    is conditioned on result_status IS NULL)."""
    import json as _json
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            """UPDATE coolbet_daemon_commands
                  SET result_status  = %s,
                      result_message = %s,
                      result_actions = %s::jsonb
                WHERE id = %s
                  AND result_status IS NULL""",
            (status, message, _json.dumps(actions or [], default=str), command_id),
        )
    except Exception as e:
        log.warning("finish_daemon_command failed: %s", e)


def log_heal_attempt(*, triggered_by: str, result: dict,
                       duration_s: float) -> None:
    """Append a row to coolbet_heal_log for every auto_self_heal invocation.
    Best-effort — observability must not break the heal path itself.

    triggered_by: 'auto' (from daemon consecutive-error path), 'operator_tg'
    (Telegram inline button), 'operator_cli' (--full-heal command), 'pipeline'
    (VPS-side helper, future)."""
    import json as _json
    try:
        from workers.api_clients.db import execute_write
        execute_write(
            """INSERT INTO coolbet_heal_log
                   (triggered_by, state_before, state_after, recovered,
                    actions, message, duration_s)
               VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)""",
            (
                triggered_by,
                result.get("state_before"),
                result.get("state_after"),
                bool(result.get("recovered")),
                _json.dumps(result.get("actions") or [], default=str),
                result.get("message"),
                round(float(duration_s), 2),
            ),
        )
    except Exception as e:
        log.debug("log_heal_attempt failed (non-fatal): %s", e)


def mark_prekickoff_run(result: dict) -> None:
    """Write the pre-kickoff catch-net's per-fire heartbeat so /admin pages
    and ad-hoc probes can verify the VPS's */5 cron actually ran without
    tailing the VPS logs. Called from
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


def mark_placer_heartbeat(placer: str, *, execute_requested: bool,
                          execute_effective: bool, refused_reason: str | None = None,
                          result: dict | None = None) -> None:
    """#139 phase A (owner decision 7): each real-money placer run on the Mac
    stamps `placer_heartbeats`, so /admin/bots can show the executor as Alive /
    Stale / Not reported next to the money switches — the web cannot see
    launchd, and it must never show "on" for a process that is not running.
    Best-effort: a failed heartbeat never stops a run."""
    import json as _json
    import socket as _socket
    _safe_write(
        """INSERT INTO placer_heartbeats
               (placer, host, last_seen_at, execute_requested, execute_effective,
                refused_reason, result)
           VALUES (%s, %s, NOW(), %s, %s, %s, %s::jsonb)
           ON CONFLICT (placer) DO UPDATE SET
               host = EXCLUDED.host, last_seen_at = EXCLUDED.last_seen_at,
               execute_requested = EXCLUDED.execute_requested,
               execute_effective = EXCLUDED.execute_effective,
               refused_reason = EXCLUDED.refused_reason, result = EXCLUDED.result""",
        (placer, _socket.gethostname()[:100], bool(execute_requested), bool(execute_effective),
         (refused_reason or None) and str(refused_reason)[:500],
         _json.dumps(result or {}, default=str)),
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
    """Returns (paused, reason). The operator KILL switch for real money.

    FAILS CLOSED (2026-09-15, OWN-ARMED-UNDER-PAUSE). Until today this fell
    OPEN — a DB error read as "not paused" — on the argument that a transient
    lookup failure should not halt placements. That argument was wrong for a
    kill switch: this read is the ONLY thing between a scheduled `--execute`
    job and the account, and `ui_place_enabled_bots()` 200 lines away already
    failed CLOSED for the same money. Two safety reads pointing in opposite
    directions is `RELIABILITY_LEDGER` material. A DB blip now resolves to
    "paused" with the reason in the second element, so callers that only log
    (health_alerts, the daily summary) can still tell the two apart.

    A MISSING row is also paused: no state is not a licence to stake."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT placement_paused, placement_paused_reason FROM coolbet_session_state WHERE id = 1"
        )
        if not rows:
            return (True, "coolbet_session_state row missing — failing CLOSED")
        return (bool(rows[0].get("placement_paused")),
                rows[0].get("placement_paused_reason"))
    except Exception as e:
        log.error("placement_paused read failed — failing CLOSED (treated as PAUSED): %s", e)
        return (True, f"unreadable ({type(e).__name__}) — failing CLOSED")


def is_real_money_armed() -> tuple[bool, str | None]:
    """Returns (armed, reason). The ARMING switch (migration 354). FALSE means
    no executor may stake regardless of bot toggles or env vars. FAILS CLOSED:
    any error, or a missing row, or a missing column (migration not yet
    applied) reads as NOT armed. Only the owner sets it, via
    `set_real_money_armed`, with a reason."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT real_money_armed, real_money_armed_reason FROM coolbet_session_state WHERE id = 1"
        )
        if not rows:
            return (False, "coolbet_session_state row missing")
        return (bool(rows[0].get("real_money_armed")),
                rows[0].get("real_money_armed_reason"))
    except Exception as e:
        log.error("real_money_armed read failed — failing CLOSED (treated as NOT armed): %s", e)
        return (False, f"unreadable ({type(e).__name__})")


# ── audited fleet-switch writes (#139 phase A, migration 413) ─────────────────
# Every setter below writes the change AND its `control_changes` audit row in ONE
# statement, so the page's Activity log sees engine writes (the daemon
# self-pause and auto-clear, the ops CLI) as well as page clicks. Without them
# the log would show page clicks only and lie by omission — the 343 note-vs-flag
# disagreement came from exactly that gap.
#
# If the audited write fails (e.g. control_changes not deployed yet), a STOP
# direction (pause, disarm) falls back to the plain UPDATE so an emergency stop
# never depends on the audit table; a START direction (resume, arm) does not
# fall back and is logged as failed. This keeps each setter's old contract:
# best-effort, never raises (except arming without a reason).
# switch column -> (its _at column, its _reason column), spelled out so a grep for a column
# finds its writer.
_FLEET_SWITCHES = {
    "placement_paused": ("placement_paused_at", "placement_paused_reason"),
    "publishing_paused": ("publishing_paused_at", "publishing_paused_reason"),
    "daemons_paused": ("daemons_paused_at", "daemons_paused_reason"),
    "real_money_armed": ("real_money_armed_at", "real_money_armed_reason"),
}


def _caller_actor(depth: int = 3) -> str:
    """'engine:<module>' of whoever called the public setter."""
    import sys
    try:
        return f"engine:{sys._getframe(depth).f_globals.get('__name__', '?')}"
    except Exception:  # noqa: BLE001
        return "engine:?"


def _resume_placement_via_fn(actor: str, reason: str | None) -> None:
    """Resume placement from the ENGINE. Since migration 413 a table trigger refuses any
    placement_paused true->false that does not come through `admin_set_control`, and the
    function lets source='engine' clear only the daemon's OWN self-pause (exact marker, never a
    strategic or operator pause). Everything else is resumed on /admin/bots. Never raises."""
    try:
        from workers.api_clients.db import execute_write_returning
        rows = execute_write_returning(
            "SELECT admin_set_control('placement_paused', NULL, 'false'::jsonb, %s, NULL, %s, "
            "NULL, 'engine', NULL, NULL) AS r",
            ((reason or None) and str(reason)[:500], str(actor)[:200]),
        )
        r = (rows[0]["r"] if rows else None) or {}
        if r.get("outcome") not in ("applied", "noop"):
            log.error("placement resume refused: %s", r.get("refusal") or r)
    except Exception as e:  # noqa: BLE001
        log.error("placement resume failed — NOT applied (a start never skips its audit): %s", e)


def _set_fleet_switch(control: str, value: bool, stored_reason: str | None, *,
                      audit_reason: str | None, actor: str | None, source: str,
                      stop_direction: bool) -> None:
    at_col, reason_col = _FLEET_SWITCHES[control]
    actor = actor or _caller_actor()
    # The two money STARTs are guarded at the table (migration 413): arming only through
    # admin_arm_real_money (/admin/bots, owner), resuming only through admin_set_control.
    if control == "real_money_armed" and value:
        log.error("real money is armed only on /admin/bots (owner, typed ARM REAL MONEY + reason) "
                  "— refusing the %s arm from %s", source, actor)
        return
    if control == "placement_paused" and not value:
        _resume_placement_via_fn(actor, audit_reason)
        return
    # A pause over an existing pause is a no-op: it must never rewrite the standing reason
    # (a strategic stop re-labelled as a daemon self-pause gets auto-cleared). The DB trigger
    # (migration 413) enforces the same for every writer; this just avoids the write.
    already = f" AND NOT coalesce({control}, false)" if value and control != "real_money_armed" else ""
    plain = (f"UPDATE coolbet_session_state SET {control} = %s, "
             f"{at_col} = CASE WHEN %s THEN NOW() ELSE NULL END, "
             f"{reason_col} = %s WHERE id = 1{already}")
    audited = f"""
        WITH old AS (SELECT {control} AS v FROM coolbet_session_state WHERE id = 1),
        upd AS ({plain} RETURNING {control} AS v)
        INSERT INTO control_changes (actor, source, control, old_value, new_value, reason, outcome)
        SELECT %s, %s, %s, to_jsonb(old.v), to_jsonb(upd.v), %s,
               CASE WHEN old.v IS NOT DISTINCT FROM upd.v THEN 'noop' ELSE 'applied' END
          FROM old, upd"""
    try:
        from workers.api_clients.db import execute_write
        execute_write(audited, (value, value, stored_reason, str(actor)[:200], source, control,
                                (audit_reason or None) and str(audit_reason)[:500]))
        return
    except Exception as e:  # noqa: BLE001
        if not stop_direction:
            log.error("%s=%s audited write failed — NOT applied (a start direction never "
                      "skips its audit row): %s", control, value, e)
            return
        log.error("%s=%s audited write failed — applying the STOP without its audit row: %s",
                  control, value, e)
    _safe_write(plain, (value, value, stored_reason))


def set_real_money_armed(armed: bool, *, reason: str | None = None,
                         actor: str | None = None, source: str = "cli") -> None:
    """Arming switch. Since #139 phase A (migration 413) real money is ARMED only on
    /admin/bots through `admin_arm_real_money` (owner, typed ARM REAL MONEY + reason);
    a table trigger refuses any other false->true, so `armed=True` here is refused
    and logged. DISARMING (armed=False) works from anywhere and is audited."""
    if armed and not (reason or "").strip():
        raise ValueError("arming real money requires a reason")
    _set_fleet_switch("real_money_armed", armed, reason, audit_reason=reason,
                      actor=actor, source=source, stop_direction=not armed)


# SIGNAL-PAUSE-DECOUPLE (2026-08-27): the marker the daemon stamps into
# placement_paused_reason when it pauses itself. Defined once, here, because
# three call sites now branch on it (daemon self-pause write, daemon
# auto-clear, betting_pipeline signal gate) and a drifting substring would
# silently turn a self-pause into an operator pause — which is exactly the
# failure that muted Telegram for 4 days.
DAEMON_SELF_PAUSE_MARKER = "daemon self-pause"


def is_daemon_self_pause(reason: str | None) -> bool:
    """True when `reason` is a pause the daemon set on itself (as opposed to
    an operator /pause). Daemon self-pauses stop placement but must NOT stop
    signaling, and may be auto-cleared; operator pauses are cleared only by
    the operator.

    NOTE (2026-09-15): this distinction no longer decides whether picks are
    PUBLISHED — `is_publishing_paused()` does, and neither kind of placement
    pause touches it. It still decides auto-clear eligibility.
    """
    # PREFIX match on "<marker>:" — exactly how coolbet_mac_daemon writes it
    # (f"{DAEMON_SELF_PAUSE_MARKER}: {n} consecutive errors over {m}m"). A substring
    # match auto-resumed reasons like "this is NOT a daemon self-pause" (#139 review);
    # the SQL function admin_set_control uses the same prefix rule (migration 413).
    return bool(reason) and reason.startswith(f"{DAEMON_SELF_PAUSE_MARKER}:")


def is_publishing_paused() -> tuple[bool, str | None]:
    """Returns (paused, reason) for the PUBLIC @oddsintelpicks channel.

    PICKS-PUBLISH-DECOUPLED-FROM-OWN-PAUSE (2026-09-15). Publishing picks to
    customers is a 👥 PICKS decision; halting real-money placement is a 🤖 OWN
    one. They shared `placement_paused` until today, which meant the OWN-path
    verdict (docs/OWN_PATH_VERDICT_2026_09_14.md) took the customer Telegram
    feed offline as a side effect nobody chose and nothing reported.

    Falls open (NOT paused) on DB error — deliberately the OPPOSITE of
    `is_placement_paused`, which fails CLOSED since 2026-09-15: the
    risk profile is the same in reverse — a transient lookup failure should not
    silently mute the customer feed, which is the failure mode we are fixing.
    Publishing makes no Coolbet API call and writes no `real_bets` row, so
    falling open cannot stake money.
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT publishing_paused, publishing_paused_reason "
            "FROM coolbet_session_state WHERE id = 1"
        )
        if not rows:
            return (False, None)
        return (bool(rows[0].get("publishing_paused")),
                rows[0].get("publishing_paused_reason"))
    except Exception as e:
        log.warning("publishing_paused read failed (defaulting to NOT paused): %s", e)
        return (False, None)


def set_publishing_paused(paused: bool, *, reason: str | None = None,
                          actor: str | None = None, source: str = "engine") -> None:
    """Operator kill switch for the customer picks channel. Telegram
    /pausepicks sets it; /resumepicks clears it; /admin/bots has its own switch.
    Deliberately separate from `set_placement_paused` — see
    `is_publishing_paused`. Audited (`control_changes`). Neither direction moves
    money, so both fall back to the plain write if the audit table is missing."""
    _set_fleet_switch("publishing_paused", paused, reason if paused else None,
                      audit_reason=reason, actor=actor, source=source, stop_direction=True)


def set_placement_paused(paused: bool, *, reason: str | None = None,
                         actor: str | None = None, source: str = "engine") -> None:
    """Operator kill switch. Engine callers: the daemon self-pause (True) and its
    auto-clear (False). Operators pause from /admin/bots or Telegram /pause and
    resume ONLY from /admin/bots (owner decision 3, 2026-09-24). Audited
    (`control_changes`); a pause falls back to the plain write if the audit
    table is unavailable. A resume goes through `admin_set_control`, which lets
    the engine clear ONLY its own daemon self-pause — never an operator or
    strategic (OWN-PATH-VERDICT) pause."""
    _set_fleet_switch("placement_paused", paused, reason if paused else None,
                      audit_reason=reason, actor=actor, source=source, stop_direction=paused)


def is_daemons_paused() -> tuple[bool, str | None]:
    """Returns (paused, reason) for the GLOBAL Coolbet footprint pause
    (COOLBET-DAEMONS-PAUSE). Every Coolbet footprint daemon — odds-snapshot
    (coolbet_explorer --board), feed-watchdog, and the mac-daemon tick — calls
    this at the start of its run and skips ALL Coolbet HTTP work when True, so
    the operator can drop the request footprint from the /admin/shadow-bots
    dashboard when Imperva escalates (the "STAY COOL" wall).

    Distinct from is_placement_paused (that only stops real-money PLACEMENT;
    this stops the footprint that provokes Imperva). FAILS CLOSED since
    2026-09-15 (OWN-ARMED-UNDER-PAUSE): a DB error reads as PAUSED. The cost is
    one skipped sweep on a DB blip — and a sweep that cannot reach the DB could
    not have written its rows anyway. The old fall-open argument ("must not
    silently freeze collection") traded a harmless skip for a footprint that
    kept running through the exact outages the pause exists for."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT daemons_paused, daemons_paused_reason FROM coolbet_session_state WHERE id = 1"
        )
        if not rows:
            return (True, "coolbet_session_state row missing — failing CLOSED")
        return (bool(rows[0].get("daemons_paused")),
                rows[0].get("daemons_paused_reason"))
    except Exception as e:
        log.error("daemons_paused read failed — failing CLOSED (treated as PAUSED): %s", e)
        return (True, f"unreadable ({type(e).__name__}) — failing CLOSED")


def set_daemons_paused(paused: bool, *, reason: str | None = None,
                       actor: str | None = None, source: str = "engine") -> None:
    """Set the global Coolbet footprint pause. Written by the dashboard, the
    ops CLI and /admin/bots. Audited (`control_changes`); not a money switch, so
    both directions fall back to the plain write if the audit table is missing."""
    _set_fleet_switch("daemons_paused", paused, reason if paused else None,
                      audit_reason=reason, actor=actor, source=source, stop_direction=True)


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

    Why this matters: Imperva 403's /s/auth/login from the VPS IPs but
    accepts it from residential IPs. Without DB-backed JWT, the VPS loses
    its session on every restart and can't re-login from there. With it,
    local enrollment writes a fresh JWT to DB → the VPS reads from DB on
    next start → keeps it alive via /s/auth/renew-token (which IS
    accepted from the VPS IP).

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
    """Best-effort process tag — 'railway_*' on the VPS, 'local_*' otherwise.
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
    available JWT across the local + the VPS pair, so neither side has
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
