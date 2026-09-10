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
# The UI placer runs hourly and records an attempt per candidate. Older than this
# with no attempt is a warning (no picks, or the job/browser is down) — not a gate.
UI_ATTEMPT_STALE_MIN = int(os.getenv("COOLBET_READINESS_UI_ATTEMPT_STALE_MIN", "90"))


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

    # ── REAL-MONEY gates — exactly what the UI placer (place_coolbet_ui.py
    #    --all-enabled --execute) enforces. It stakes via the operator's LOGGED-IN
    #    CDP-Chrome browser session, NOT the API/FlareSolverr JWT and NOT the paper
    #    mac-daemon. So JWT/session_healthy/paper-daemon-tick are reported below as
    #    non-gating CONTEXT, never as real-money blockers. (READINESS-PATH-FIX
    #    2026-09-10: the first version blocked on those and cried wolf while real
    #    money was being placed fine through the browser.)
    placement_paused = bool(state.get("placement_paused"))
    placement_paused_reason = state.get("placement_paused_reason")
    daemons_paused = bool(state.get("daemons_paused"))
    daemons_paused_reason = state.get("daemons_paused_reason")
    enabled_bots = [b.get("bot_name") for b in bots if b.get("ui_place_enabled")]
    disabled_bots = [b.get("bot_name") for b in bots if not b.get("ui_place_enabled")]

    blockers: list[str] = []
    if placement_paused:
        blockers.append(
            f"placement paused (operator kill switch): {placement_paused_reason or 'no reason given'}"
        )
    if daemons_paused:
        blockers.append(
            f"daemons paused (global footprint kill switch): {daemons_paused_reason or 'no reason given'}"
        )
    if not enabled_bots:
        blockers.append("no bot toggled ON for placement (coolbet_placer_bots.ui_place_enabled)")
    can_place_now = not blockers

    # ── UI-placer liveness (the real path) ───────────────────────────────
    # The hourly UI placer records a coolbet_placement_attempts row per candidate.
    # No recent attempt means EITHER no qualifying picks OR the job/browser session
    # is down — we can't tell those apart from the DB, so it is a WARNING (surfaced),
    # never a hard blocker. A recent real placement proves the browser is logged in.
    last_ui_attempt_at = _as_utc(state.get("last_ui_attempt_at"))
    ui_attempt_age_min = _age_minutes(last_ui_attempt_at, now)
    last_real_placement_at = _as_utc(state.get("last_real_placement_at"))
    warnings: list[str] = []
    if ui_attempt_age_min is None:
        warnings.append(
            "UI placer has no recorded attempts — verify coolbet-ui-placer is loaded and the Coolbet tab is logged in"
        )
    elif ui_attempt_age_min > UI_ATTEMPT_STALE_MIN:
        warnings.append(
            f"UI placer's last attempt was {ui_attempt_age_min:.0f} min ago (>{UI_ATTEMPT_STALE_MIN}) — "
            "likely just no qualifying picks, but check coolbet-ui-placer + the browser session if unexpected"
        )

    # ── odds/API-path CONTEXT — NOT real-money placement gates ───────────
    jwt_exp_at = _as_utc(state.get("jwt_exp_at"))
    jwt_ttl_minutes = _age_minutes(now, jwt_exp_at) if jwt_exp_at else None  # +ve == time left
    jwt_valid = jwt_ttl_minutes is not None and jwt_ttl_minutes > 0
    session_healthy = bool(state.get("session_healthy"))
    daemon_last_tick_at = _as_utc(state.get("mac_daemon_last_tick_at"))
    daemon_tick_age_min = _age_minutes(daemon_last_tick_at, now)

    return {
        "can_place_now": can_place_now,
        "blockers": blockers,
        "warnings": warnings,
        "placement_paused": placement_paused,
        "placement_paused_reason": placement_paused_reason,
        "daemons_paused": daemons_paused,
        "daemons_paused_reason": daemons_paused_reason,
        "enabled_bots": enabled_bots,
        "disabled_bots": disabled_bots,
        "last_ui_attempt_at": last_ui_attempt_at,
        "ui_attempt_age_min": round(ui_attempt_age_min, 1) if ui_attempt_age_min is not None else None,
        "last_real_placement_at": last_real_placement_at,
        # informational only — the odds/API path, which does NOT gate UI placement
        "odds_api_path": {
            "jwt_valid": jwt_valid,
            "jwt_ttl_minutes": round(jwt_ttl_minutes, 1) if jwt_ttl_minutes is not None else None,
            "session_healthy": session_healthy,
            "last_error": state.get("last_error"),
            "paper_daemon_tick_age_min": round(daemon_tick_age_min, 1) if daemon_tick_age_min is not None else None,
        },
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

        # The REAL-money liveness signal: the UI placer's own attempt ledger
        # (coolbet_placement_attempts). last attempt = job/browser ran; last
        # outcome='placed' = real money actually moved (browser was logged in).
        att = execute_query(
            """SELECT max(attempted_at) AS last_ui_attempt_at,
                      max(attempted_at) FILTER (WHERE outcome = 'placed') AS last_real_placement_at
                 FROM coolbet_placement_attempts"""
        )
        if att:
            state["last_ui_attempt_at"] = att[0].get("last_ui_attempt_at")
            state["last_real_placement_at"] = att[0].get("last_real_placement_at")

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
            "warnings": [],
            "placement_paused": None,
            "placement_paused_reason": None,
            "daemons_paused": None,
            "daemons_paused_reason": None,
            "enabled_bots": [],
            "disabled_bots": [],
            "last_ui_attempt_at": None,
            "ui_attempt_age_min": None,
            "last_real_placement_at": None,
            "odds_api_path": {"jwt_valid": False, "jwt_ttl_minutes": None,
                              "session_healthy": None, "last_error": None,
                              "paper_daemon_tick_age_min": None},
        }


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "never"
    return _as_utc(dt).strftime("%Y-%m-%d %H:%M UTC")


