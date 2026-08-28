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
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

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


# Never place inside this window before kickoff — Coolbet suspends markets
# around the start and a placement racing the whistle is the worst time to be
# trusting a DOM.
KICKOFF_CUTOFF_MIN = 3

# Unattended spend ceilings. A UI bug that loops is the realistic failure mode,
# not a bad pick, so cap the blast radius by count AND by money.
MAX_BETS_PER_DAY = 20
MAX_STAKE_PER_DAY = 200.00

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
    """
    rows = execute_query(
        """SELECT 1 FROM coolbet_placement_attempts
            WHERE shadow_bet_id = %s AND outcome = 'placed' LIMIT 1""",
        (shadow_bet_id,),
    )
    return bool(rows)


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
    with sync_playwright() as pw:
        browser, page = up.attach(pw)
        if not up.is_logged_in(page):
            print("still not logged in after auto-login — aborting")
            return 2

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        n_today, stake_today = spent_today()

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

            if args.execute:
                if n_today + placed >= MAX_BETS_PER_DAY:
                    print(f"STOP     daily bet cap reached ({MAX_BETS_PER_DAY})")
                    break
                if stake_today + (placed * args.stake) + args.stake > MAX_STAKE_PER_DAY:
                    print(f"STOP     daily stake cap reached (EUR {MAX_STAKE_PER_DAY:.2f})")
                    break

            res = up.stage_bet(
                page, p, args.stake,
                execute=args.execute,
                edge_threshold=threshold,
            )
            if res.placed:
                placed += 1
                mark_pick(p["shadow_bet_id"], MARK_PLACED)
                print(f"PLACED   {label}\n         {'; '.join(res.notes)}")
            elif res.ok:
                staged += 1
                print(f"staged   {label}\n         {'; '.join(res.notes)}")
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
    recorded = execute_query(
        """SELECT COUNT(*) AS n FROM coolbet_placement_attempts
            WHERE attempted_at >= NOW() - INTERVAL '1 hour'"""
    )[0]["n"]
    lock.__exit__(None, None, None)

    print(f"\nplaced={placed} staged={staged} skipped={rejected} "
          f"already-placed={skipped_done} "
          f"— {recorded} attempt(s) recorded in the last hour")
    if recorded < len(picks):
        print(f"WARNING: {len(picks)} picks processed but only {recorded} recorded — "
              f"audit trail is incomplete")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
