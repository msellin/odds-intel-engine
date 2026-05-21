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
    python3 scripts/scrape_oddsportal.py --parallel 4   # run 4 jobs at once (default: 3)
    python3 scripts/scrape_oddsportal.py --dry-run       # print jobs, don't run
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
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
from rich.text import Text
from rich import box

console = Console()

# ── League definitions ─────────────────────────────────────────────────────────
LEAGUES = [
    ("england-premier-league",      "England PL"),
    ("england-championship",        "England Championship"),
    ("germany-bundesliga",          "Germany Bundesliga"),
    ("germany-bundesliga-2",        "Germany Bund. 2"),
    ("spain-laliga",                "Spain La Liga"),
    ("spain-laliga2",               "Spain La Liga 2"),
    ("italy-serie-a",               "Italy Serie A"),
    ("italy-serie-b",               "Italy Serie B"),
    ("france-ligue-1",              "France Ligue 1"),
    ("france-ligue-2",              "France Ligue 2"),
    ("eredivisie",                  "Netherlands"),
    ("jupiler-pro-league",          "Belgium"),
    ("liga-portugal",               "Portugal"),
    ("turkey-super-lig",            "Turkey"),
    ("greece-super-league",         "Greece"),
    ("scotland-premiership",        "Scotland"),
    ("austria-bundesliga",          "Austria"),
    ("switzerland-super-league",    "Switzerland"),
    ("denmark-superliga",           "Denmark"),
    ("poland-ekstraklasa",          "Poland"),
    ("ireland-premier-division",    "Ireland"),
    ("russia-premier-league",       "Russia"),
    ("champions-league",            "UCL"),
    ("europa-league",               "UEL"),
    ("conference-league",           "UECL"),
    ("argentina-liga-profesional",  "Argentina"),
    ("brazil-serie-a",              "Brazil"),
    ("usa-mls",                     "USA MLS"),
    ("mexico-liga-mx",              "Mexico"),
    ("japan-j1-league",             "Japan"),
    ("china-super-league",          "China"),
]

LEAGUE_SLUGS = {slug for slug, _ in LEAGUES}
LEAGUE_NAMES = {slug: name for slug, name in LEAGUES}

MARKETS = "1x2,btts,double_chance,dnb,over_under_1_5,over_under_2_5,over_under_3_5,asian_handicap_-1,asian_handicap_-0_5,asian_handicap_0,asian_handicap_+0_5,asian_handicap_+1"

# Status symbols
_SYM = {
    "pending":  ("⬜", "dim"),
    "running":  ("⏳", "yellow"),
    "done":     ("✓",  "green"),
    "skip":     ("–",  "dim"),
    "error":    ("✗",  "red"),
}


def run_job(slug: str, season: str, dest: Path, markets: str = MARKETS,
            timeout_s: int = 2400, concurrency: int = 6,
            max_pages: int | None = None,
            dry_run: bool = False) -> tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "oddsharvester", "historic",
        "-s", "football",
        "-l", slug,
        "--season", season,
        "-m", markets,
        "-f", "json",
        "-o", str(dest),
        "--headless",
        "-c", str(concurrency),
        "--request-delay", "0.5",
    ]
    if max_pages is not None:
        cmd += ["--max-pages", str(max_pages)]

    if dry_run:
        time.sleep(0.05)
        return True, "dry-run"

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        start = time.monotonic()
        deadline = start + timeout_s
        output_lines: list[str] = []

        while True:
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                return False, f"timeout (>{timeout_s // 60}min)"
            try:
                line = proc.stdout.readline()
            except Exception:
                break
            if line:
                output_lines.append(line.rstrip())
            elif proc.poll() is not None:
                break
            else:
                time.sleep(0.2)

        proc.wait()
        if proc.returncode != 0:
            # Return last non-empty line of output as the error message
            last = next((l for l in reversed(output_lines) if l.strip()), f"exit {proc.returncode}")
            return False, last[:80]

        if dest.exists() and dest.stat().st_size > 200:
            return True, f"{dest.stat().st_size // 1024} KB"
        return False, "empty output"

    except FileNotFoundError:
        return False, "oddsharvester not installed"
    except Exception as e:
        return False, str(e)[:60]


