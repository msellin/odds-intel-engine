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
    # BANKROLL-SHADOW-BLIND (2026-09-21). `current_bankroll` is rebuilt from
    # `simulated_bets` ONLY, and 12 of the 14 active bots write no simulated
    # bets at all — their settled P&L lives in `shadow_bets`. So the column
    # reads `starting_bankroll` forever, with zero drift, for exactly the bots
    # whose signals the operator follows with real money
    # ([[project_realmoney_shadow_signals]]). "No drift" meant "no data", and
    # the two are indistinguishable in the old output.
    #
    # The column is NOT redefined to merge the two ledgers. They are different
    # bet streams (every bot is re-evaluated into shadow_bets every 30 min),
    # so adding them produces a number that is neither — bot_v10_all has 662
    # simulated and 5,626 shadow bets, and their sum describes nothing. What
    # changes is that the shadow ledger is REPORTED, so a shadow-only bot is
    # visibly "not applicable, here is where its money actually is" instead of
    # silently green.
    rows = execute_query("""
        SELECT b.id, b.name, b.starting_bankroll, b.current_bankroll,
               COALESCE(sim.pnl, 0) AS live_pnl,
               (b.starting_bankroll + COALESCE(sim.pnl, 0)) AS correct_bankroll,
               COALESCE(sim.n, 0)   AS sim_n,
               COALESCE(shd.n, 0)   AS shadow_n,
               COALESCE(shd.pnl, 0) AS shadow_pnl
        FROM bots b
        LEFT JOIN (
            SELECT bot_id, COUNT(*) AS n,
                   SUM(pnl) FILTER (WHERE result IN ('won','lost')) AS pnl
            FROM simulated_bets GROUP BY bot_id
        ) sim ON sim.bot_id = b.id
        LEFT JOIN (
            SELECT bot_id, COUNT(*) AS n,
                   SUM(pnl) FILTER (WHERE result IN ('won','lost')) AS pnl
            FROM shadow_bets GROUP BY bot_id
        ) shd ON shd.bot_id = b.id
        WHERE b.retired_at IS NULL
        ORDER BY b.name
    """)

    # Shadow-only bots: the column cannot drift because nothing ever moves it.
    # Reported first, because "this bot's bankroll is meaningless" is the more
    # useful fact than "these three bots are €4 out".
    shadow_only = [r for r in rows
                   if int(r["sim_n"]) == 0 and int(r["shadow_n"]) > 0]
    if shadow_only:
        st = Table(title=f"Bankroll NOT APPLICABLE — {len(shadow_only)} shadow-only bots")
        for c in ("bot", "stored bankroll", "shadow bets", "shadow P&L"):
            st.add_column(c)
        for r in sorted(shadow_only, key=lambda r: -abs(float(r["shadow_pnl"] or 0))):
            st.add_row(r["name"],
                       f"€{float(r['current_bankroll']):.2f}",
                       str(int(r["shadow_n"])),
                       f"€{float(r['shadow_pnl'] or 0):+,.2f}")
        console.print(st)
        console.print("[dim]current_bankroll tracks simulated_bets only; these bots "
                      "write none, so it is frozen at starting_bankroll by "
                      "construction. Their real P&L is the shadow column.[/dim]\n")
    drifted = [
        r for r in rows
        if abs(float(r["current_bankroll"]) - float(r["correct_bankroll"])) > 0.50
    ]
    if not drifted:
        console.print("[green]✓ No drift among bots that write simulated_bets[/green]")
        return

    t = Table(title=f"Bankroll drift — {len(drifted)} bots with a simulated ledger")
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
