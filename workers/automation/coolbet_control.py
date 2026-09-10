"""
COOLBET-PLACEMENT-READINESS — one surface for "can I place real money now?".

Part of COOLBET-OWN-UNIFIED-FLOW-EPIC (monitor/control surface, sub-item #20).

Why this exists:
The answer to "is real-money placement possible right now?" was scattered
across three tables and two kill switches: the operator pause
(`placement_paused`), the global footprint pause (`daemons_paused`), the
session/JWT health (`session_healthy`, `jwt_exp_at`), the Mac daemon's
liveness (`mac_daemon_last_tick_at`), and which bots are actually toggled ON
(`coolbet_placer_bots.ui_place_enabled`). Nobody surface answered all of it
at once, so "why didn't it place?" meant a manual join across all of them.

`placement_readiness()` aggregates exactly those existing bits of DB state
into one dict with a single boolean `can_place_now` and a list of
human-readable `blockers`. It is a READ-ONLY status surface: it never places,
never toggles a switch, never touches a floor or an execute path. It only
reports what the placer's own gates (`ui_place_enabled_bots`,
`is_placement_paused`, `is_daemons_paused`) will find when they run.

The decision logic is factored into the pure helper `_evaluate_readiness()`
so it can be tested with no DB. `placement_readiness()` just does the reads
and hands the rows to the helper.

Design note — the "can place" conjunction mirrors what the real placer
(`scripts/place_coolbet_ui.py`) and the pause gates
(`coolbet_state.is_placement_paused` / `is_daemons_paused`) already enforce.
This surface does NOT introduce a new gate; if it says BLOCKED, the placer
would place nothing anyway.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Daemon tick freshness threshold. The Mac daemon ticks every ~30 min, so a
# tick older than this means the launchd job is dead/asleep/unloaded and no
# placement is actually happening even if every other gate is green. Matches
# the daily-summary DAEMON_STALE_MIN default.
TICK_FRESH_MIN = int(os.getenv("COOLBET_READINESS_TICK_FRESH_MIN", "60"))


def _as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to tz-aware UTC. Naive datetimes (e.g. from a test
    fixture) are assumed to already be UTC. Returns None unchanged."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_minutes(then: datetime | None, now: datetime) -> float | None:
    """Minutes elapsed from `then` to `now`, or None if `then` is None."""
    then = _as_utc(then)
    if then is None:
        return None
    return (now - then).total_seconds() / 60.0


def _evaluate_readiness(state: dict, bots: list[dict], now: datetime | None = None) -> dict:
    """PURE decision function — no DB. Given the singleton
    `coolbet_session_state` row (`state`) and the `coolbet_placer_bots` rows
    (`bots`, each with `bot_name` + `ui_place_enabled`), decide whether real
    money can be placed right now and enumerate every failing condition.

    `now` defaults to the current UTC time; pass it explicitly in tests for
    determinism. Never raises — this is the tested core, so it must be total.
    """
    now = _as_utc(now) or datetime.now(timezone.utc)
    state = state or {}
    bots = bots or []

    # ── JWT ──────────────────────────────────────────────────────────────
    jwt_exp_at = _as_utc(state.get("jwt_exp_at"))
    jwt_ttl_minutes = _age_minutes(now, jwt_exp_at) if jwt_exp_at else None
    #  _age_minutes(now, exp) = (exp - now) in minutes → positive == time left.
    jwt_valid = jwt_ttl_minutes is not None and jwt_ttl_minutes > 0

    # ── pauses / session ─────────────────────────────────────────────────
    placement_paused = bool(state.get("placement_paused"))
    placement_paused_reason = state.get("placement_paused_reason")
    daemons_paused = bool(state.get("daemons_paused"))
    daemons_paused_reason = state.get("daemons_paused_reason")
    session_healthy = bool(state.get("session_healthy"))
    last_error = state.get("last_error")

    # ── daemon liveness ──────────────────────────────────────────────────
    daemon_last_tick_at = _as_utc(state.get("mac_daemon_last_tick_at"))
    daemon_tick_age_min = _age_minutes(daemon_last_tick_at, now)
    tick_fresh = daemon_tick_age_min is not None and daemon_tick_age_min <= TICK_FRESH_MIN

    # ── enabled bots ─────────────────────────────────────────────────────
    enabled_bots = [b.get("bot_name") for b in bots if b.get("ui_place_enabled")]
    disabled_bots = [b.get("bot_name") for b in bots if not b.get("ui_place_enabled")]

    # ── blockers (one human-readable string per failing condition) ───────
    blockers: list[str] = []
    if placement_paused:
        blockers.append(
            f"placement paused (operator kill switch): {placement_paused_reason or 'no reason given'}"
        )
    if daemons_paused:
        blockers.append(
            f"daemons paused (global footprint kill switch): {daemons_paused_reason or 'no reason given'}"
        )
    if not session_healthy:
        blockers.append(
            f"session not healthy: {last_error or 'no error recorded'}"
        )
    if not jwt_valid:
        if jwt_exp_at is None:
            blockers.append("JWT missing (no jwt_exp_at recorded)")
        else:
            blockers.append(
                f"JWT expired (expired {abs(jwt_ttl_minutes):.0f} min ago)"
            )
    if not enabled_bots:
        blockers.append("no bot toggled ON for placement (coolbet_placer_bots.ui_place_enabled)")
    if not tick_fresh:
        if daemon_tick_age_min is None:
            blockers.append("Mac daemon has never ticked (mac_daemon_last_tick_at is NULL)")
        else:
            blockers.append(
                f"Mac daemon tick stale ({daemon_tick_age_min:.0f} min ago > {TICK_FRESH_MIN} min)"
            )

    can_place_now = not blockers

    return {
        "can_place_now": can_place_now,
        "blockers": blockers,
        "jwt_exp_at": jwt_exp_at,
        "jwt_valid": jwt_valid,
        "jwt_ttl_minutes": round(jwt_ttl_minutes, 1) if jwt_ttl_minutes is not None else None,
        "placement_paused": placement_paused,
        "placement_paused_reason": placement_paused_reason,
        "daemons_paused": daemons_paused,
        "daemons_paused_reason": daemons_paused_reason,
        "session_healthy": session_healthy,
        "last_error": last_error,
        "daemon_last_tick_at": daemon_last_tick_at,
        "daemon_last_tick_result": state.get("mac_daemon_last_tick_result"),
        "daemon_tick_age_min": round(daemon_tick_age_min, 1) if daemon_tick_age_min is not None else None,
        "enabled_bots": enabled_bots,
        "disabled_bots": disabled_bots,
    }


def placement_readiness() -> dict:
    """Read-only aggregate: can real money be placed on Coolbet right now?

    Reads the singleton `coolbet_session_state` row and every
    `coolbet_placer_bots` row, then defers the decision to the pure
    `_evaluate_readiness()`. NEVER raises — on any DB error it returns a dict
    with `can_place_now=False` and a "status read failed" blocker, because a
    surface that can't read the state must report NOT-ready rather than a
    misleading green. (Note this is the opposite fall-open direction from
    `is_placement_paused()`: that gate must not silently HALT placement on a
    read hiccup, whereas this reporter must not silently CLAIM readiness.)
    """
    try:
        from workers.api_clients.db import execute_query

        rows = execute_query(
            """SELECT jwt_exp_at, jwt_current, session_healthy,
                      placement_paused, placement_paused_reason,
                      daemons_paused, daemons_paused_reason,
                      mac_daemon_last_tick_at, mac_daemon_last_tick_result,
                      last_heartbeat_at, last_heartbeat_ok,
                      last_error, last_error_at
                 FROM coolbet_session_state WHERE id = 1"""
        )
        state = dict(rows[0]) if rows else {}

        bots = execute_query(
            "SELECT bot_name, ui_place_enabled, note FROM coolbet_placer_bots ORDER BY bot_name"
        ) or []
        bots = [dict(b) for b in bots]

        return _evaluate_readiness(state, bots)
    except Exception as e:
        log.warning("placement_readiness read failed: %s", e)
        return {
            "can_place_now": False,
            "blockers": [f"status read failed: {e}"],
            "jwt_exp_at": None,
            "jwt_valid": False,
            "jwt_ttl_minutes": None,
            "placement_paused": None,
            "placement_paused_reason": None,
            "daemons_paused": None,
            "daemons_paused_reason": None,
            "session_healthy": None,
            "last_error": None,
            "daemon_last_tick_at": None,
            "daemon_last_tick_result": None,
            "daemon_tick_age_min": None,
            "enabled_bots": [],
            "disabled_bots": [],
        }


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    return _as_utc(dt).strftime("%Y-%m-%d %H:%M UTC")


def format_readiness(r: dict) -> str:
    """Pretty multi-line rendering for the CLI and for the daily summary's
    one-liner source. Kept here so both callers stay in sync."""
    verdict = "READY ✅" if r.get("can_place_now") else "BLOCKED ⛔"
    lines = [f"Coolbet real-money placement: {verdict}"]
    if r.get("blockers"):
        for b in r["blockers"]:
            lines.append(f"  ⛔ {b}")
    lines.append("")
    ttl = r.get("jwt_ttl_minutes")
    lines.append(
        f"  JWT: {'valid' if r.get('jwt_valid') else 'INVALID'}"
        + (f" (TTL {ttl:.0f} min, exp {_fmt_dt(r.get('jwt_exp_at'))})" if ttl is not None else "")
    )
    lines.append(f"  session_healthy: {r.get('session_healthy')}")
    lines.append(
        f"  placement_paused: {r.get('placement_paused')}"
        + (f" — {r.get('placement_paused_reason')}" if r.get('placement_paused') else "")
    )
    lines.append(
        f"  daemons_paused: {r.get('daemons_paused')}"
        + (f" — {r.get('daemons_paused_reason')}" if r.get('daemons_paused') else "")
    )
    age = r.get("daemon_tick_age_min")
    lines.append(
        f"  daemon last tick: {_fmt_dt(r.get('daemon_last_tick_at'))}"
        + (f" ({age:.0f} min ago)" if age is not None else "")
    )
    lines.append(f"  enabled bots:  {', '.join(r.get('enabled_bots') or []) or '(none)'}")
    lines.append(f"  disabled bots: {', '.join(r.get('disabled_bots') or []) or '(none)'}")
    if r.get("last_error"):
        lines.append(f"  last_error: {r.get('last_error')}")
    return "\n".join(lines)


def readiness_summary_line(r: dict) -> str:
    """One compact line for the daily Telegram summary. Lead with the verdict
    glyph, then the blocker reasons (or 'all gates green')."""
    if r.get("can_place_now"):
        return "🟢 PLACEMENT READY ✅ — all gates green"
    reasons = "; ".join(r.get("blockers") or ["unknown"])
    return f"⛔ PLACEMENT BLOCKED — {reasons}"


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Coolbet placement-readiness surface (read-only).")
    p.add_argument("--status", action="store_true",
                   help="Pretty-print whether real money can be placed right now.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    # --status is the only mode; default to it so bare invocation is useful.
    r = placement_readiness()
    print(format_readiness(r))
    return 0 if r.get("can_place_now") else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
