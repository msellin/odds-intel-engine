"""PLACEMENT-GATE (2026-09-15, OWN-ARMED-UNDER-PAUSE) — the ONE gate every
real-money executor calls before it touches a browser or an API.

WHY THIS FILE EXISTS. On 2026-09-15 the OWN audit found real money "paused" in
three places and armed underneath all three:

  * `is_placement_paused()` FAILED OPEN — a DB hiccup read as "not paused" —
    while `ui_place_enabled_bots()` 200 lines away failed CLOSED. Two safety
    reads pointing in opposite directions.
  * The pause was read only INSIDE `stage_bet`, after the stake had been typed
    into the slip; `place_coolbet_ui.main()` never read it at run level, and
    `unibet_placer.place_bet()` never read it at all.
  * `best_price_router.route()` iterated `PLACEABLE_BOTS` (the code whitelist),
    not `effective_allowlist()` (whitelist ∩ DB toggle) — so `ROUTER_ALLOW_REAL=1`
    alone would have staked for both placer bots with the DB toggles OFF.
  * A THIRD executor, `_drain_manual_placement_queue` on the VPS every 10 s →
    `coolbet_placer.place_bet_by_id`, never consulted the pause (paper today
    only because `execute=False` is a literal in the call).
  * Two `--execute` launchd jobs were loaded on the Mac, inert only because an
    env var was unset and an allowlist intersection happened to be empty.

`RELIABILITY_LEDGER` §4 names the pattern: a second code path to the same money
inherits none of the first one's gates. The fix is not a fourth copy of the
checks — it is ONE function that every path must call FIRST, that FAILS CLOSED
on every read, and that a smoke test proves is called before any browser or
API action in every executor (`PLACEMENT-GATE-ALL-EXECUTORS`).

WHAT IT CHECKS, in order — any failure raises `PlacementRefused`, any exception
anywhere ALSO raises `PlacementRefused` (never a boolean that can be ignored):

  1. `placement_paused`      — operator kill switch. Read via
                               `coolbet_state.is_placement_paused()`, which now
                               fails CLOSED (returns paused on any error).
  2. `real_money_armed`      — migration 354. Replaces the `ROUTER_ALLOW_REAL`
                               env var as the "is real money allowed AT ALL"
                               switch, because an env var's ABSENCE on one host
                               is not a pause — it is an accident that has not
                               happened yet. Defaults FALSE; the owner arms it
                               explicitly (Phase 3 of the OWN plan).
  3. allowlist               — `effective_allowlist()` = `PLACEABLE_BOTS` ∩
                               `coolbet_placer_bots.ui_place_enabled`. Fails
                               closed to the empty set.
  4. kickoff cutoff          — `KICKOFF_CUTOFF_MIN` (when a kickoff is given).
  5. daily caps              — `spent_today()` vs `MAX_BETS_PER_DAY` /
                               `MAX_STAKE_PER_DAY` (when `check_caps`).

Per-MATCH exposure (`exposure_conflict`) is deliberately NOT here: it needs the
pass's in-memory `held` list and stays with the callers. Publishing
(`publishing_paused`, migration 353) is deliberately NOT here either — halting
OWN staking is not a decision to mute the customer channel, and the two must
never be re-unified (`RELIABILITY_LEDGER` §9b).

`PLACEABLE_BOTS`, `ui_place_enabled_bots` and `effective_allowlist` LIVE here
now; `scripts/place_coolbet_ui.py` re-exports them so its existing imports and
smoke pins keep working. The whitelist is still a hardcoded set on purpose — a
default is not a guard, and the registry drift test asserts it matches.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


class PlacementRefused(RuntimeError):
    """Raised by the gate. Callers must treat it as "do not place" — it is an
    exception precisely so that it cannot be dropped like a False."""


# ── the code-level hard whitelist ────────────────────────────────────────────
# Bots that may EVER stake real money. Intersected at runtime with the DB toggle
# `coolbet_placer_bots.ui_place_enabled`. Adding a bot here is a code review,
# not a config change; that is the point. value_v1 (line-shop) retired
# 2026-09-08; both remaining bots are toggled OFF in the DB since 2026-09-13/14.
PLACEABLE_BOTS = {"bot_coolbet_ou_model_v1", "bot_coolbet_1x2_model_v1"}


def ui_place_enabled_bots() -> set[str]:
    """Bots flipped ON for real-money placement in `coolbet_placer_bots`.

    FAILS CLOSED: on ANY database error this returns the EMPTY set. This read
    ENABLES money, so "cannot read the toggle" must resolve to "place nothing".
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT bot_name FROM coolbet_placer_bots WHERE ui_place_enabled = true"
        )
        return {r["bot_name"] for r in (rows or [])}
    except Exception as e:  # noqa: BLE001
        log.error("ui_place_enabled_bots: DB read failed — failing CLOSED "
                  "(placing nothing): %s", e)
        return set()


def effective_allowlist() -> set[str]:
    """Bots that may place REAL money right now: code whitelist ∩ DB toggle."""
    return PLACEABLE_BOTS & ui_place_enabled_bots()


