#!/usr/bin/env python3
"""OU-LINE-BACKFILL-2026-09-11 — populate odds_snapshots.handicap_line for the
goals over/under families, where the market name determines it unambiguously.

WHY THIS IS A SCRIPT AND NOT A MIGRATION
It was written as migration 332 first. That was wrong twice over:

  * `migrate.yml` sets `statement_timeout=600000` (10 min) inside a 15-minute
    job. A dry run of the single UPDATE was still going at 6 minutes on the
    first of two statements — it would have hit the timeout, failed the
    migration, and left the table half-done with the file marked unapplied.
  * `odds_snapshots` is hot: the AF sweep writes every 30 minutes and three
    direct scrapers write continuously. One 8M-row transaction holds row locks
    for the duration, so a slow migration would stall live odds ingestion.

There is also no correctness reason to rush it. The writers now emit the number
(api_football.py, coolbet_explorer.py), so every NEW row is unambiguous; this is
consistency work on history, which can take as long as it likes. Batched,
committed per batch, resumable, and safe to run repeatedly.

WHAT IT DOES NOT DO
It does not guess. `str(float(line)).replace('.','')` is not reversible: "275"
is 2.75 or 27.5, "125" is 1.25 or 12.5. 27.5 goals is absurd and 2.75 is
"obviously" intended — but a backtest once picked the obvious-looking reading of
`over_under_1h_125`, settled 634 bets against it and lost every one
(MARKET-LINE-ENCODING-LOSSY-2026-09-06). So ~252k rows across 35 ambiguous
markets are left NULL deliberately. Honestly absent beats confidently wrong.

RECOVERY RULE — safe because a line below 1.0 keeps its leading zero
(`str(0.75)` -> "075", three chars; `str(7.5)` -> "75", two), so length
disambiguates those:

    token starts with '0', 3 chars -> /100    "075"  -> 0.75
    token starts with '0'          -> /10     "05"   -> 0.5
    token <= 2 chars               -> /10     "25"   -> 2.5
    token == 3 chars, no leading 0 -> AMBIGUOUS, skipped
    token >= 4 chars               -> /100    "1275" -> 12.75

Verified before running: on every (market, handicap_line) pair that ALREADY
carries a line, this rule reproduces the stored value exactly — 0 mismatches.

Usage:
    python3 scripts/backfill_ou_handicap_line.py --dry-run
    python3 scripts/backfill_ou_handicap_line.py --batch-size 50000
"""
import argparse, os, sys, time
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# The predicate and the derivation, written once and shared by the dry run and
# the real run so they cannot disagree about what would be touched.
_TOKEN = "substring(market from (CASE WHEN market LIKE 'over_under_1h_%' THEN 15 ELSE 12 END))"
_DERIVE = f"""
    CASE
      WHEN {_TOKEN} LIKE '0%' AND length({_TOKEN}) = 3 THEN {_TOKEN}::numeric / 100
      WHEN {_TOKEN} LIKE '0%'                          THEN {_TOKEN}::numeric / 10
      WHEN length({_TOKEN}) <= 2                       THEN {_TOKEN}::numeric / 10
      WHEN length({_TOKEN}) >= 4                       THEN {_TOKEN}::numeric / 100
    END"""
_ELIGIBLE = f"""
      market ~ '^over_under_(1h_)?[0-9]+$'
      AND handicap_line IS NULL
      AND NOT (length({_TOKEN}) = 3 AND {_TOKEN} NOT LIKE '0%')"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=50_000)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sleep", type=float, default=0.3,
                    help="pause between batches so the live sweeps get the table")
    ap.add_argument("--max-batches", type=int, default=1000)
    args = ap.parse_args()

    con = psycopg2.connect(os.environ["DATABASE_URL"])
    con.autocommit = True
    cur = con.cursor()
    # never let one batch wedge the table
    cur.execute("SET statement_timeout = 120000")
    cur.execute("SET lock_timeout = 10000")

    cur.execute(f"SELECT count(*) FROM odds_snapshots WHERE {_ELIGIBLE}")
    eligible = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM odds_snapshots
                    WHERE market ~ '^over_under_(1h_)?[0-9]+$' AND handicap_line IS NULL""")
    all_null = cur.fetchone()[0]
    print(f"eligible (name determines the line): {eligible:,}")
    print(f"ambiguous, deliberately skipped    : {all_null - eligible:,}")

    if args.dry_run:
        cur.execute(f"""SELECT market, {_DERIVE} AS derived, count(*)
                          FROM odds_snapshots WHERE {_ELIGIBLE}
                         GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12""")
        print("\nwould set (top 12 by row count):")
        for m, d, n in cur.fetchall():
            print(f"  {m:<22} -> {float(d):>6.2f}   {n:>10,}")
        return 0

    done = t0 = 0
    t0 = time.time()
    for i in range(args.max_batches):
        cur.execute(
            f"""UPDATE odds_snapshots SET handicap_line = {_DERIVE}
                 WHERE id IN (SELECT id FROM odds_snapshots
                               WHERE {_ELIGIBLE} LIMIT {args.batch_size})""")
        n = cur.rowcount
        done += n
        if n == 0:
            break
        rate = done / max(time.time() - t0, 0.001)
        print(f"  batch {i + 1}: {n:,} rows   total {done:,}/{eligible:,} "
              f"({100 * done / max(eligible, 1):.1f} pct, {rate:,.0f} rows/s)", flush=True)
        time.sleep(args.sleep)

    print(f"\ndone: {done:,} rows in {time.time() - t0:.0f}s")
    cur.execute(f"SELECT count(*) FROM odds_snapshots WHERE {_ELIGIBLE}")
    print(f"eligible remaining: {cur.fetchone()[0]:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
