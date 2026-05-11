"""
OddsIntel — Odds Realism Audit

Three questions:
  1. TIMING: How many hours before kickoff are we placing bets?
  2. PINNACLE GAP: How much worse is Pinnacle vs best-across-books (odds_at_pick)?
  3. DRIFT: How much do Pinnacle odds move from placement time to kickoff?

Together these quantify how much of our reported +12% CLV is paper-trading
optimism vs edge we could actually capture at a real book.

Usage:
    python3 scripts/odds_realism_audit.py
    python3 scripts/odds_realism_audit.py --market 1x2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.text import Text

from workers.api_clients.db import execute_query

console = Console()


def section_timing(market_filter: str | None):
    """How many hours before kickoff are bets placed?"""
    mkt_clause = "AND sb.market = %s" if market_filter else ""
    params = [market_filter] if market_filter else []

    rows = execute_query(f"""
        SELECT
            sb.market,
            COUNT(*) as bets,
            AVG(EXTRACT(EPOCH FROM (m.date::timestamptz - sb.pick_time)) / 3600) as avg_h,
            PERCENTILE_CONT(0.25) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (m.date::timestamptz - sb.pick_time)) / 3600
            ) as p25_h,
            PERCENTILE_CONT(0.50) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (m.date::timestamptz - sb.pick_time)) / 3600
            ) as p50_h,
            PERCENTILE_CONT(0.75) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (m.date::timestamptz - sb.pick_time)) / 3600
            ) as p75_h,
            MIN(EXTRACT(EPOCH FROM (m.date::timestamptz - sb.pick_time)) / 3600) as min_h,
            MAX(EXTRACT(EPOCH FROM (m.date::timestamptz - sb.pick_time)) / 3600) as max_h
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.result IN ('won','lost')
          AND m.date IS NOT NULL
          {mkt_clause}
        GROUP BY sb.market
        ORDER BY bets DESC
    """, params)

    console.print("\n[bold cyan]1. TIMING — Hours before kickoff when bet is placed[/bold cyan]")
    console.print("   (Odds we use are from the snapshot AT placement time — if match is at 20:45,")
    console.print("    morning bets use 07:00 odds. Edge may be gone by kickoff.)\n")

    t = Table(show_lines=False)
    t.add_column("Market", style="cyan")
    t.add_column("Bets", justify="right")
    t.add_column("Avg h", justify="right")
    t.add_column("p25 h", justify="right")
    t.add_column("p50 h", justify="right")
    t.add_column("p75 h", justify="right")
    t.add_column("Range", justify="right", style="dim")

    for r in rows:
        avg = float(r["avg_h"] or 0)
        t.add_row(
            r["market"],
            str(int(r["bets"])),
            f"{float(r['avg_h'] or 0):.1f}h",
            f"{float(r['p25_h'] or 0):.1f}h",
            f"{float(r['p50_h'] or 0):.1f}h",
            f"{float(r['p75_h'] or 0):.1f}h",
            f"{float(r['min_h'] or 0):.0f}-{float(r['max_h'] or 0):.0f}h",
        )

    console.print(t)


def section_pinnacle_gap(market_filter: str | None):
    """Compare odds_at_pick (best-across-books) to Pinnacle's odds at the same time."""
    mkt_clause = "AND sb.market = %s" if market_filter else ""
    params = [market_filter] if market_filter else []

    # For each bet, find the nearest Pinnacle snapshot at or before pick_time
    # for the same match/market/selection.
    # We use a LATERAL join to get closest Pinnacle snapshot.
    rows = execute_query(f"""
        SELECT
            sb.market,
            sb.selection,
            COUNT(*) as bets,
            AVG(sb.odds_at_pick) as avg_pick_odds,
            AVG(pin.odds) as avg_pinnacle_odds,
            AVG(sb.odds_at_pick - pin.odds) as avg_gap,
            AVG((sb.odds_at_pick - pin.odds) / NULLIF(pin.odds, 0)) as avg_gap_pct,
            -- How many bets would STILL have edge at Pinnacle odds (using same edge_percent threshold)?
            -- edge_percent is stored in the DB as a decimal
            COUNT(*) FILTER (WHERE (sb.model_probability - 1.0/pin.odds) > sb.edge_percent) as still_edge
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        CROSS JOIN LATERAL (
            SELECT os.odds
            FROM odds_snapshots os
            WHERE os.match_id = sb.match_id
              AND os.bookmaker = 'Pinnacle'
              AND os.market = CASE
                    WHEN LOWER(sb.market) = '1x2' THEN '1x2'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%1.5%%' THEN 'over_under_15'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%2.5%%' THEN 'over_under_25'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%3.5%%' THEN 'over_under_35'
                    WHEN LOWER(sb.market) = 'btts' THEN 'btts'
                    WHEN sb.market = 'asian_handicap' THEN 'asian_handicap'
                    ELSE LOWER(sb.market)
                  END
              AND os.selection = CASE
                    WHEN LOWER(sb.selection) IN ('home','away','draw','over','under','yes','no') THEN LOWER(sb.selection)
                    WHEN sb.selection LIKE 'over%%' THEN 'over'
                    WHEN sb.selection LIKE 'under%%' THEN 'under'
                    ELSE LOWER(sb.selection)
                  END
              AND os.timestamp <= sb.pick_time + interval '3 hours'
              AND os.timestamp >= sb.pick_time - interval '3 hours'
            ORDER BY ABS(EXTRACT(EPOCH FROM (os.timestamp - sb.pick_time)))
            LIMIT 1
        ) pin
        WHERE sb.result IN ('won','lost')
          {mkt_clause}
        GROUP BY sb.market, sb.selection
        HAVING COUNT(*) >= 5
        ORDER BY COUNT(*) DESC
    """, params)

    console.print("\n[bold cyan]2. PINNACLE GAP — Best-across-books (what we used) vs Pinnacle[/bold cyan]")
    console.print("   (Pinnacle = sharpest book, closest to true market. Gap = optimism in our odds.)\n")

    if not rows:
        console.print("  [yellow]No Pinnacle snapshots found within ±3h of pick_time.[/yellow]")
        console.print("  [yellow]Either Pinnacle coverage is low for these markets, or timestamps don't align.[/yellow]\n")
        return

    t = Table(show_lines=False)
    t.add_column("Market", style="cyan")
    t.add_column("Selection")
    t.add_column("Bets", justify="right")
    t.add_column("Avg pick odds", justify="right")
    t.add_column("Avg Pinnacle", justify="right")
    t.add_column("Avg gap", justify="right")
    t.add_column("Gap %", justify="right")
    t.add_column("Still edge at Pin", justify="right")

    for r in rows:
        gap_pct = float(r["avg_gap_pct"] or 0)
        gap_str = f"{gap_pct:+.2%}"
        gap_text = Text(gap_str, style="yellow" if gap_pct > 0.01 else "green")
        still = int(r["still_edge"] or 0)
        total = int(r["bets"])
        t.add_row(
            r["market"],
            r["selection"],
            str(total),
            f"{float(r['avg_pick_odds'] or 0):.3f}",
            f"{float(r['avg_pinnacle_odds'] or 0):.3f}",
            f"{float(r['avg_gap'] or 0):+.3f}",
            gap_text,
            f"{still}/{total} ({still/total:.0%})" if total else "-",
        )

    console.print(t)


