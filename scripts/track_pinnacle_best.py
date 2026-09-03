#!/usr/bin/env python3
"""PINNACLE-BEST-NO-EDGE — track whether "sharp book on top" predicts losses.

THE HYPOTHESIS
When `recommended_bookmaker` is Pinnacle, the SHARPEST book held the best
price, which means no soft book was offering value. Our edge is then measured
against a fair price, so there is nothing to take — we are betting into the
sharp line.

WHAT THE DATA SAYS (settled 1X2, priced at odds live at pick time)

    tier 1   Pinnacle best  n=112  ROI -23.5%   t=-1.72
             soft best      n=338  ROI +26.6%
    tier 2   Pinnacle best  n= 47  ROI +11.1%   <- OPPOSITE SIGN
             soft best      n= 99  ROI +24.8%

WHY THIS IS A TRACKER AND NOT A GATE
t=-1.72 on n=112 is suggestive, not decisive, and the tier-2 cell runs the
other way. A blanket "Pinnacle best => skip" rule would remove profitable
tier-2 picks — the exact mistake BOT-GATE-OU-BTTS exists to warn about. So the
rule is surfaced as a display warning on /admin/shadow-bots and measured here
until the tier-1 cell either reaches significance or reverts.

No new column: bookmaker, market and league tier are all already stored, so the
flag is derivable retrospectively. Adding a column would have created a second
source of truth for something computable.

    python3 scripts/track_pinnacle_best.py
    python3 scripts/track_pinnacle_best.py --since 2026-09-03   # forward-only
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

ACCESSIBLE = ("Unibet", "Betano", "Marathonbet", "10Bet", "888Sport",
              "Pinnacle", "Coolbet")


def _stats(v: list[float]) -> tuple[int, float, float]:
    n = len(v)
    if n < 2:
        return n, 0.0, 0.0
    m = sum(v) / n
    var = sum((x - m) ** 2 for x in v) / (n - 1)
    se = math.sqrt(var / n)
    return n, 100.0 * m / 10.0, (m / se if se else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2026-05-04")
    args = ap.parse_args()

    from workers.api_clients.db import execute_query

    rows = execute_query(
        """SELECT sb.recommended_bookmaker AS bk, sb.result,
                  sb.odds_at_pick_live::float AS ol, l.tier
             FROM simulated_bets sb
             JOIN bots b ON b.id = sb.bot_id
             JOIN matches m ON m.id = sb.match_id
             LEFT JOIN leagues l ON l.id = m.league_id
            WHERE sb.result IN ('won','lost')
              -- STALE-BEST-ODDS: odds_at_pick is a high-water mark, so this
              -- question can only be asked at prices that were really live.
              AND sb.odds_at_pick_live IS NOT NULL
              AND sb.market = '1x2'
              AND b.name NOT LIKE 'inplay_%%'
              AND sb.pick_time >= %s::date
              AND sb.recommended_bookmaker = ANY(%s)""",
        (args.since, list(ACCESSIBLE)),
    )
    if not rows:
        console.print("[yellow]No settled, live-priced 1X2 picks in range.[/yellow]")
        return 0

    def ret(r) -> float:
        return (r["ol"] - 1) * 10 if r["result"] == "won" else -10.0

    t = Table(title=f"Pinnacle-best vs soft-best, 1X2, since {args.since}",
              show_header=True, header_style="bold")
    for c in ("tier", "pin n", "pin ROI", "pin t", "soft n", "soft ROI", "gap"):
        t.add_column(c, justify="right" if c != "tier" else "left")

    tiers = sorted({r["tier"] for r in rows if r["tier"] is not None})
    for tier in tiers + ["ALL"]:
        sel = rows if tier == "ALL" else [r for r in rows if r["tier"] == tier]
        pin = [ret(r) for r in sel if r["bk"] == "Pinnacle"]
        soft = [ret(r) for r in sel if r["bk"] != "Pinnacle"]
        if len(pin) < 10 or len(soft) < 10:
            continue
        np_, rp, tp = _stats(pin)
        ns, rs, _ = _stats(soft)
        t.add_row(str(tier), str(np_), f"{rp:+.1f}%", f"{tp:+.2f}",
                  str(ns), f"{rs:+.1f}%", f"{rs - rp:+.1f}pp")
    console.print(t)
    console.print("  [dim]The rule is scoped to TIER 1 on purpose: tier 2 has run the "
                  "other way (+11.1%), and gating it would repeat the mistake "
                  "BOT-GATE-OU-BTTS warns about. Promote to a hard veto only when "
                  "the tier-1 cell clears |t| > 2 and tier 2 still shows nothing.[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
