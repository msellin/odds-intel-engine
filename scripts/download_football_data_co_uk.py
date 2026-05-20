"""
Download historical match + odds CSVs from football-data.co.uk.

Two URL patterns:
  Main leagues:  /mmz4281/YYRR/CODE.csv  — one file per season
  Extra leagues: /new/CODE.csv           — single file, all seasons combined

Output:
  data/raw/football_data_co_uk/main/<CODE>/<season>.csv
  data/raw/football_data_co_uk/extra/<CODE>.csv

Usage:
    python3 scripts/download_football_data_co_uk.py
    python3 scripts/download_football_data_co_uk.py --from-season 2015
    python3 scripts/download_football_data_co_uk.py --leagues E0 D1 SP1
    python3 scripts/download_football_data_co_uk.py --extra-only
    python3 scripts/download_football_data_co_uk.py --dry-run
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

BASE_URL  = "https://www.football-data.co.uk/mmz4281"
EXTRA_URL = "https://www.football-data.co.uk/new"
DELAY_S   = 0.8   # polite delay between requests

# ── Main leagues — one CSV per season, back to ~1993 ─────────────────────────
# (code, display_name)
MAIN_LEAGUES = [
    # England
    ("E0",  "England Premier League"),
    ("E1",  "England Championship"),
    ("E2",  "England League 1"),
    ("E3",  "England League 2"),
    ("EC",  "England Conference / National League"),
    # Scotland
    ("SC0", "Scotland Premiership"),
    ("SC1", "Scotland Div 1"),
    ("SC2", "Scotland Div 2"),
    ("SC3", "Scotland Div 3"),
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
]

# ── Extra leagues — single CSV file, all seasons ──────────────────────────────
# (code, display_name)  — codes follow football-data.co.uk's /new/ naming
EXTRA_LEAGUES = [
    ("ARG", "Argentina Primera Division"),
    ("AUT", "Austria Bundesliga"),
    ("BRA", "Brazil Serie A"),
    ("CHN", "China Super League"),
    ("DNK", "Denmark Superliga"),
    ("FIN", "Finland Veikkausliiga"),
    ("IRL", "Ireland Premier Division"),
    ("JPN", "Japan J-League"),
    ("MEX", "Mexico Liga MX"),
    ("NOR", "Norway Eliteserien"),
    ("POL", "Poland Ekstraklasa"),
    ("ROU", "Romania Liga 1"),
    ("RUS", "Russia Premier League"),
    ("SWE", "Sweden Allsvenskan"),
    ("SWZ", "Switzerland Super League"),
    ("USA", "USA MLS"),
]

ALL_CODES = {c for c, _ in MAIN_LEAGUES} | {c for c, _ in EXTRA_LEAGUES}


def season_code(start_year: int) -> str:
    """2023 → '2324',  2000 → '0001',  1999 → '9900'"""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def season_label(start_year: int) -> str:
    return f"{start_year}/{str(start_year + 1)[-2:]}"


def download_file(url: str, dest: Path) -> tuple[bool, str]:
    """Download one file. Returns (success, status_message)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if len(data) < 100:
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
        return False, str(e)[:50]


