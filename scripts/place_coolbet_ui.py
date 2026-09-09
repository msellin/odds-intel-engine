#!/usr/bin/env python3
"""
COOLBET-UI-PLACER — drive today's picks through the Coolbet UI.

Stage-only by default. Every attempt is recorded in coolbet_placement_attempts
whatever the outcome, so a run that places nothing is distinguishable from a
run with nothing to place.

    # see what would happen — no account interaction beyond reading pages
    venv/bin/python scripts/place_coolbet_ui.py

    # same, but leave each qualifying bet staked in the slip for review
    venv/bin/python scripts/place_coolbet_ui.py --stage

    # place for real (operator action)
    venv/bin/python scripts/place_coolbet_ui.py --execute

Requires the operator's CDP-Chrome to be running with a Coolbet tab open:
    ./local/launch_chrome_for_sync.sh
    venv/bin/python -m workers.automation.coolbet_browser_sync --cdp-auto-login
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# SMOKE-SUITE-AUDIT 2026-09-01: playwright is imported lazily, inside the one
# function that actually drives a browser. It used to be a module-level import,
# which meant simply reading a constant from this file — e.g.
# `from scripts.place_coolbet_ui import PLACEABLE_BOTS`, which the
# COOLBET-PLACER-CONTROL smoke test does — required the browser driver to be
# installed. playwright is not in requirements.txt, so that test failed in CI
# with ModuleNotFoundError while passing locally. _session_alive() already
# deferred its import this way; main() now matches.

from workers.api_clients.db import execute_query, execute_write
from workers.automation import coolbet_ui_placer as up

log = logging.getLogger("place_coolbet_ui")

# Flat staking. Kelly on an unproven bot compounds a modelling error into a
# bankroll error; flat keeps every row equally weighted for the later audit.
DEFAULT_STAKE = 10.00

# The bot fires at a 3pct true edge (daily_pipeline_v2 _LINESHOP_TRUE_EDGE_MIN).
# Keep this in step with BOT_EDGE_THRESHOLDS on the shadow-bots admin page —
# they drifted apart once already and every min-odds floor was wrong.
BOT_THRESHOLDS = {
    # bot_coolbet_value_v1 (line-shop) RETIRED 2026-09-08 — removed from the map.
    # COOLBET-MODEL-OU-SHADOW-BOT-2026-09-08: the model-edge O/U bot fires at an
    # 8% calibrated edge (mirrors _MIN_EDGE_BY_MARKET['o/u'] and the mirror job's
    # EDGE_FLOOR). The placer's live-edge gate 1/(cal_prob - threshold) uses this,
    # so it MUST be 0.08 or the min-odds floor would be computed at the wrong edge.
    "bot_coolbet_ou_model_v1": 0.08,
    # COOLBET-MODEL-1X2-SHADOW-BOT-2026-09-08: the model-edge 1x2 bot fires at a
    # 13% calibrated edge (mirrors _MIN_EDGE_BY_MARKET['1x2'] and the mirror job's
    # EDGE_FLOOR). The placer's live-edge gate 1/(cal_prob - threshold) uses this,
    # so it MUST be 0.13 — the validated 2D gate is edge>=13% AND odds>=2.80
    # (per-market floor _min_odds_for('1x2')=2.80). Replaces the paused line-shop 1x2.
    "bot_coolbet_1x2_model_v1": 0.13,
}
DEFAULT_BOT = "bot_coolbet_ou_model_v1"  # value_v1 (line-shop) retired 2026-09-08

# ── REAL-MONEY ALLOWLIST — two layers (COOLBET-PLACER-CONTROL-2026-09-08) ─────
#
# The effective allowlist is an INTERSECTION of two independent gates:
#
#   PLACEABLE_BOTS  ∩  ui_place_enabled_bots()
#   └ code-level        └ runtime DB toggle (coolbet_placer_bots)
#
# PLACEABLE_BOTS is the hard boundary: the complete set of bots that may EVER
# stake real money. It is source code, changed only by a deploy + review. A bot
# outside it can NEVER place, no matter what the DB says — so inserting an
# enabled row in coolbet_placer_bots for some experimental bot does nothing.
#
# ui_place_enabled_bots() reads the runtime toggle. A superadmin flips a bot on
# or off from /admin/shadow-bots without a deploy and without touching pick
# generation. Because it is intersected with PLACEABLE_BOTS, the DB can only
# ever REDUCE what places — never widen it past what the code already trusts.
#
# This replaces the old code-level set EXECUTE_ALLOWED_BOTS and the
# COOLBET_UI_MODEL_EDGE_OU env flag. The seed (migration 310) keeps
# bot_coolbet_value_v1 ON and bot_coolbet_ou_model_v1 OFF, so behaviour is
# unchanged on day one: only the line-shop bot places by default.
#
# Added 2026-08-28 (as EXECUTE_ALLOWED_BOTS) when scoping bot_coolbet_dc_v1 and
# bot_coolbet_ah_v1: new experimental bots must be able to run the whole
# pipeline — matching, pricing, snapshots, audit rows — WITHOUT any path by
# which an unproven strategy reaches the account. A default is not a guard;
# --bot could name any bot and --execute would have honoured it.
PLACEABLE_BOTS = {"bot_coolbet_ou_model_v1", "bot_coolbet_1x2_model_v1"}  # value_v1 (line-shop) retired 2026-09-08


def ui_place_enabled_bots() -> set[str]:
    """Bots flipped ON for real-money UI placement in coolbet_placer_bots.

    FAILS CLOSED. On ANY database error this returns the EMPTY set — the placer
    then places nothing. That is the SAFE direction here: unlike a kill-switch
    (where a read failure must not silently disable the stop), this gate ENABLES
    real money, so a read failure must not silently enable it. "Can't read the
    toggle" resolves to "place nothing", never "place everything".

    Note the row set is intersected with PLACEABLE_BOTS by the caller, so even a
    corrupted/injected row can only enable a bot the code already trusts.
    """
    try:
        rows = execute_query(
            "SELECT bot_name FROM coolbet_placer_bots WHERE ui_place_enabled = true"
        )
        return {r["bot_name"] for r in (rows or [])}
    except Exception as e:
        log.error(
            "ui_place_enabled_bots: DB read failed — failing CLOSED (placing "
            "nothing this run): %s", e
        )
        return set()


def effective_allowlist() -> set[str]:
    """The set of bots that may place REAL money right now: the code-level hard
    whitelist intersected with the runtime DB toggle."""
    return PLACEABLE_BOTS & ui_place_enabled_bots()

# COOLBET-LINESHOP-OU-STOP-2026-09-08. The real-money line-shop bot
# (bot_coolbet_value_v1) LOSES on O/U: realized -17.0% ROI over n=1109 settled,
# negative in EVERY time fold AND every month (Aug -24%, Sep -10%), while its
# 1x2 is +13.4% (n=1910). O/U is ~37% of its volume — a steady money leak.
# Model-edge O/U is +15% (fold-robust) on the same market, so the fix is to stop
# placing line-shop O/U real money (the model-edge O/U path is the unified-flow
# work, COOLBET-REALMONEY-EDGE-GATE-RECONCILE). Gated at PLACEMENT, not
# generation, so O/U keeps writing to shadow_bets for the ongoing comparison.
# Env override RESTORES it if ever needed: COOLBET_UI_PLACE_OU=1.
REALMONEY_SKIP_MARKET_PREFIXES = () if os.getenv("COOLBET_UI_PLACE_OU") == "1" else ("over_under", "o/u")


# REALMONEY-ODDS-BAND-MISMATCH-2026-09-05 — minimum odds for real placement.
#
# The placer had NO odds gate: it checked min-edge, per-match exposure, the daily
# cap and the kickoff cutoff, but nothing on price level. It would therefore
# place at 1.40 in a band where our own de-vigged Pinnacle CLV is decisively
# negative.
#
# CLV by odds band, n=729 settled prematch singles from non-retired bots,
# recomputed at EXECUTABLE prices (the stored column was stale-priced — see
# CLV-DEVIG-COLUMN-REBUILD):
#
#     < 2.0     -2.62%   n=88    t = -5.64   <- decisively negative
#     2.0-2.8   -2.90%   n=204   t = -3.99   <- decisively negative
#     2.8-3.5   -0.99%   n=211   t = -1.10      not distinguishable from zero
#     3.5+      +9.95%   n=111   t = +3.64      positive
#
# Corroborated independently by two days of real money: <2.0 returned -66.0%
# and 2.0-2.8 -37.3%, while 3.5+ returned +5.0% and BEAT its expected win count.
# 22 of those 36 bets (61%) sat in the two decisively-negative bands.
#
# The floor is set at 2.80, not 3.50, deliberately. 2.80 excludes only the bands
# where CLV is decisively negative (t < -3.9). The 2.8-3.5 band is merely
# unproven, not proven bad, and cutting to 3.50 would keep just 11 of 36 recent
# bets while concentrating every stake into the widest-variance band on n=111
# that still carries an unresolved longshot de-vig question.
#
# CLV is the right basis for this decision: it needs ~334 bets to conclude where
# ROI needs ~19,400 (per-bet unit-return sd 1.421). Do NOT re-tune this on a few
# days of ROI.
# 2D-GATE-PER-MARKET-ODDS-FLOOR-2026-09-08: the price floor is now per-market,
# not a single global 2.80. The real-money CLV evidence above was measured on a
# 1x2-dominated sample, so 2.80 is kept for 1x2/default; O/U's own executable +
# CLV evidence puts its safe floor at 1.80 (it beats the close at 1.8+ and only
# turns negative below). `_min_odds_for` is the single source of truth shared
# with `coolbet_placer.py` so the two placement paths cannot drift.
from workers.automation.coolbet_placer import _min_odds_for
# Back-compat: the 1x2/default floor, still env-tunable via COOLBET_MIN_ODDS.
MIN_ODDS_FOR_PLACEMENT = float(os.getenv("COOLBET_MIN_ODDS", "2.80"))

# Never place inside this window before kickoff — Coolbet suspends markets
# around the start and a placement racing the whistle is the worst time to be
# trusting a DOM.
KICKOFF_CUTOFF_MIN = 3

# Unattended spend ceilings. A UI bug that loops is the realistic failure mode,
# not a bad pick, so cap the blast radius by count AND by money.
#
# COOLBET-DAILY-CAP-RAISE (2026-09-05). Raised 20 -> 80 bets and EUR 200 -> 800.
# The old pair was not acting as a blast-radius guard, it was the binding
# constraint on normal trading: it capped output on 9 of the 11 active days in
# the preceding fortnight, turning away 13-49 qualified picks a day (46 picks
# available 2026-09-05, 65 on 08-30, 69 on 08-29). Observed daily maximum is 69,
# so 80 clears real volume with headroom while still bounding a runaway loop.
#
# Note the two caps bound at the SAME point at a EUR 10 flat stake (20 x 10 =
# 200), so raising only the count would have moved the wall by nothing. They are
# raised together and must stay consistent if the stake changes.
#
# What still limits the blast radius after this change:
#   * MAX_BETS_PER_MATCH / MAX_STAKE_PER_MATCH (2 bets, EUR 20) — added
#     2026-09-01 after the 2026-08-31 incident (19 bets / EUR 190 / -EUR 92.80).
#     That incident's mechanism was repeated bets on ONE match, which is now
#     guarded directly rather than incidentally by the daily count.
#   * PLACEABLE_BOTS ∩ coolbet_placer_bots toggle — only enabled, code-trusted
#     bots may ever place (value_v1 seeded ON, ou_model_v1 seeded OFF).
#   * MARKET_FAMILY — at most one bet per (match, family).
#   * KICKOFF_CUTOFF_MIN — nothing placed inside 3 min of kickoff.
# Residual risk accepted by the owner 2026-09-05: a loop spanning MANY distinct
# matches is now bounded at EUR 800/day rather than EUR 200/day.
#
# Both are env-overridable so the ceiling can be tuned without a deploy.
MAX_BETS_PER_DAY = int(os.getenv("COOLBET_MAX_BETS_PER_DAY", "80"))
MAX_STAKE_PER_DAY = float(os.getenv("COOLBET_MAX_STAKE_PER_DAY", "800.00"))

# COOLBET-MATCH-EXPOSURE-GUARD (2026-09-01). Per-match ceilings, added after
# 2026-08-31: 19 bets / EUR 190 for -EUR 92.80, with no per-match state of any
# kind in this script. The daily caps above were the ONLY blast-radius limit
# and the run stopped one bet short of MAX_BETS_PER_DAY.
#
# Two bets on one match is allowed, but they must be independent opinions —
# see MARKET_FAMILY. What actually happened without that rule:
#   Colwyn Bay v Llandudno  1x2/away @3.10 (13:03) AND 1x2/home @2.65 (16:00)
#   Airbus UK v Holywell    1x2/away + U2.5 + U3.5  (6-2, all three lost)
#   Barcelona v Rayo        U3.5 + U2.5              (5-2, both lost)
MAX_BETS_PER_MATCH = 2
MAX_STAKE_PER_MATCH = 20.00

# Market families. At most ONE bet per (match, family): the two bets a match is
# allowed must not be two forms of the same opinion.
#
# `result` groups 1x2 with the handicap/derived result markets on purpose —
# double_chance, draw_no_bet and asian_handicap are all re-expressions of who
# wins, so backing 1x2/home and draw_no_bet/home is one position, not two.
# `totals` groups the whole over/under ladder for the same reason: under 2.5
# and under 3.5 are one goals opinion staked twice, which is exactly how
# Airbus UK cost EUR 30 on a single scoreline.
MARKET_FAMILY = {
    "1x2": "result",
    "double_chance": "result",
    "draw_no_bet": "result",
    "asian_handicap": "result",
    "o/u": "totals",
    "btts": "btts",
}

# The operator's own row in profiles — pick marks are per-user UI state.
OPERATOR_USER_ID = "c0b8031b-cb8a-4316-9969-81c8c7cfa794"

MARK_CHECKED = 1   # eye / reviewed
MARK_PLACED = 2    # checkmark / bet placed


def mark_pick(pick_id: str, state: int) -> None:
    """Mark a pick as reviewed (1) or placed (2) on the picks list.

    Idempotent and never fatal — a marking failure must not stop a run or,
    worse, cause a re-place on the next pass. Placement truth lives in
    coolbet_placement_attempts; this is UI state on top of it.
    """
    try:
        execute_write(
            """INSERT INTO user_pick_marks (user_id, pick_id, state, marked_at)
               VALUES (%s, %s, %s, NOW())
               ON CONFLICT (user_id, pick_id)
               DO UPDATE SET state = EXCLUDED.state, marked_at = NOW()""",
            (OPERATOR_USER_ID, pick_id, state),
        )
    except Exception as e:
        log.warning("could not mark pick %s state=%s: %s", pick_id, state, e)


def already_placed(shadow_bet_id: str) -> bool:
    """True if this pick has a CONFIRMED placement already.

    Guards the whole point of running periodically: re-running must never
    double-place. Keyed on a confirmed 'placed' row, and placement is only
    recorded as confirmed when the balance actually moved.

    NOTE (COOLBET-MATCH-EXPOSURE-GUARD 2026-09-01): this is keyed on the pick's
    UUID and that is NOT sufficient on its own. `shadow_bets_unique` emits
    several ids for the same logical pick — on 2026-08-31, 359 rows collapsed
    to 197 distinct (match_id, market, selection), so ~45pct were duplicate ids
    that this function cannot recognise as duplicates. Colwyn Bay had two
    1x2/home rows and two over_under_35/over rows; only run timing stopped a
    double-place, and combos already double-placed twice in August. The real
    dedup is exposure_conflict() on the normalised triple; this stays as the
    cheap first check because it needs no per-match state.
    """
    rows = execute_query(
        """SELECT 1 FROM coolbet_placement_attempts
            WHERE shadow_bet_id = %s AND outcome = 'placed' LIMIT 1""",
        (shadow_bet_id,),
    )
    return bool(rows)


# ── Per-match exposure (COOLBET-MATCH-EXPOSURE-GUARD 2026-09-01) ─────────────
#
# real_bets holds TWO vocabularies for the same bet, because two placers write
# to it: coolbet_placer.py posts `o/u` + 'over 2.5', this UI placer writes
# `over_under_25` + 'over'. Both appear in August. Any guard that reads
# real_bets without collapsing them sees half the book and lets the other half
# through, so every exposure check below goes through canon_bet() first.

def canon_bet(market: str, selection: str) -> tuple[str, str] | None:
    """Collapse either vocabulary onto (family, canonical_selection).

    Returns None for rows that carry no per-match meaning:
      - `combo`, whose match_id is only a placeholder for the first leg, so
        counting it as exposure on that match would be wrong;
      - anything unrecognised, which must not be silently treated as a match
        for some other bet.
    """
    m = (market or "").strip().lower()
    sel = (selection or "").strip().lower()
    if not m or not sel or m == "combo":
        return None

    # over_under_25 / over_under_35 -> ('totals', 'over 2.5')
    ou = re.fullmatch(r"over_under_(\d{2,3})", m)
    if ou:
        digits = ou.group(1)
        line = float(f"{digits[0]}.{digits[1:]}") if len(digits) > 1 else float(digits)
        if sel not in ("over", "under"):
            return None
        return ("totals", f"{sel} {line:g}")

    # o/u + 'over 2.5' -> ('totals', 'over 2.5')
    if m == "o/u":
        parts = sel.split()
        if len(parts) != 2 or parts[0] not in ("over", "under"):
            return None
        try:
            return ("totals", f"{parts[0]} {float(parts[1]):g}")
        except ValueError:
            return None

    family = MARKET_FAMILY.get(m)
    if family is None:
        return None
    # Asian handicap selections carry a line ('home -1.0'); keep it in the
    # canonical form so two different lines are not read as one bet. The
    # family rule blocks a second result-family bet either way.
    return (family, sel)


def match_exposure(match_ids: list[str]) -> dict[str, list[dict]]:
    """Existing real_bets exposure per match, keyed by match_id.

    NOT filtered by placed_at or by result: a bet placed yesterday on a match
    kicking off today is still exposure, which is the same reasoning the
    coolbet_placer dedup records. Callers only ask about matches whose kickoff
    is still ahead, so settled rows are not expected here anyway.
    """
    out: dict[str, list[dict]] = {mid: [] for mid in match_ids}
    if not match_ids:
        return out
    rows = execute_query(
        """SELECT match_id::text AS match_id, market, selection, stake
             FROM real_bets
            WHERE match_id = ANY(%s::uuid[])""",
        (list(match_ids),),
    )
    for r in rows or []:
        canon = canon_bet(r["market"], r["selection"])
        if canon is None:
            continue
        out.setdefault(r["match_id"], []).append(
            {"family": canon[0], "canon": canon[1], "stake": float(r["stake"] or 0)}
        )
    return out


def exposure_conflict(pick: dict, held: list[dict], stake: float) -> str | None:
    """Reason this pick must not be placed given what we already hold on the
    match, or None if it is clear.

    `held` is the live exposure list for the match — seeded from real_bets and
    appended to as this pass places, because a DB-only check is racy within one
    pass: Airbus UK's three bets landed at 13:00, 13:02 and 13:02.
    """
    canon = canon_bet(pick["market"], pick["selection"])
    if canon is None:
        return (f"unrecognised market/selection {pick['market']!r}/{pick['selection']!r} "
                f"— cannot check per-match exposure, refusing")
    family, sel = canon

    for h in held:
        if h["family"] == family and h["canon"] == sel:
            return f"already hold this exact bet on the match ({family} {sel})"

    for h in held:
        if h["family"] == family:
            return (f"already hold a {family} bet on this match ({h['canon']}); "
                    f"{sel} is the same opinion, not a second one")

    if len(held) >= MAX_BETS_PER_MATCH:
        return (f"per-match bet cap reached ({MAX_BETS_PER_MATCH}): "
                f"holding {', '.join(h['canon'] for h in held)}")

    staked = sum(h["stake"] for h in held)
    if staked + stake > MAX_STAKE_PER_MATCH:
        return (f"per-match stake cap: EUR {staked:.2f} held + EUR {stake:.2f} "
                f"exceeds EUR {MAX_STAKE_PER_MATCH:.2f}")

    return None


def spent_today() -> tuple[int, float]:
    """(bets, stake) confirmed placed since midnight UTC."""
    r = execute_query(
        """SELECT COUNT(*) AS n, COALESCE(SUM(stake_applied), 0) AS s
             FROM coolbet_placement_attempts
            WHERE outcome = 'placed'
              AND attempted_at >= date_trunc('day', NOW() AT TIME ZONE 'UTC')"""
    )[0]
    return int(r["n"]), float(r["s"])


# ── ACCOUNT VERIFICATION — FIRST real-money gate (COOLBET-ACCOUNT-VERIFY-GATE) ──
#
# 2026-09-08. Before ANY bot places real money, read the operator's ACTUAL
# Coolbet account (its pending single tickets) and do two things:
#
#   1. Reconcile those tickets into `real_bets`, so a bet placed MANUALLY (or by
#      a prior run) is visible to the per-match exposure dedup and to the picks
#      "placed" column. The mac daemon marks `simulated_bets.user_placed_at` for
#      this, but the UI placer never reads that column — that was the gap.
#   2. Refuse to place any pick already held on the account.
#
# FAIL CLOSED. If the account cannot be READ AND VERIFIED — the tab is not on the
# history page, the session is not logged in, CDP is unreachable, or anything
# raises — the whole run is forced to dry-run. "Cannot verify the account" must
# never place real money. A verified-but-empty read is a trustworthy "no pending
# bets" and is NOT a failure.


def fetch_account_holds(page) -> tuple[bool, list[dict]]:
    """Read the operator's ACTUAL Coolbet pending single bets.

    Returns (verified, holds):
      * (True, [norms]) — the account was READ AND VERIFIED: the tab is
        confirmed on the history page AND the session is logged in. An EMPTY
        list here is a trustworthy "the account has no pending bets".
      * (False, [])     — could NOT verify (tab not on history, not logged in,
        CDP error, or any exception). The caller MUST fail closed: no real
        money this run.

    Only single tickets are returned; combo tickets are logged and skipped
    (single-bet dedup only). Wraps everything in try/except — any failure
    resolves to (False, []), never a raise, so a read problem can never place.
    """
    from workers.automation.coolbet_browser_sync import normalize_for_dedup, HISTORY_PAGE
    try:
        # Drive the placer's OWN playwright tab (same CDP Chrome) to the history
        # page and capture the app's /s/sbgate/bets/history XHR via the sync
        # API. Raw-CDP asyncio.run() CANNOT be used here — the sync_playwright
        # context already runs an event loop, so asyncio.run() raises
        # "cannot be called from a running event loop" and the gate would fail
        # closed on every run (halting even the working 1x2 bot).
        try:
            with page.expect_response(
                lambda r: "/s/sbgate/bets/history" in r.url and r.status == 200,
                timeout=25000,
            ) as resp_info:
                page.goto(HISTORY_PAGE, wait_until="commit", timeout=30000)
            body = resp_info.value.json()
        except Exception as e:
            log.warning("account-verify: history XHR not captured: %s", e)
            print(f"account-verify: could not read the Coolbet history feed "
                  f"({type(e).__name__}) — cannot verify account")
            return (False, [])

        # CONFIRM we are on the history page AND logged in — else an empty read
        # cannot be trusted as "no bets" — fail closed.
        cur_url = page.url or ""
        if "panuste-ajalugu" not in cur_url:
            print(f"account-verify: tab not on history page (url={cur_url[:80]!r}) "
                  f"— cannot verify account")
            return (False, [])
        try:
            logged_in = up.is_logged_in(page)
        except Exception as e:
            log.warning("account-verify: is_logged_in raised: %s", e)
            return (False, [])
        if not logged_in:
            print("account-verify: session not logged in on the history page — "
                  "cannot verify account")
            return (False, [])

        # Parse the tickets; keep singles, log+drop combos.
        items = ((body.get("tickets") or body.get("data") or body.get("results")
                  or body.get("items") or []) if isinstance(body, dict)
                 else (body if isinstance(body, list) else []))
        holds: list[dict] = []
        combos = 0
        for t in items:
            norm = normalize_for_dedup(t)
            if not norm:
                continue
            if norm.get("is_combo"):
                combos += 1
                continue
            holds.append(norm)
        if combos:
            print(f"account-verify: {combos} combo ticket(s) present on the "
                  f"account — skipped from single-bet holds (logged only)")
        print(f"account-verify: VERIFIED — {len(holds)} single pending bet(s) "
              f"on the account")
        return (True, holds)
    except Exception as e:
        log.error("account-verify: fetch_account_holds failed — failing CLOSED: %s", e)
        print(f"account-verify: read FAILED ({type(e).__name__}: {str(e)[:120]}) "
              f"— failing closed, no real-money placement this run")
        return (False, [])


def reconcile_account_to_real_bets(norms: list[dict]) -> int:
    """Make `real_bets` reflect the operator's ACTUAL Coolbet account.

    For each verified single ticket, resolve it to a fixture bet (match_id +
    market/selection) via `match_coolbet_to_simulated` over upcoming-fixture
    candidates, and INSERT a `real_bets` row if none already exists for that
    canonical (match, family, selection) key. Idempotent — a ticket already
    represented in `real_bets` (in EITHER market vocabulary, via `canon_bet`)
    is skipped. Returns the number of rows inserted.

    This is what feeds BOTH the per-match exposure dedup (`match_exposure`
    reads `real_bets`) and the picks "placed" column, so a manually-placed bet
    — or one from a prior run — is seen by every downstream gate.
    """
    if not norms:
        return 0
    from datetime import datetime, timezone
    from workers.automation.coolbet_browser_sync import match_coolbet_to_simulated

    # Candidates: upcoming-fixture bets from BOTH simulated_bets and shadow_bets
    # — enough to resolve match_id + market/selection for a Coolbet ticket.
    # Mirrors the mac daemon's candidate shape, widened to shadow_bets and to
    # m.date > NOW() - 6h (a just-kicked-off bet is still real exposure).
    candidates = execute_query(
        """SELECT match_id, market, selection, bot_id,
                  home_team, away_team, match_date FROM (
              SELECT sb.match_id::text AS match_id, sb.market, sb.selection,
                     sb.bot_id::text   AS bot_id,
                     ht.name AS home_team, at2.name AS away_team,
                     m.date  AS match_date
                FROM simulated_bets sb
                JOIN matches m   ON m.id  = sb.match_id
                JOIN teams   ht  ON ht.id = m.home_team_id
                JOIN teams   at2 ON at2.id = m.away_team_id
               WHERE m.date > NOW() - INTERVAL '6 hours'
              UNION ALL
              SELECT s.match_id::text AS match_id, s.market, s.selection,
                     s.bot_id::text   AS bot_id,
                     ht.name AS home_team, at2.name AS away_team,
                     m.date  AS match_date
                FROM shadow_bets s
                JOIN matches m   ON m.id  = s.match_id
                JOIN teams   ht  ON ht.id = m.home_team_id
                JOIN teams   at2 ON at2.id = m.away_team_id
               WHERE m.date > NOW() - INTERVAL '6 hours'
           ) c"""
    )
    candidates = [dict(r) for r in (candidates or [])]
    if not candidates:
        return 0

    # PLACER-RECONCILE-ATTRIB-2026-09-09: a manual ticket usually matches BOTH a
    # reference bot's pick (bot_v10_all in simulated_bets) and the PLACEABLE bot's
    # mirror (bot_coolbet_ou_model_v1 in shadow_bets). match_coolbet_to_simulated
    # returns just one, and if it's the reference bot the real-money card's
    # "placed today" (filtered by the placeable bot's id) misses the manual bet —
    # the operator sees "placed 1" when the account holds 3. So when a PLACEABLE
    # bot has the SAME canonical bet on the same fixture, attribute the reconciled
    # row to it, so the real-money count reflects the account.
    _placeable_bot_ids: set[str] = set()
    try:
        _pb = execute_query(
            "SELECT id::text AS id FROM bots WHERE name = ANY(%s)",
            [list(PLACEABLE_BOTS)],
        )
        _placeable_bot_ids = {r["id"] for r in (_pb or [])}
    except Exception:
        _placeable_bot_ids = set()

    inserted = 0
    for norm in norms:
        matched = match_coolbet_to_simulated(norm, candidates)
        if not matched:
            log.info("account-verify: no fixture match for account ticket %s (%s) "
                     "— cannot reconcile to real_bets",
                     norm.get("ticket_id"), norm.get("match_name"))
            continue
        canon = canon_bet(matched.get("market"), matched.get("selection"))
        if canon is None:
            continue
        # Prefer a placeable bot's pick for the same fixture bet (see above).
        if matched.get("bot_id") not in _placeable_bot_ids:
            for c in candidates:
                if (
                    c.get("match_id") == matched["match_id"]
                    and c.get("bot_id") in _placeable_bot_ids
                    and canon_bet(c.get("market"), c.get("selection")) == canon
                ):
                    matched = {**matched, "bot_id": c["bot_id"]}
                    break
        # Idempotent: skip if real_bets already holds this canonical bet on the
        # match (match_exposure canonicalises BOTH vocabularies, so a row
        # written by either placer is recognised).
        held = match_exposure([matched["match_id"]]).get(matched["match_id"], [])
        if any(h["family"] == canon[0] and h["canon"] == canon[1] for h in held):
            continue

        try:
            stake_val = float(norm.get("stake") or 0)
        except (TypeError, ValueError):
            stake_val = 0.0
        try:
            odds_val = float(norm.get("odds") or 0)
        except (TypeError, ValueError):
            odds_val = 0.0
        note = (f"coolbet-account-sync ticket #{norm.get('ticket_id')} "
                f"(self-verified {datetime.now(timezone.utc):%Y-%m-%d})")
        try:
            # slippage_pct is a GENERATED column — never inserted.
            execute_write(
                """INSERT INTO real_bets
                       (bot_id, match_id, market, selection, bookmaker,
                        captured_odds, actual_odds, stake, placed_at,
                        result, notes)
                   VALUES (%s, %s, %s, %s, 'Coolbet',
                           %s, %s, %s, NOW(), 'pending', %s)""",
                (matched.get("bot_id"), matched["match_id"], matched["market"],
                 matched["selection"], odds_val, odds_val, stake_val, note),
            )
            inserted += 1
            print(f"account-verify: reconciled {matched['home_team']} v "
                  f"{matched['away_team']} | {matched['market']}/"
                  f"{matched['selection']} → real_bets (ticket #{norm.get('ticket_id')})")
        except Exception as e:
            log.warning("account-verify: real_bets insert failed for ticket %s: %s",
                        norm.get("ticket_id"), e)
    return inserted


def load_picks(bot_name: str) -> list[dict]:
    """Unsettled picks for `bot_name` whose kickoff is still ahead."""
    return execute_query(
        """SELECT s.id::text        AS shadow_bet_id,
                  s.bot_id::text    AS bot_id,
                  b.name            AS bot_name,
                  s.match_id::text  AS match_id,
                  s.market, s.selection,
                  s.odds_at_pick, s.model_probability, s.calibrated_prob,
                  ht.name AS home_team, at.name AS away_team,
                  m.date  AS match_date
             FROM shadow_bets_unique s
             JOIN bots    b  ON b.id  = s.bot_id
             JOIN matches m  ON m.id  = s.match_id
             JOIN teams   ht ON ht.id = m.home_team_id
             JOIN teams   at ON at.id = m.away_team_id
            WHERE b.name = %s
              AND m.date > NOW()
              -- unsettled rows carry result='pending', not NULL
              AND (s.result IS NULL OR s.result = 'pending')
            ORDER BY m.date""",
        (bot_name,),
    )


LOCK_PATH = Path.home() / ".coolbet-daemon" / "ui-placer.lock"


@contextmanager
def single_run_lock():
    """Refuse to start if another pass is already driving the browser.

    Both the scheduled job and any manual run drive the SAME Chrome tab, so
    two passes type into the same search box and navigate the same page. On
    2026-08-27 that produced five fixtures failing to match inside a 90-second
    window — Ararat, Iberia, St. Gallen, Simba and Brann all matched fine in
    every other pass — plus a Fulham search that returned nothing. It reads as
    a matcher bug and is not one.

    flock releases automatically if the holder dies, so a crashed pass cannot
    wedge the lock.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "another Coolbet UI pass is running — refusing to drive the "
                "same browser twice (see ~/.coolbet-daemon/ui-placer.lock)"
            )
        fh.write(str(os.getpid()))
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def _session_alive() -> bool:
    """Is the Coolbet session live? Opens and closes its own browser context.

    Deliberately separate from the run's context so the caller can heal the
    session before that context exists — see the note in main().
    """
    from playwright.sync_api import sync_playwright as _sp
    try:
        with _sp() as pw:
            _, page = up.attach(pw)
            return up.is_logged_in(page)
    except Exception as e:
        log.warning("session check failed: %s", e)
        return False


