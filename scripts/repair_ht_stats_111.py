#!/usr/bin/env python3
"""REPAIR `_ht` STATS WRITTEN FROM THE FULL-MATCH BLOCK ([[#111]], 2026-09-24).

`parse_fixture_stats_halftime` fell back to a team's full-match `statistics` when the
response had no `statistics_1h`. Settlement mostly parses the batch `fixtures?ids=`
response, which never carries the half split, so from ~2026-08-31 the `_ht` columns
of most new rows hold FULL-MATCH values (650 of 652 rows in the week of 09-14 had
shots and corners at half-time exactly equal to full time).

The normal upsert cannot fix this: it is COALESCE, so it never replaces a stored value
with NULL, and a stored wrong value survives any re-fetch that lacks the split. This
script therefore OVERWRITES the `_ht` columns directly: with the real first-half split
when AF returns one (`half=true`), and with NULL when it does not — the stored numbers
are known to be wrong either way.

Candidates: rows since --since whose home/away shots AND home corners at half-time
equal full time (a genuine match with zero second-half shots for both sides and no
second-half home corner is rare — 4 of 544 in the week before the bug — and those
rows are simply re-fetched and restored correctly).

    python3 scripts/repair_ht_stats_111.py --dry-run
    python3 scripts/repair_ht_stats_111.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CANDIDATES = """
    SELECT m.id::text match_id, m.api_football_id af
      FROM matches m JOIN match_stats ms ON ms.match_id = m.id
     WHERE m.date >= %s::timestamptz AND m.api_football_id IS NOT NULL
       AND ms.shots_home_ht = ms.shots_home AND ms.shots_away_ht = ms.shots_away
       AND ms.corners_home_ht = ms.corners_home
     ORDER BY m.date"""


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(".env")
    from workers.api_clients.api_football import (
        budget, get_fixture_statistics, parse_fixture_stats_halftime)
    from workers.api_clients.db import execute_query, get_conn
    from workers.api_clients.supabase_client import _MATCH_STATS_FIELDS

    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-17")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ht_cols = [f for f in _MATCH_STATS_FIELDS if f.endswith("_ht")]
    rows = execute_query(CANDIDATES, (a.since,))
    print(f"{len(rows)} rows with half-time == full-time since {a.since}; {len(ht_cols)} _ht columns")
    if a.dry_run:
        return 0
    restored = blanked = errors = 0
    set_sql = ", ".join(f"{c} = %s" for c in ht_cols)
    with get_conn() as conn:
        with conn.cursor() as cur:
            for i, r in enumerate(rows):
                if not budget.can_call():
                    print("budget exhausted — stopping; re-run to continue")
                    break
                try:
                    ht = parse_fixture_stats_halftime(get_fixture_statistics(int(r["af"])))
                except Exception as e:
                    errors += 1
                    print(f"  fixture {r['af']}: {e}")
                    continue
                cur.execute(f"UPDATE match_stats SET {set_sql} WHERE match_id = %s",
                            [ht.get(c) for c in ht_cols] + [r["match_id"]])
                if ht:
                    restored += 1
                else:
                    blanked += 1
                if i % 100 == 99:
                    conn.commit()
                    print(f"  {i + 1}/{len(rows)}  restored {restored}  blanked {blanked}", flush=True)
            conn.commit()
    print(f"done: restored {restored} with the real first-half split, blanked {blanked}, errors {errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
