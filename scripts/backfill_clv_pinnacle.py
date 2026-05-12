"""
OddsIntel — Backfill clv_pinnacle on settled bets (PIN-5 backfill)

Computes clv_pinnacle = (odds_at_pick / pinnacle_closing_odds) - 1 for all
settled simulated_bets that currently have clv_pinnacle = NULL.

Pinnacle closing odds come from odds_snapshots WHERE bookmaker = 'Pinnacle',
preferring is_closing = TRUE snapshots, falling back to the latest snapshot.

Safe to re-run: only updates rows where clv_pinnacle IS NULL.

Run:
    python3 scripts/backfill_clv_pinnacle.py
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn

from workers.api_clients.db import execute_query, execute_write

console = Console()


def _normalize_market(market: str) -> str:
    m = market.strip().lower()
    if m in ("1x2", "1×2"):
        return "1x2"
    if m in ("o/u", "ou", "over/under"):
        return "over_under_25"
    return m


def _normalize_selection(selection: str) -> str:
    s = selection.strip().lower()
    if s in ("home", "h"):
        return "home"
    if s in ("away", "a"):
        return "away"
    if s in ("draw", "d", "x"):
        return "draw"
    if s.startswith("over"):
        return "over"
    if s.startswith("under"):
        return "under"
    return s


def run():
    console.print("[cyan]Backfill clv_pinnacle on settled bets[/cyan]")

    bets = execute_query(
        """SELECT id, match_id, market, selection, odds_at_pick
           FROM simulated_bets
           WHERE result != 'pending'
             AND clv_pinnacle IS NULL
           ORDER BY created_at""",
        [],
    )
    if not bets:
        console.print("[green]Nothing to backfill — all settled bets already have clv_pinnacle.[/green]")
        return

    console.print(f"  {len(bets):,} settled bets without clv_pinnacle")

    match_ids = list({b["match_id"] for b in bets})
    snap_rows = execute_query(
        """SELECT DISTINCT ON (match_id, market, selection)
               match_id, market, selection, odds
           FROM odds_snapshots
           WHERE match_id = ANY(%s::uuid[])
             AND bookmaker = 'Pinnacle'
             AND odds > 1.0
           ORDER BY match_id, market, selection,
                    is_closing DESC, timestamp DESC""",
        (match_ids,),
    )

    snap_idx: dict[tuple, float] = {}
    for r in snap_rows:
        key = (str(r["match_id"]), r["market"], r["selection"])
        snap_idx[key] = float(r["odds"])

    updated = 0
    missing = 0

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("bets", total=len(bets))
        for bet in bets:
            mkt = _normalize_market(bet["market"])
            sel = _normalize_selection(bet["selection"])
            key = (str(bet["match_id"]), mkt, sel)
            pin_close = snap_idx.get(key)

            if pin_close is None or pin_close <= 1.0:
                missing += 1
                progress.advance(task)
                continue

            odds_at_pick = float(bet["odds_at_pick"])
            clv_pin = round((odds_at_pick / pin_close) - 1, 4)

            execute_write(
                "UPDATE simulated_bets SET clv_pinnacle = %s WHERE id = %s",
                [clv_pin, bet["id"]],
            )
            updated += 1
            progress.advance(task)

    console.print(f"[green]Done — {updated:,} updated, {missing:,} skipped (no Pinnacle snapshot)[/green]")


if __name__ == "__main__":
    run()