# ── the gate ─────────────────────────────────────────────────────────────────
def assert_run_may_place() -> None:
    """Run-level gate: pause + armed. Call once at the top of any run that MAY
    stake. Raises `PlacementRefused` with the reason; never returns False."""
    try:
        # Module-attribute lookups on purpose, so the existing kill-switch smoke
        # tests that monkeypatch `coolbet_state.is_placement_paused` still
        # exercise this path.
        import workers.automation.coolbet_state as cs
        paused, why = cs.is_placement_paused()
    except Exception as e:  # noqa: BLE001
        raise PlacementRefused(f"cannot read placement_paused ({e}) — refusing") from e
    if paused:
        raise PlacementRefused(f"placement_paused: {why or 'no reason given'}")

    try:
        armed, why = cs.is_real_money_armed()
    except Exception as e:  # noqa: BLE001
        raise PlacementRefused(f"cannot read real_money_armed ({e}) — refusing") from e
    if not armed:
        raise PlacementRefused(
            "real_money_armed is FALSE (coolbet_session_state, migration 354) — "
            "real money is not armed; the owner arms it explicitly"
            + (f": {why}" if why else "")
        )


def assert_may_place(
    *,
    bot_name: str | None,
    book: str,
    stake: float,
    kickoff_at: datetime | None = None,
    now: datetime | None = None,
    check_caps: bool = True,
) -> None:
    """Per-pick gate. Everything in `assert_run_may_place()` PLUS allowlist,
    kickoff cutoff and daily caps. Raises `PlacementRefused`; never returns
    False. `book` is recorded in the reason only — the allowlist is per bot.
    """
    assert_run_may_place()

    if not bot_name:
        raise PlacementRefused("pick carries no bot_name — cannot check the allowlist, refusing")
    allowed = effective_allowlist()
    if bot_name not in allowed:
        why = ("not in PLACEABLE_BOTS (code whitelist)" if bot_name not in PLACEABLE_BOTS
               else "ui_place_enabled is OFF in coolbet_placer_bots")
        raise PlacementRefused(f"{bot_name} may not place at {book}: {why}")

    now = now or datetime.now(timezone.utc)
    if kickoff_at is not None:
        # Lazy import: place_coolbet_ui imports this module at load time.
        from scripts.place_coolbet_ui import KICKOFF_CUTOFF_MIN
        ko = kickoff_at if kickoff_at.tzinfo else kickoff_at.replace(tzinfo=timezone.utc)
        if ko - timedelta(minutes=KICKOFF_CUTOFF_MIN) <= now:
            raise PlacementRefused(
                f"inside {KICKOFF_CUTOFF_MIN} min kickoff cutoff (KO {ko:%H:%M} UTC)")

    if check_caps:
        try:
            from scripts.place_coolbet_ui import (
                spent_today, MAX_BETS_PER_DAY, MAX_STAKE_PER_DAY,
            )
            n_today, stake_today = spent_today()
        except Exception as e:  # noqa: BLE001
            raise PlacementRefused(f"cannot read today's spend ({e}) — the daily "
                                   f"cap is the runaway backstop, refusing") from e
        if n_today + 1 > MAX_BETS_PER_DAY:
            raise PlacementRefused(f"daily bet cap reached ({n_today} >= {MAX_BETS_PER_DAY})")
        if stake_today + float(stake) > MAX_STAKE_PER_DAY:
            raise PlacementRefused(
                f"daily stake cap: EUR {stake_today:.2f} + {float(stake):.2f} "
                f"> {MAX_STAKE_PER_DAY:.2f}")


def gate_status() -> dict:
    """Read-only summary for `coolbet_control --status` and the admin safety
    strip. Never raises. Each field is None when unreadable."""
    # GATE-STATUS-READS-THE-SAME-ENV (2026-09-16). `best_price_router` reads
    # ROUTER_ALLOW_REAL inside the scheduler process, where `.env` has been
    # loaded (api_clients.db calls load_dotenv() at import). This function used
    # to read os.getenv BEFORE any of that ran — the dict literal was evaluated
    # before the `import coolbet_state` below — so a bare `--status` process saw
    # an empty env and reported `router_allow_real_env: false` while the live
    # router had it TRUE. A safety strip that reports the opt-in as OFF when it
    # is ON is worse than no strip. Load the same file the router does, first.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001 — reporting must never raise
        pass
    out: dict = {"placement_paused": None, "placement_paused_reason": None,
                 "real_money_armed": None, "allowlist": None,
                 "router_allow_real_env": bool(
                     os.getenv("ROUTER_ALLOW_REAL", "").strip().lower()
                     in ("1", "true", "yes"))}
    try:
        import workers.automation.coolbet_state as cs
        p, r = cs.is_placement_paused()
        out["placement_paused"], out["placement_paused_reason"] = p, r
        a, _ = cs.is_real_money_armed()
        out["real_money_armed"] = a
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200]
    try:
        out["allowlist"] = sorted(effective_allowlist())
    except Exception:  # noqa: BLE001
        out["allowlist"] = None
    return out
