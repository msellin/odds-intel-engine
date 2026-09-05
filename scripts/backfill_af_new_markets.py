"""AF-ODDS-7DAY-BACKFILL-2026-09-05 — recover 7 days of the newly-parsed markets.

API-Football retains odds for EXACTLY 7 days, then drops them. Probed 2026-09-05:
fixtures 1/2/3/7 days old return full odds (6/6 each, with corners and team
totals); 8/9/10/11/12/13/14/30/60 days old return nothing (0/6). The date-paged
endpoint works inside the same window — 29 pages at -1d, 68 at -7d, 0 at -8d —
so a whole sweep costs roughly 280 calls against a 150,000/day limit we use at
9-23%.

Every market added on 2026-09-05 (corners, cards, team totals, first-half)
started collecting at 18:30 with ZERO history, which blocks the corners edge
measurement and the lambda-split rejection gate on days of accumulation. This
recovers 7 days immediately — and the window loses a day every day, so the data
is perishable.

WHAT THIS WRITES, AND WHY IT CANNOT DISTURB ANYTHING
----------------------------------------------------
Only the NEW market families. `1x2`, `over_under_*`, `btts`, `asian_handicap`
and `double_chance` already have history and are deliberately skipped: nothing
is overwritten, and no existing metric changes.

Timestamp semantics, chosen deliberately (see gotchas 30, 34, 40, 44 — a
mislabelled price basis has caused four separate incidents here):

  timestamp          = kickoff - 1 minute
  minutes_to_kickoff = 1
  is_closing         = FALSE

These are the last prices AF retained, so they are late pre-match snapshots.
Writing `now()` would make FINISHED matches appear to carry current prices,
poisoning `ODDS_MAX_LAG_HOURS` staleness logic and every
`DISTINCT ON ... ORDER BY timestamp DESC` reader. `minutes_to_kickoff = 1`
satisfies the verified-safe pre-match predicate (> 0 had zero false positives on
10.9M rows). `is_closing` is left FALSE on purpose so no historical CLV anchor
is retroactively altered.

Usage:
    python3 scripts/backfill_af_new_markets.py --dry-run
    python3 scripts/backfill_af_new_markets.py --days 7
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.api_football import get_odds_by_date, parse_fixture_odds  # noqa: E402
from workers.api_clients.db import execute_query, execute_write  # noqa: E402

# Families that did not exist before 2026-09-05 and therefore have no history to
# disturb. Anything not matching these prefixes is skipped.
NEW_PREFIXES = ("corners_", "cards_", "team_total_", "over_under_1h", "1x2_1h")

# AF retains odds for exactly 7 days; beyond that the endpoint returns nothing.
MAX_DAYS = 7


def is_new_market(market: str) -> bool:
    return any(market.startswith(p) for p in NEW_PREFIXES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=MAX_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    days = min(args.days, MAX_DAYS)

    total_rows = 0
    for off in range(1, days + 1):
        d = (date.today() - timedelta(days=off)).isoformat()
        try:
            by_fixture = get_odds_by_date(d)
        except Exception as exc:
            print(f"{d}: sweep failed ({exc}) — skipping")
            continue
        if not by_fixture:
            print(f"{d}: no odds returned (outside AF's retention window)")
            continue

        # Map AF fixture ids to our matches, and carry kickoff for the timestamp.
        afids = [int(f) for f in by_fixture]
        rows = execute_query(
            """SELECT api_football_id afid, id::text mid, date
                 FROM matches WHERE api_football_id = ANY(%s::bigint[])""",
            [afids],
        )
        meta = {int(r["afid"]): (r["mid"], r["date"]) for r in rows}

        payload = []
        for afid, entries in by_fixture.items():
            hit = meta.get(int(afid))
            if not hit:
                continue
            mid, kickoff = hit
            if kickoff is None:
                continue
            for parsed in parse_fixture_odds(entries):
                if not is_new_market(parsed["market"]):
                    continue
                payload.append((
                    mid, parsed["bookmaker"], parsed["market"],
                    parsed["selection"], float(parsed["odds"]), kickoff,
                ))

        print(f"{d}: {len(by_fixture)} fixtures, {len(meta)} matched, "
              f"{len(payload):,} new-market rows")
        total_rows += len(payload)
        if args.dry_run or not payload:
            continue

        for i in range(0, len(payload), 5000):
            chunk = payload[i:i + 5000]
            execute_write(
                """INSERT INTO odds_snapshots
                     (match_id, bookmaker, market, selection, odds,
                      timestamp, minutes_to_kickoff, is_closing, is_live)
                   SELECT v.mid::uuid, v.bk, v.mkt, v.sel, v.odds,
                          v.ko - interval '1 minute', 1, FALSE, FALSE
                     FROM (VALUES %s) AS v(mid, bk, mkt, sel, odds, ko)"""
                % ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(chunk)),
                [x for row in chunk for x in row],
            )
        print(f"      wrote {len(payload):,}")

    print(f"\n{'would write' if args.dry_run else 'wrote'} {total_rows:,} rows total")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
