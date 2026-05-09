"""
One-shot fix-up for bets that were settled by the buggy settle_bet_result:

  - BTTS yes/no never won (market not handled).
  - O/U with line in selection (e.g. `O/U` + `over 1.5`) used a default 2.5 line.

Re-runs settle_bet_result on every 'won'/'lost' simulated_bets row, updates
result + pnl where they differ, then recomputes current_bankroll and the
running bankroll_after column for every bot whose bets changed.

Idempotent — safe to run twice.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, execute_write
from workers.jobs.settlement import settle_bet_result

console = Console()


def main(dry_run: bool = False) -> None:
    rows = execute_query(
        """
        SELECT b.id, b.bot_id, b.match_id, b.market, b.selection,
               b.odds_at_pick, b.stake, b.result, b.pnl, b.pick_time,
               m.score_home, m.score_away
        FROM simulated_bets b
        JOIN matches m ON m.id = b.match_id
        WHERE b.result IN ('won', 'lost')
          AND m.score_home IS NOT NULL
          AND m.score_away IS NOT NULL
        ORDER BY b.bot_id, b.pick_time
        """,
        []
    )
    console.print(f"Scanned [cyan]{len(rows)}[/cyan] settled bets")

    changed: list[dict] = []
    for r in rows:
        new = settle_bet_result(
            {
                "market": r["market"],
                "selection": r["selection"],
                "stake": r["stake"],
                "odds_at_pick": r["odds_at_pick"],
            },
            int(r["score_home"]),
            int(r["score_away"]),
            None,
        )
        if new["result"] != r["result"]:
            r["new_result"] = new["result"]
            r["new_pnl"] = new["pnl"]
            changed.append(r)

    if not changed:
        console.print("[green]Nothing to fix — all settled bets already correct.[/green]")
        return

    t = Table(title=f"{len(changed)} bets to re-settle")
    t.add_column("bot_id"); t.add_column("market/selection"); t.add_column("score")
    t.add_column("was"); t.add_column("now"); t.add_column("Δpnl", justify="right")
    affected_bots: set[str] = set()
    for r in changed:
        affected_bots.add(str(r["bot_id"]))
        delta = float(r["new_pnl"]) - float(r["pnl"])
        t.add_row(
            str(r["bot_id"])[:8],
            f"{r['market']}/{r['selection']}",
            f"{r['score_home']}-{r['score_away']}",
            r["result"],
            r["new_result"],
            f"{delta:+.2f}",
        )
    console.print(t)

    if dry_run:
        console.print("[yellow]DRY RUN — no DB writes[/yellow]")
        return

    # 1. Update each changed bet's result + pnl. bankroll_after recomputed below.
    for r in changed:
        execute_write(
            "UPDATE simulated_bets SET result = %s, pnl = %s WHERE id = %s",
            [r["new_result"], r["new_pnl"], r["id"]],
        )
    console.print(f"[green]Updated {len(changed)} simulated_bets rows[/green]")

    # 2. For each affected bot, recompute current_bankroll = starting + sum(pnl)
    # and rewrite bankroll_after as a running total ordered by pick_time.
    for bot_id in affected_bots:
        bot = execute_query(
            "SELECT name, starting_bankroll FROM bots WHERE id = %s",
            [bot_id],
        )
        if not bot:
            continue
        starting = float(bot[0]["starting_bankroll"])
        bot_name = bot[0]["name"]

        # Walk the bot's whole settled history in chronological order.
        bet_rows = execute_query(
            """
            SELECT id, pnl, result FROM simulated_bets
            WHERE bot_id = %s AND result IN ('won', 'lost', 'void')
            ORDER BY pick_time
            """,
            [bot_id],
        )
        running = starting
        for br in bet_rows:
            running += float(br["pnl"] or 0)
            execute_write(
                "UPDATE simulated_bets SET bankroll_after = %s WHERE id = %s",
                [round(running, 2), br["id"]],
            )

        execute_write(
            "UPDATE bots SET current_bankroll = %s WHERE id = %s",
            [round(running, 2), bot_id],
        )
        console.print(f"  {bot_name}: bankroll → [cyan]{running:.2f}[/cyan]")


if __name__ == "__main__":
    import sys
    main(dry_run="--dry-run" in sys.argv)
