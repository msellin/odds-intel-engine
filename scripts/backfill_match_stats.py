#!/usr/bin/env python3
"""BACKFILL-MATCH-STATS ([[#078]]) — recover match statistics we never stored.

TWO MODES, AND THE SECOND IS THE BIGGER PRIZE
---------------------------------------------
    --mode columns  (default)  fill the NEW columns on rows we already have
    --mode rows                create stats rows for fixtures that have NONE

`--mode columns` updates 12 columns on ~56k existing rows: the shot-location
split, free kicks, shots off target, pass % and goals prevented.

`--mode rows` targets the 17,393 finished matches that sit in leagues AF DOES
cover for statistics and have no `match_stats` row at all. It writes a COMPLETE
row through the normal `store_match_stats_full` path (~50 fields including the
half-time splits), so per successful fixture it is worth far more than a column
fill.

⚠️ **AND IT IS ALMOST CERTAINLY NOT WORTH RUNNING. MEASURE FIRST — I DID, AND MY
HYPOTHESIS WAS WRONG.**

The year distribution (2022 **2,731**, 2023 **2,672**, 2024 3,576, 2025 4,192,
2026 3,545) looked exactly like the `half=true` defect in
`get_fixture_statistics`, which returned `results: 0` for 2022 and 2023 fixtures.
The obvious inference was that the fallback had just re-opened them.

**It had not. Probing 12 random missing fixtures per year: 0/12 in 2022, 0/12 in
2023, 0/12 in 2024, 0/12 in 2025 — 0 of 48 — actually have statistics at AF.**
Sequential runs agree: 4/25 newest-first (those are fixtures that finished hours
ago and whose stats land with a lag, which settlement picks up anyway) and
**0/25 oldest-first**.

The cause is simply that `coverage_statistics_fixtures` is a LEAGUE-level flag
while AF's real per-fixture coverage inside those leagues is patchy. The gap is
not recoverable, and running this mode at scale would spend ~17,000 calls to gain
almost nothing.

**So: use `--mode columns`.** It fills 12 fields on ~56k rows that demonstrably
exist. `--mode rows` is kept for the recently-finished tail and so the
measurement above is not re-discovered by the next person.

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
    python3 scripts/backfill_match_stats.py --limit 200 --dry-run
    python3 scripts/backfill_match_stats.py --limit 5000
    python3 scripts/backfill_match_stats.py --limit 60000 --sleep 0.05
    python3 scripts/backfill_match_stats.py --mode rows --limit 50   # see the warning
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
    parse_fixture_stats_halftime, get_fixtures_batch, parse_fixture_lineups,
)
from workers.api_clients.supabase_client import (  # noqa: E402
    store_match_stats_full, store_match_lineups,
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


def todo_columns(limit: int, oldest_first: bool):
    """Rows we already have, missing the columns this backfill owns."""
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


def todo_rows(limit: int, oldest_first: bool):
    """Finished fixtures in leagues AF DOES cover, with no stats row at all.

    Scoped to `coverage_statistics_fixtures = true` deliberately: probing 30
    fixtures across 30 flagged-FALSE leagues returned statistics for 0 of them,
    so the flag is accurate and calling for those would burn quota for nothing.
    """
    order = "ASC" if oldest_first else "DESC"
    return execute_query(f"""
        SELECT m.id AS match_id, m.api_football_id afid, m.date
          FROM matches m
          JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished' AND m.api_football_id IS NOT NULL
           AND COALESCE(l.coverage_statistics_fixtures, false) = true
           AND NOT EXISTS (SELECT 1 FROM match_stats s WHERE s.match_id = m.id)
         ORDER BY m.date {order}
         LIMIT %s""", (limit,))


def backfill_fixture_block(limit: int, sleep: float, dry_run: bool) -> int:
    """REFEREE + LINEUPS from the /fixtures?ids= batch — 20 fixtures per call.

    Both fields ride the SAME response, and that response takes 20 fixtures at a
    time, so this is ~20x cheaper per fixture than either statistics mode. It
    exists because [[#081]] and [[#088]] found settlement discarding both blocks
    from a call it was already making: the forward path is fixed, and this is
    the history those fixes cannot reach (enrichment only walks yesterday+today).

    Measured recovery for referee: 24/40 September, 23/40 August — ~60%.
    Fill-if-empty on both, so a value we already hold is never overwritten.
    """
    rows = execute_query("""
        SELECT m.id AS match_id, m.api_football_id afid, m.date, m.referee,
               (m.lineups_home IS NOT NULL) AS has_lineup
          FROM matches m JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished' AND m.api_football_id IS NOT NULL
           AND COALESCE(l.coverage_statistics_fixtures, false) = true
           AND (m.referee IS NULL OR m.lineups_home IS NULL)
         ORDER BY m.date DESC
         LIMIT %s""", (limit,))
    remaining = execute_query("""
        SELECT count(*) n FROM matches m JOIN leagues l ON l.id = m.league_id
         WHERE m.status='finished' AND m.api_football_id IS NOT NULL
           AND COALESCE(l.coverage_statistics_fixtures,false) = true
           AND (m.referee IS NULL OR m.lineups_home IS NULL)""")[0]["n"]
    print(f"BACKFILL-MATCH-STATS [fixture] — {len(rows)} this run, "
          f"{remaining:,} outstanding{' [DRY RUN]' if dry_run else ''}")
    if not rows:
        print("nothing to do")
        return 0

    by_afid = {r["afid"]: r for r in rows}
    refs = lus = 0
    ids = list(by_afid)
    for i in range(0, len(ids), 20):
        chunk = ids[i:i + 20]
        try:
            batch = get_fixtures_batch(chunk)
        except Exception as e:  # noqa: BLE001
            print(f"  chunk {i//20}: {type(e).__name__}: {e}")
            time.sleep(sleep)
            continue
        for afid, f in batch.items():
            row = by_afid.get(afid)
            if not row:
                continue
            ref = ((f.get("fixture") or {}).get("referee") or "").strip() or None
            if ref and not row["referee"]:
                if not dry_run:
                    execute_write("UPDATE matches SET referee = %s "
                                  "WHERE id = %s AND referee IS NULL",
                                  (ref, row["match_id"]))
                refs += 1
                if refs <= 5:
                    print(f"  [{'dry' if dry_run else 'set'}] {afid}: referee {ref}")
            if not row["has_lineup"]:
                lu = parse_fixture_lineups(f.get("lineups") or [])
                if lu.get("formation_home") or lu.get("lineups_home"):
                    if not dry_run:
                        store_match_lineups(row["match_id"], lu)
                    lus += 1
        time.sleep(sleep)

    print(f"\ndone: {refs} referees, {lus} lineups recovered from "
          f"{(len(ids)+19)//20} calls ({len(ids)} fixtures)")
    print(f"outstanding after this run: ~{max(0, remaining - max(refs, lus)):,}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("columns", "rows", "fixture"), default="columns",
                    help="`columns` (use this) fills 12 fields on ~56k rows that "
                         "exist. `rows` creates complete rows for fixtures with "
                         "none — MEASURED YIELD 0/48 on 2022-2025, so it is not "
                         "worth running at scale; see the module docstring. "
                         "`fixture` backfills REFEREE and LINEUPS from the "
                         "/fixtures?ids= batch — 20 fixtures per call, so it is "
                         "20x cheaper than the others; measured ~60% referee "
                         "recovery ([[#088]]).")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--sleep", type=float, default=0.12,
                    help="seconds between calls; this shares the AF budget with "
                         "the live pipeline, so do not set it to 0 during the day")
    ap.add_argument("--oldest-first", action="store_true",
                    help="default is NEWEST first — recent seasons are the ones a "
                         "model would train on, and the ones most at risk if AF "
                         "withdraws more fields")
    ap.add_argument("--dry-run", action="store_true",
                    help="STILL CALLS THE API — it only skips the write. So it "
                         "costs the same quota as a real run and stores nothing, "
                         "which makes it a poor default. Its one honest use is "
                         "eyeballing the parsed values before committing to a "
                         "long run, so it now PRINTS what it would have written "
                         "for the first few fixtures. For anything else, just "
                         "run it for real with a small --limit.")
    a = ap.parse_args()

    if a.mode == "fixture":
        return backfill_fixture_block(a.limit, a.sleep, a.dry_run)

    if a.mode == "rows":
        rows = todo_rows(a.limit, a.oldest_first)
        remaining = execute_query("""
            SELECT count(*) n FROM matches m JOIN leagues l ON l.id = m.league_id
             WHERE m.status='finished' AND m.api_football_id IS NOT NULL
               AND COALESCE(l.coverage_statistics_fixtures,false) = true
               AND NOT EXISTS (SELECT 1 FROM match_stats s WHERE s.match_id=m.id)""")[0]["n"]
    else:
        rows = todo_columns(a.limit, a.oldest_first)
        remaining = execute_query("""
            SELECT count(*) n FROM match_stats s JOIN matches m ON m.id = s.match_id
             WHERE m.api_football_id IS NOT NULL AND s.shots_insidebox_home IS NULL
               AND s.shots_outsidebox_home IS NULL
               AND s.shots_off_target_home IS NULL""")[0]["n"]
    print(f"BACKFILL-MATCH-STATS [{a.mode}] — {len(rows)} this run, "
          f"{remaining:,} outstanding{' [DRY RUN]' if a.dry_run else ''}")
    if not rows:
        print("nothing to do")
        return 0

    filled = empty = failed = 0
    for i, r in enumerate(rows, 1):
        try:
            raw = get_fixture_statistics(r["afid"])
            parsed = parse_fixture_stats(raw)
            if a.mode == "rows":
                # Full-row mode writes EVERYTHING the parse produces, including
                # the half-time splits the same response already carries — the
                # whole point is that these fixtures have no row at all.
                parsed = {**parsed, **parse_fixture_stats_halftime(raw)}
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [{i}] fixture {r['afid']}: {type(e).__name__}: {e}")
            time.sleep(a.sleep)
            continue

        if a.mode == "rows":
            # `store_match_stats_full` is the same writer settlement uses, so a
            # backfilled row is indistinguishable from a live one — no second
            # code path to diverge.
            if not parsed or all(v is None for v in parsed.values()):
                empty += 1
            elif a.dry_run:
                filled += 1
                if filled <= 5:
                    got = {k: v for k, v in parsed.items()
                           if v is not None and not k.endswith("_team")}
                    print(f"  [dry-run] fixture {r['afid']} ({r['date'].date()}): "
                          f"{len(got)} fields — {dict(list(got.items())[:6])}")
            else:
                store_match_stats_full(r["match_id"], parsed)
                filled += 1
            if i % 100 == 0:
                print(f"  {i}/{len(rows)}  filled {filled}  no-data {empty}  failed {failed}")
            time.sleep(a.sleep)
            continue

        vals = {c: parsed.get(c) for c in COLS}
        if all(v is None for v in vals.values()):
            # AF has no location split for this fixture. Recorded as `empty` and
            # NOT retried on the next run only because the selection predicate
            # will pick it up again — accepted: a cheap repeat beats a sentinel
            # value that a later analysis would have to know to ignore.
            empty += 1
        elif a.dry_run:
            filled += 1
            if filled <= 5:
                # Column names keep their _home/_away suffix — stripping both
                # made every pair print the same label twice.
                print(f"  [dry-run] fixture {r['afid']} ({r['date'].date()}): "
                      + ", ".join(f"{c}={vals[c]}" for c in COLS if vals[c] is not None))
        else:
            sets = ", ".join(f"{c} = %s" for c in COLS)
            execute_write(f"UPDATE match_stats SET {sets} WHERE match_id = %s",
                          tuple(vals[c] for c in COLS) + (r["match_id"],))
            filled += 1

        if i % 100 == 0:
            print(f"  {i}/{len(rows)}  filled {filled}  no-data {empty}  failed {failed}")
        time.sleep(a.sleep)

    print(f"\ndone: filled {filled}, no location data {empty}, failed {failed}")
    print(f"outstanding after this run: ~{max(0, remaining - filled):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
