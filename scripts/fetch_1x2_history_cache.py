#!/usr/bin/env python3
"""FETCH 1X2 HISTORY CACHE ([[#141]]) — prior-season results for leagues we only
started tracking recently, written to a RESEARCH CACHE, never to `matches`.

WHY
---
`scripts/ab_1x2_rating_arms.py` found (2026-09-24) that rating accuracy depends
on history depth — dynamic-Poisson log-loss 1.056 when a team has 1-3 prior
matches vs 1.011 at 60+ — and that 97% of the low-depth test rows (4,799 of
4,945) are in leagues first seen in 2026. 655 leagues with an API-Football id
were first seen on/after 2025-10-01; their earlier seasons were never fetched.

WHY A CACHE AND NOT `matches`
-----------------------------
`scripts/backfill_historical.py` writes into `matches`, which feeds public
coverage counts, the track record, MFV builders and settlement sweeps. Whether
the extra history is worth having in production is exactly what the harness has
to measure first, so it lands in `data/models/_research/1x2/af_history.parquet`
(gitignored) and the harness merges it in memory.

Budget: one /fixtures call per league-season, ~3 seasons per league (~2,000 calls
of a 150,000/day plan). Aborts below MIN_REMAINING. Resumable: finished
league-seasons are recorded in the parquet and skipped on re-run.

Usage:
    python3 scripts/fetch_1x2_history_cache.py --since 2025-10-01 --seasons 3
    python3 scripts/fetch_1x2_history_cache.py --dry-run
    python3 scripts/fetch_1x2_history_cache.py --to-db     # cache -> rating_history_results
Read-only against the database except `--to-db`, which upserts the cache into the
private `rating_history_results` table (migration 412) that the shadow job reads.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

CACHE = ROOT / "data" / "models" / "_research" / "1x2"
OUT = CACHE / "af_history.parquet"
DONE = CACHE / "af_history_done.parquet"
MIN_REMAINING = 20_000
FINISHED = {"FT", "AET", "PEN"}


def _rows(fixtures: list[dict], league_af: int, season: int) -> list[dict]:
    out = []
    for f in fixtures:
        st = (f.get("fixture") or {}).get("status", {}).get("short")
        if st not in FINISHED:
            continue
        sc = f.get("score") or {}
        ft, ht = sc.get("fulltime") or {}, sc.get("halftime") or {}
        if ft.get("home") is None or ft.get("away") is None:
            continue
        out.append(dict(
            af_fixture_id=int(f["fixture"]["id"]), af_league_id=league_af, season=season,
            kickoff=f["fixture"]["date"], status=st,
            home_af=int(f["teams"]["home"]["id"]), away_af=int(f["teams"]["away"]["id"]),
            home_name=f["teams"]["home"]["name"], away_name=f["teams"]["away"]["name"],
            gh=int(ft["home"]), ga=int(ft["away"]),          # 90-minute result: what 1X2 settles on
            hh=ht.get("home"), ha=ht.get("away"),
        ))
    return out


def to_db() -> int:
    import psycopg2
    from psycopg2.extras import execute_values
    h = pd.read_parquet(OUT)
    h["kickoff"] = pd.to_datetime(h["kickoff"], utc=True)
    cols = ["af_fixture_id", "af_league_id", "season", "kickoff", "home_af", "away_af",
            "home_name", "away_name", "gh", "ga", "hh", "ha"]
    rows = [tuple(None if pd.isna(v) else (int(v) if isinstance(v, float) and float(v).is_integer() else v)
                  for v in r) for r in h[cols].itertuples(index=False)]
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    with c, c.cursor() as cur:
        execute_values(cur, f"""
            INSERT INTO rating_history_results ({", ".join(cols)}) VALUES %s
            ON CONFLICT (af_fixture_id) DO UPDATE SET gh = EXCLUDED.gh, ga = EXCLUDED.ga,
                hh = EXCLUDED.hh, ha = EXCLUDED.ha, fetched_at = now()""", rows, page_size=5000)
    c.close()
    print(f"upserted {len(rows):,} rows into rating_history_results")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-10-01", help="leagues first seen on/after this date")
    ap.add_argument("--seasons", type=int, default=3, help="current season and N-1 before it")
    ap.add_argument("--max-calls", type=int, default=3000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--to-db", action="store_true")
    a = ap.parse_args()
    if a.to_db:
        return to_db()

    import psycopg2
    from workers.api_clients.api_football import get_fixtures_by_league_season, get_remaining_requests
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    leagues = pd.read_sql("""
        SELECT l.api_football_id af, l.af_season_current cur, min(m.date) first_seen
          FROM matches m JOIN leagues l ON l.id = m.league_id
         WHERE m.status = 'finished' AND l.api_football_id IS NOT NULL
         GROUP BY 1, 2 HAVING min(m.date) >= %(s)s""", c, params={"s": a.since})
    c.close()
    todo = []
    for r in leagues.itertuples():
        cur = int(r.cur) if pd.notna(r.cur) else int(str(r.first_seen)[:4])
        todo += [(int(r.af), s) for s in range(cur - a.seasons + 1, cur + 1)]
    done = set()
    if DONE.exists():
        d = pd.read_parquet(DONE)
        done = set(zip(d.af_league_id, d.season))
    todo = [t for t in todo if t not in done]
    print(f"{len(leagues)} leagues, {len(todo)} league-seasons to fetch (skipping {len(done)} done)")
    if a.dry_run:
        return 0
    rem = get_remaining_requests()["remaining"]
    if rem < MIN_REMAINING:
        print(f"only {rem} AF requests left today (< {MIN_REMAINING}) — aborting"); return 1

    CACHE.mkdir(parents=True, exist_ok=True)
    rows, done_rows, calls = [], [], 0
    prev = pd.read_parquet(OUT) if OUT.exists() else pd.DataFrame()
    prev_done = pd.read_parquet(DONE) if DONE.exists() else pd.DataFrame()

    def flush():
        allr = pd.concat([prev, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("af_fixture_id")
        allr.to_parquet(OUT)
        pd.concat([prev_done, pd.DataFrame(done_rows)], ignore_index=True).to_parquet(DONE)
        return len(allr)

    for i, (lg, season) in enumerate(todo):
        if calls >= a.max_calls:
            break
        try:
            fx = get_fixtures_by_league_season(lg, season)
        except Exception as e:                        # one bad league must not stop the run
            print(f"  {lg}/{season}: {e}"); fx = None
        calls += 1
        if fx is not None:
            rows += _rows(fx, lg, season)
            done_rows.append(dict(af_league_id=lg, season=season, n_fixtures=len(fx)))
        if i % 100 == 99:
            print(f"  {i + 1}/{len(todo)} calls, {len(rows):,} finished fixtures so far (cache {flush():,})", flush=True)
    n = flush()
    print(f"done: {calls} calls, {len(rows):,} new finished fixtures; cache holds {n:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
