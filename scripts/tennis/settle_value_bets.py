#!/usr/bin/env python3
"""
TENNIS-PAPER-BETS Phase 1.4 — settle finished tennis_value_bets rows.

Calls The Odds API /scores per active tennis sport, matches completed events
back to tennis_value_bets by fixture_id (= Odds API event id), updates result
+ pnl. CLV stays NULL until Phase 1.5 (closing-odds capture) lands.

Cost: ~3 active sports × 1 credit per run × 2 runs/day = ~6 credits/day,
well within the 500/mo free tier (alongside scanner's ~6/day).

Result codes written:
  'win'  — bet's selection matches the winner of the match
  'loss' — bet's selection lost
  'void' — match retirement / walkover / partial result we can't trust
  (rows past kickoff but still incomplete are left untouched for the next run)

Usage:
    python3 scripts/tennis/settle_value_bets.py              # live
    python3 scripts/tennis/settle_value_bets.py --dry-run    # print only
    python3 scripts/tennis/settle_value_bets.py --days 3     # daysFrom (1-3)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parents[2]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parents[2] / ".env")

from workers.api_clients.db import execute_query, execute_write  # noqa: E402

BASE = "https://api.the-odds-api.com/v4"
DEFAULT_STAKE = 1.0

_requests_remaining: int | None = None


def call(path: str, **params) -> tuple[int, list | dict | None]:
    global _requests_remaining
    key = os.environ.get("OA_KEY") or os.environ.get("ODDS_API_KEY", "")
    if not key:
        print("ERROR: OA_KEY / ODDS_API_KEY env var not set")
        sys.exit(1)
    params["apiKey"] = key
    url = f"{BASE}/{path.lstrip('/')}"
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            print(f"  HTTP error {path}: {e}")
            return -1, None
        if r.status_code == 429:
            wait = 2 ** attempt
            print(f"  429 — waiting {wait}s (attempt {attempt+1}/4)")
            time.sleep(wait)
            continue
        rem = r.headers.get("x-requests-remaining")
        if rem is not None:
            _requests_remaining = int(rem)
        if r.status_code != 200:
            print(f"  HTTP {r.status_code} on {path}: {r.text[:200]}")
            return r.status_code, None
        return 200, r.json()
    return 429, None


def list_active_tennis_sports() -> list[dict]:
    code, body = call("sports", all="true")
    if code != 200 or not isinstance(body, list):
        return []
    return [
        s for s in body
        if isinstance(s, dict)
        and s.get("active")
        and "tennis" in (s.get("key") or "").lower()
    ]


def derive_winner(event: dict) -> str | None:
    """
    From a /scores event with completed=true, decide the winner.
    Returns the player name (home_team or away_team) or None if we can't tell
    (will settle as 'void' upstream).
    """
    home = event.get("home_team")
    away = event.get("away_team")
    scores = event.get("scores")
    if not scores or not home or not away:
        return None

    def _num(s) -> float | None:
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    home_score = None
    away_score = None
    for sc in scores:
        if not isinstance(sc, dict):
            continue
        name = sc.get("name")
        val = _num(sc.get("score"))
        if val is None:
            continue
        if name == home:
            home_score = val
        elif name == away:
            away_score = val

    if home_score is None or away_score is None:
        return None
    if home_score == away_score:
        return None
    return home if home_score > away_score else away


def settle_event(event_id: str, home_team: str, away_team: str,
                 winner: str | None, dry_run: bool) -> tuple[int, int, int]:
    """
    Settle every unsettled tennis_value_bets row for this event.
    Returns (wins, losses, voids).
    """
    rows = execute_query(
        """
        SELECT id, selection, book_odds, stake, player_home, player_away
          FROM tennis_value_bets
         WHERE fixture_id = %s
           AND result IS NULL
        """,
        (event_id,),
    )
    if not rows:
        return 0, 0, 0

    wins = losses = voids = 0
    for row in rows:
        sel = row["selection"]
        # 'home' / 'away' map to the values in player_home / player_away on
        # the row at write time. Stay consistent with that — don't re-resolve
        # via the freshly-fetched event's home/away strings.
        backed_player = row["player_home"] if sel == "home" else row["player_away"]

        if winner is None:
            result = "void"
            pnl = 0.0
            voids += 1
        elif backed_player == winner:
            result = "win"
            pnl = float(row["stake"] or DEFAULT_STAKE) * (float(row["book_odds"]) - 1.0)
            wins += 1
        else:
            result = "loss"
            pnl = -float(row["stake"] or DEFAULT_STAKE)
            losses += 1

        if dry_run:
            print(f"    [dry] row {row['id']}  sel={sel}({backed_player[:18]:18s})  "
                  f"→ {result}  pnl={pnl:+.2f}")
        else:
            execute_write(
                """
                UPDATE tennis_value_bets
                   SET result = %s,
                       pnl    = %s
                 WHERE id = %s
                """,
                (result, round(pnl, 4), row["id"]),
            )
    return wins, losses, voids


def settle_sport(sport: dict, days_from: int, dry_run: bool) -> dict:
    sport_key = sport["key"]
    sport_title = sport.get("title") or sport_key
    code, body = call(f"sports/{sport_key}/scores", daysFrom=days_from)
    if code != 200 or not isinstance(body, list):
        return {"sport": sport_title, "events": 0, "settled_events": 0,
                "wins": 0, "losses": 0, "voids": 0,
                "skipped_incomplete": 0, "skipped_no_match": 0}

    stats = {"sport": sport_title, "events": len(body), "settled_events": 0,
             "wins": 0, "losses": 0, "voids": 0,
             "skipped_incomplete": 0, "skipped_no_match": 0}

    for ev in body:
        if not ev.get("completed"):
            stats["skipped_incomplete"] += 1
            continue
        event_id = ev.get("id")
        if not event_id:
            continue
        winner = derive_winner(ev)
        w, l, v = settle_event(
            event_id=event_id,
            home_team=ev.get("home_team") or "",
            away_team=ev.get("away_team") or "",
            winner=winner,
            dry_run=dry_run,
        )
        if (w + l + v) == 0:
            stats["skipped_no_match"] += 1
            continue
        stats["settled_events"] += 1
        stats["wins"] += w
        stats["losses"] += l
        stats["voids"] += v

    print(
        f"  {sport_title:30s}  events={stats['events']:3d}  "
        f"settled={stats['settled_events']:3d}  "
        f"W/L/V={stats['wins']}/{stats['losses']}/{stats['voids']}  "
        f"(incomplete={stats['skipped_incomplete']}  no_match={stats['skipped_no_match']})"
    )
    return stats


def main(days_from: int, dry_run: bool) -> int:
    print("=" * 70)
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"TENNIS SETTLEMENT  {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}  ({mode}, daysFrom={days_from})")
    print("=" * 70)

    sports = list_active_tennis_sports()
    if not sports:
        print("No active tennis sports — nothing to settle.")
        return 0

    # Cheap pre-check: any unsettled rows from past matches at all? If not,
    # don't burn credits on /scores calls.
    pending = execute_query(
        """
        SELECT COUNT(*) AS n
          FROM tennis_value_bets
         WHERE result IS NULL
           AND kickoff_time < now() - interval '2 hours'
        """
    )
    pending_n = pending[0]["n"] if pending else 0
    print(f"Unsettled rows past KO+2h: {pending_n}")
    if pending_n == 0:
        print("Nothing to settle. Skipping /scores fetches.")
        return 0

    print(f"Active tennis sports: {len(sports)}")
    totals = {"events": 0, "settled_events": 0,
              "wins": 0, "losses": 0, "voids": 0}
    for s in sports:
        st = settle_sport(s, days_from, dry_run)
        for k in totals:
            totals[k] += st[k]
        time.sleep(0.3)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print(f"  events seen across active sports: {totals['events']}")
    print(f"  events settled:                   {totals['settled_events']}")
    print(f"  rows: {totals['wins']} W / {totals['losses']} L / {totals['voids']} V")
    if _requests_remaining is not None:
        print(f"  Odds API credits remaining this month: {_requests_remaining}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=2, choices=(1, 2, 3),
                    help="/scores daysFrom (1-3, default 2)")
    args = ap.parse_args()
    sys.exit(main(days_from=args.days, dry_run=args.dry_run))
