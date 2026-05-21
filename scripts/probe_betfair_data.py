"""
Probe Betfair data accessibility from this host (Railway / server).

Tests:
  1. DNS + TCP reachability of betfair.com and historicdata.betfair.com
  2. Free BSP (Betfair Starting Price) CSV data — no auth required
     https://promo.betfair.com/betfairsp/prices/dfbetfairsp{DDMMYYYY}.csv
  3. historicdata.betfair.com landing page (auth required to actually download,
     but 200/403 vs timeout tells us if Railway can reach it at all)

If BSP access works: run with --download to pull the last N days of football
BSP files into data/raw/betfair_bsp/.

BSP data covers: football, horse racing, greyhounds. Football rows include
BSP price per selection (home/draw/away), settled result, BSP volume.
Free, no registration, goes back to ~2016.

For full historical tick data (historicdata.betfair.com):
  - Requires Betfair account + data purchase (£10-50/month per sport)
  - If Railway can reach the site, you can register via Railway IP if
    needed (no geo-block at server level)

Usage:
    python3 scripts/probe_betfair_data.py
    python3 scripts/probe_betfair_data.py --download --days 30
    python3 scripts/probe_betfair_data.py --download --days 365 --out-dir data/raw/betfair_bsp
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from datetime import date, timedelta
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from rich.console import Console
    from rich.table import Table
    console = Console()
    def info(msg): console.print(msg)
    def ok(msg):   console.print(f"[green]{msg}[/green]")
    def err(msg):  console.print(f"[red]{msg}[/red]")
    def warn(msg): console.print(f"[yellow]{msg}[/yellow]")
except ImportError:
    def info(msg): print(msg)
    def ok(msg):   print(f"OK: {msg}")
    def err(msg):  print(f"ERROR: {msg}")
    def warn(msg): print(f"WARN: {msg}")


BSP_BASE = "https://promo.betfair.com/betfairsp/prices"
HIST_BASE = "https://historicdata.betfair.com"
BETFAIR_MAIN = "https://www.betfair.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)",
    "Accept": "text/html,application/xhtml+xml,text/csv,*/*",
}


def _tcp_check(host: str, port: int = 443, timeout: float = 5.0) -> tuple[bool, float]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, time.monotonic() - start
    except Exception:
        return False, time.monotonic() - start


def _http_get(url: str, timeout: float = 10.0, stream: bool = False):
    if not HAS_REQUESTS:
        raise RuntimeError("requests not installed")
    return requests.get(url, headers=HEADERS, timeout=timeout, stream=stream, allow_redirects=True)


def probe_connectivity() -> dict[str, bool]:
    info("\n[bold]── Connectivity probe ──[/bold]")
    results = {}

    checks = [
        ("betfair.com",             "www.betfair.com",         443),
        ("historicdata.betfair.com","historicdata.betfair.com",443),
        ("promo.betfair.com (BSP)", "promo.betfair.com",       443),
    ]
    for label, host, port in checks:
        reachable, latency_s = _tcp_check(host, port)
        if reachable:
            ok(f"  TCP {label:40s} {latency_s*1000:.0f}ms")
        else:
            err(f"  TCP {label:40s} UNREACHABLE")
        results[label] = reachable

    return results


def probe_bsp_sample() -> bool:
    """Try downloading a recent BSP file and show sample rows."""
    info("\n[bold]── BSP sample (free, no auth) ──[/bold]")
    if not HAS_REQUESTS:
        err("requests not installed — pip install requests")
        return False

    # Try last 7 days to find one that exists
    for days_ago in range(1, 8):
        d = date.today() - timedelta(days=days_ago)
        fname = f"dfbetfairsp{d.strftime('%d%m%Y')}.csv"
        url = f"{BSP_BASE}/{fname}"
        try:
            r = _http_get(url, timeout=15)
            if r.status_code == 200:
                size_kb = len(r.content) // 1024
                ok(f"  BSP {fname}: HTTP 200, {size_kb} KB")
                lines = r.text.splitlines()
                info(f"  Header: {lines[0][:120]}")
                # Show football rows only
                football_rows = [l for l in lines[1:] if ",SOC," in l or ",SOCCER," in l or ",Football," in l]
                if football_rows:
                    ok(f"  Football rows: {len(football_rows)} (of {len(lines)-1} total)")
                    info(f"  Sample: {football_rows[0][:120]}")
                else:
                    warn(f"  No football rows found in sample (check column names below)")
                    info(f"  First data row: {lines[1][:120] if len(lines) > 1 else 'empty'}")
                return True
            else:
                warn(f"  {fname}: HTTP {r.status_code}")
        except Exception as e:
            err(f"  {fname}: {e}")

    err("  Could not fetch any BSP file in last 7 days")
    return False


def probe_histdata_site() -> None:
    """Check if historicdata.betfair.com responds (even 403 = reachable)."""
    info("\n[bold]── historicdata.betfair.com ──[/bold]")
    if not HAS_REQUESTS:
        return
    try:
        r = _http_get(HIST_BASE, timeout=10)
        if r.status_code in (200, 302, 301):
            ok(f"  historicdata.betfair.com: HTTP {r.status_code} — REACHABLE, login required")
        elif r.status_code == 403:
            warn(f"  historicdata.betfair.com: HTTP 403 — reachable but access denied (need login)")
        else:
            warn(f"  historicdata.betfair.com: HTTP {r.status_code}")
        info(f"  Final URL: {r.url}")
    except Exception as e:
        err(f"  historicdata.betfair.com: {e}")


def download_bsp(days: int, out_dir: Path) -> None:
    """Download BSP CSV files for the last N days."""
    info(f"\n[bold]── Downloading BSP files (last {days} days → {out_dir}) ──[/bold]")
    if not HAS_REQUESTS:
        err("requests not installed — pip install requests")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    n_ok = n_skip = n_fail = 0

    for days_ago in range(1, days + 1):
        d = date.today() - timedelta(days=days_ago)
        fname = f"dfbetfairsp{d.strftime('%d%m%Y')}.csv"
        dest = out_dir / fname

        if dest.exists() and dest.stat().st_size > 100:
            n_skip += 1
            continue

        url = f"{BSP_BASE}/{fname}"
        try:
            r = _http_get(url, timeout=20)
            if r.status_code == 200 and len(r.content) > 100:
                dest.write_bytes(r.content)
                size_kb = len(r.content) // 1024
                ok(f"  ✓ {fname}  {size_kb} KB")
                n_ok += 1
            elif r.status_code == 404:
                n_fail += 1  # future date or missing day — silent
            else:
                warn(f"  {fname}: HTTP {r.status_code}")
                n_fail += 1
            time.sleep(0.3)  # polite
        except Exception as e:
            err(f"  {fname}: {e}")
            n_fail += 1

    info(f"\n  Downloaded: {n_ok}  |  Skipped (exists): {n_skip}  |  Failed/missing: {n_fail}")
    existing = list(out_dir.glob("dfbetfairsp*.csv"))
    if existing:
        total_mb = sum(f.stat().st_size for f in existing) / 1024 / 1024
        ok(f"  Total BSP files in {out_dir}: {len(existing)} ({total_mb:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Probe Betfair data accessibility from this host")
    ap.add_argument("--download", action="store_true",
                    help="Download BSP CSV files after probing")
    ap.add_argument("--days", type=int, default=30,
                    help="Days of BSP history to download (default: 30)")
    ap.add_argument("--out-dir", type=Path, default=Path("data/raw/betfair_bsp"),
                    help="Output dir for BSP files (default: data/raw/betfair_bsp)")
    args = ap.parse_args()

    info(f"\n[bold]Betfair data probe[/bold]  (host: {socket.gethostname()})")

    tcp_results = probe_connectivity()
    bsp_ok = probe_bsp_sample()
    probe_histdata_site()

    info("\n[bold]── Summary ──[/bold]")
    if bsp_ok:
        ok("  ✓ BSP data accessible — free football closing prices available")
        info("    Covers: home/draw/away BSP price per match, settled result, volume")
        info("    Goes back to ~2016, updates daily")
        if args.download:
            download_bsp(args.days, args.out_dir)
        else:
            info(f"\n  To download: python3 scripts/probe_betfair_data.py --download --days {args.days}")
    else:
        err("  ✗ BSP data not accessible from this host")

    hist_reachable = tcp_results.get("historicdata.betfair.com", False)
    if hist_reachable:
        ok("  ✓ historicdata.betfair.com reachable — full tick data available after account setup")
        info("    To access: register at betfair.com (from this server/VPN), then purchase")
        info("    Football data: ~£10-20/month, full order book every second")
    else:
        err("  ✗ historicdata.betfair.com unreachable from this host")


if __name__ == "__main__":
    main()
