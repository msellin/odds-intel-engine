#!/usr/bin/env python3
"""DATA-COVERAGE-REPORT — what we actually hold, field by field, and what changed.

Owner (2026-09-23): *"what i want to do is start backfills and measure actually
with some intervals how much and what data keeps flowing in"*.

WHY THIS EXISTS RATHER THAN A ONE-OFF QUERY
-------------------------------------------
Three separate coverage facts were discovered by hand in one afternoon, each of
which had been true and invisible for weeks:

  * API-Football silently WITHDREW `expected_goals` from whole leagues -- daily
    xG on our stats rows fell from 109/156 (2026-08-30) to 0-1/day across
    2026-09-04..08 while stats-row volume held steady, and nothing noticed.
  * `Shots insidebox` / `Shots outsidebox` had been arriving on every response
    since 2018 and were never stored.
  * `get_fixture_statistics` sent `half=true`, which returns `results: 0` rather
    than degrading -- so 2018-2023 fixtures looked like they had no statistics
    at all.

Every one of those is a COVERAGE question, and none of them was visible anywhere.
This makes them visible, on demand and in one screen.

Run it before a backfill, then again after, then weekly. `--save` appends a
timestamped snapshot so the deltas are real measurements rather than memory.

Usage:
    python3 scripts/data_coverage_report.py
    python3 scripts/data_coverage_report.py --save
    python3 scripts/data_coverage_report.py --days 30      # recent window only
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402

SNAPSHOT = pathlib.Path(__file__).parent.parent / "data" / "coverage_snapshots.jsonl"

# (label, table, column) — NULL-count coverage against the finished-match base.
STATS_FIELDS = [
    ("shots",             "shots_home"),
    ("shots on target",   "shots_on_target_home"),
    ("shots INSIDE box",  "shots_insidebox_home"),
    ("shots OUTSIDE box", "shots_outsidebox_home"),
    ("shots off target",  "shots_off_target_home"),
    ("blocked shots",     "blocked_shots_home"),
    ("corners",           "corners_home"),
    ("free kicks",        "free_kicks_home"),
    ("possession",        "possession_home"),
    ("passes",            "passes_home"),
    ("pass %",            "pass_pct_home"),
    ("xG",                "xg_home"),
    ("goals prevented",   "goals_prevented_home"),
]


def _exists(col: str) -> bool:
    return bool(execute_query(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='match_stats' AND column_name=%s", (col,)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0,
                    help="restrict to matches in the last N days (0 = all time)")
    ap.add_argument("--save", action="store_true",
                    help="append a timestamped snapshot so the next run can show deltas")
    a = ap.parse_args()

    where = "m.status='finished'"
    params: tuple = ()
    if a.days:
        where += " AND m.date > now() - interval '%s days'" % a.days
    base = execute_query(f"SELECT count(*) n FROM matches m WHERE {where}", params)[0]["n"]

    scope = f"last {a.days}d" if a.days else "all time"
    print(f"\nDATA COVERAGE — {scope} — {base:,} finished matches")
    print("=" * 72)

    snap = {"at": datetime.now(timezone.utc).isoformat(), "scope": scope, "base": base}

    # ── match_stats field-by-field ────────────────────────────────────────────
    live = [(lbl, col) for lbl, col in STATS_FIELDS if _exists(col)]
    missing_cols = [col for lbl, col in STATS_FIELDS if not _exists(col)]
    sel = ", ".join(f"count(s.{c}) AS \"{c}\"" for _, c in live)
    row = execute_query(f"""
        SELECT count(s.match_id) AS any_stats, {sel}
          FROM matches m LEFT JOIN match_stats s ON s.match_id = m.id
         WHERE {where}""", params)[0]

    print(f"\nmatch_stats — any row: {row['any_stats']:,} ({100*row['any_stats']/base:.1f}%)")
    for lbl, col in live:
        n = row[col]
        bar = "█" * int(30 * n / base) if base else ""
        print(f"   {lbl:<18} {n:>8,}  {100*n/base:>5.1f}%  {bar}")
        snap[col] = n
    if missing_cols:
        print(f"   ⚠️  columns not in the schema yet: {', '.join(missing_cols)}")

    # ── the other enrichment tables ───────────────────────────────────────────
    print("\nother enrichment (distinct matches)")
    for tbl in ("match_events", "match_player_stats", "match_injuries"):
        try:
            n = execute_query(f"""SELECT count(DISTINCT t.match_id) n FROM {tbl} t
                JOIN matches m ON m.id = t.match_id WHERE {where}""", params)[0]["n"]
        except Exception:
            continue
        bar = "█" * int(30 * n / base) if base else ""
        print(f"   {tbl:<18} {n:>8,}  {100*n/base:>5.1f}%  {bar}")
        snap[tbl] = n

    for lbl, col in (("lineups (JSONB)", "lineups_home"), ("half-time score", "ht_score_home")):
        n = execute_query(f"SELECT count(m.{col}) n FROM matches m WHERE {where}", params)[0]["n"]
        bar = "█" * int(30 * n / base) if base else ""
        print(f"   {lbl:<18} {n:>8,}  {100*n/base:>5.1f}%  {bar}")
        snap[col] = n

    # ── deltas against the previous snapshot ──────────────────────────────────
    if SNAPSHOT.exists():
        prev = None
        for line in SNAPSHOT.read_text().splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("scope") == scope:
                prev = d
        if prev:
            moved = [(k, snap[k] - prev[k]) for k in snap
                     if k in prev and isinstance(snap.get(k), int)
                     and isinstance(prev.get(k), int) and snap[k] != prev[k]]
            print(f"\nCHANGE since {prev['at'][:19]}Z")
            if not moved:
                print("   nothing moved — if a backfill is running, that is a problem")
            for k, d in sorted(moved, key=lambda x: -abs(x[1])):
                print(f"   {k:<24} {d:+,}")

    if a.save:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        with SNAPSHOT.open("a") as fh:
            fh.write(json.dumps(snap) + "\n")
        print(f"\nsnapshot appended to {SNAPSHOT}")
    else:
        print("\n(run with --save to record a snapshot and get deltas next time)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
