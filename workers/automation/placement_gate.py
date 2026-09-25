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
  3. allowlist               — `effective_allowlist()` = bots with a placement
                               path (`placement_path_bots()`, code rule over
                               bot_config) ∩ the DB eligibility list
                               (`coolbet_placer_bots`). Fails closed to ∅.
  4. kickoff cutoff          — `KICKOFF_CUTOFF_MIN` (when a kickoff is given).
  5. daily caps              — `spent_today()` vs `MAX_BETS_PER_DAY` /
                               `MAX_STAKE_PER_DAY` (when `check_caps`).

Per-MATCH exposure (`exposure_conflict`) is deliberately NOT here: it needs the
pass's in-memory `held` list and stays with the callers. Publishing
(`publishing_paused`, migration 353) is deliberately NOT here either — halting
OWN staking is not a decision to mute the customer channel, and the two must
never be re-unified (`RELIABILITY_LEDGER` §9b).

`placement_path_bots`, `ui_place_enabled_bots` and `effective_allowlist` LIVE
here; `scripts/place_coolbet_ui.py` re-exports them.

WHO MAY BET MOVED TO THE DB (#139 phase A, owner decision 4, migration 413).
The hand-listed `PLACEABLE_BOTS = {two names}` is gone. The owner selects which
bots actively bet from /admin/bots: the eligibility list is the rows of
`coolbet_placer_bots` (seeded OFF by migration for every capable bot, updated
only through the audited `admin_set_control`), the per-bot switch is
`ui_place_enabled`. What stays in CODE is the RULE for which bots have a
placement path at all (`placement_path_reason`, below) — so a DB row can never
"enable" a bot into nothing. Every read is fresh and fails CLOSED (empty set) on
any error, including the column not existing yet. A row with `locked_reason`
set, or a retired bot, is never eligible.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


class PlacementRefused(RuntimeError):
    """Raised by the gate. Callers must treat it as "do not place" — it is an
    exception precisely so that it cannot be dropped like a False."""


# ── the code-level rule: which bots HAVE a placement path ───────────────────
# Owner decision 4 (2026-09-24), as corrected the same day: the capable set is
# NOT a hand-listed set of names. It is what the placers can technically place:
#
#   * the picks live in `shadow_bets` — `scripts/place_coolbet_ui.load_picks` and
#     `best_price_router` read `shadow_bets_unique` by bot name, for ANY bot;
#   * they are PRE-MATCH — both placers refuse kicked-off matches, so the in-play
#     family is never capable;
#   * they are priced at a book a placer supports — Coolbet (the UI placer and the
#     router) or Unibet-Site (the router's Unibet arm);
#   * the bot is not a pre-registered publish-only test or its control.
#
# `simulated_bets` bots are NOT capable: the only placer that reads that ledger is
# the old API placer (`coolbet_placer.place_all_bets`), which is not a supported
# real-money executor any more — its launchd daemon is retired, the VPS drain is
# pinned paper (`MANUAL_PLACE_EXECUTE = False`) and only a hand-run CLI remains.
#
# The rule lives HERE (code, reviewed); the facts it is applied to (family,
# ledger, books) come from `bot_config`, which `scripts/export_bot_config.py`
# builds by importing the running code's own objects. Which capable bots may
# actually bet is the DB eligibility list (`coolbet_placer_bots`, switched from
# /admin/bots, audited), so a DB write alone can still never make a bot with no
# placement path stake.
PLACER_BOOKS = ("Coolbet", "Unibet-Site")   # == best_price_router.PLACEABLE_BOOKS (smoke-pinned)
PLACEMENT_LEDGER = "shadow_bets"
NO_PLACEMENT_FAMILIES = {
    "inplay": "in-play — the placers are pre-match only",
    "forward_test": "publish-only pre-registered test — it is never staked",
    "control": "publish-only pre-registered control — it is never staked",
}
# bot_config older than this cannot vouch for a placement path (daily export).
CONFIG_MAX_AGE_H = 36


def placement_path_reason(family: str | None, ledger: str | None,
                          books: list[str] | tuple[str, ...] | None) -> str | None:
    """None when a bot with this config HAS a placement path; otherwise the
    reason it does not, in words the page shows next to the switch."""
    if family in NO_PLACEMENT_FAMILIES:
        return NO_PLACEMENT_FAMILIES[family]
    if ledger == "simulated_bets":
        return ("no real-money placer reads simulated_bets (the old API placer is "
                "not a supported executor)")
    if ledger != PLACEMENT_LEDGER:
        return "no placer reads this ledger" + (f" ({ledger})" if ledger else "")
    if not set(books or ()) & set(PLACER_BOOKS):
        return "priced only at books no placer supports (placers: Coolbet, Unibet-Site)"
    return None


def placement_path_bots() -> set[str]:
    """ACTIVE bots whose exported config has a placement path. Read fresh.

    FAILS CLOSED: any DB error, or an export older than CONFIG_MAX_AGE_H, reads
    as the EMPTY set — this read ENABLES money."""
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(
            "SELECT c.bot_name, c.family, c.ledger, c.books FROM bot_config c "
            "JOIN bots b ON b.name = c.bot_name "
            "WHERE b.is_active AND b.retired_at IS NULL "
            f"AND c.exported_at > NOW() - INTERVAL '{int(CONFIG_MAX_AGE_H)} hours'"
        )
        return {r["bot_name"] for r in (rows or [])
                if placement_path_reason(r.get("family"), r.get("ledger"), r.get("books")) is None}
    except Exception as e:  # noqa: BLE001
        log.error("placement_path_bots: DB read failed — failing CLOSED "
                  "(placing nothing): %s", e)
        return set()


# The DB eligibility read. Every condition narrows; none widens:
#   ui_place_enabled          the per-bot switch (audited, /admin/bots)
#   locked_reason IS NULL     pinned OFF by evidence (migration 413)
#   b.is_active / retired_at  a retired bot never stakes, even if its switch was left on
ELIGIBLE_SQL = (
    "SELECT p.bot_name FROM coolbet_placer_bots p "
    "JOIN bots b ON b.name = p.bot_name "
    "WHERE p.ui_place_enabled = true AND p.locked_reason IS NULL "
    "AND b.is_active AND b.retired_at IS NULL"
)


def ui_place_enabled_bots() -> set[str]:
    """Bots selected to bet real money: the DB eligibility list
    (`coolbet_placer_bots`, owner decision 4) — switched ON, not locked, not
    retired.

    FAILS CLOSED: on ANY database error this returns the EMPTY set. This read
    ENABLES money, so "cannot read the toggle" must resolve to "place nothing".
    """
    try:
        from workers.api_clients.db import execute_query
        rows = execute_query(ELIGIBLE_SQL)
        return {r["bot_name"] for r in (rows or [])}
    except Exception as e:  # noqa: BLE001
        log.error("ui_place_enabled_bots: DB read failed — failing CLOSED "
                  "(placing nothing): %s", e)
        return set()


def effective_allowlist() -> set[str]:
    """Bots that may place REAL money right now: has a placement path (code rule
    over the exported config) ∩ the DB eligibility list. Empty on any error."""
    return placement_path_bots() & ui_place_enabled_bots()


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

    # #162 W0.2 (migration 436): the per-bot floors and the cross-book daily cap are not unified yet,
    # so a switched-on bot would stake a looser strategy than the one it is scored on. The DB refuses
    # switching a bot ON or arming while this is FALSE; this refuses a run in case a switch is
    # already ON (or the engine runs ahead of the migration — the read fails closed).
    try:
        ready, why = cs.is_money_gate_ready()
    except Exception as e:  # noqa: BLE001
        raise PlacementRefused(f"cannot read money_gate_ready ({e}) — refusing") from e
    if not ready:
        raise PlacementRefused(f"money_gate_ready: not ready (migration 436) — {why or 'placement checks not unified yet (#162 W4)'}")


def assert_may_place(
    *,
    bot_name: str | None,
    book: str,
    stake: float,
    pick: dict | None,
    held: list[dict] | None,
    kickoff_at: datetime | None = None,
    now: datetime | None = None,
    check_caps: bool = True,
    odds: float | None = None,
    prob: float | None = None,
) -> None:
    """Per-pick gate. Everything in `assert_run_may_place()` PLUS allowlist,
    kickoff cutoff, daily caps, per-match exposure and — when `odds` is given —
    the bot's placement floor at that price. Raises `PlacementRefused`; never
    returns False. `book` is recorded in the reason only — the allowlist is per bot.

    [[#162]] W4.2: `pick` (match_id / market / selection) and `held` (exposure this
    run has already taken on the match, on top of what `real_bets` shows) are
    REQUIRED keywords with no default, so an executor cannot call the gate without
    deciding what it holds. The exposure rule (same bet, same market family, per-match
    caps) and the placement floor used to live only in the callers — every executor
    now gets them from the last gate before money moves. `pick=None` refuses.
    """
    assert_run_may_place()

    if not bot_name:
        raise PlacementRefused("pick carries no bot_name — cannot check the allowlist, refusing")
    capable = placement_path_bots()
    allowed = capable & ui_place_enabled_bots()
    if bot_name not in allowed:
        why = ("no placement path (placement_gate.placement_path_reason over bot_config)"
               if bot_name not in capable
               else "not eligible: ui_place_enabled is OFF, the row is locked, or the bot is retired (coolbet_placer_bots)")
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

    # [[#162]] W4.2: per-match exposure — what real_bets holds on the match (any book, placed or
    # unverified) plus this run's in-pass `held`, through the ONE rule (exposure_conflict).
    if not pick or not pick.get("match_id"):
        raise PlacementRefused("no pick (match/market/selection) given — cannot check exposure, refusing")
    try:
        from scripts.place_coolbet_ui import exposure_conflict, match_exposure
        mid = str(pick["match_id"])
        on_match = list(match_exposure([mid]).get(mid, [])) + list(held or [])
        conflict = exposure_conflict(pick, on_match, float(stake))
    except Exception as e:  # noqa: BLE001
        raise PlacementRefused(f"cannot read per-match exposure ({e}) — refusing") from e
    if conflict:
        raise PlacementRefused(f"per-match exposure: {conflict}")

    # [[#162]] W4.3 at the gate: the bot's placement floor at the price about to be staked.
    if odds is not None:
        from workers.automation.placement_floor import pick_clears
        ok, why = pick_clears(bot_name, pick.get("market"), pick.get("selection"), odds, prob)
        if not ok:
            raise PlacementRefused(f"placement floor at {book} @ {odds}: {why}")


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
