"""Audit unmatched football-data team names from fd_cache CSVs.

Cross-references every unique team name in dev/active/fd_cache/* against
our teams table (with normalize_team_name + TEAM_ALIASES applied). Outputs
the top-N unmatched names by frequency, so we know which aliases would
yield the most additional matched rows.

Run: python3 scripts/audit_unmatched_extras.py [--limit 50]
"""
from __future__ import annotations
import argparse
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import csv
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query
from scripts.ingest_football_data_csvs import TEAM_ALIASES, normalize_team_name, resolve_team

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    # Build DB normalized → team_id map (matches what ingest does)
    rows = execute_query("SELECT id::text AS id, name FROM teams")
    db_normalized = {}
    for r in rows:
        norm = normalize_team_name(r["name"])
        if norm:
            db_normalized.setdefault(norm, r["id"])

    # Scan fd_cache CSVs for unique team names
    cache = Path(__file__).resolve().parent.parent / "dev" / "active" / "fd_cache"
    if not cache.exists():
        console.print(f"[yellow]No fd_cache at {cache} — nothing to audit[/yellow]")
        return
    fd_counts: Counter = Counter()
    files = sorted(cache.rglob("*.csv"))
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    for col in ("HomeTeam", "AwayTeam"):
                        name = row.get(col)
                        if name:
                            fd_counts[name.strip()] += 1
        except Exception:
            continue
    console.print(f"  Scanned {len(files):,} CSVs · {len(fd_counts):,} unique FD team names")

    # Match each against DB using the FULL resolver (alias + normalize + substring + rapidfuzz)
    unmatched: Counter = Counter()
    for fd_name, freq in fd_counts.items():
        if resolve_team(fd_name, db_normalized) is None:
            unmatched[fd_name] = freq

    # Top-N by frequency
    t = Table(title=f"Top {args.limit} unmatched FD team names by row frequency")
    for c in ("fd_name", "n_rows", "normalized"):
        t.add_column(c)
    for fd_name, freq in unmatched.most_common(args.limit):
        norm = normalize_team_name(fd_name)
        t.add_row(fd_name, str(freq), norm)
    console.print(t)
    console.print(f"\nTotal unmatched FD names: {len(unmatched):,}  |  "
                  f"total unmatched rows: {sum(unmatched.values()):,}  |  "
                  f"total matched rows: {sum(fd_counts.values()) - sum(unmatched.values()):,}")


if __name__ == "__main__":
    main()
