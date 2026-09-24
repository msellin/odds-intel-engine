"""
COOLBET-PLACEMENT-READINESS — one surface for "can I place real money now?".

Part of COOLBET-OWN-UNIFIED-FLOW-EPIC (monitor/control surface, sub-item #20).

Why this exists:
The answer to "is real-money placement possible right now?" was scattered
across three tables and several switches: the operator kill switch
(`placement_paused`), the arming switch (`real_money_armed`), the footprint pause
(`daemons_paused` — context only since 2026-09-24: it stops sweeping, not bets), the
session/JWT health (`session_healthy`, `jwt_exp_at`), the Mac daemon's
liveness (`mac_daemon_last_tick_at`), and which bots are actually toggled ON
(`coolbet_placer_bots.ui_place_enabled`). Nobody surface answered all of it
at once, so "why didn't it place?" meant a manual join across all of them.

`placement_readiness()` aggregates exactly those existing bits of DB state
into one dict with a single boolean `can_place_now` and a list of
human-readable `blockers`. It is a READ-ONLY status surface: it never places,
never toggles a switch, never touches a floor or an execute path. It only
reports what the placer's own gates (`ui_place_enabled_bots`,
`is_placement_paused`, `is_real_money_armed`) will find when they run. The
footprint pause (`daemons_paused`) is reported as context only: it stops odds
sweeping, not the real-money placers (#139, owner decision 2026-09-24).

The decision logic is factored into the pure helper `_evaluate_readiness()`
so it can be tested with no DB. `placement_readiness()` just does the reads
and hands the rows to the helper.

Design note — the "can place" conjunction mirrors what the real placer
(`scripts/place_coolbet_ui.py`) and the run-level gate
(`placement_gate.assert_run_may_place`: placement_paused + real_money_armed) already enforce.
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

    # PLACEMENT-GATE (2026-09-15): the ARMING switch (migration 354). Missing
    # column (migration not yet applied) or NULL reads as NOT armed.
    real_money_armed = bool(state.get("real_money_armed"))
    real_money_armed_reason = state.get("real_money_armed_reason")

    blockers: list[str] = []
    if not real_money_armed:
        blockers.append(
            "real money NOT ARMED (coolbet_session_state.real_money_armed, migration 354)"
            + (f": {real_money_armed_reason}" if real_money_armed_reason else "")
        )
    if placement_paused:
        blockers.append(
            f"placement paused (operator kill switch): {placement_paused_reason or 'no reason given'}"
        )
    # FOOTPRINT-NOT-A-MONEY-GATE (#139, owner decision 2026-09-24): `daemons_paused`
    # stops the Coolbet odds SWEEPS, the feed watchdog and the paper Mac daemon. It
    # does NOT stop the real-money placers — `placement_gate.assert_run_may_place()`
    # reads only placement_paused + real_money_armed, and the owner chose to keep it
    # that way ("only sweeping stops"). It used to be listed as a blocker here, so the
    # daily summary said BLOCKED on a pause that would not have stopped a real bet.
    # It is context now (a warning, added below); the kill switch stops real bets.
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
    if daemons_paused:
        warnings.append(
            f"Coolbet sweeping paused (footprint): {daemons_paused_reason or 'no reason given'} — "
            "odds collection only; this does NOT stop real bets (use placement_paused)"
        )
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
        "real_money_armed": real_money_armed,
        "real_money_armed_reason": real_money_armed_reason,
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
        # Arming switch (migration 354) via the FAIL-CLOSED helper, so a not-yet-
        # applied migration reads as "not armed" instead of breaking the whole
        # status read.
        from workers.automation.coolbet_state import is_real_money_armed
        armed, armed_reason = is_real_money_armed()
        state["real_money_armed"], state["real_money_armed_reason"] = armed, armed_reason

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


def host_executors() -> dict:
    """PLACEMENT-GATE 0.E (2026-09-15): what THIS host could execute, read from
    the OS rather than the DB. `_evaluate_readiness` sees only the DB row; the
    2026-09-14 pause left two `--execute` launchd jobs loaded and the router's
    env opt-in set in `.env`. Never raises; every field None when unreadable.

    Returns loaded launchd agents whose program arguments contain `--execute`,
    whether `ROUTER_ALLOW_REAL` is set in the environment this process sees
    (which is what the launchd jobs see too, via load_dotenv), and the current
    effective allowlist.
    """
    import os
    import subprocess
    out: dict = {"execute_agents_loaded": None, "router_allow_real_env": None,
                 "allowlist": None}
    try:
        uid = os.getuid()
        listing = subprocess.run(["launchctl", "list"], capture_output=True, text=True,
                                 timeout=10).stdout
        loaded = [ln.split()[-1] for ln in listing.splitlines()
                  if "com.oddsintel." in ln]
        armed = []
        for label in loaded:
            try:
                pr = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                                    capture_output=True, text=True, timeout=10).stdout
            except Exception:  # noqa: BLE001
                continue
            if "--execute" in pr:
                armed.append(label)
        out["execute_agents_loaded"] = sorted(armed)
    except Exception as e:  # noqa: BLE001
        out["launchd_error"] = str(e)[:120]
    try:
        out["router_allow_real_env"] = bool(
            os.getenv("ROUTER_ALLOW_REAL", "").lower() in ("1", "true", "yes"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from workers.automation.placement_gate import effective_allowlist
        out["allowlist"] = sorted(effective_allowlist())
    except Exception:  # noqa: BLE001
        pass
    return out


def can_stake(readiness: dict, host: dict) -> tuple[bool, list[str]]:
    """The one answer the owner asked for: could ANY executor on this host stake
    right now? TRUE requires the DB gate open (not paused AND armed AND a bot
    toggled ON) AND at least one loaded `--execute` agent or the router env
    opt-in. Returns (can_stake, reasons_it_cannot). Pure; never raises."""
    why: list[str] = []
    if readiness.get("placement_paused") in (True, None):
        why.append("placement_paused" if readiness.get("placement_paused") else
                   "placement_paused unreadable (fails closed)")
    if not readiness.get("real_money_armed"):
        why.append("real_money_armed is FALSE")
    if not (readiness.get("enabled_bots") or []):
        why.append("no bot toggled ON")
    agents = host.get("execute_agents_loaded") or []
    if not agents and not host.get("router_allow_real_env"):
        why.append("no --execute agent loaded and ROUTER_ALLOW_REAL not set")
    return (not why, why)


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
        f"  real_money_armed: {r.get('real_money_armed')}"
        + (f" — {r.get('real_money_armed_reason')}" if r.get('real_money_armed_reason') else "")
    )
    lines.append(
        f"  daemons_paused (sweeping only, not a money gate): {r.get('daemons_paused')}"
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
    # The footprint pause is a warning, not a blocker (#139, 2026-09-24) — still show it here, or a
    # paused Coolbet sweep vanishes from the daily summary while placement is disarmed.
    warn = f" (⚠️ {'; '.join(r['warnings'])})" if r.get("warnings") else ""
    return f"⛔ PLACEMENT BLOCKED — {reasons}{warn}"


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
    h = host_executors()
    print("")
    print("  ── THIS HOST (PLACEMENT-GATE 0.E) ──")
    print(f"  --execute launchd agents loaded: "
          f"{', '.join(h.get('execute_agents_loaded') or []) or '(none)'}")
    print(f"  ROUTER_ALLOW_REAL in env: {h.get('router_allow_real_env')}")
    print(f"  effective allowlist: {', '.join(h.get('allowlist') or []) or '(empty)'}")
    ok, why = can_stake(r, h)
    print("")
    print(f"CAN_STAKE: {'yes' if ok else 'no'}"
          + ("" if ok else f"  ({'; '.join(why)})"))
    return 0 if r.get("can_place_now") else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