def section_closing_drift(market_filter: str | None):
    """How much do Pinnacle odds move from placement to last-snapshot-before-kickoff?"""
    mkt_clause = "AND sb.market = %s" if market_filter else ""
    params = [market_filter] if market_filter else []

    rows = execute_query(f"""
        SELECT
            sb.market,
            sb.selection,
            COUNT(*) as bets,
            AVG(pin_open.odds) as avg_open,
            AVG(pin_close.odds) as avg_close,
            AVG(pin_close.odds - pin_open.odds) as avg_drift,
            AVG((pin_close.odds - pin_open.odds) / NULLIF(pin_open.odds, 0)) as avg_drift_pct,
            -- Odds moved against us (got shorter from our pick direction)
            COUNT(*) FILTER (WHERE pin_close.odds < pin_open.odds) as drifted_shorter,
            COUNT(*) FILTER (WHERE pin_close.odds > pin_open.odds) as drifted_longer
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        -- Pinnacle odds around pick_time
        -- Market/selection mapping: simulated_bets uses display names (1X2, O/U, BTTS)
        -- but odds_snapshots uses DB names (1x2, over_under_25, btts).
        CROSS JOIN LATERAL (
            SELECT os.odds
            FROM odds_snapshots os
            WHERE os.match_id = sb.match_id
              AND os.bookmaker = 'Pinnacle'
              AND os.market = CASE
                    WHEN LOWER(sb.market) = '1x2' THEN '1x2'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%1.5%%' THEN 'over_under_15'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%2.5%%' THEN 'over_under_25'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%3.5%%' THEN 'over_under_35'
                    WHEN LOWER(sb.market) = 'btts' THEN 'btts'
                    WHEN sb.market = 'asian_handicap' THEN 'asian_handicap'
                    ELSE LOWER(sb.market)
                  END
              AND os.selection = CASE
                    WHEN LOWER(sb.selection) IN ('home','away','draw','over','under','yes','no') THEN LOWER(sb.selection)
                    WHEN sb.selection LIKE 'over%%' THEN 'over'
                    WHEN sb.selection LIKE 'under%%' THEN 'under'
                    ELSE LOWER(sb.selection)
                  END
              AND os.timestamp <= sb.pick_time + interval '3 hours'
              AND os.timestamp >= sb.pick_time - interval '3 hours'
            ORDER BY ABS(EXTRACT(EPOCH FROM (os.timestamp - sb.pick_time)))
            LIMIT 1
        ) pin_open
        -- Pinnacle odds closest to kickoff (closing line)
        CROSS JOIN LATERAL (
            SELECT os.odds
            FROM odds_snapshots os
            WHERE os.match_id = sb.match_id
              AND os.bookmaker = 'Pinnacle'
              AND os.market = CASE
                    WHEN LOWER(sb.market) = '1x2' THEN '1x2'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%1.5%%' THEN 'over_under_15'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%2.5%%' THEN 'over_under_25'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%3.5%%' THEN 'over_under_35'
                    WHEN LOWER(sb.market) = 'btts' THEN 'btts'
                    WHEN sb.market = 'asian_handicap' THEN 'asian_handicap'
                    ELSE LOWER(sb.market)
                  END
              AND os.selection = CASE
                    WHEN LOWER(sb.selection) IN ('home','away','draw','over','under','yes','no') THEN LOWER(sb.selection)
                    WHEN sb.selection LIKE 'over%%' THEN 'over'
                    WHEN sb.selection LIKE 'under%%' THEN 'under'
                    ELSE LOWER(sb.selection)
                  END
              AND os.timestamp <= m.date::timestamptz + interval '30 minutes'
            ORDER BY os.timestamp DESC
            LIMIT 1
        ) pin_close
        WHERE sb.result IN ('won','lost')
          {mkt_clause}
        GROUP BY sb.market, sb.selection
        HAVING COUNT(*) >= 5
        ORDER BY COUNT(*) DESC
    """, params)

    console.print("\n[bold cyan]3. DRIFT — Pinnacle odds from placement to closing line[/bold cyan]")
    console.print("   (If odds drift SHORTER after we 'bet', the edge we saw was already being priced out.)\n")

    if not rows:
        console.print("  [yellow]Not enough matched Pinnacle snapshots to compute drift.[/yellow]\n")
        return

    t = Table(show_lines=False)
    t.add_column("Market", style="cyan")
    t.add_column("Selection")
    t.add_column("Bets", justify="right")
    t.add_column("Avg open", justify="right")
    t.add_column("Avg close", justify="right")
    t.add_column("Avg drift %", justify="right")
    t.add_column("Shorter", justify="right")
    t.add_column("Longer", justify="right")

    for r in rows:
        drift_pct = float(r["avg_drift_pct"] or 0)
        drift_str = f"{drift_pct:+.2%}"
        # Shorter = bad for us (the market was pricing us out)
        shorter = int(r["drifted_shorter"] or 0)
        longer = int(r["drifted_longer"] or 0)
        total = int(r["bets"])
        t.add_row(
            r["market"],
            r["selection"],
            str(total),
            f"{float(r['avg_open'] or 0):.3f}",
            f"{float(r['avg_close'] or 0):.3f}",
            Text(drift_str, style="red" if drift_pct < -0.01 else ("yellow" if drift_pct < 0 else "green")),
            f"{shorter} ({shorter/total:.0%})" if total else "-",
            f"{longer} ({longer/total:.0%})" if total else "-",
        )

    console.print(t)


