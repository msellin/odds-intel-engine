"""
ODDS-QUALITY-CLEANUP — follow-up fix: un-void inplay bets.

Stage C of ODDS-QUALITY-CLEANUP voided 53 OU bets whose odds_at_pick had no
surviving snapshot in odds_snapshots. 14 of those were inplay bets
(xg_source='live' or bot name starts with 'inplay_') whose original snapshot
came from bookmaker='api-football-live' — the source we hard-deleted in
Stage B. The void was a false positive: the prices were genuine live
api-football-live snapshots that the inplay bots correctly used at the time
(inplay bots read from live_match_snapshots directly, not the polluted
odds_snapshots best-price aggregator, so the original bug never affected
them).

This script re-settles those 14 bets via settle_bet_result + the actual
match score, restores result + pnl, and recomputes bankrolls for the
affected bots. Idempotent: skips bets that no longer carry the cleanup
marker.

Dry-run by default; --apply to execute.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query, execute_write
from workers.jobs.settlement import settle_bet_result

console = Console()

CLEANUP_MARKER = "[ODDS-QUALITY-CLEANUP-2026-05-10]"
UNVOID_MARKER = "[ODDS-QUALITY-CLEANUP-UNVOID]"


def find_overvoided_inplay() -> list[dict]:
    """All bets we voided that should not have been (live source)."""
    rows = execute_query(
        """
        SELECT b.id, b.bot_id, b.match_id, b.market, b.selection,
               b.odds_at_pick, b.stake, b.pick_time, b.reasoning, b.xg_source,
               bo.name AS bot_name,
               m.score_home, m.score_away
          FROM simulated_bets b
          JOIN bots bo ON bo.id = b.bot_id
          JOIN matches m ON m.id = b.match_id
         WHERE b.reasoning LIKE %s
           AND (b.xg_source = 'live' OR bo.name LIKE 'inplay_%%')
           AND m.score_home IS NOT NULL
           AND m.score_away IS NOT NULL
        """,
        (f"%{CLEANUP_MARKER}%",),
    )
    return rows


def unvoid(bet: dict, apply: bool) -> dict:
    """Re-settle and update."""
    settled = settle_bet_result(
        {"market": bet["market"], "selection": bet["selection"],
         "stake": bet["stake"], "odds_at_pick": bet["odds_at_pick"]},
        int(bet["score_home"]),
        int(bet["score_away"]),
        closing_odds=None,  # CLV recompute not needed for inplay
    )
    if not apply:
        return settled

    new_reasoning = f"{UNVOID_MARKER} live api-football-live snapshot deleted in Stage B; bet was genuine. {bet.get('reasoning') or ''}".strip()
    execute_write(
        """UPDATE simulated_bets
              SET result = %s, pnl = %s, reasoning = %s
            WHERE id = %s""",
        [settled["result"], settled["pnl"], new_reasoning, bet["id"]],
    )
    return settled


def recompute_bankrolls(bot_ids: set, apply: bool) -> None:
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
        console.print(f"  {bot_name:<22} bankroll → [cyan]{running:.2f}[/cyan] ({len(rows)} settled bets)")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    apply = args.apply

    console.print(f"[bold cyan]ODDS-QUALITY-CLEANUP — unvoid inplay {'APPLY' if apply else 'DRY-RUN'}[/bold cyan]")

    bets = find_overvoided_inplay()
    if not bets:
        console.print("[green]Nothing to unvoid.[/green]")
        return

    console.print(f"\n[bold]Inplay bets to unvoid:[/bold] {len(bets)}")

    table = Table(title="Per-bet re-settlement")
    table.add_column("bot")
    table.add_column("selection")
    table.add_column("odds", justify="right")
    table.add_column("score", justify="right")
    table.add_column("→result")
    table.add_column("→pnl", justify="right")

    bot_ids: set = set()
    for b in bets:
        settled = unvoid(b, apply)
        bot_ids.add(b["bot_id"])
        table.add_row(
            b["bot_name"], b["selection"], f"{b['odds_at_pick']}",
            f"{b['score_home']}-{b['score_away']}",
            settled["result"], f"{settled['pnl']:+.2f}",
        )
    console.print(table)

    if bot_ids:
        console.print(f"\n[bold]Recomputing bankrolls for {len(bot_ids)} bot(s):[/bold]")
        recompute_bankrolls(bot_ids, apply)


if __name__ == "__main__":
    main()
