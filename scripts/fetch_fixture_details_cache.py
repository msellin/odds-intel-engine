#!/usr/bin/env python3
"""FETCH FIXTURE DETAILS CACHE ([[#141]] round 3c) — lineups + player stats + match stats
for every finished fixture we know, into a RESEARCH CACHE (never `matches` or
`match_player_stats`).

Why: round 3c tests whether a player-strength input (the confirmed XI's rating)
improves the combined 1X2 model — the literature's strongest remaining lever
(Arntzen & Hvattum 2021; Holmes & McHale 2024). Existing coverage is far too thin
(8,561 matches in match_player_stats, 20-46% of even the best leagues), and a player
rating needs unbroken match history.

Cost: `/fixtures?ids=` returns up to 20 fixtures per call WITH nested lineups,
players, events and statistics (verified 2026-09-24 on 2024 League One/Two), so
~433k fixtures (175k in `matches` + 258k in `rating_history_results`) is ~22k calls.

Guards: stops when AF's remaining quota < --reserve (default 20,000) or at
--stop-utc (default 23:45, before the daily reset), and paces itself to
--per-min (default 350) so the live pipeline keeps most of the 900/min limit.
Resumable: parts are written every 100 calls; finished fixture ids are skipped.

    python3 scripts/fetch_fixture_details_cache.py --dry-run
    python3 scripts/fetch_fixture_details_cache.py --max-calls 25000
Read-only against the database.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

OUT = ROOT / "data" / "models" / "_research" / "1x2" / "fixture_details"


def _num(v):
    try:
        return float(str(v).rstrip("%")) if v not in (None, "") else None
    except ValueError:
        return None


def parse(fx: dict) -> tuple[list, list, list]:
    fid = int(fx["fixture"]["id"])
    players, lineups, stats = [], [], []
    for t in fx.get("players") or []:
        tid = (t.get("team") or {}).get("id")
        for p in t.get("players") or []:
            s = (p.get("statistics") or [{}])[0]
            g = s.get("games") or {}
            players.append(dict(fixture_id=fid, team_id=tid, player_id=(p.get("player") or {}).get("id"),
                                position=g.get("position"), minutes=g.get("minutes"),
                                rating=_num(g.get("rating")), substitute=g.get("substitute"),
                                goals=(s.get("goals") or {}).get("total"),
                                assists=(s.get("goals") or {}).get("assists")))
    for lu in fx.get("lineups") or []:
        lineups.append(dict(fixture_id=fid, team_id=(lu.get("team") or {}).get("id"),
                            formation=lu.get("formation"),
                            xi=",".join(str((x.get("player") or {}).get("id")) for x in lu.get("startXI") or []),
                            subs=",".join(str((x.get("player") or {}).get("id")) for x in lu.get("substitutes") or [])))
    for st in fx.get("statistics") or []:
        d = {x.get("type"): x.get("value") for x in st.get("statistics") or []}
        stats.append(dict(fixture_id=fid, team_id=(st.get("team") or {}).get("id"),
                          shots_on=_num(d.get("Shots on Goal")), shots=_num(d.get("Total Shots")),
                          xg=_num(d.get("expected_goals")), possession=_num(d.get("Ball Possession"))))
    return players, lineups, stats


def todo_ids() -> list[int]:
    import psycopg2
    c = psycopg2.connect(os.environ["DATABASE_URL"])
    with c.cursor() as cur:
        cur.execute("""SELECT api_football_id FROM matches
                        WHERE status = 'finished' AND api_football_id IS NOT NULL AND date >= '2022-01-01'
                        ORDER BY date DESC""")
        ids = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT af_fixture_id FROM rating_history_results ORDER BY kickoff DESC")
        seen = set(ids)
        ids += [r[0] for r in cur.fetchall() if r[0] not in seen]
    c.close()
    done = set()
    for p in OUT.glob("done_*.parquet"):
        done |= set(pd.read_parquet(p).fixture_id)
    return [i for i in ids if i not in done]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=25000)
    ap.add_argument("--reserve", type=int, default=20000)
    ap.add_argument("--per-min", type=int, default=350)
    ap.add_argument("--stop-utc", default="23:45")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from workers.api_clients.api_football import get_fixtures_batch, get_remaining_requests
    ids = todo_ids()
    print(f"{len(ids):,} fixtures to fetch = {-(-len(ids) // 20):,} calls", flush=True)
    if a.dry_run:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    stop_h, stop_m = map(int, a.stop_utc.split(":"))
    part = len(list(OUT.glob("done_*.parquet")))
    buf_p, buf_l, buf_s, buf_d = [], [], [], []
    calls, t_last = 0, 0.0

    def flush():
        nonlocal part, buf_p, buf_l, buf_s, buf_d
        if not buf_d:
            return
        for name, rows in (("players", buf_p), ("lineups", buf_l), ("stats", buf_s), ("done", buf_d)):
            pd.DataFrame(rows).to_parquet(OUT / f"{name}_{part:05d}.parquet")
        part += 1
        buf_p, buf_l, buf_s, buf_d = [], [], [], []

    for i in range(0, len(ids), 20):
        now = datetime.now(timezone.utc)
        if calls >= a.max_calls or (now.hour, now.minute) >= (stop_h, stop_m):
            print(f"stop: calls={calls} time={now:%H:%M}", flush=True); break
        if calls % 100 == 0:
            rem = get_remaining_requests()["remaining"]
            if rem < a.reserve:
                print(f"stop: remaining {rem} < reserve {a.reserve}", flush=True); break
        wait = 60.0 / a.per_min - (time.time() - t_last)
        if wait > 0:
            time.sleep(wait)
        t_last = time.time()
        chunk = ids[i:i + 20]
        res = get_fixtures_batch(chunk)
        calls += 1
        for fid, fx in res.items():
            p, l, s = parse(fx)
            buf_p += p; buf_l += l; buf_s += s
        buf_d += [dict(fixture_id=f, got=f in res) for f in chunk]
        if calls % 100 == 0:
            flush()
            print(f"  {calls:,} calls, {i + len(chunk):,}/{len(ids):,} fixtures", flush=True)
    flush()
    print(f"done: {calls:,} calls", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
