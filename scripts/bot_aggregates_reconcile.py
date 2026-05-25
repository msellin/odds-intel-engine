"""BOT-AGGREGATES-SSOT — reconciliation diagnostic.

Reads the latest `dashboard_cache.bot_breakdown` and compares it to
ground-truth aggregates computed live from simulated_bets. Reports any
per-bot divergence beyond a 1% (or absolute) threshold.

What it catches:
  - Stale cache (settlement ran but cache writer skipped or crashed)
  - Filter drift (cache query excludes bot via NOT LIKE, frontend includes it)
  - Math drift (FILTER clauses miss new statuses like half_won)
  - Bankroll vs P&L inconsistency (current_bankroll - starting_bankroll != total_pnl)

Run:
  python3 scripts/bot_aggregates_reconcile.py             # report only
  python3 scripts/bot_aggregates_reconcile.py --fail-on-drift  # exit 1 if any bot drifts
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.01, help="Relative drift threshold (0.01 = 1 percent)")
    ap.add_argument("--abs-threshold", type=float, default=1.0, help="Absolute drift threshold in EUR (small-pnl floor)")
    ap.add_argument("--fail-on-drift", action="store_true")
    args = ap.parse_args()

    console.print("[bold]BOT-AGGREGATES-SSOT — cache vs live reconciliation[/bold]")

    # 1. Latest dashboard_cache row
    cache_rows = execute_query("""
        SELECT bot_breakdown, computed_at
        FROM dashboard_cache
        ORDER BY computed_at DESC LIMIT 1
    """)
    if not cache_rows or not cache_rows[0]["bot_breakdown"]:
        console.print("[red]No dashboard_cache row with bot_breakdown — abort.[/red]")
        sys.exit(1)
    cache_age_min = (
        execute_query("SELECT EXTRACT(EPOCH FROM (NOW() - computed_at))/60 AS m FROM dashboard_cache ORDER BY computed_at DESC LIMIT 1")[0]["m"]
    )
    cache_breakdown = cache_rows[0]["bot_breakdown"]
    if isinstance(cache_breakdown, str):
        import json
        cache_breakdown = json.loads(cache_breakdown)
    console.print(f"  Cache written {cache_age_min:.0f} min ago — {len(cache_breakdown)} active bots")

    # 2. Live ground truth (same query as write_dashboard_cache.bot_breakdown)
    live_rows = execute_query("""
        SELECT
            b.name,
            COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) as settled,
            COUNT(sb.id) FILTER (WHERE sb.result = 'won') as won,
            SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as total_pnl,
            SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as total_staked,
            AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv
        FROM bots b
        LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
        WHERE b.is_active = true
          AND b.retired_at IS NULL
          AND b.name NOT LIKE 'bot_acca%%'
          AND b.name NOT LIKE 'bot_combo%%'
        GROUP BY b.id, b.name
    """)
    live_by_name = {r["name"]: r for r in live_rows}
    cache_by_name = {b.get("name"): b for b in cache_breakdown}

    # 3. Compare
    t = Table(title="Cache vs live drift per bot")
    for c in ("bot", "settled (c→l)", "Δ settled", "pnl (c→l)", "Δ pnl", "verdict"):
        t.add_column(c)

    drifted = []
    only_in_cache = set(cache_by_name) - set(live_by_name)
    only_in_live = set(live_by_name) - set(cache_by_name)

    for name in sorted(set(cache_by_name) | set(live_by_name)):
        c = cache_by_name.get(name) or {}
        l = live_by_name.get(name) or {}
        c_settled = int(c.get("settled") or 0)
        l_settled = int(l.get("settled") or 0)
        c_pnl = float(c.get("total_pnl") or 0)
        l_pnl = float(l.get("total_pnl") or 0)
        d_settled = l_settled - c_settled
        d_pnl = l_pnl - c_pnl
        rel = abs(d_pnl) / max(abs(l_pnl), 1)
        is_drift = (
            (abs(d_settled) > 0 and abs(d_settled) / max(l_settled, 1) > args.threshold)
            or (abs(d_pnl) > args.abs_threshold and rel > args.threshold)
            or name in only_in_cache
            or name in only_in_live
        )
        verdict = "[red]DRIFT[/red]" if is_drift else "[green]ok[/green]"
        if is_drift:
            drifted.append((name, d_settled, d_pnl, rel))
        if name in only_in_cache:
            verdict = "[red]only in cache[/red]"
        if name in only_in_live:
            verdict = "[red]only live (cache stale)[/red]"
        t.add_row(
            name,
            f"{c_settled}→{l_settled}",
            f"{d_settled:+d}" if d_settled else "0",
            f"{c_pnl:.2f}→{l_pnl:.2f}",
            f"€{d_pnl:+.2f}" if abs(d_pnl) > 0.01 else "€0",
            verdict,
        )
    console.print(t)

    # 4. Bankroll cross-check (current - starting should equal total_pnl from live)
    console.print("\n[bold]Bankroll sanity check (current_bankroll - starting_bankroll vs total_pnl)[/bold]")
    bb = execute_query("""
        SELECT b.name, b.current_bankroll, b.starting_bankroll,
               COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')), 0) AS live_pnl
        FROM bots b
        LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
        WHERE b.is_active = true AND b.retired_at IS NULL
        GROUP BY b.id
        HAVING ABS(b.current_bankroll - b.starting_bankroll - COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')), 0)) > 5
    """)
    if bb:
        console.print(f"[yellow]⚠ {len(bb)} bots have bankroll-vs-PnL drift > €5:[/yellow]")
        for r in bb[:10]:
            delta = float(r["current_bankroll"]) - float(r["starting_bankroll"]) - float(r["live_pnl"])
            console.print(f"  {r['name']}: bankroll drift €{delta:+.2f}")
    else:
        console.print("[green]✓ All active bots: bankroll ↔ pnl within €5 tolerance[/green]")

    # 5. Verdict
    if drifted or bb or only_in_cache or only_in_live:
        console.print(f"\n[red]{len(drifted)} drifted bots · {len(only_in_cache)} cache-only · {len(only_in_live)} live-only · {len(bb)} bankroll mismatch[/red]")
        if args.fail_on_drift:
            sys.exit(1)
    else:
        console.print(f"\n[green]✓ Cache + live + bankroll all reconcile (threshold {args.threshold*100:.0f}%, abs €{args.abs_threshold})[/green]")


if __name__ == "__main__":
    main()