def check_oddsharvester() -> bool:
    try:
        r = subprocess.run(["oddsharvester", "--help"], capture_output=True, timeout=10)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _build_table(
    job_status: dict[tuple[str, str], tuple[str, str]],
    leagues: list[tuple[str, str]],
    seasons: list[str],
    n_done: int,
    n_total: int,
    start_ts: float,
) -> Table:
    """Build the live status grid — leagues as rows, seasons as columns."""
    short_seasons = [s.split("-")[0] for s in seasons]  # "2020" instead of "2020-2021"

    t = Table(box=box.SIMPLE_HEAD, padding=(0, 1), expand=False, show_footer=False)
    t.add_column("League", style="bold", min_width=16, no_wrap=True)
    for s in short_seasons:
        t.add_column(s, justify="center", min_width=6, no_wrap=True)

    for slug, name in leagues:
        cells = [name]
        for season in seasons:
            state, detail = job_status.get((slug, season), ("pending", ""))
            sym, style = _SYM[state]
            if state in ("running", "error") and detail:
                cells.append(Text(f"{sym}{detail[:12]}", style=style))
            else:
                cells.append(Text(sym, style=style))
        t.add_row(*cells)

    elapsed = int(time.monotonic() - start_ts)
    h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
    footer = f"  {n_done}/{n_total} done  •  {h:02d}:{m:02d}:{s:02d} elapsed"
    if n_done > 0 and n_done < n_total:
        eta_s = int(elapsed / n_done * (n_total - n_done))
        eh, em = eta_s // 3600, (eta_s % 3600) // 60
        footer += f"  •  ~{eh}h{em:02d}m remaining"

    return Panel(t, title=f"[bold]OddsPortal scrape[/bold]", subtitle=footer, border_style="cyan")


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape OddsPortal historical odds via OddsHarvester")
    ap.add_argument("--from-season", type=int, default=2020, metavar="YEAR")
    ap.add_argument("--to-season", type=int, default=2025, metavar="YEAR")
    ap.add_argument("--leagues", nargs="+", metavar="SLUG")
    ap.add_argument("--markets", default=MARKETS)
    ap.add_argument("--out-dir", type=Path, default=Path("data/raw/oddsportal"))
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--timeout", type=int, default=7200, metavar="SECONDS",
                    help="Max seconds per job (default: 7200 = 2h)")
    ap.add_argument("--concurrency", type=int, default=6, metavar="N",
                    help="Concurrent match pages per job (default: 6)")
    ap.add_argument("--parallel", type=int, default=1, metavar="N",
                    help="Jobs to run simultaneously (default: 1 — avoids rate-limiting)")
    ap.add_argument("--stagger", type=int, default=15, metavar="SECONDS",
                    help="Seconds to wait before starting each parallel job (default: 15)")
    ap.add_argument("--max-pages", type=int, default=None, metavar="N",
                    help="Limit results pages per job (e.g. --max-pages 1 for ~50 matches, useful for testing)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    leagues = [(s, n) for s, n in LEAGUES
               if args.leagues is None or s in args.leagues]
    seasons = [f"{yr}-{yr + 1}" for yr in range(args.from_season, args.to_season + 1)]

    if args.leagues:
        unknown = set(args.leagues) - LEAGUE_SLUGS
        if unknown:
            console.print(f"[red]Unknown league slugs: {unknown}[/red]")
            console.print(f"Valid: {sorted(LEAGUE_SLUGS)}")
            sys.exit(1)

    if not args.dry_run and not check_oddsharvester():
        console.print("[bold red]oddsharvester not found — pip install oddsharvester[/bold red]")
        sys.exit(1)

    # Build initial status map
    job_status: dict[tuple[str, str], tuple[str, str]] = {}
    jobs_to_run: list[tuple[str, str, str, Path]] = []

    for slug, name in leagues:
        for season in seasons:
            dest = args.out_dir / slug / f"{season}.json"
            if args.skip_existing and dest.exists() and dest.stat().st_size > 200:
                job_status[(slug, season)] = ("skip", f"{dest.stat().st_size // 1024}k")
            else:
                job_status[(slug, season)] = ("pending", "")
                jobs_to_run.append((slug, name, season, dest))

    n_skip = sum(1 for v in job_status.values() if v[0] == "skip")
    n_todo = len(jobs_to_run)
    n_total_display = n_skip + n_todo

    est_min = n_todo * 10 // max(1, args.parallel)
    console.print(f"\n[bold]OddsPortal scraper[/bold]  "
                  f"{len(leagues)} leagues · {len(seasons)} seasons · "
                  f"{n_todo} jobs to run ({n_skip} already done) · "
                  f"parallel={args.parallel} · ~{est_min // 60}h{est_min % 60:02d}m est.")
    if args.dry_run:
        console.print("[yellow]DRY RUN[/yellow]")
    console.print()

    _lock = threading.Lock()
    n_done = n_skip
    errors: list[tuple[str, str, str]] = []
    start_ts = time.monotonic()

    with Live(
        _build_table(job_status, leagues, seasons, n_done, n_total_display, start_ts),
        console=console,
        refresh_per_second=2,
        vertical_overflow="visible",
    ) as live:

        def _refresh():
            live.update(_build_table(job_status, leagues, seasons, n_done, n_total_display, start_ts))

        _job_index = 0
        _job_index_lock = threading.Lock()

        def _run(job: tuple[str, str, str, Path]) -> tuple[bool, str, str, str]:
            nonlocal _job_index
            slug, name, season, dest = job

            # Stagger starts to avoid simultaneous browser launches hitting rate limits
            with _job_index_lock:
                idx = _job_index
                _job_index += 1
            if idx > 0 and args.stagger > 0 and not args.dry_run:
                time.sleep(idx * args.stagger % (args.parallel * args.stagger))

            with _lock:
                job_status[(slug, season)] = ("running", "")
                _refresh()

            job_start = time.monotonic()

            # Ticker: update elapsed time while job runs
            stop_ticker = threading.Event()
            def _tick():
                while not stop_ticker.wait(timeout=15):
                    with _lock:
                        elapsed = int(time.monotonic() - job_start)
                        job_status[(slug, season)] = ("running", f" {elapsed}s")
                        _refresh()
            ticker = threading.Thread(target=_tick, daemon=True)
            ticker.start()

            ok, msg = run_job(slug, season, dest,
                              markets=args.markets,
                              timeout_s=args.timeout,
                              concurrency=args.concurrency,
                              max_pages=args.max_pages,
                              dry_run=args.dry_run)
            stop_ticker.set()
            return ok, msg, slug, season

        with ThreadPoolExecutor(max_workers=args.parallel) as pool:
            futures = {pool.submit(_run, job): job for job in jobs_to_run}

            for fut in as_completed(futures):
                ok, msg, slug, season = fut.result()
                with _lock:
                    if ok:
                        n_done += 1
                        job_status[(slug, season)] = ("done", msg)
                    else:
                        n_done += 1
                        job_status[(slug, season)] = ("error", msg)
                        errors.append((slug, season, msg))
                    _refresh()

    # ── Summary ────────────────────────────────────────────────────────────────
    console.print()
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_row("[green]Scraped[/green]",        str(sum(1 for v in job_status.values() if v[0] == "done")))
    t.add_row("[dim]Skipped (exists)[/dim]",   str(n_skip))
    t.add_row("[red]Errors[/red]",             str(len(errors)))
    console.print(t)

    if errors:
        console.print("\n[red]Failed jobs:[/red]")
        for slug, season, msg in errors:
            console.print(f"  {slug} {season}: {msg}")
        slugs_str = " ".join(sorted({s for s, _, _ in errors}))
        console.print(f"\nRetry: python3 scripts/scrape_oddsportal.py --leagues {slugs_str}")

    console.print(f"\n[bold green]✓ {n_done} jobs complete — files in {args.out_dir}[/bold green]")


if __name__ == "__main__":
    main()
