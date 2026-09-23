#!/usr/bin/env python3
"""BACKFILL-SHOT-LOCATION ([[#078]]) — recover the shot-location split AF has
been sending us since 2018 and we have never stored.

WHAT AND WHY
------------
`Shots insidebox` / `Shots outsidebox` (and `goals_prevented`) are in every
`/fixtures/statistics` response we already pay for. Nothing stored them until
migration 376. This re-fetches statistics for fixtures we already hold and fills
the new columns.

VERIFIED BEFORE WRITING THIS (2026-09-23):
  * AF still serves statistics for 2018 fixtures — probed fixture 135777
    (2018-06-14): results=2, `Shots insidebox` PRESENT.
  * And it is present where xG is NOT: that same 2018 fixture has no
    `expected_goals` at all, and neither does Argentine Liga Profesional
    fixture 1493144 from 2026-09-22 — a league that carried 109 xG matches in
    the preceding 90 days.
  * 30,446 of our 56,463 stats rows have no xG. Nearly all can carry this.

So the backfill is worth running over the WHOLE history, not just the recent part.

DESIGN RULES THIS OBEYS
-----------------------
* RESUMABLE. Selects only fixtures still missing the columns, so re-running
  continues rather than restarting. Kill it and start it again freely.
* QUOTA-AWARE. One call per fixture, `--limit` bounds a run, and `--sleep`
  paces it. The AF plan is 150k calls/day against ~56k fixtures, so this fits in
  one day — but it shares that budget with the live pipeline, which is why the
  default is a bounded run rather than "everything".
* WRITES ONLY THE NEW COLUMNS. It does not touch xG, shots, corners or anything
  else already stored, so a fixture whose stats have since changed upstream is
  not silently rewritten from under the analyses that used it.
* IDEMPOTENT. Re-running over an already-filled fixture is a no-op.

Usage:
    python3 scripts/backfill_shot_location.py --limit 200 --dry-run
    python3 scripts/backfill_shot_location.py --limit 5000
    python3 scripts/backfill_shot_location.py --limit 60000 --sleep 0.05
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _line in pathlib.Path(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
).read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

from workers.api_clients.db import execute_query, execute_write  # noqa: E402
from workers.api_clients.api_football import (  # noqa: E402
    get_fixture_statistics, parse_fixture_stats,
)

# Only the columns this backfill owns. Listed explicitly rather than derived, so
# a future widening of the parse cannot silently start rewriting other fields.
COLS = (
    "shots_insidebox_home", "shots_insidebox_away",
    "shots_outsidebox_home", "shots_outsidebox_away",
    "goals_prevented_home", "goals_prevented_away",
    "free_kicks_home", "free_kicks_away",
    "shots_off_target_home", "shots_off_target_away",
    "pass_pct_home", "pass_pct_away",
)


def todo(limit: int, oldest_first: bool):
    order = "ASC" if oldest_first else "DESC"
    return execute_query(f"""
        SELECT s.match_id, m.api_football_id afid, m.date
          FROM match_stats s
          JOIN matches m ON m.id = s.match_id
         WHERE m.api_football_id IS NOT NULL
           AND s.shots_insidebox_home IS NULL
           AND s.shots_outsidebox_home IS NULL
           AND s.shots_off_target_home IS NULL
         ORDER BY m.date {order}
         LIMIT %s""", (limit,))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--sleep", type=float, default=0.12,
                    help="seconds between calls; this shares the AF budget with "
                         "the live pipeline, so do not set it to 0 during the day")
    ap.add_argument("--oldest-first", action="store_true",
                    help="default is NEWEST first — recent seasons are the ones a "
                         "model would train on, and the ones most at risk if AF "
                         "withdraws more fields")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = todo(a.limit, a.oldest_first)
    remaining = execute_query("""
        SELECT count(*) n FROM match_stats s JOIN matches m ON m.id = s.match_id
         WHERE m.api_football_id IS NOT NULL AND s.shots_insidebox_home IS NULL
           AND s.shots_outsidebox_home IS NULL""")[0]["n"]
    print(f"BACKFILL-SHOT-LOCATION — {len(rows)} this run, {remaining:,} outstanding"
          f"{' [DRY RUN]' if a.dry_run else ''}")
    if not rows:
        print("nothing to do")
        return 0

    filled = empty = failed = 0
    for i, r in enumerate(rows, 1):
        try:
            parsed = parse_fixture_stats(get_fixture_statistics(r["afid"]))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [{i}] fixture {r['afid']}: {type(e).__name__}: {e}")
            time.sleep(a.sleep)
            continue

        vals = {c: parsed.get(c) for c in COLS}
        if all(v is None for v in vals.values()):
            # AF has no location split for this fixture. Recorded as `empty` and
            # NOT retried on the next run only because the selection predicate
            # will pick it up again — accepted: a cheap repeat beats a sentinel
            # value that a later analysis would have to know to ignore.
            empty += 1
        elif not a.dry_run:
            sets = ", ".join(f"{c} = %s" for c in COLS)
            execute_write(f"UPDATE match_stats SET {sets} WHERE match_id = %s",
                          tuple(vals[c] for c in COLS) + (r["match_id"],))
            filled += 1
        else:
            filled += 1

        if i % 100 == 0:
            print(f"  {i}/{len(rows)}  filled {filled}  no-data {empty}  failed {failed}")
        time.sleep(a.sleep)

    print(f"\ndone: filled {filled}, no location data {empty}, failed {failed}")
    print(f"outstanding after this run: ~{max(0, remaining - filled):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
