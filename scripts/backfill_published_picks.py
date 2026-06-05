"""
GROWTH-ACCURACY-PICKS-LOG — one-shot backfill of historical picks.

Walks every finished match with ensemble predictions and inserts the top
pick per market into `published_picks` with is_backfilled=true.

These rows are NOT credibility-equivalent to live-published picks (the
public accuracy page MUST label them differently). They give us a base
sample to publish accuracy stats over while the live cron accumulates
real pre-kickoff timestamped picks day-by-day.

Backfilled rows use picked_at = matches.date - INTERVAL '6 hours' as an
approximation of the typical pre-kickoff window. outcome is settled
inline since we know the actual final score.

Run once:
    python scripts/backfill_published_picks.py            # dry-run + count
    python scripts/backfill_published_picks.py --apply    # actually write rows
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


MARKETS: list[dict] = [
    {"market": "1x2", "selections": ["1x2_home", "1x2_draw", "1x2_away"],
     "sel_map": {"1x2_home": "home", "1x2_draw": "draw", "1x2_away": "away"}},
    {"market": "over_under_15", "selections": ["over15", "under15"],
     "sel_map": {"over15": "over", "under15": "under"}},
    {"market": "over_under_25", "selections": ["over25", "under25"],
     "sel_map": {"over25": "over", "under25": "under"}},
    {"market": "btts", "selections": ["btts_yes", "btts_no"],
     "sel_map": {"btts_yes": "yes", "btts_no": "no"}},
]


def _conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    return psycopg2.connect(url)


def _is_hit(market: str, selection: str, score_home: int, score_away: int) -> bool:
    """Pure outcome check. Mirrors publish_daily_picks._is_hit."""
    total = score_home + score_away
    if market == "1x2":
        if selection == "home": return score_home > score_away
        if selection == "draw": return score_home == score_away
        if selection == "away": return score_home < score_away
    elif market == "over_under_15":
        if selection == "over": return total >= 2
        if selection == "under": return total < 2
    elif market == "over_under_25":
        if selection == "over": return total >= 3
        if selection == "under": return total < 3
    elif market == "btts":
        both = score_home >= 1 and score_away >= 1
        if selection == "yes": return both
        if selection == "no": return not both
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Actually write rows. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit the number of MATCHES processed (for testing)")
    args = parser.parse_args()

    sql = """
    SELECT
        m.id            AS match_id,
        m.date          AS kickoff_at,
        m.score_home    AS sh,
        m.score_away    AS sa,
        p.market        AS market,
        p.model_probability AS prob,
        p.model_version AS model_version
    FROM matches m
    JOIN predictions p ON p.match_id = m.id
    WHERE m.status = 'finished'
      AND m.score_home IS NOT NULL
      AND m.score_away IS NOT NULL
      AND p.source = 'ensemble'
      AND p.market IN (
          '1x2_home','1x2_draw','1x2_away',
          'over15','under15','over25','under25',
          'btts_yes','btts_no'
      )
    """
    if args.limit:
        # Wrap the matches selection in a limit; simpler than DISTINCT subquery
        # since we'll group in Python anyway
        sql += " AND m.id IN (SELECT id FROM matches WHERE status='finished' LIMIT %s)"

    with _conn() as conn:
        with conn.cursor() as cur:
            params = (args.limit,) if args.limit else None
            cur.execute(sql, params)
            rows = cur.fetchall()

    # Group by (match_id, model_version)
    by_match: dict[tuple, dict] = defaultdict(dict)
    meta: dict[str, dict] = {}
    for match_id, kickoff_at, sh, sa, market, prob, model_version in rows:
        key = (match_id, model_version)
        by_match[key][market] = float(prob)
        meta[match_id] = {"kickoff_at": kickoff_at, "sh": int(sh), "sa": int(sa)}

    print(f"Found {len(by_match)} match × model pairs across "
          f"{len(meta)} unique matches with ensemble predictions.")

    # Build rows to insert
    pending: list[tuple] = []
    hits = misses = 0
    for (match_id, model_version), probs in by_match.items():
        m = meta[match_id]
        kickoff_at = m["kickoff_at"]
        # Approximate picked_at as kickoff - 6h (typical pre-match window)
        picked_at = kickoff_at - timedelta(hours=6)
        for spec in MARKETS:
            available = {s: probs[s] for s in spec["selections"] if s in probs}
            if not available:
                continue
            top_key, top_prob = max(available.items(), key=lambda kv: kv[1])
            selection = spec["sel_map"][top_key]
            hit = _is_hit(spec["market"], selection, m["sh"], m["sa"])
            outcome = "hit" if hit else "miss"
            if hit: hits += 1
            else:   misses += 1
            pending.append((
                match_id, spec["market"], selection, top_prob,
                model_version, picked_at, kickoff_at, outcome, kickoff_at,
            ))

    total = len(pending)
    rate = (hits / total * 100) if total else 0.0
    print(f"Would insert {total} rows: {hits} hits / {misses} misses ({rate:.1f}% accuracy)")

    if not args.apply:
        print("Dry-run only. Re-run with --apply to write.")
        return 0

    # Batch-insert with SAVEPOINT per row so one bad row doesn't poison the
    # transaction. Commit every BATCH_SIZE rows so partial progress survives
    # any catastrophic failure.
    BATCH_SIZE = 500
    print(f"Writing {total} rows in batches of {BATCH_SIZE} ...")
    inserted = 0
    failed = 0
    with _conn() as conn:
        with conn.cursor() as cur:
            for i, row in enumerate(pending):
                cur.execute("SAVEPOINT row_save")
                try:
                    cur.execute(
                        """
                        INSERT INTO published_picks
                            (match_id, market, selection, model_probability,
                             model_version, picked_at, kickoff_at,
                             outcome, settled_at, is_backfilled)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (match_id, market, model_version) DO NOTHING
                        """,
                        row,
                    )
                    inserted += cur.rowcount
                    cur.execute("RELEASE SAVEPOINT row_save")
                except Exception as e:
                    cur.execute("ROLLBACK TO SAVEPOINT row_save")
                    failed += 1
                    if failed <= 5:
                        print(f"  insert failed for {row[0]} {row[1]}: {e}")
                # Commit every batch to checkpoint progress
                if (i + 1) % BATCH_SIZE == 0:
                    conn.commit()
                    print(f"  {i + 1}/{total} processed, {inserted} inserted, {failed} failed")
            conn.commit()
    print(f"Inserted {inserted} new rows. {failed} failed. "
          f"({total - inserted - failed} were already present due to UNIQUE constraint.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
