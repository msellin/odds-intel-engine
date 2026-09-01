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
# `from scripts.place_coolbet_ui import EXECUTE_ALLOWED_BOTS`, which the
# COOLBET-UI-PLACER smoke test does — required the browser driver to be
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
BOT_THRESHOLDS = {"bot_coolbet_value_v1": 0.03}
DEFAULT_BOT = "bot_coolbet_value_v1"

# REAL-MONEY ALLOWLIST. Only these bots may ever be placed with --execute.
# Everything else is forced to dry-run no matter what flags are passed.
#
# Added 2026-08-28 when scoping bot_coolbet_dc_v1 and bot_coolbet_ah_v1: new
# experimental bots must be able to run through the whole pipeline — matching,
# pricing, snapshots, audit rows — WITHOUT any path by which an unproven
# strategy reaches the account. A default is not a guard; --bot could name any
# bot and --execute would have honoured it.
EXECUTE_ALLOWED_BOTS = {"bot_coolbet_value_v1"}


# Never place inside this window before kickoff — Coolbet suspends markets
# around the start and a placement racing the whistle is the worst time to be
# trusting a DOM.
KICKOFF_CUTOFF_MIN = 3

# Unattended spend ceilings. A UI bug that loops is the realistic failure mode,
# not a bad pick, so cap the blast radius by count AND by money.
MAX_BETS_PER_DAY = 20
MAX_STAKE_PER_DAY = 200.00

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", default=DEFAULT_BOT)
    ap.add_argument("--stake", type=float, default=DEFAULT_STAKE)
    ap.add_argument("--stage", action="store_true",
                    help="leave qualifying bets staked in the slip (still places nothing)")
    ap.add_argument("--execute", action="store_true",
                    help="PLACE REAL BETS. Operator action.")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    threshold = BOT_THRESHOLDS.get(args.bot, 0.03)

    if args.execute and args.bot not in EXECUTE_ALLOWED_BOTS:
        print(f"REFUSING --execute for {args.bot!r}: not in the real-money allowlist "
              f"({', '.join(sorted(EXECUTE_ALLOWED_BOTS))}). Running dry instead.")
        args.execute = False

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

    picks = load_picks(args.bot)
    if args.limit:
        picks = picks[: args.limit]
    if not picks:
        print(f"No open picks for {args.bot}.")
        return 0

    try:
        lock = single_run_lock()
        lock.__enter__()
    except RuntimeError as e:
        print(f"SKIP — {e}")
        return 0

    mode = "EXECUTE" if args.execute else ("STAGE" if args.stage else "DRY-RUN")
    print(f"\n{args.bot} — {len(picks)} pick(s) — stake EUR {args.stake:.2f} flat "
          f"— min-edge {threshold:.0%} — mode {mode}\n")

    placed = staged = rejected = skipped_done = 0
    from playwright.sync_api import sync_playwright
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

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        # COOLBET-UI-PLACER-AUDIT-WARN: `now` is stamped after the lock is held
        # (LOCK_EX|LOCK_NB, for the whole pass), so no other run can interleave
        # rows into this window — it is a safe lower bound for "this run".
        run_started = now
        # Incremented at exactly the points that write a
        # coolbet_placement_attempts row, so the reconciliation at the end
        # compares like with like. The old check counted len(picks), which
        # includes branches that deliberately write no row.
        expected_rows = 0
        n_today, stake_today = spent_today()

        # COOLBET-MATCH-EXPOSURE-GUARD: seed live per-match exposure once, then
        # keep it current in memory as this pass places. Re-querying per pick
        # would also work for the cross-pass case but not the within-pass one.
        exposure = match_exposure([p["match_id"] for p in picks])

        for p in picks:
            label = (f"{p['home_team']} v {p['away_team']} | "
                     f"{p['market']}/{p['selection']} @ {p['odds_at_pick']}")

            # Re-running through the day is the whole design, so the dedup
            # check comes first — a confirmed placement is never repeated.
            if already_placed(p["shadow_bet_id"]):
                skipped_done += 1
                continue

            ko = p["match_date"]
            if ko and ko - timedelta(minutes=KICKOFF_CUTOFF_MIN) <= now:
                rejected += 1
                print(f"skip     {label}\n         inside {KICKOFF_CUTOFF_MIN}min "
                      f"kickoff cutoff (KO {ko:%H:%M} UTC)")
                mark_pick(p["shadow_bet_id"], MARK_CHECKED)
                continue

            held = exposure.setdefault(p["match_id"], [])
            conflict = exposure_conflict(p, held, args.stake)
            if conflict:
                rejected += 1
                # Recorded, not just skipped: a guard nobody can measure is
                # indistinguishable from a guard that never fires
                # ([[feedback_silent_failures]]).
                expected_rows += 1
                up.record_attempt(
                    p, outcome="rejected", stage="exposure_guard",
                    reason=conflict, stake_requested=args.stake,
                    execute_mode=args.execute,
                )
                mark_pick(p["shadow_bet_id"], MARK_CHECKED)
                print(f"skip     {label}\n         per-match exposure: {conflict}")
                continue

            if args.execute:
                if n_today + placed >= MAX_BETS_PER_DAY:
                    print(f"STOP     daily bet cap reached ({MAX_BETS_PER_DAY})")
                    break
                if stake_today + (placed * args.stake) + args.stake > MAX_STAKE_PER_DAY:
                    print(f"STOP     daily stake cap reached (EUR {MAX_STAKE_PER_DAY:.2f})")
                    break

            # stage_bet writes a coolbet_placement_attempts row on EVERY exit,
            # including its own internal rejections — one call, one row.
            expected_rows += 1
            res = up.stage_bet(
                page, p, args.stake,
                execute=args.execute,
                edge_threshold=threshold,
            )
            if res.placed:
                placed += 1
                _canon = canon_bet(p["market"], p["selection"])
                if _canon:
                    held.append({"family": _canon[0], "canon": _canon[1],
                                 "stake": float(res.stake_applied or args.stake)})
                mark_pick(p["shadow_bet_id"], MARK_PLACED)
                print(f"PLACED   {label}\n         {'; '.join(res.notes)}")
            elif res.ok:
                staged += 1
                # Without --execute nothing is placed, so in-run exposure would
                # never grow and a dry run would clear every bet on a match —
                # showing the opposite of what the guard does live. Count a
                # would-place as exposure so dry runs are representative.
                if not args.execute:
                    _canon = canon_bet(p["market"], p["selection"])
                    if _canon:
                        held.append({"family": _canon[0], "canon": _canon[1],
                                     "stake": float(res.stake_applied or args.stake)})
                print(f"staged   {label}\n         {'; '.join(res.notes)}")
            elif res.reason.startswith("BLOCKED:"):
                # Abort the ENTIRE pass. Continuing would send one search per
                # remaining pick from an IP Coolbet has already flagged, which
                # is how a temporary block becomes a persistent one. The job
                # stays scheduled so it recovers by itself once the block
                # lifts — one cheap check per pass instead of ~30.
                print(f"ABORT    {res.reason}")
                print("         stopping this pass; the job will retry next slot.")
                break
            else:
                rejected += 1
                # Below-floor now can clear later, so mark it reviewed rather
                # than placed — the next pass re-checks it.
                mark_pick(p["shadow_bet_id"], MARK_CHECKED)
                print(f"skip     {label}\n         {res.reason}")

    # Count what actually landed rather than asserting it. The first version of
    # this script printed "all N recorded" unconditionally while the audit
    # INSERT was silently rolling back — the exact failure shape the audit
    # table exists to expose.
    # COOLBET-UI-PLACER-AUDIT-WARN (2026-09-01). This previously counted rows
    # from the last HOUR across all runs and compared them to len(picks), so it
    # was wrong on both sides and warned on nearly every pass:
    #   - the job runs hourly, so a pass at :00 also counted the previous run's
    #     rows;
    #   - len(picks) includes three branches that deliberately write no row —
    #     already_placed (the dedup that makes re-running safe, so ANY prior
    #     placement guaranteed the warning), the kickoff-cutoff skip, and the
    #     daily-cap break.
    # The check exists because an earlier version printed "all N recorded"
    # while the audit INSERT was silently rolling back. That is worth catching,
    # which is exactly why it must not cry wolf every run.
    recorded = execute_query(
        """SELECT COUNT(*) AS n FROM coolbet_placement_attempts
            WHERE attempted_at >= %s""",
        (run_started,),
    )[0]["n"]
    lock.__exit__(None, None, None)

    print(f"\nplaced={placed} staged={staged} skipped={rejected} "
          f"already-placed={skipped_done} "
          f"— {recorded}/{expected_rows} attempt(s) recorded this run")
    if recorded < expected_rows:
        print(f"WARNING: {expected_rows} attempt(s) should have been written but "
              f"only {recorded} landed — audit trail is incomplete")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
