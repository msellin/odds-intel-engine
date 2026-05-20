"""Place ONE real-money bet via Coolbet, interactively, then exit.

The safe path to your first live auto-placement. Unlike the daemon
(which loops over every qualifying pending bet), this script:
  • Loads today's pending value bets (≥5% edge by default)
  • Lets you pick one from a numbered list
  • Asks for final y/N confirmation
  • POSTs to Coolbet's bet API
  • Records the result to real_bets + Telegram-pings the outcome

Use this for your first execute-mode trial. After it works once, you
can confidently flip the daemon to --place-mode=execute.

Usage:
    python3 scripts/place_one_real_bet.py

    # higher edge threshold (e.g. only show 7%+ edges)
    python3 scripts/place_one_real_bet.py --min-edge 0.07

    # custom stake cap
    python3 scripts/place_one_real_bet.py --max-stake 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table

from workers.automation.coolbet_session import CoolbetSession
from workers.automation.coolbet_placer import (
    PlacementGuard, fetch_coolbet_events, fuzzy_match_event,
    search_coolbet_event, _place_bet_api, load_qualified_bets,
)
from workers.automation.coolbet_explorer import (
    fetch_match_markets, fetch_odds_for_markets, resolve_placement_target,
)
from workers.api_clients.supabase_client import store_real_bet, store_coolbet_odds_snapshot
from workers.notify.telegram import send_telegram

console = Console()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-edge", type=float, default=0.05,
                    help="Minimum edge (decimal) to consider, default 0.05 = 5%%")
    ap.add_argument("--max-stake", type=float, default=2.0,
                    help="Cap on per-bet stake regardless of Kelly (default €2)")
    ap.add_argument("--use-kelly-stake", action="store_true", default=True,
                    help="Use Kelly stake from simulated_bets (default on)")
    args = ap.parse_args()

    # 1. Find qualifying bets
    import workers.automation.coolbet_placer as cp
    cp._MIN_EDGE = args.min_edge   # placer reads this module-level constant
    pending = load_qualified_bets()
    if not pending:
        console.print("[yellow]No qualifying pending bets at this edge threshold.[/yellow]")
        return 0

    guard = PlacementGuard(
        use_kelly_stake=args.use_kelly_stake,
        max_stake_per_bet=args.max_stake,
        require_confirm=False,  # we have our own confirm flow below
    )

    # 2. Show the menu
    t = Table(show_header=True, title=f"Pending value bets (edge ≥ {args.min_edge*100:.0f}%)")
    t.add_column("#")
    t.add_column("Match")
    t.add_column("Bet")
    t.add_column("Model odds", justify="right")
    t.add_column("Edge", justify="right")
    t.add_column("Kelly stake", justify="right")
    for i, b in enumerate(pending, 1):
        kelly = guard.stake_for(b)
        t.add_row(
            str(i),
            f"{b['home_team']} vs {b['away_team']}",
            f"{b['market']} {b['selection']}",
            f"{float(b['model_odds']):.3f}",
            f"{float(b['edge_percent']) * 100:.2f}%",
            f"€{kelly:.2f}",
        )
    console.print(t)

    # 3. Pick one
    try:
        choice = input(f"\nPick a bet # to PLACE FOR REAL (1-{len(pending)}, or q to quit): ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if choice.lower() in ("q", "quit", "exit", ""):
        return 0
    try:
        idx = int(choice) - 1
        assert 0 <= idx < len(pending)
    except Exception:
        console.print("[red]Invalid choice — exiting.[/red]")
        return 1
    bet = pending[idx]
    stake = guard.stake_for(bet)

    # 4. Find Coolbet event for this match
    console.print(f"\nResolving Coolbet event for {bet['home_team']} vs {bet['away_team']} …")
    session = CoolbetSession()
    ev = search_coolbet_event(session, bet["home_team"], bet["away_team"])
    if ev is None:
        # fo-category fallback (might 403 — handle gracefully)
        try:
            cat_events = fetch_coolbet_events(session)
            ev = fuzzy_match_event(bet["home_team"], bet["away_team"], cat_events)
        except Exception:
            ev = None
    if ev is None:
        console.print("[red]Could not find a Coolbet event for this match. Aborting.[/red]")
        return 1

    # 5. Resolve target market+outcome+odds_id
    markets = fetch_match_markets(session, int(ev["id"]))
    odds_map = fetch_odds_for_markets(session, markets)
    target = resolve_placement_target(markets, odds_map, bet["market"], bet["selection"])
    if target is None:
        console.print(f"[red]Coolbet doesn't expose this market/selection ({bet['market']} "
                      f"{bet['selection']}). Aborting.[/red]")
        return 1
    market_id, outcome_id, odds_id, current_odds = target

    # 6. Final confirmation with all details
    console.print()
    console.print(f"[bold]FINAL CONFIRMATION:[/bold]")
    console.print(f"  Match:     {ev['home']} vs {ev['away']}")
    console.print(f"  Bet:       {bet['market']} {bet['selection']}")
    console.print(f"  Coolbet:   match_id={ev['id']}  market_id={market_id}  outcome_id={outcome_id}")
    console.print(f"  Odds now:  {current_odds:.3f}  (your model: {float(bet['model_odds']):.3f})")
    console.print(f"  Stake:     [yellow]€{stake:.2f}[/yellow]  (REAL money)")
    try:
        ans = input("\nProceed with REAL bet at Coolbet? Type 'yes' to confirm: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return 0
    if ans != "yes":
        console.print("[yellow]Cancelled — no bet placed.[/yellow]")
        return 0

    # 7. POST to Coolbet
    console.print("[cyan]Placing bet at Coolbet…[/cyan]")
    try:
        match_name = f"{ev['home']} - {ev['away']}"
        ticket_id = _place_bet_api(
            session, outcome_id, odds_id, stake, match_name,
            f"{bet['market']} {bet['selection']}",
        )
    except Exception as e:
        console.print(f"[red]Coolbet API error: {e}[/red]")
        send_telegram(f"❌ Manual one-bet trial FAILED: {e}", dedup_key=None)
        return 1
    console.print(f"[bold green]✓ Placed at Coolbet![/bold green]  ticket={ticket_id}")

    # 8. Snapshot odds + record to real_bets
    try:
        store_coolbet_odds_snapshot(
            str(bet["match_id"]), bet["market"], bet["selection"], current_odds, None,
        )
    except Exception:
        pass
    real_bet_id = store_real_bet(
        match_id=str(bet["match_id"]),
        market=bet["market"],
        selection=bet["selection"],
        bookmaker="Coolbet",
        captured_odds=current_odds,
        actual_odds=current_odds,
        stake=stake,
        bot_id=str(bet["bot_id"]),
        simulated_bet_id=str(bet["simulated_bet_id"]),
        notes=f"manual one-bet trial ticket={ticket_id}",
    )
    console.print(f"  recorded as real_bet={real_bet_id}")

    # 9. Telegram ping
    send_telegram(
        f"💰 <b>FIRST EXECUTE</b> — manually placed:\n"
        f"  {ev['home']} vs {ev['away']}\n"
        f"  {bet['market']} {bet['selection']} @ {current_odds:.3f}\n"
        f"  €{stake:.2f}  ticket {ticket_id}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