def main() -> None:
    ap = argparse.ArgumentParser(description="Download football-data.co.uk CSVs")
    ap.add_argument("--from-season", type=int, default=2005, metavar="YEAR",
                    help="Earliest season start year for main leagues (default: 2005)")
    ap.add_argument("--to-season", type=int, default=2025, metavar="YEAR",
                    help="Latest season start year (default: 2025 = 2025/26)")
    ap.add_argument("--leagues", nargs="+", metavar="CODE",
                    help="Specific league codes to download (default: all)")
    ap.add_argument("--main-only",  action="store_true", help="Only download main leagues")
    ap.add_argument("--extra-only", action="store_true", help="Only download extra leagues")
    ap.add_argument("--out-dir", type=Path,
                    default=Path("data/raw/football_data_co_uk"),
                    help="Output root directory (default: data/raw/football_data_co_uk)")
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print jobs without downloading")
    args = ap.parse_args()

    if args.leagues:
        unknown = set(args.leagues) - ALL_CODES
        if unknown:
            console.print(f"[red]Unknown codes: {unknown}[/red]")
            sys.exit(1)

    do_main  = not args.extra_only
    do_extra = not args.main_only

    main_leagues  = [(c, n) for c, n in MAIN_LEAGUES
                     if args.leagues is None or c in args.leagues] if do_main else []
    extra_leagues = [(c, n) for c, n in EXTRA_LEAGUES
                     if args.leagues is None or c in args.leagues] if do_extra else []
    seasons       = list(range(args.from_season, args.to_season + 1))

    total = len(main_leagues) * len(seasons) + len(extra_leagues)

    console.print(f"\n[bold]football-data.co.uk downloader[/bold]")
    if do_main:
        console.print(f"  Main leagues:  {len(main_leagues)} × {len(seasons)} seasons "
                      f"= {len(main_leagues) * len(seasons)} files")
    if do_extra:
        console.print(f"  Extra leagues: {len(extra_leagues)} single files")
    console.print(f"  Total:         {total} files  →  {args.out_dir}")
    if args.dry_run:
        console.print("[yellow]  DRY RUN[/yellow]\n")
    else:
        console.print()

    results = {"downloaded": 0, "skipped": 0, "missing": 0, "error": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description:<35}"),
        BarColumn(bar_width=36),
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

        # ── Main leagues (per-season files) ───────────────────────────────────
        for code, name in main_leagues:
            for yr in seasons:
                sc   = season_code(yr)
                dest = args.out_dir / "main" / code / f"{sc}.csv"
                lbl  = f"{code} {season_label(yr)}"

                if args.skip_existing and dest.exists():
                    results["skipped"] += 1
                    bar.update(task, advance=1, last=f"skip {lbl}")
                    continue

                if args.dry_run:
                    console.print(f"  {BASE_URL}/{sc}/{code}.csv  →  {dest}")
                    bar.update(task, advance=1, last=lbl)
                    continue

                url = f"{BASE_URL}/{sc}/{code}.csv"
                bar.update(task, description=f"[yellow]{lbl}[/yellow]", last="")
                ok, msg = download_file(url, dest)

                if ok:                results["downloaded"] += 1
                elif msg == "404":   results["missing"] += 1
                else:
                    results["error"] += 1
                    console.print(f"[red]  ✗ {lbl}: {msg}[/red]")

                bar.update(task, advance=1, last=f"{lbl} → {msg}")
                time.sleep(DELAY_S)

        # ── Extra leagues (single files) ──────────────────────────────────────
        for code, name in extra_leagues:
            dest = args.out_dir / "extra" / f"{code}.csv"
            lbl  = f"{code} ({name})"

            if args.skip_existing and dest.exists():
                results["skipped"] += 1
                bar.update(task, advance=1, last=f"skip {code}")
                continue

            if args.dry_run:
                console.print(f"  {EXTRA_URL}/{code}.csv  →  {dest}")
                bar.update(task, advance=1, last=lbl)
                continue

            url = f"{EXTRA_URL}/{code}.csv"
            bar.update(task, description=f"[yellow]{lbl}[/yellow]", last="")
            ok, msg = download_file(url, dest)

            if ok:              results["downloaded"] += 1
            elif msg == "404":  results["missing"] += 1
            else:
                results["error"] += 1
                console.print(f"[red]  ✗ {lbl}: {msg}[/red]")

            bar.update(task, advance=1, last=f"{code} → {msg}")
            time.sleep(DELAY_S)

    # ── Summary ───────────────────────────────────────────────────────────────
    if not args.dry_run:
        console.print()
        t = Table(show_header=False, box=None, padding=(0, 2))
        t.add_row("[green]Downloaded[/green]",    str(results["downloaded"]))
        t.add_row("[dim]Skipped (exists)[/dim]",  str(results["skipped"]))
        t.add_row("[dim]Not available (404)[/dim]", str(results["missing"]))
        t.add_row("[red]Errors[/red]",            str(results["error"]))
        console.print(t)
        done = results["downloaded"] + results["skipped"]
        console.print(f"\n[bold green]✓ {done} files ready in {args.out_dir}[/bold green]")


if __name__ == "__main__":
    main()
