"""
Generate targets_extended.csv — historical match data for Poisson training
from all AF-backed leagues not already in targets_poisson_history.csv or
targets_global.csv.

Uses a single PostgreSQL COPY TO STDOUT — no Python row loops.
Output: data/processed/targets_extended.csv

Usage:
    python scripts/generate_targets_extended.py
    python scripts/generate_targets_extended.py --min-matches 20
    python scripts/generate_targets_extended.py --dry-run
"""

import sys
import io
import argparse
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    Progress, BarColumn, MofNCompleteColumn,
    TimeRemainingColumn, SpinnerColumn, TextColumn,
)

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from workers.api_clients.db import get_conn

console = Console()

# AF league IDs already covered by targets_poisson_history.csv and targets_global.csv.
# Exporting them again is harmless but wastes space — skip them.
ALREADY_COVERED_AF_IDS = frozenset([
    # Phase 1
    39, 140, 78, 135, 61, 88, 94, 203, 144, 179, 113, 103, 119, 218, 207, 71, 128, 253, 262,
    # Phase 2
    40, 141, 79, 136, 62, 89, 95, 204, 145, 235, 197, 333, 106, 345, 283, 292, 98,
    188, 210, 271, 286, 169, 72, 129, 41, 42, 180,
    # Phase 3
    43, 142, 80, 137, 63, 114, 104, 120, 219, 208, 234, 332, 107, 346, 181, 182,
    73, 130, 265, 239, 99, 293, 284,
])

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "processed" / "targets_extended.csv"


class _StreamingWriter:
    """File-like object that buffers COPY output and counts rows for the progress bar."""

    def __init__(self, bar, task_id: int, total: int):
        self._buf = io.BytesIO()
        self._bar = bar
        self._task_id = task_id
        self._total = total
        self._rows = 0
        self._header_skipped = False

    def write(self, data: bytes) -> int:
        self._buf.write(data)
        newlines = data.count(b"\n")
        if not self._header_skipped and newlines:
            # First newline is the CSV header — don't count it as a data row
            newlines -= 1
            self._header_skipped = True
        if newlines > 0:
            self._rows += newlines
            self._bar.update(self._task_id, completed=min(self._rows, self._total))
        return len(data)

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _count_eligible(cur, excluded_ids: frozenset, min_matches: int) -> tuple[int, list[int]]:
    """Return (total_rows, eligible_af_ids) for the COPY query."""
    excl_str = ", ".join(str(x) for x in sorted(excluded_ids)) if excluded_ids else "0"
    cur.execute(
        f"""
        SELECT l.api_football_id, COUNT(*) AS cnt
        FROM matches m
        JOIN leagues l ON l.id = m.league_id
        WHERE m.status = 'finished'
          AND m.score_home IS NOT NULL
          AND m.score_away IS NOT NULL
          AND m.result IS NOT NULL
          AND l.api_football_id IS NOT NULL
          AND l.api_football_id NOT IN ({excl_str})
        GROUP BY l.api_football_id
        HAVING COUNT(*) >= %s
        ORDER BY cnt DESC
        """,
        [min_matches],
    )
    rows = cur.fetchall()
    eligible_ids = [r[0] for r in rows]
    total_rows = sum(r[1] for r in rows)
    return total_rows, eligible_ids


def _build_copy_sql(eligible_ids: list[int]) -> str:
    ids_str = ", ".join(str(x) for x in eligible_ids)
    return f"""
        COPY (
            SELECT
                m.date::date                        AS "Date",
                ht.name                             AS home_team,
                at.name                             AS away_team,
                CASE m.result
                    WHEN 'home' THEN 'H'
                    WHEN 'draw' THEN 'D'
                    WHEN 'away' THEN 'A'
                END                                 AS result,
                m.score_home::float                 AS "FTHG",
                m.score_away::float                 AS "FTAG",
                (m.score_home + m.score_away)::float AS total_goals,
                CASE WHEN (m.score_home + m.score_away) > 2 THEN 1 ELSE 0 END AS over_25,
                CASE WHEN m.score_home > 0 AND m.score_away > 0 THEN 1 ELSE 0 END AS btts,
                'AF_' || l.api_football_id::text    AS league_code,
                l.name                              AS league,
                l.country                           AS country
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            JOIN leagues l  ON l.id  = m.league_id
            WHERE m.status = 'finished'
              AND m.score_home IS NOT NULL
              AND m.score_away IS NOT NULL
              AND m.result    IS NOT NULL
              AND l.api_football_id IN ({ids_str})
            ORDER BY m.date
        ) TO STDOUT WITH CSV HEADER
    """


def generate(min_matches: int = 10, dry_run: bool = False) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            console.print("[bold]Counting eligible leagues…[/bold]")
            total_rows, eligible_ids = _count_eligible(cur, ALREADY_COVERED_AF_IDS, min_matches)

            if not eligible_ids:
                console.print("[yellow]No eligible leagues found — nothing to export.[/yellow]")
                return

            console.print(
                f"Found [bold]{len(eligible_ids)}[/bold] leagues · "
                f"[bold]{total_rows:,}[/bold] rows to export "
                f"(min {min_matches} matches per league)"
            )

            if dry_run:
                console.print("[yellow]Dry run — no file written.[/yellow]")
                # Print league breakdown
                excl_str = ", ".join(str(x) for x in sorted(ALREADY_COVERED_AF_IDS))
                cur.execute(
                    f"""
                    SELECT l.api_football_id, l.name, l.country, COUNT(*) AS cnt
                    FROM matches m
                    JOIN leagues l ON l.id = m.league_id
                    WHERE m.status = 'finished'
                      AND m.score_home IS NOT NULL
                      AND l.api_football_id NOT IN ({excl_str})
                    GROUP BY l.api_football_id, l.name, l.country
                    HAVING COUNT(*) >= %s
                    ORDER BY cnt DESC
                    LIMIT 30
                    """,
                    [min_matches],
                )
                for row in cur.fetchall():
                    console.print(f"  AF{row[0]:>5}  {row[2]:<20}  {row[1]:<40}  {row[3]:>5} matches")
                return

            copy_sql = _build_copy_sql(eligible_ids)

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn("[progress.percentage]{task.percentage:>5.1f}%"),
                TimeRemainingColumn(),
                console=console,
                transient=False,
            ) as bar:
                task_id = bar.add_task("Exporting rows", total=max(total_rows, 1))
                writer = _StreamingWriter(bar, task_id, total_rows)
                cur.copy_expert(copy_sql, writer)
                bar.update(task_id, completed=total_rows)

            csv_bytes = writer.getvalue()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(csv_bytes)

    size_kb = len(csv_bytes) / 1024
    line_count = csv_bytes.count(b"\n") - 1  # subtract header
    console.print(
        f"\n[bold green]✓ Written:[/bold green] {OUTPUT_PATH}\n"
        f"  {line_count:,} data rows · {size_kb:,.0f} KB"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export historical DB matches to targets_extended.csv for Poisson training"
    )
    parser.add_argument(
        "--min-matches", type=int, default=10,
        help="Minimum finished matches a league must have to be included (default 10)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print league breakdown, do not write file",
    )
    args = parser.parse_args()
    generate(min_matches=args.min_matches, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