def section_pinnacle_only_sim(market_filter: str | None):
    """Simulate what ROI/CLV looks like if we had only used Pinnacle odds for placement."""
    mkt_clause = "AND sb.market = %s" if market_filter else ""
    params = [market_filter] if market_filter else []

    rows = execute_query(f"""
        SELECT
            sb.market,
            sb.selection,
            COUNT(*) as total_bets,
            -- Actual performance
            AVG(sb.clv) FILTER (WHERE sb.clv IS NOT NULL) as actual_avg_clv,
            SUM(sb.pnl) as actual_pnl,
            SUM(sb.stake) as total_staked,
            -- Simulated performance using Pinnacle closing line as pick odds
            -- CLV vs Pinnacle closing = pin_open / pin_close - 1
            AVG((pin_open.odds / NULLIF(pin_close.odds, 0)) - 1.0) as pinnacle_clv,
            -- How many would pass edge threshold at Pinnacle pick odds?
            COUNT(*) FILTER (
                WHERE (sb.model_probability - 1.0/NULLIF(pin_open.odds, 0)) >= sb.edge_percent
            ) as would_fire_at_pinnacle
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        CROSS JOIN LATERAL (
            SELECT os.odds
            FROM odds_snapshots os
            WHERE os.match_id = sb.match_id
              AND os.bookmaker = 'Pinnacle'
              AND os.market = CASE
                    WHEN LOWER(sb.market) = '1x2' THEN '1x2'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%1.5%%' THEN 'over_under_15'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%2.5%%' THEN 'over_under_25'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%3.5%%' THEN 'over_under_35'
                    WHEN LOWER(sb.market) = 'btts' THEN 'btts'
                    WHEN sb.market = 'asian_handicap' THEN 'asian_handicap'
                    ELSE LOWER(sb.market)
                  END
              AND os.selection = CASE
                    WHEN LOWER(sb.selection) IN ('home','away','draw','over','under','yes','no') THEN LOWER(sb.selection)
                    WHEN sb.selection LIKE 'over%%' THEN 'over'
                    WHEN sb.selection LIKE 'under%%' THEN 'under'
                    ELSE LOWER(sb.selection)
                  END
              AND os.timestamp <= sb.pick_time + interval '3 hours'
              AND os.timestamp >= sb.pick_time - interval '3 hours'
            ORDER BY ABS(EXTRACT(EPOCH FROM (os.timestamp - sb.pick_time)))
            LIMIT 1
        ) pin_open
        CROSS JOIN LATERAL (
            SELECT os.odds
            FROM odds_snapshots os
            WHERE os.match_id = sb.match_id
              AND os.bookmaker = 'Pinnacle'
              AND os.market = CASE
                    WHEN LOWER(sb.market) = '1x2' THEN '1x2'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%1.5%%' THEN 'over_under_15'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%2.5%%' THEN 'over_under_25'
                    WHEN sb.market = 'O/U' AND sb.selection LIKE '%%3.5%%' THEN 'over_under_35'
                    WHEN LOWER(sb.market) = 'btts' THEN 'btts'
                    WHEN sb.market = 'asian_handicap' THEN 'asian_handicap'
                    ELSE LOWER(sb.market)
                  END
              AND os.selection = CASE
                    WHEN LOWER(sb.selection) IN ('home','away','draw','over','under','yes','no') THEN LOWER(sb.selection)
                    WHEN sb.selection LIKE 'over%%' THEN 'over'
                    WHEN sb.selection LIKE 'under%%' THEN 'under'
                    ELSE LOWER(sb.selection)
                  END
              AND os.timestamp <= m.date::timestamptz + interval '30 minutes'
            ORDER BY os.timestamp DESC
            LIMIT 1
        ) pin_close
        WHERE sb.result IN ('won','lost')
          {mkt_clause}
        GROUP BY sb.market, sb.selection
        HAVING COUNT(*) >= 5
        ORDER BY COUNT(*) DESC
    """, params)

    console.print("\n[bold cyan]4. PINNACLE-ONLY SIMULATION[/bold cyan]")
    console.print("   Reported CLV (best-across-books) vs what CLV looks like using Pinnacle as the pick odds.")
    console.print("   Pinnacle CLV = Pinnacle odds at pick time / Pinnacle closing — the honest benchmark.\n")

    if not rows:
        console.print("  [yellow]Not enough Pinnacle data for simulation.[/yellow]\n")
        return

    t = Table(show_lines=False)
    t.add_column("Market", style="cyan")
    t.add_column("Selection")
    t.add_column("Bets", justify="right")
    t.add_column("Reported CLV", justify="right")
    t.add_column("Pinnacle CLV", justify="right")
    t.add_column("Inflated by", justify="right")
    t.add_column("Fire at Pinnacle", justify="right")

    for r in rows:
        rep_clv = float(r["actual_avg_clv"] or 0)
        pin_clv = float(r["pinnacle_clv"] or 0)
        inflation = rep_clv - pin_clv
        total = int(r["total_bets"])
        would_fire = int(r["would_fire_at_pinnacle"] or 0)

        t.add_row(
            r["market"],
            r["selection"],
            str(total),
            Text(f"{rep_clv:+.2%}", style="green" if rep_clv > 0 else "red"),
            Text(f"{pin_clv:+.2%}", style="green" if pin_clv > 0.005 else ("yellow" if pin_clv > 0 else "red")),
            Text(f"{inflation:+.2%}", style="yellow" if inflation > 0.01 else "dim"),
            f"{would_fire}/{total} ({would_fire/total:.0%})" if total else "-",
        )

    console.print(t)
    console.print(
        "\n  [bold]Key:[/bold] If Pinnacle CLV > 0%, that market likely has real edge.")
    console.print(
        "  If Pinnacle CLV ≈ 0% or negative, reported edge was from book-shopping, not model edge.\n")


def main():
    parser = argparse.ArgumentParser(description="Odds realism audit")
    parser.add_argument("--market", type=str, default=None,
                        help="Filter to a single market (e.g. 1x2, over_under_25)")
    args = parser.parse_args()

    console.print("\n[bold]═══ OddsIntel Odds Realism Audit ═══[/bold]")
    console.print("Diagnosing gap between reported paper-trade edge and achievable real-money edge.\n")

    section_timing(args.market)
    section_pinnacle_gap(args.market)
    section_closing_drift(args.market)
    section_pinnacle_only_sim(args.market)


if __name__ == "__main__":
    main()
