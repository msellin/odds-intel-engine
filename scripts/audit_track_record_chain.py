"""
GROWTH-TRACK-RECORD-CONTINUITY — audit the paper-bet chain.

Walks the daily count of `simulated_bets` rows and flags gaps (0-day) or
anomalous-low days (< threshold). Use to verify the public track-record
chain is unbroken before publishing claims like "tracked across N matches
since YYYY."

Usage:
    python scripts/audit_track_record_chain.py                  # last 60 days
    python scripts/audit_track_record_chain.py --days 14        # last 14 days
    python scripts/audit_track_record_chain.py --since 2026-05-01

Exits non-zero if any gap (0-day post-launch) is found.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

# Launch date — the bot chain officially started this day. Anything before
# this is "no bots yet", not "broken chain". See CLAUDE.md.
LAUNCH_DATE = date(2026, 4, 27)
LOW_THRESHOLD = 5


def _conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    return psycopg2.connect(url)


def fetch_daily_counts(since: date, until: date) -> list[tuple[date, int]]:
    sql = """
    WITH days AS (
      SELECT generate_series(%s::date, %s::date, INTERVAL '1 day')::date AS d
    ),
    counts AS (
      SELECT pick_time::date AS d, COUNT(*) AS n
      FROM simulated_bets
      WHERE pick_time::date BETWEEN %s AND %s
      GROUP BY pick_time::date
    )
    SELECT days.d, COALESCE(counts.n, 0) AS picks
    FROM days
    LEFT JOIN counts ON counts.d = days.d
    ORDER BY days.d;
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (since, until, since, until))
        return [(row[0], int(row[1])) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60,
                        help="Window length in days back from today (default 60)")
    parser.add_argument("--since", default=None,
                        help="Override start date (YYYY-MM-DD)")
    parser.add_argument("--threshold", type=int, default=LOW_THRESHOLD,
                        help=f"Daily count below this triggers a warning (default {LOW_THRESHOLD})")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress per-day output; show only gaps + summary")
    args = parser.parse_args()

    today = date.today()
    since = (
        date.fromisoformat(args.since)
        if args.since
        else today - timedelta(days=args.days)
    )

    counts = fetch_daily_counts(since, today)

    gaps_post_launch: list[date] = []
    weak_days: list[tuple[date, int]] = []
    pre_launch_days = 0
    healthy_days = 0
    total_picks = 0

    for d, n in counts:
        total_picks += n
        if d < LAUNCH_DATE:
            pre_launch_days += 1
            continue
        if n == 0:
            gaps_post_launch.append(d)
        elif n < args.threshold:
            weak_days.append((d, n))
        else:
            healthy_days += 1

    if not args.quiet:
        for d, n in counts:
            tag = ""
            if d < LAUNCH_DATE:
                tag = "(pre-launch)"
            elif n == 0:
                tag = "❌ CHAIN BROKEN"
            elif n < args.threshold:
                tag = "⚠️ WEAK"
            bar = "█" * min(n, 50)
            print(f"  {d}: {n:>4}  {bar} {tag}")

    print()
    print(f"Window: {since} → {today} ({len(counts)} days)")
    print(f"  Pre-launch days (excluded): {pre_launch_days}")
    print(f"  Healthy days (≥{args.threshold} picks): {healthy_days}")
    print(f"  Weak days (1..{args.threshold - 1} picks): {len(weak_days)}")
    print(f"  Broken days (0 picks, post-launch): {len(gaps_post_launch)}")
    print(f"  Total picks in window: {total_picks:,}")

    if gaps_post_launch:
        print()
        print("CHAIN GAPS (these days are permanently lost from public history):")
        for d in gaps_post_launch:
            print(f"  - {d}")

    if weak_days:
        print()
        print(f"WEAK DAYS (count < {args.threshold} — investigate):")
        for d, n in weak_days:
            print(f"  - {d}: {n} picks")

    if gaps_post_launch:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
