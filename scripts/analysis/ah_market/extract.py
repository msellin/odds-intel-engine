#!/usr/bin/env python3
"""#187 AH market bot — phase 1 extraction (read-only).

Pulls every PRE-MATCH Asian-handicap quote for FINISHED matches from 2026-07-01 (before that the AH history
is ~1 snapshot per match from 4-5 books — useless for a pick-time test; measured 2026-09-26) and writes:

  data/models/_research/ah_market/runs.parquet     one row per PRICE RUN (match, book, side, line, odds):
                                                   first_ts (the price appeared), last_ts (last seen)
  data/models/_research/ah_market/pin_fetch.parquet every Pinnacle AH row (fetch-level: the fair price at
                                                   any instant is the latest Pinnacle fetch, not a run)
  data/models/_research/ah_market/matches.parquet  kickoff, final score, league, tier

`handicap_line` is the HOME team's line on both rows (verified per book 2026-09-26: same-side log-diff to
Pinnacle ≈ 0.03 for every book vs ≈ 0.12–0.65 to the other side). Runs are gaps-and-islands on the odds value
per (match, book, side, line), computed in SQL week by week so no week reads more than ~1.5M rows.

    python3 scripts/analysis/ah_market/extract.py [--since 2026-07-01]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import pandas as pd  # noqa: E402
import psycopg2  # noqa: E402

OUT = "data/models/_research/ah_market"

RUNS_SQL = """
SET LOCAL statement_timeout = '600s';
WITH m AS (
  SELECT id, date FROM matches
   WHERE status = 'finished' AND score_home IS NOT NULL AND date >= %(a)s AND date < %(b)s
), s AS (
  SELECT o.match_id, o.bookmaker, o.selection, o.handicap_line AS line, o.odds::float AS odds, o.timestamp AS ts
    FROM odds_snapshots o JOIN m ON m.id = o.match_id
   WHERE o.market = 'asian_handicap' AND NOT coalesce(o.is_live, false)
     AND o.timestamp >= %(a)s - interval '4 days' AND o.timestamp < m.date
     AND o.handicap_line IS NOT NULL AND o.bookmaker <> 'Unibet-Kambi'
), g AS (
  SELECT *, CASE WHEN odds = lag(odds) OVER w THEN 0 ELSE 1 END AS new_run
    FROM s WINDOW w AS (PARTITION BY match_id, bookmaker, selection, line ORDER BY ts)
), r AS (
  SELECT *, sum(new_run) OVER (PARTITION BY match_id, bookmaker, selection, line ORDER BY ts) AS run_id FROM g
)
SELECT match_id::text, bookmaker, selection, line::float AS line, min(odds) AS odds,
       min(ts) AS first_ts, max(ts) AS last_ts, count(*) AS n_rows
  FROM r GROUP BY match_id, bookmaker, selection, line, run_id
"""

PIN_SQL = """
SET LOCAL statement_timeout = '600s';
SELECT o.match_id::text, o.selection, o.handicap_line::float AS line, o.odds::float AS odds, o.timestamp AS ts
  FROM odds_snapshots o JOIN matches m ON m.id = o.match_id
 WHERE m.status = 'finished' AND m.score_home IS NOT NULL AND m.date >= %(a)s AND m.date < %(b)s
   AND o.market = 'asian_handicap' AND o.bookmaker = 'Pinnacle' AND NOT coalesce(o.is_live, false)
   AND o.timestamp >= %(a)s - interval '4 days' AND o.timestamp < m.date AND o.handicap_line IS NOT NULL
"""

MATCH_SQL = """
SELECT m.id::text AS match_id, m.date AS kickoff, m.score_home, m.score_away,
       l.name AS league, l.country, l.tier
  FROM matches m LEFT JOIN leagues l ON l.id = m.league_id
 WHERE m.status = 'finished' AND m.score_home IS NOT NULL AND m.date >= %(a)s AND m.date < %(b)s
"""


def weeks(since: date, until: date):
    d = since
    while d < until:
        e = min(d + timedelta(days=7), until)
        yield (datetime(d.year, d.month, d.day, tzinfo=timezone.utc),
               datetime(e.year, e.month, e.day, tzinfo=timezone.utc))
        d = e


def fetch(conn, sql: str, params: dict) -> pd.DataFrame:
    with conn.cursor() as cur:
        stmts = [x for x in sql.split(";") if x.strip()]
        for st in stmts[:-1]:
            cur.execute(st)
        cur.execute(stmts[-1], params)
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-01")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    since = date.fromisoformat(a.since)
    until = datetime.now(timezone.utc).date()
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    conn.set_session(readonly=True, autocommit=False)
    runs, pins, ms = [], [], []
    for wa, wb in weeks(since, until):
        p = {"a": wa, "b": wb}
        r = fetch(conn, RUNS_SQL, p); conn.rollback()
        q = fetch(conn, PIN_SQL, p); conn.rollback()
        mm = fetch(conn, MATCH_SQL, p); conn.rollback()
        runs.append(r); pins.append(q); ms.append(mm)
        print(f"{wa:%Y-%m-%d}: matches {len(mm):>5}  runs {len(r):>8}  pinnacle rows {len(q):>7}", flush=True)
    pd.concat(runs, ignore_index=True).to_parquet(f"{OUT}/runs.parquet")
    pd.concat(pins, ignore_index=True).to_parquet(f"{OUT}/pin_fetch.parquet")
    pd.concat(ms, ignore_index=True).to_parquet(f"{OUT}/matches.parquet")
    print("written to", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
