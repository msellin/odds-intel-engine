#!/usr/bin/env python3
"""Load a manually-copied egamersworld CS2 ranking snapshot."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workers.api_clients.db import execute_write


def parse(text: str) -> list[tuple[int, str, int]]:
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
    p.add_argument("--file")
    p.add_argument("--stdin", action="store_true")
    p.add_argument("--date")
    args = p.parse_args()

    if args.stdin:
        text = sys.stdin.read()
    elif args.file:
        text = Path(args.file).read_text()
    else:
        today = datetime.now(timezone.utc).date()
        default = Path(f"data/esports/cs2/egamersworld_snapshot_{today}.txt")
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
            INSERT INTO cs2_egamersworld_rankings (team_name, egw_rank, egw_rating, snapshot_date)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (team_name, snapshot_date) DO UPDATE SET
                egw_rank   = EXCLUDED.egw_rank,
                egw_rating = EXCLUDED.egw_rating
        """, (team, rank, rating, snapshot_date))
        n += 1
    print(f"  wrote {n} rows to cs2_egamersworld_rankings")


if __name__ == "__main__":
    main()
