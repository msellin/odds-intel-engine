"""BOT-BANKROLL-DRIFT — one-shot fix: rebuild bots.current_bankroll from bet history.

Background: 11 active bots have current_bankroll != starting_bankroll + SUM(pnl).
Likely cause: un-retire/re-retire cycles (migrations 117, 122) reset bankroll
without rebuilding from bet history. This is display-only — bot stake sizing
does NOT use current_bankroll (it uses a hardcoded €1000 base), so this is
purely a UI correctness issue.

What it does:
  UPDATE bots SET current_bankroll = starting_bankroll + COALESCE(SUM(pnl), 0)
  FROM (SELECT bot_id, SUM(pnl) AS pnl FROM simulated_bets
        WHERE result IN ('won','lost') GROUP BY bot_id) bp
  WHERE bots.id = bp.bot_id

Idempotent. Dry-run by default; --apply to commit.

Run:
  python3 scripts/fix_bot_bankroll_drift.py             # report drift only
  python3 scripts/fix_bot_bankroll_drift.py --apply     # rebuild + commit
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

from workers.api_clients.db import execute_query, get_conn

console = Console()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    console.print("[bold]BOT-BANKROLL-DRIFT — diagnose + rebuild current_bankroll[/bold]")
    rows = execute_query("""
        SELECT b.id, b.name, b.starting_bankroll, b.current_bankroll,
               COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')), 0) AS live_pnl,
               (b.starting_bankroll + COALESCE(SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')), 0)) AS correct_bankroll
        FROM bots b
        LEFT JOIN simulated_bets sb ON sb.bot_id = b.id
        GROUP BY b.id, b.name, b.starting_bankroll, b.current_bankroll
        ORDER BY b.name
    """)
    drifted = [
        r for r in rows
        if abs(float(r["current_bankroll"]) - float(r["correct_bankroll"])) > 0.50
    ]
    if not drifted:
        console.print("[green]✓ No drift — bankrolls already match bet history[/green]")
        return

    t = Table(title=f"Bankroll drift — {len(drifted)} bots")
    for c in ("bot", "starting", "current (stored)", "correct (from pnl)", "drift"):
        t.add_column(c)
    for r in drifted:
        cur = float(r["current_bankroll"])
        corr = float(r["correct_bankroll"])
        drift = cur - corr
        t.add_row(
            r["name"],
            f"€{float(r['starting_bankroll']):.2f}",
            f"€{cur:.2f}",
            f"€{corr:.2f}",
            f"€{drift:+.2f}",
        )
    console.print(t)

    if not args.apply:
        console.print("\n[yellow]Dry run — pass --apply to rebuild current_bankroll from pnl history[/yellow]")
        return

    # Apply: one UPDATE FROM aggregating per-bot pnl
    console.print("\n[bold]Rebuilding current_bankroll from bet history...[/bold]")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE bots b
                SET current_bankroll = b.starting_bankroll + bp.live_pnl
                FROM (
                    SELECT bot_id,
                           COALESCE(SUM(pnl) FILTER (WHERE result IN ('won','lost')), 0) AS live_pnl
                    FROM simulated_bets
                    GROUP BY bot_id
                ) bp
                WHERE b.id = bp.bot_id
                  AND ABS(b.current_bankroll - (b.starting_bankroll + bp.live_pnl)) > 0.50
            """)
            n = cur.rowcount
        conn.commit()
    console.print(f"[green]✓ Updated {n} bot rows[/green]")


if __name__ == "__main__":
    main()
