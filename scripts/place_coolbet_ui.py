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
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

from workers.api_clients.db import execute_query
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

    picks = load_picks(args.bot)
    if args.limit:
        picks = picks[: args.limit]
    if not picks:
        print(f"No open picks for {args.bot}.")
        return 0

    mode = "EXECUTE" if args.execute else ("STAGE" if args.stage else "DRY-RUN")
    print(f"\n{args.bot} — {len(picks)} pick(s) — stake EUR {args.stake:.2f} flat "
          f"— min-edge {threshold:.0%} — mode {mode}\n")

    placed = staged = rejected = 0
    with sync_playwright() as pw:
        browser, page = up.attach(pw)
        if not up.is_logged_in(page):
            print("NOT LOGGED IN — run: "
                  "venv/bin/python -m workers.automation.coolbet_browser_sync --cdp-auto-login")
            return 2

        for p in picks:
            label = (f"{p['home_team']} v {p['away_team']} | "
                     f"{p['market']}/{p['selection']} @ {p['odds_at_pick']}")
            res = up.stage_bet(
                page, p, args.stake,
                execute=args.execute,
                edge_threshold=threshold,
            )
            if res.placed:
                placed += 1
                print(f"PLACED   {label}\n         {'; '.join(res.notes)}")
            elif res.ok:
                staged += 1
                print(f"staged   {label}\n         {'; '.join(res.notes)}")
            else:
                rejected += 1
                print(f"skip     {label}\n         {res.reason}")

    # Count what actually landed rather than asserting it. The first version of
    # this script printed "all N recorded" unconditionally while the audit
    # INSERT was silently rolling back — the exact failure shape the audit
    # table exists to expose.
    recorded = execute_query(
        """SELECT COUNT(*) AS n FROM coolbet_placement_attempts
            WHERE attempted_at >= NOW() - INTERVAL '1 hour'"""
    )[0]["n"]
    print(f"\nplaced={placed} staged={staged} skipped={rejected} "
          f"— {recorded} attempt(s) recorded in the last hour")
    if recorded < len(picks):
        print(f"WARNING: {len(picks)} picks processed but only {recorded} recorded — "
              f"audit trail is incomplete")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