def format_readiness(r: dict) -> str:
    """Pretty multi-line rendering for the CLI and for the daily summary's
    one-liner source. Kept here so both callers stay in sync."""
    verdict = "READY ✅" if r.get("can_place_now") else "BLOCKED ⛔"
    lines = [f"Coolbet real-money placement (UI placer): {verdict}"]
    for b in (r.get("blockers") or []):
        lines.append(f"  ⛔ {b}")
    for w in (r.get("warnings") or []):
        lines.append(f"  ⚠️ {w}")
    lines.append("")
    lines.append(
        f"  placement_paused: {r.get('placement_paused')}"
        + (f" — {r.get('placement_paused_reason')}" if r.get('placement_paused') else "")
    )
    lines.append(
        f"  daemons_paused: {r.get('daemons_paused')}"
        + (f" — {r.get('daemons_paused_reason')}" if r.get('daemons_paused') else "")
    )
    lines.append(f"  enabled bots:  {', '.join(r.get('enabled_bots') or []) or '(none)'}")
    lines.append(f"  disabled bots: {', '.join(r.get('disabled_bots') or []) or '(none)'}")
    age = r.get("ui_attempt_age_min")
    lines.append(
        f"  UI placer last attempt: {_fmt_dt(r.get('last_ui_attempt_at'))}"
        + (f" ({age:.0f} min ago)" if age is not None else "")
    )
    lines.append(f"  last REAL placement: {_fmt_dt(r.get('last_real_placement_at'))}")
    # odds/API path — informational, does NOT gate UI placement
    api = r.get("odds_api_path") or {}
    ttl = api.get("jwt_ttl_minutes")
    lines.append("")
    lines.append("  ── odds/API path (NOT a real-money placement gate) ──")
    lines.append(
        f"  API JWT: {'valid' if api.get('jwt_valid') else 'expired/absent'}"
        + (f" (TTL {ttl:.0f} min)" if ttl is not None else "")
        + f" · session_healthy={api.get('session_healthy')}"
    )
    pd = api.get("paper_daemon_tick_age_min")
    lines.append(f"  paper daemon last tick: {f'{pd:.0f} min ago' if pd is not None else 'never'}")
    return "\n".join(lines)


def readiness_summary_line(r: dict) -> str:
    """One compact line for the daily Telegram summary. Lead with the verdict
    glyph, then the blocker reasons (or 'all gates green')."""
    if r.get("can_place_now"):
        warn = f" (⚠️ {'; '.join(r['warnings'])})" if r.get("warnings") else ""
        return f"🟢 PLACEMENT READY ✅ — kill switches clear, {len(r.get('enabled_bots') or [])} bot(s) ON{warn}"
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
