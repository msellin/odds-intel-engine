"""
ODDS-QUALITY-CLEANUP — Stage C: void/delete OU bets that used garbage prices.

Logic per simulated_bets row (market='O/U'):
  1. Look up surviving odds_snapshots rows for the same (match_id, OU line, side)
     within ±5% of odds_at_pick at any time before pick_time.
  2. If no surviving snapshot can be matched → the price came from a row we just
     deleted in Stage B → the bet is unbacked.
     - Settled (won/lost): mark result='void', pnl=0, prepend marker to reasoning.
     - Pending: delete the row.
  3. After voiding, recompute bots.current_bankroll = starting + sum(pnl) for any
     bot with at least one affected bet, and rewrite simulated_bets.bankroll_after
     in chronological order.

Idempotent: rows whose reasoning already starts with the cleanup marker are skipped.
Dry-run by default; pass --apply to execute.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, execute_write

console = Console()

CLEANUP_MARKER = "[ODDS-QUALITY-CLEANUP-2026-05-10]"
TOLERANCE = 0.05  # ±5% price match window


def _ou_market_label(selection: str) -> str | None:
    """Map a bet selection like 'over 1.5' / 'Over 1.5' to its odds_snapshots market label."""
    if not selection:
        return None
    parts = selection.lower().split()
    if len(parts) < 2:
        return None
    try:
        line = float(parts[1])
    except ValueError:
        return None
    return f"over_under_{str(line).replace('.', '')}"


def _selection_side(selection: str) -> str | None:
    if not selection:
        return None
    head = selection.lower().split()[0]
    return head if head in ("over", "under") else None


def find_orphans() -> list[dict]:
    """Find OU bets whose odds_at_pick has no matching surviving snapshot."""
    bets = execute_query(
        """
        SELECT b.id, b.bot_id, b.match_id, b.market, b.selection,
               b.odds_at_pick, b.pick_time, b.result, b.pnl, b.reasoning,
               bo.name AS bot_name
          FROM simulated_bets b
          JOIN bots bo ON bo.id = b.bot_id
         WHERE b.market = 'O/U'
           AND b.odds_at_pick IS NOT NULL
        """
    )
    orphans = []
    for b in bets:
        # Skip already-processed rows (idempotency).
        if b.get("reasoning") and b["reasoning"].startswith(CLEANUP_MARKER):
            continue
        market_label = _ou_market_label(b["selection"])
        side = _selection_side(b["selection"])
        if not market_label or not side:
            continue
        odds = float(b["odds_at_pick"])
        lo, hi = odds * (1 - TOLERANCE), odds * (1 + TOLERANCE)
        match = execute_query(
            """
            SELECT 1 FROM odds_snapshots
             WHERE match_id  = %s
               AND market    = %s
               AND selection = %s
               AND odds BETWEEN %s AND %s
               AND timestamp <= %s
             LIMIT 1
            """,
            (b["match_id"], market_label, side, lo, hi, b["pick_time"]),
        )
        if not match:
            orphans.append(b)
    return orphans


def void_or_delete(orphans: list[dict], apply: bool) -> tuple[int, int, set]:
    """Void settled rows, delete pending rows. Returns (voided, deleted, affected_bots)."""
    settled = [o for o in orphans if o["result"] in ("won", "lost")]
    pending = [o for o in orphans if o["result"] == "pending"]
    affected_bots = {o["bot_id"] for o in orphans}

    if not apply:
        return len(settled), len(pending), affected_bots

    for o in settled:
        new_reasoning = (
            f"{CLEANUP_MARKER} voided (orig odds {o['odds_at_pick']}, no surviving snapshot). "
            f"{o.get('reasoning') or ''}"
        ).strip()
        execute_write(
            """UPDATE simulated_bets
                  SET result = 'void', pnl = 0, reasoning = %s
                WHERE id = %s""",
            [new_reasoning, o["id"]],
        )
    for o in pending:
        execute_write("DELETE FROM simulated_bets WHERE id = %s", [o["id"]])

    return len(settled), len(pending), affected_bots


def recompute_bankrolls(bot_ids: set, apply: bool) -> None:
    """For each affected bot: rewrite bankroll_after running totals + current_bankroll."""
    for bot_id in bot_ids:
        bot = execute_query(
            "SELECT id, name, starting_bankroll FROM bots WHERE id = %s",
            [bot_id],
        )
        if not bot:
            continue
        starting = float(bot[0]["starting_bankroll"])
        bot_name = bot[0]["name"]

        rows = execute_query(
            """SELECT id, pnl FROM simulated_bets
                WHERE bot_id = %s AND result IN ('won','lost','void')
                ORDER BY pick_time""",
            [bot_id],
        )
        running = starting
        for r in rows:
            running += float(r["pnl"] or 0)
            if apply:
                execute_write(
                    "UPDATE simulated_bets SET bankroll_after = %s WHERE id = %s",
                    [round(running, 2), r["id"]],
                )
        if apply:
            execute_write(
                "UPDATE bots SET current_bankroll = %s, updated_at = NOW() WHERE id = %s",
                [round(running, 2), bot_id],
            )
        console.print(
            f"  {bot_name:<28} bankroll → [cyan]{running:.2f}[/cyan] "
            f"(starting {starting:.2f}, {len(rows)} settled bets)"
        )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    apply = args.apply

    console.print(f"[bold cyan]ODDS-QUALITY-CLEANUP — Stage C {'APPLY' if apply else 'DRY-RUN'}[/bold cyan]")

    orphans = find_orphans()
    console.print(f"\n[bold]Orphan OU bets:[/bold] {len(orphans):,}")
    if not orphans:
        console.print("[green]No orphan bets — nothing to do.[/green]")
        return

    by_bot: dict[str, list] = {}
    for o in orphans:
        by_bot.setdefault(o["bot_name"], []).append(o)
    table = Table(title="Orphan bets per bot (settled / pending)")
    table.add_column("bot")
    table.add_column("settled won/lost", justify="right")
    table.add_column("pending", justify="right")
    table.add_column("settled pnl Σ", justify="right")
    for name in sorted(by_bot):
        rows = by_bot[name]
        s = [r for r in rows if r["result"] in ("won", "lost")]
        pen = [r for r in rows if r["result"] == "pending"]
        spnl = sum(float(r["pnl"] or 0) for r in s)
        table.add_row(name, str(len(s)), str(len(pen)), f"{spnl:+.2f}")
    console.print(table)

    voided, deleted, bot_ids = void_or_delete(orphans, apply)
    console.print(
        f"\n[bold]{'Voided' if apply else 'Would void'}:[/bold] {voided:,} settled bets"
    )
    console.print(
        f"[bold]{'Deleted' if apply else 'Would delete'}:[/bold] {deleted:,} pending bets"
    )

    if bot_ids:
        console.print(f"\n[bold]Recomputing bankrolls for {len(bot_ids)} bot(s):[/bold]")
        recompute_bankrolls(bot_ids, apply)


if __name__ == "__main__":
    main()
