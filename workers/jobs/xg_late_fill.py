"""XG LATE FILL ([[#111]], 2026-09-24) — re-fetch statistics for finished matches whose
stored row has no xG, once API-Football has had time to add it.

WHY THIS EXISTS. From ~2026-08-31, API-Football began publishing `expected_goals`
(and `goals_prevented`) one to four days AFTER a match instead of within hours.
Settlement fetches statistics once, 2-7 h after kickoff, and never again — so from
that date nearly every row was stored without xG (EPL 0/20, La Liga 0/27 over
09-12..21) although the field arrives later. Measured 2026-09-24 on 496 such rows:
AF had xG for 0/3 matches one day old, 3/4 at two days, 6/8 at three, and 8/8 at
four and ten-eleven days. Nothing on our side drops it — the parser, the batch
response and the per-fixture response all carry it once AF has it.

WHAT IT DOES. Daily, for finished matches LATE_MIN_H..LATE_MAX_D old whose stats row
has no xG, in leagues that carried xG in the last 90 days (so leagues AF never rates
cost nothing): one `fixtures/statistics?half=true` call each (~100-500/day; a match
whose xG never arrives is retried daily until it leaves the window), which also yields the
half-time xG, stored through `bulk_store_match_stats` — COALESCE on update, so a
missing field can never overwrite a value already stored.

    python3 -m workers.jobs.xg_late_fill                      # the daily window
    python3 -m workers.jobs.xg_late_fill --since 2026-08-25   # one-off backfill
"""
from __future__ import annotations

import argparse
import logging

log = logging.getLogger(__name__)

LATE_MIN_H = 36          # AF has xG for ~75% of matches by day 2-3
LATE_MAX_D = 6           # after this, a still-missing xG is treated as never coming
XG_LEAGUE_LOOKBACK_D = 90

_SQL = """
    WITH xg_leagues AS (
        SELECT m.league_id FROM matches m JOIN match_stats ms ON ms.match_id = m.id
         WHERE ms.xg_home IS NOT NULL
           AND m.date > now() - make_interval(days => %(lookback)s)
         GROUP BY 1)
    SELECT m.id::text match_id, m.api_football_id af
      FROM matches m JOIN match_stats ms ON ms.match_id = m.id
     WHERE ms.xg_home IS NULL AND m.status = 'finished' AND m.api_football_id IS NOT NULL
       AND m.league_id IN (SELECT league_id FROM xg_leagues)
       AND m.date < now() - make_interval(hours => %(min_h)s)
       AND m.date >= COALESCE(%(since)s::timestamptz, now() - make_interval(days => %(max_d)s))
     ORDER BY m.date"""


def run(since: str | None = None, limit: int | None = None) -> dict:
    from workers.api_clients.api_football import (
        budget, get_fixture_statistics, parse_fixture_stats, parse_fixture_stats_halftime)
    from workers.api_clients.db import execute_query
    from workers.api_clients.supabase_client import bulk_store_match_stats

    rows = execute_query(_SQL, {"lookback": XG_LEAGUE_LOOKBACK_D, "min_h": LATE_MIN_H,
                                "max_d": LATE_MAX_D, "since": since})
    if limit:
        rows = rows[:limit]
    counts = {"candidates": len(rows), "fetched": 0, "xg_filled": 0, "no_budget": 0}
    batch = []
    for r in rows:
        if not budget.can_call():
            counts["no_budget"] = counts["candidates"] - counts["fetched"]
            break
        try:
            raw = get_fixture_statistics(int(r["af"]))
        except Exception as e:                       # one bad fixture must not stop the run
            log.warning("xg_late_fill: fixture %s: %s", r["af"], e)
            continue
        counts["fetched"] += 1
        stats = {**parse_fixture_stats(raw), **parse_fixture_stats_halftime(raw)}
        if stats.get("xg_home") is not None:
            counts["xg_filled"] += 1
            batch.append((r["match_id"], stats))
        if len(batch) >= 200:
            bulk_store_match_stats(batch)
            batch = []
    if batch:
        bulk_store_match_stats(batch)
    return counts


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="backfill from this date instead of the daily window")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    print(run(a.since, a.limit))


if __name__ == "__main__":
    main()