def place_for_bot(page, bot_name: str, picks: list[dict], execute: bool,
                  stake: float, now, account_holds: list[dict] | None = None) -> dict:
    """Run the existing per-bot placement flow for one bot over `picks`.

    Extracted from main() so a single run can drive several enabled bots through
    the SAME browser session and lock (COOLBET-PLACER-CONTROL-2026-09-08). Every
    gate is unchanged — dedup, line-shop O/U stop (scoped to value_v1), kickoff
    cutoff, per-market odds floor, per-match exposure, daily caps — and each is
    evaluated per bot with that bot's own edge threshold.

    Returns a dict of counts plus `abort`: True means the WHOLE run must stop
    (a daily cap was hit, or Coolbet served an interstitial). Both are run-level
    conditions — the daily cap is global, and continuing after a block would
    send more traffic from an already-flagged IP — so the driver breaks out of
    the bot loop rather than moving to the next bot.
    """
    from datetime import timedelta
    from workers.automation.coolbet_browser_sync import match_coolbet_to_simulated

    account_holds = account_holds or []
    threshold = BOT_THRESHOLDS.get(bot_name, 0.03)
    placed = staged = rejected = skipped_done = 0
    expected_rows = 0
    abort = False

    # Daily caps are GLOBAL across bots. spent_today() re-reads confirmed
    # 'placed' rows from the DB, and each --execute placement commits its row
    # before the next pick, so re-reading here makes a second bot see the first
    # bot's placements — the EUR 800/day ceiling bounds the whole run, not each
    # bot separately.
    n_today, stake_today = spent_today()

    # COOLBET-MATCH-EXPOSURE-GUARD: seed live per-match exposure once, then keep
    # it current in memory as this pass places. Re-querying per pick would also
    # work for the cross-pass case but not the within-pass one.
    exposure = match_exposure([p["match_id"] for p in picks])

    for p in picks:
        label = (f"{p['home_team']} v {p['away_team']} | "
                 f"{p['market']}/{p['selection']} @ {p['odds_at_pick']}")

        # Re-running through the day is the whole design, so the dedup
        # check comes first — a confirmed placement is never repeated.
        if already_placed(p["shadow_bet_id"]):
            skipped_done += 1
            continue

        # COOLBET-ACCOUNT-VERIFY-GATE-2026-09-08: belt-and-suspenders on top of
        # exposure_conflict. Refuse any pick already held on the ACTUAL Coolbet
        # account (manual or a prior placement). `account_holds` was read once
        # at run start and reconciled into real_bets; this per-pick check also
        # catches a bet that landed on the account between that reconcile and
        # now. Uses the same conservative fuzzy matcher as the reconcile.
        if account_holds and any(
                match_coolbet_to_simulated(h, [p]) for h in account_holds):
            rejected += 1
            expected_rows += 1
            up.record_attempt(
                p, outcome="rejected", stage="already_on_coolbet_account",
                reason="already held on the Coolbet account (manual or prior placement)",
                stake_requested=stake, execute_mode=execute,
            )
            mark_pick(p["shadow_bet_id"], MARK_CHECKED)
            print(f"skip     {label}\n         already on the Coolbet account "
                  f"(manual or prior placement)")
            continue

        # COOLBET-LINESHOP-OU-STOP-2026-09-08: never place line-shop O/U real
        # money (realized -17% ROI, negative every month). See the constant.
        # SCOPED to bot_coolbet_value_v1 ONLY (COOLBET-MODEL-OU-SHADOW-BOT):
        # the O/U leak is the LINE-SHOP bot's, not the model-edge O/U bot's
        # (bot_coolbet_ou_model_v1, +15% fold-robust). This stop must never
        # block the model-edge O/U bot, whose entire purpose is to place O/U.
        _mkt = (p.get("market") or "").lower()
        if (bot_name == "bot_coolbet_value_v1"
                and any(_mkt.startswith(pre) for pre in REALMONEY_SKIP_MARKET_PREFIXES)):
            rejected += 1
            expected_rows += 1
            up.record_attempt(
                p, outcome="rejected", stage="lineshop_ou_stop",
                reason="line-shop O/U real-money placement disabled (-17% ROI); "
                       "model-edge O/U is the unified-flow path",
                stake_requested=stake, execute_mode=execute,
            )
            mark_pick(p["shadow_bet_id"], MARK_CHECKED)
            print(f"skip     {label}\n         line-shop O/U placement disabled "
                  f"(-17% ROI; COOLBET_UI_PLACE_OU=1 to override)")
            continue

        ko = p["match_date"]
        if ko and ko - timedelta(minutes=KICKOFF_CUTOFF_MIN) <= now:
            rejected += 1
            print(f"skip     {label}\n         inside {KICKOFF_CUTOFF_MIN}min "
                  f"kickoff cutoff (KO {ko:%H:%M} UTC)")
            mark_pick(p["shadow_bet_id"], MARK_CHECKED)
            continue

        # REALMONEY-ODDS-BAND-MISMATCH-2026-09-05: reject bands where our own
        # de-vigged CLV is decisively negative. Gated on `odds_at_pick`
        # because that is the basis the CLV analysis bucketed on — gating on
        # a different price than the one the evidence was measured at is how
        # this codebase has repeatedly fooled itself.
        #
        # Slippage caveat: the executed price can land below this floor even
        # when the pick clears it. That is bounded by the placer's own
        # min-odds check downstream, and is a smaller error than placing in a
        # band with t = -5.64 CLV by design.
        try:
            _pick_odds = float(p.get("odds_at_pick") or 0)
        except (TypeError, ValueError):
            _pick_odds = 0.0
        _floor = _min_odds_for(p.get("market"))
        if _pick_odds < _floor:
            rejected += 1
            expected_rows += 1
            up.record_attempt(
                p, outcome="rejected", stage="odds_floor",
                reason=(f"odds {_pick_odds:.2f} < {p.get('market')} floor "
                        f"{_floor:.2f} (CLV negative below it)"),
                stake_requested=stake, execute_mode=execute,
            )
            mark_pick(p["shadow_bet_id"], MARK_CHECKED)
            print(f"skip     {label}\n         odds {_pick_odds:.2f} below "
                  f"{p.get('market')} floor {_floor:.2f} — CLV in this band is negative")
            continue

        held = exposure.setdefault(p["match_id"], [])
        conflict = exposure_conflict(p, held, stake)
        if conflict:
            rejected += 1
            # Recorded, not just skipped: a guard nobody can measure is
            # indistinguishable from a guard that never fires
            # ([[feedback_silent_failures]]).
            expected_rows += 1
            up.record_attempt(
                p, outcome="rejected", stage="exposure_guard",
                reason=conflict, stake_requested=stake,
                execute_mode=execute,
            )
            mark_pick(p["shadow_bet_id"], MARK_CHECKED)
            print(f"skip     {label}\n         per-match exposure: {conflict}")
            continue

        if execute:
            if n_today + placed >= MAX_BETS_PER_DAY:
                print(f"STOP     daily bet cap reached ({MAX_BETS_PER_DAY})")
                abort = True
                break
            if stake_today + (placed * stake) + stake > MAX_STAKE_PER_DAY:
                print(f"STOP     daily stake cap reached (EUR {MAX_STAKE_PER_DAY:.2f})")
                abort = True
                break

        # stage_bet writes a coolbet_placement_attempts row on EVERY exit,
        # including its own internal rejections — one call, one row.
        expected_rows += 1
        res = up.stage_bet(
            page, p, stake,
            execute=execute,
            edge_threshold=threshold,
        )
        if res.placed:
            placed += 1
            _canon = canon_bet(p["market"], p["selection"])
            if _canon:
                held.append({"family": _canon[0], "canon": _canon[1],
                             "stake": float(res.stake_applied or stake)})
            mark_pick(p["shadow_bet_id"], MARK_PLACED)
            print(f"PLACED   {label}\n         {'; '.join(res.notes)}")
        elif res.ok:
            staged += 1
            # Without --execute nothing is placed, so in-run exposure would
            # never grow and a dry run would clear every bet on a match —
            # showing the opposite of what the guard does live. Count a
            # would-place as exposure so dry runs are representative.
            if not execute:
                _canon = canon_bet(p["market"], p["selection"])
                if _canon:
                    held.append({"family": _canon[0], "canon": _canon[1],
                                 "stake": float(res.stake_applied or stake)})
            print(f"staged   {label}\n         {'; '.join(res.notes)}")
        elif res.reason.startswith("BLOCKED:"):
            # Abort the ENTIRE pass. Continuing would send one search per
            # remaining pick from an IP Coolbet has already flagged, which
            # is how a temporary block becomes a persistent one. The job
            # stays scheduled so it recovers by itself once the block
            # lifts — one cheap check per pass instead of ~30.
            print(f"ABORT    {res.reason}")
            print("         stopping this pass; the job will retry next slot.")
            abort = True
            break
        else:
            rejected += 1
            # Below-floor now can clear later, so mark it reviewed rather
            # than placed — the next pass re-checks it.
            mark_pick(p["shadow_bet_id"], MARK_CHECKED)
            print(f"skip     {label}\n         {res.reason}")

    return {
        "placed": placed, "staged": staged, "rejected": rejected,
        "skipped_done": skipped_done, "expected_rows": expected_rows,
        "abort": abort,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default=DEFAULT_BOT)
    ap.add_argument("--stake", type=float, default=DEFAULT_STAKE)
    ap.add_argument("--stage", action="store_true",
                    help="leave qualifying bets staked in the slip (still places nothing)")
    ap.add_argument("--execute", action="store_true",
                    help="PLACE REAL BETS. Operator action.")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--all-enabled", action="store_true",
                    help="place ALL bots currently enabled in coolbet_placer_bots "
                         "(intersected with PLACEABLE_BOTS) in one run, sharing one "
                         "browser session and one lock. Ignores --bot.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Effective real-money allowlist for THIS run: PLACEABLE_BOTS (code-level
    # hard whitelist) ∩ the DB toggle (coolbet_placer_bots). Read once; every
    # per-bot execute decision below checks against it. Fails CLOSED (empty set)
    # on any DB error, so a toggle we can't read means "place nothing".
    allowed = effective_allowlist()

    if args.all_enabled:
        # Only enabled + placeable bots run. They are all in `allowed`, so
        # --execute is honoured for each; --bot is ignored in this mode.
        bots_to_run = sorted(allowed)
        if not bots_to_run:
            print("no bots enabled for real-money UI placement in "
                  "coolbet_placer_bots (∩ PLACEABLE_BOTS) — nothing to do.")
            return 0
    else:
        bots_to_run = [args.bot]

    # Per-bot execute gate. A bot outside the effective allowlist is forced to
    # dry-run no matter what flags are passed — the same rule the single-bot
    # path always enforced, now sourced from PLACEABLE_BOTS ∩ DB toggle rather
    # than a lone code constant. --stage/--execute still print & record; they
    # just never touch the account for a disallowed bot.
    bot_execute = {b: (args.execute and b in allowed) for b in bots_to_run}
    for b in bots_to_run:
        if args.execute and not bot_execute[b]:
            reason = ("not in PLACEABLE_BOTS (hard code-level whitelist)"
                      if b not in PLACEABLE_BOTS
                      else "ui_place_enabled is OFF in coolbet_placer_bots")
            print(f"REFUSING --execute for {b!r}: {reason}. "
                  f"Running dry for this bot instead.")

    # Pre-load picks per bot BEFORE opening the browser or taking the lock, so a
    # run with nothing to do stays cheap — no session heal, no lock contention.
    picks_by_bot: dict[str, list[dict]] = {}
    for b in bots_to_run:
        pk = load_picks(b)
        if args.limit:
            pk = pk[: args.limit]
        picks_by_bot[b] = pk
    if not any(picks_by_bot.values()):
        print(f"No open picks for {', '.join(bots_to_run)}.")
        return 0

    # Self-heal the session BEFORE opening the run's browser context.
    # cdp_auto_login opens its own sync_playwright, and Playwright refuses a
    # second sync context inside the first ("Sync API inside the asyncio
    # loop"). Calling it from within the run's `with sync_playwright()` block
    # broke every unattended recovery from 2026-08-27 21:30 to 2026-08-28
    # 06:00+ — the loop woke, found the session gone, and failed to heal on
    # every pass. Keep this OUTSIDE the run context.
    if not _session_alive():
        print("session lost — logging in…")
        from workers.automation.coolbet_browser_sync import cdp_auto_login
        try:
            rc = cdp_auto_login()
        except Exception as e:
            print(f"auto-login raised: {type(e).__name__}: {str(e)[:140]}")
            rc = 1
        if rc != 0:
            print("AUTO-LOGIN FAILED — if Coolbet asked for SMS, complete it in "
                  "the browser; otherwise check COOLBET_USER/COOLBET_PASS.")
            return 2
        print("session restored.")

    try:
        lock = single_run_lock()
        lock.__enter__()
    except RuntimeError as e:
        print(f"SKIP — {e}")
        return 0

    totals = {"placed": 0, "staged": 0, "rejected": 0, "skipped_done": 0}
    expected_total = 0
    recorded = 0
    from datetime import datetime, timezone
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser, page = up.attach(pw)
            blocked = up.detect_block(page)
            if blocked:
                print(f"ABORT — {blocked}")
                print("  Coolbet is serving an interstitial to this IP. Do NOT retry or "
                      "re-login; both add traffic from an already-flagged address. The "
                      "job stays scheduled and will recover on its own once it lifts.")
                return 0
            if not up.is_logged_in(page):
                print("still not logged in after auto-login — aborting")
                return 2

            # ── ACCOUNT VERIFICATION — FIRST real-money gate ──────────────────
            # Read the operator's ACTUAL account ONCE per run. This is the first
            # gate real money passes through.
            #   • UNVERIFIED  -> FAIL CLOSED: force EVERY bot to dry-run for this
            #     run, so nothing is staked when we cannot see the account. The
            #     run still proceeds as a dry-run so matching/pricing/audit rows
            #     are produced.
            #   • VERIFIED    -> reconcile the account's pending tickets into
            #     real_bets (so exposure dedup + the picks "placed" column are
            #     current) and pass the holds into every bot's per-pick gate.
            account_verified, account_holds = fetch_account_holds(page)
            if not account_verified:
                print("\n*** account UNVERIFIED — failing closed, no real-money "
                      "placement this run. Every bot forced to DRY-RUN. ***\n")
                bot_execute = {b: False for b in bots_to_run}
            else:
                n_reconciled = reconcile_account_to_real_bets(account_holds)
                if n_reconciled:
                    print(f"account-verify: reconciled {n_reconciled} account "
                          f"ticket(s) into real_bets before placing")

            now = datetime.now(timezone.utc)
            # COOLBET-UI-PLACER-AUDIT-WARN: `now` is stamped after the lock is held
            # (LOCK_EX|LOCK_NB, for the whole run), so no other run can interleave
            # rows into this window — it is a safe lower bound for "this run",
            # across every bot placed in it.
            run_started = now

            # One browser session, one lock, each enabled bot in turn. Daily caps
            # and Coolbet blocks are run-level, so a bot returning abort=True stops
            # the whole run rather than advancing to the next bot.
            for b in bots_to_run:
                picks = picks_by_bot[b]
                if not picks:
                    continue
                exec_b = bot_execute[b]
                threshold = BOT_THRESHOLDS.get(b, 0.03)
                mode = "EXECUTE" if exec_b else ("STAGE" if args.stage else "DRY-RUN")
                print(f"\n{b} — {len(picks)} pick(s) — stake EUR {args.stake:.2f} flat "
                      f"— min-edge {threshold:.0%} — mode {mode}\n")
                counts = place_for_bot(page, b, picks, exec_b, args.stake, now,
                                       account_holds=account_holds)
                for k in totals:
                    totals[k] += counts[k]
                expected_total += counts["expected_rows"]
                if counts["abort"]:
                    break

            # Count what actually landed rather than asserting it. The first
            # version of this script printed "all N recorded" unconditionally
            # while the audit INSERT was silently rolling back — the exact
            # failure shape the audit table exists to expose.
            # COOLBET-UI-PLACER-AUDIT-WARN (2026-09-01): scoped to attempts
            # written at/after run_started so a run at :00 does not count the
            # previous hourly run's rows, and compared against expected_total
            # (rows the loop actually tried to write, summed across bots) rather
            # than len(picks), which includes branches that write no row.
            recorded = execute_query(
                """SELECT COUNT(*) AS n FROM coolbet_placement_attempts
                    WHERE attempted_at >= %s""",
                (run_started,),
            )[0]["n"]
    finally:
        lock.__exit__(None, None, None)

    print(f"\nplaced={totals['placed']} staged={totals['staged']} "
          f"skipped={totals['rejected']} already-placed={totals['skipped_done']} "
          f"— {recorded}/{expected_total} attempt(s) recorded this run")
    if recorded < expected_total:
        print(f"WARNING: {expected_total} attempt(s) should have been written but "
              f"only {recorded} landed — audit trail is incomplete")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
