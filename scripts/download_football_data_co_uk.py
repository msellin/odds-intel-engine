"""
Download historical match + odds CSVs from football-data.co.uk.

Output: data/raw/football_data_co_uk/<league_code>/<season>.csv
  e.g.  data/raw/football_data_co_uk/E0/2324.csv

Usage:
    python3 scripts/download_football_data_co_uk.py
    python3 scripts/download_football_data_co_uk.py --from-season 2015
    python3 scripts/download_football_data_co_uk.py --leagues E0 D1 SP1
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

console = Console()

# ── League definitions ────────────────────────────────────────────────────────
# (code, display_name)  — only leagues with reliable odds data
LEAGUES = [
    # England
    ("E0",  "England Premier League"),
    ("E1",  "England Championship"),
    ("E2",  "England League 1"),
    ("E3",  "England League 2"),
    # Germany
    ("D1",  "Germany Bundesliga"),
    ("D2",  "Germany 2. Bundesliga"),
    # Spain
    ("SP1", "Spain La Liga"),
    ("SP2", "Spain Segunda"),
    # Italy
    ("I1",  "Italy Serie A"),
    ("I2",  "Italy Serie B"),
    # France
    ("F1",  "France Ligue 1"),
    ("F2",  "France Ligue 2"),
    # Netherlands
    ("N1",  "Netherlands Eredivisie"),
    # Belgium
    ("B1",  "Belgium Jupiler"),
    # Portugal
    ("P1",  "Portugal Primeira Liga"),
    # Turkey
    ("T1",  "Turkey Super Lig"),
    # Greece
    ("G1",  "Greece Super League"),
    # Scotland
    ("SC0", "Scotland Premiership"),
]

LEAGUE_CODES = {code for code, _ in LEAGUES}
LEAGUE_NAMES = {code: name for code, name in LEAGUES}

BASE_URL = "https://www.football-data.co.uk/mmz4281"
DELAY_S  = 0.8   # polite delay between requests


def season_code(start_year: int) -> str:
    """2023 → '2324',  2000 → '0001',  1999 → '9900'"""
    y1 = start_year % 100
    y2 = (start_year + 1) % 100
    return f"{y1:02d}{y2:02d}"


def season_label(start_year: int) -> str:
    return f"{start_year}/{str(start_year + 1)[-2:]}"


def download_csv(url: str, dest: Path) -> tuple[bool, str]:
    """Download one CSV. Returns (success, status_message)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if len(data) < 100:  # empty / placeholder file
            return False, "empty"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        rows = data.count(b"\n")
        return True, f"{rows} rows"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "404"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:40]


def main() -> None:
    ap = argparse.ArgumentParser(description="Download football-data.co.uk CSVs")
    ap.add_argument("--from-season", type=int, default=2005, metavar="YEAR",
                    help="Start year of earliest season to download (default: 2005)")
    ap.add_argument("--to-season", type=int, default=2025, metavar="YEAR",
                    help="Start year of latest season to download (default: 2025)")
    ap.add_argument("--leagues", nargs="+", metavar="CODE",
                    help="League codes to download (default: all). E.g. E0 D1 SP1")
    ap.add_argument("--out-dir", type=Path,
                    default=Path("data/raw/football_data_co_uk"),
                    help="Output directory (default: data/raw/football_data_co_uk)")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="Skip files already downloaded (default: true)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print job list without downloading")
    args = ap.parse_args()

    leagues = [(c, n) for c, n in LEAGUES
               if args.leagues is None or c in args.leagues]
    seasons = list(range(args.from_season, args.to_season + 1))

    if args.leagues:
        unknown = set(args.leagues) - LEAGUE_CODES
        if unknown:
            console.print(f"[red]Unknown league codes: {unknown}[/red]")
            console.print(f"Valid codes: {sorted(LEAGUE_CODES)}")
            sys.exit(1)

    total = len(leagues) * len(seasons)
    console.print(f"\n[bold]football-data.co.uk downloader[/bold]")
    console.print(f"  Leagues:  {len(leagues)}  ({', '.join(c for c, _ in leagues)})")
    console.print(f"  Seasons:  {season_label(seasons[0])} → {season_label(seasons[-1])}  ({len(seasons)} seasons)")
    console.print(f"  Total:    {total} files  →  {args.out_dir}")
    if args.dry_run:
        console.print("[yellow]  DRY RUN — no downloading[/yellow]")
        for code, name in leagues:
            for yr in seasons:
                sc = season_code(yr)
                url = f"{BASE_URL}/{sc}/{code}.csv"
                dest = args.out_dir / code / f"{sc}.csv"
                status = "[dim]exists[/dim]" if dest.exists() else "to download"
                console.print(f"  {code:4s}  {season_label(yr)}  {status}  {url}")
        return
    console.print()

    results = {"downloaded": 0, "skipped": 0, "missing": 0, "error": 0}
    log_rows = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        TextColumn("• [dim]{task.fields[last]}[/dim]"),
        console=console,
        refresh_per_second=8,
    ) as bar:
        task = bar.add_task("downloading", total=total, last="")

        for code, name in leagues:
            for yr in seasons:
                sc = season_code(yr)
                dest = args.out_dir / code / f"{sc}.csv"
                label = f"{code} {season_label(yr)}"

                if args.skip_existing and dest.exists():
                    results["skipped"] += 1
                    bar.update(task, advance=1, last=f"skip {label}")
                    continue

                url = f"{BASE_URL}/{sc}/{code}.csv"
                bar.update(task, description=f"downloading  [yellow]{label}[/yellow]", last="")
                ok, msg = download_csv(url, dest)

                if ok:
                    results["downloaded"] += 1
                    log_rows.append((code, season_label(yr), "✓", msg))
                elif msg == "404":
                    results["missing"] += 1
                    log_rows.append((code, season_label(yr), "–", "not available"))
                else:
                    results["error"] += 1
                    log_rows.append((code, season_label(yr), "✗", msg))
                    console.print(f"[red]  ERROR {label}: {msg}[/red]")

                bar.update(task, advance=1, last=f"{label} → {msg}")
                time.sleep(DELAY_S)

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("[green]Downloaded[/green]", str(results["downloaded"]))
    t.add_row("[dim]Skipped (exists)[/dim]", str(results["skipped"]))
    t.add_row("[dim]Not available[/dim]",   str(results["missing"]))
    t.add_row("[red]Errors[/red]",          str(results["error"]))
    console.print(t)

    # Show a few errors if any
    errors = [(c, s, m) for c, s, st, m in log_rows if st == "✗"]
    if errors:
        console.print("\n[red]Failed downloads:[/red]")
        for c, s, m in errors[:10]:
            console.print(f"  {c} {s}: {m}")

    downloaded_count = results["downloaded"] + results["skipped"]
    console.print(f"\n[bold green]✓ {downloaded_count} CSV files ready in {args.out_dir}[/bold green]")


if __name__ == "__main__":
    main()
