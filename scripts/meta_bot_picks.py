"""META-BOT-PORTFOLIO — simultaneous-Kelly across selected bots (personal use).

Implements Steps 1-7 from dev/active/meta-bot-portfolio-plan.md:
  1. Pool pending bets from --bots
  2. Resolve conflicts (opposite sides → net; same side → higher edge wins)
  3. Correlation discount (0.5× for 2nd+ bet on same match)
  4. Simultaneous Kelly via scipy.optimize.minimize over 2^N outcome space
  5. Half-Kelly (×0.5)
  6. Cap total exposure at 20% of bankroll
  7. Round to €0.50, drop sub-€1

Status: STUB. The algorithm is implemented but bot selection is parameterised
because we don't yet know which bots have real-money +ROI. Once the 200-bet
cohort report at ~2026-06-30 identifies the winners, hardcode the default
list and the script becomes a daily-driver.

Usage:
  python3 scripts/meta_bot_picks.py --bots bot_v10_all,bot_aggressive_v2 --bankroll 1000

If --bots omitted: prints WAITING message + exits.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import numpy as np
from scipy.optimize import minimize
from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()


def fetch_pending_bets(bot_names: list[str]) -> list[dict]:
    rows = execute_query("""
        SELECT sb.id, sb.match_id, sb.market, sb.selection,
               sb.odds_at_pick AS odds,
               sb.calibrated_prob, sb.edge_percent, sb.kelly_fraction,
               b.name AS bot_name,
               th.name AS home_team, ta.name AS away_team,
               m.date AS kickoff
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        LEFT JOIN teams th ON th.id = m.home_team_id
        LEFT JOIN teams ta ON ta.id = m.away_team_id
        WHERE b.name = ANY(%s)
          AND sb.result = 'pending'
          AND m.date > NOW()
        ORDER BY sb.match_id, sb.edge_percent DESC
    """, (bot_names,))
    return rows or []


def resolve_conflicts(bets: list[dict]) -> list[dict]:
    by_market: dict[tuple, list[dict]] = {}
    for b in bets:
        key = (str(b["match_id"]), b["market"])
        by_market.setdefault(key, []).append(b)

    keep: list[dict] = []
    for (match_id, market), group in by_market.items():
        sides = {b["selection"]: b for b in group}
        if len(sides) == 1:
            best = max(group, key=lambda x: float(x["edge_percent"] or 0))
            keep.append(best)
        else:
            # Opposite sides — net Kelly: pick the side with highest edge,
            # subtract opposite-side Kelly. Net ≤ 0 → drop both.
            top = max(sides.values(), key=lambda x: float(x["edge_percent"] or 0))
            opp_kelly = sum(
                float(b["kelly_fraction"] or 0)
                for s, b in sides.items() if s != top["selection"]
            )
            net = float(top["kelly_fraction"] or 0) - opp_kelly
            if net > 0:
                top = {**top, "kelly_fraction": net}
                keep.append(top)
    return keep


def correlation_discount(bets: list[dict]) -> list[dict]:
    by_match: dict[str, list[dict]] = {}
    for b in bets:
        by_match.setdefault(str(b["match_id"]), []).append(b)

    out = []
    for match_id, group in by_match.items():
        group.sort(key=lambda x: float(x["edge_percent"] or 0), reverse=True)
        for i, b in enumerate(group):
            mult = 1.0 if i == 0 else 0.5 ** i  # 1.0, 0.5, 0.25...
            b_out = {**b, "_corr_mult": mult}
            out.append(b_out)
    return out


def simultaneous_kelly(bets: list[dict]) -> np.ndarray:
    """Return optimal fractions for the list of bets, jointly Kelly-sized."""
    n = len(bets)
    if n == 0:
        return np.array([])
    p = np.array([float(b["calibrated_prob"] or 0) for b in bets])
    b_arr = np.array([float(b["odds"] or 1) - 1 for b in bets])

    def neg_logwealth(f: np.ndarray) -> float:
        f = np.clip(f, 0, 1)
        # Enumerate 2^n outcome scenarios
        total = 0.0
        for scenario in range(2 ** n):
            mask = np.array([(scenario >> i) & 1 for i in range(n)])
            prob = float(np.prod(np.where(mask == 1, p, 1 - p)))
            growth = float(1.0 + np.sum(np.where(mask == 1, f * b_arr, -f)))
            if growth <= 0:
                return 1e9  # invalid scenario — penalise heavily
            total += prob * np.log(growth)
        return -total

    x0 = np.array([float(b["kelly_fraction"] or 0) for b in bets])
    x0 = np.clip(x0, 0, 0.1)
    bounds = [(0.0, 0.5) for _ in range(n)]
    res = minimize(neg_logwealth, x0, method="L-BFGS-B", bounds=bounds)
    return np.clip(res.x, 0, 0.5)


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bots", help="Comma-separated bot names")
    ap.add_argument("--bankroll", type=float, default=1000.0)
    ap.add_argument("--cap", type=float, default=0.20, help="Max total exposure as bankroll fraction")
    args = ap.parse_args(list(argv) if argv else None)

    if not args.bots:
        console.print("[yellow]META-BOT-PORTFOLIO is WAITING.[/yellow]")
        console.print("Once the 200-bet cohort report (~2026-06-30) identifies "
                      "which bots have real +ROI, pass them via --bots:")
        console.print("  python3 scripts/meta_bot_picks.py --bots bot_a,bot_b --bankroll 1000")
        return 0

    bot_names = [s.strip() for s in args.bots.split(",") if s.strip()]
    console.print(f"\n[bold]META-BOT-PORTFOLIO — sizing for {bot_names} (bankroll €{args.bankroll:.0f})[/bold]")
    bets = fetch_pending_bets(bot_names)
    if not bets:
        console.print("[yellow]No pending bets for the selected bots.[/yellow]")
        return 0
    console.print(f"  Step 1: pooled {len(bets)} pending bets")
    after_conflict = resolve_conflicts(bets)
    console.print(f"  Step 2: {len(after_conflict)} after conflict-resolution")
    after_corr = correlation_discount(after_conflict)
    fracs = simultaneous_kelly(after_corr)
    console.print(f"  Step 4: scipy returned {len(fracs)} joint fractions")
    half = fracs * 0.5
    # Apply correlation multiplier
    corr_mults = np.array([b["_corr_mult"] for b in after_corr])
    final_fracs = half * corr_mults
    # Cap total exposure
    total = float(np.sum(final_fracs))
    if total > args.cap:
        scale = args.cap / total
        final_fracs = final_fracs * scale
        console.print(f"  Step 6: scaled by {scale:.3f} to respect {args.cap * 100:.0f}% cap")

    t = Table(title="Portfolio recommendations")
    for c in ("match", "market", "selection", "odds", "edge%", "stake €"):
        t.add_column(c)
    placed = 0
    for b, f in zip(after_corr, final_fracs):
        stake = round((float(f) * args.bankroll) / 0.5) * 0.5
        if stake < 1.0:
            continue
        placed += 1
        t.add_row(
            f"{b['home_team']} vs {b['away_team']}",
            b["market"], b["selection"],
            f"{float(b['odds']):.2f}",
            f"{float(b['edge_percent'] or 0):+.2f}",
            f"€{stake:.2f}",
        )
    console.print(t)
    console.print(f"\n[green]Total recommended bets: {placed}[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
