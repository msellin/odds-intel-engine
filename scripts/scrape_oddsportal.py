"""
Scrape historical odds from OddsPortal via OddsHarvester.

Output: data/raw/oddsportal/<league>/<season>.json
  e.g.  data/raw/oddsportal/england-premier-league/2023-2024.json

Requirements:
    pip install oddsharvester
    playwright install chromium

Usage:
    python3 scripts/scrape_oddsportal.py
    python3 scripts/scrape_oddsportal.py --from-season 2021
    python3 scripts/scrape_oddsportal.py --leagues england-premier-league germany-bundesliga
    python3 scripts/scrape_oddsportal.py --dry-run       # print jobs, don't run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
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

# ── League definitions ─────────────────────────────────────────────────────────
# (oddsportal_slug, display_name)
LEAGUES = [
    ("england-premier-league",      "England Premier League"),
    ("england-championship",        "England Championship"),
    ("germany-bundesliga",          "Germany Bundesliga"),
    ("germany-2-bundesliga",        "Germany 2. Bundesliga"),
    ("spain-laliga",                "Spain La Liga"),
    ("spain-laliga2",               "Spain La Liga 2"),
    ("italy-serie-a",               "Italy Serie A"),
    ("italy-serie-b",               "Italy Serie B"),
    ("france-ligue-1",              "France Ligue 1"),
    ("france-ligue-2",              "France Ligue 2"),
    ("netherlands-eredivisie",      "Netherlands Eredivisie"),
    ("belgium-jupiler-pro-league",  "Belgium Jupiler Pro League"),
    ("portugal-liga-portugal",      "Portugal Liga Portugal"),
    ("turkey-super-lig",            "Turkey Super Lig"),
    ("greece-super-league",         "Greece Super League"),
    ("scotland-premiership",        "Scotland Premiership"),
]

LEAGUE_SLUGS = {slug for slug, _ in LEAGUES}
LEAGUE_NAMES = {slug: name for slug, name in LEAGUES}

# Football markets — exact names required by OddsHarvester CLI.
# OU lines: 1.5, 2.5, 3.5 cover our three OU bots.
# AH: the 5 most common handicap lines. Skipping exotic lines to keep job time reasonable.
MARKETS = "1x2,btts,double_chance,dnb,over_under_1_5,over_under_2_5,over_under_3_5,asian_handicap_-1,asian_handicap_-0_5,asian_handicap_0,asian_handicap_+0_5,asian_handicap_+1"

DELAY_BETWEEN_JOBS_S = 5   # polite pause between scrape jobs


def run_job(slug: str, season: str, dest: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Run one oddsharvester historic job. Returns (success, message)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "oddsharvester", "historic",
        "-s", "football",
        "-l", slug,
        "--season", season,
        "-m", MARKETS,
        "-f", "json",
        "-o", str(dest),
        "--headless",
        "-c", "3",           # 3 concurrent match pages per job
        "--request-delay", "0.8",
    ]

    if dry_run:
        return True, "dry-run"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,   # 10 min per job max
        )
        if result.returncode != 0:
            # grab last meaningful error line
            err_lines = [l.strip() for l in (result.stderr or result.stdout).splitlines() if l.strip()]
            msg = err_lines[-1][:80] if err_lines else "non-zero exit"
            return False, msg

        if dest.exists() and dest.stat().st_size > 200:
            size_kb = dest.stat().st_size // 1024
            return True, f"{size_kb} KB"
        return False, "output file empty or missing"

    except subprocess.TimeoutExpired:
        return False, "timeout (>10 min)"
    except FileNotFoundError:
        return False, "oddsharvester not installed — run: pip install oddsharvester"
    except Exception as e:
        return False, str(e)[:80]


def check_oddsharvester() -> bool:
    try:
        r = subprocess.run(["oddsharvester", "--help"], capture_output=True, timeout=10)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape OddsPortal historical odds via OddsHarvester")
    ap.add_argument("--from-season", type=int, default=2020, metavar="YEAR",
                    help="Start year of earliest season to scrape (default: 2020)")
    ap.add_argument("--to-season", type=int, default=2025, metavar="YEAR",
                    help="Start year of latest season to scrape (default: 2025 = 2024/25)")
    ap.add_argument("--leagues", nargs="+", metavar="SLUG",
                    help="League slugs to scrape (default: all). E.g. england-premier-league")
    ap.add_argument("--markets", default=MARKETS,
                    help=f"Comma-separated markets (default: {MARKETS})")
    ap.add_argument("--out-dir", type=Path,
                    default=Path("data/raw/oddsportal"),
                    help="Output directory (default: data/raw/oddsportal)")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="Skip already-scraped files (default: true)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print jobs without running them")
    args = ap.parse_args()

    leagues = [(s, n) for s, n in LEAGUES
               if args.leagues is None or s in args.leagues]
    seasons = [f"{yr}-{yr + 1}" for yr in range(args.from_season, args.to_season + 1)]

    if args.leagues:
        unknown = set(args.leagues) - LEAGUE_SLUGS
        if unknown:
            console.print(f"[red]Unknown league slugs: {unknown}[/red]")
            console.print(f"Valid slugs: {sorted(LEAGUE_SLUGS)}")
            sys.exit(1)

    # ── Pre-flight check ───────────────────────────────────────────────────────
    if not args.dry_run and not check_oddsharvester():
        console.print("[bold red]oddsharvester not found.[/bold red]")
        console.print("Install with:")
        console.print("  pip install oddsharvester")
        console.print("  playwright install chromium")
        sys.exit(1)

    total = len(leagues) * len(seasons)
    est_minutes = total * 3  # ~3 min per job
    console.print(f"\n[bold]OddsPortal scraper (via OddsHarvester)[/bold]")
    console.print(f"  Leagues:  {len(leagues)}")
    console.print(f"  Seasons:  {seasons[0]} → {seasons[-1]}  ({len(seasons)} seasons)")
    console.print(f"  Markets:  {args.markets}")
    console.print(f"  Total:    {total} jobs  (~{est_minutes // 60}h {est_minutes % 60}m estimated)")
    console.print(f"  Output:   {args.out_dir}")
    if args.dry_run:
        console.print("[yellow]  DRY RUN — no scraping[/yellow]")
    console.print()

    results = {"done": 0, "skipped": 0, "error": 0}
    errors: list[tuple[str, str, str]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=36),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        TextColumn("• [dim]{task.fields[last]}[/dim]"),
        console=console,
        refresh_per_second=4,
    ) as bar:
        task = bar.add_task("scraping", total=total, last="")

        for slug, name in leagues:
            for season in seasons:
                dest = args.out_dir / slug / f"{season}.json"
                label = f"{slug.split('-')[0].title()} {season}"

                if args.skip_existing and dest.exists() and dest.stat().st_size > 200:
                    results["skipped"] += 1
                    bar.update(task, advance=1, last=f"skip {label}")
                    continue

                bar.update(task, description=f"[yellow]{name}[/yellow]  {season}", last="")
                ok, msg = run_job(slug, season, dest, dry_run=args.dry_run)

                if ok:
                    results["done"] += 1
                    bar.update(task, advance=1, last=f"✓ {label}  {msg}")
                else:
                    results["error"] += 1
                    errors.append((slug, season, msg))
                    bar.update(task, advance=1, last=f"[red]✗ {label}[/red]")
                    console.print(f"  [red]✗ {name} {season}: {msg}[/red]")

                if not args.dry_run:
                    time.sleep(DELAY_BETWEEN_JOBS_S)

    # ── Summary ────────────────────────────────────────────────────────────────
    console.print()
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("[green]Scraped[/green]",        str(results["done"]))
    t.add_row("[dim]Skipped (exists)[/dim]",   str(results["skipped"]))
    t.add_row("[red]Errors[/red]",             str(results["error"]))
    console.print(t)

    if errors:
        console.print("\n[red]Failed jobs:[/red]")
        for slug, season, msg in errors:
            console.print(f"  {slug} {season}: {msg}")
        console.print("\nRetry failed jobs with:")
        slugs_str = " ".join(sorted({s for s, _, _ in errors}))
        console.print(f"  python3 scripts/scrape_oddsportal.py --leagues {slugs_str}")

    done_total = results["done"] + results["skipped"]
    console.print(f"\n[bold green]✓ {done_total} files ready in {args.out_dir}[/bold green]")


if __name__ == "__main__":
    main()
