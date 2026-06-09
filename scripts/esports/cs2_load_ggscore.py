#!/usr/bin/env python3
"""
Load a manually-copied GGScore ranking snapshot into cs2_ggscore_rankings.

GGScore is 403'd to scrapers, so the user pastes their CS2 ranking page
periodically. This script accepts either:
  - the rank/team/rating TSV file (one team per line, tab-separated)
  - stdin pasted directly

Usage:
    python3 scripts/esports/cs2_load_ggscore.py --file data/esports/cs2/ggscore_snapshot_YYYY-MM-DD.txt
    python3 scripts/esports/cs2_load_ggscore.py --stdin
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_write


def parse(text: str) -> list[tuple[int, str, int]]:
    """Each non-comment line: rank<TAB>team<TAB>rating. Returns list of (rank, team, rating)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            rank = int(parts[0].strip())
            team = parts[1].strip()
            rating = int(parts[2].strip().replace(",", ""))
        except ValueError:
            continue
        if team:
            out.append((rank, team, rating))
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--file", help="Path to TSV snapshot")
    p.add_argument("--stdin", action="store_true", help="Read from stdin")
    p.add_argument("--date", help="Snapshot date YYYY-MM-DD (default: today UTC)")
    args = p.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    elif args.file:
        text = Path(args.file).read_text()
    else:
        # Default to today's snapshot file under data/esports/cs2/
        today = datetime.now(timezone.utc).date()
        default = Path(f"data/esports/cs2/ggscore_snapshot_{today}.txt")
        if not default.exists():
            print(f"[!] no --file or --stdin and {default} doesn't exist", file=sys.stderr)
            sys.exit(1)
        text = default.read_text()

    rows = parse(text)
    snapshot_date = args.date or datetime.now(timezone.utc).date().isoformat()
    print(f"  parsed {len(rows)} teams from input")
    print(f"  snapshot_date: {snapshot_date}")

    n = 0
    for rank, team, rating in rows:
        execute_write("""
            INSERT INTO cs2_ggscore_rankings (team_name, ggscore_rank, ggscore_rating, snapshot_date)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (team_name, snapshot_date) DO UPDATE SET
                ggscore_rank   = EXCLUDED.ggscore_rank,
                ggscore_rating = EXCLUDED.ggscore_rating
        """, (team, rank, rating, snapshot_date))
        n += 1
    print(f"  wrote {n} rows to cs2_ggscore_rankings")


if __name__ == "__main__":
    main()
