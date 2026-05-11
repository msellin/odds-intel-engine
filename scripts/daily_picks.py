#!/usr/bin/env python3
"""
daily_picks.py — morning report for manual betting validation.

Shows today's recommended bets with the accessible bookmaker offering the best odds
so the user can check Bet365/Unibet/etc and place manually.

Usage:
  python3 scripts/daily_picks.py                   # today's picks
  python3 scripts/daily_picks.py --date 2026-05-10  # specific date
  python3 scripts/daily_picks.py --min-edge 0.05    # only show edge >= 5%
  python3 scripts/daily_picks.py --bookmaker Bet365 # filter by bookmaker
"""
import argparse
import os
import sys
from datetime import date, datetime

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

DB_URL = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")


def get_conn():
    return psycopg2.connect(DB_URL)


def fetch_picks(target_date: str, min_edge: float, bookmaker_filter: str | None):
    sql = """
        SELECT
            m.home_team_name,
            m.away_team_name,
            m.date AS kickoff,
            m.league_name,
            m.league_country,
            sb.market,
            sb.selection,
            sb.odds_at_pick,
            sb.edge_percent,
            sb.calibrated_prob,
            sb.recommended_bookmaker,
            sb.reasoning,
            b.name AS bot_name
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.result = 'pending'
          AND DATE(m.date AT TIME ZONE 'UTC') = %s::date
          AND sb.edge_percent >= %s
          AND (%s::text IS NULL OR sb.recommended_bookmaker = %s)
        ORDER BY m.date ASC, sb.edge_percent DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (target_date, min_edge, bookmaker_filter, bookmaker_filter))
            return cur.fetchall()


def fetch_bookmaker_breakdown(target_date: str, min_edge: float):
    """Per-bookmaker count and avg edge for the day."""
    sql = """
        SELECT
            COALESCE(sb.recommended_bookmaker, 'unknown') AS bookmaker,
            COUNT(*) AS picks,
            ROUND(AVG(sb.edge_percent)::numeric, 4) AS avg_edge
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.result = 'pending'
          AND DATE(m.date AT TIME ZONE 'UTC') = %s::date
          AND sb.edge_percent >= %s
        GROUP BY 1
        ORDER BY 2 DESC
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (target_date, min_edge))
            return cur.fetchall()


def _edge_color(edge: float) -> str:
    if edge >= 0.08:
        return "bold green"
    if edge >= 0.05:
        return "green"
    if edge >= 0.03:
        return "yellow"
    return "red"


def _market_display(market: str, selection: str) -> str:
    mapping = {
        "1X2": {"home": "Home", "draw": "Draw", "away": "Away"},
        "O/U": {"over": "Over", "under": "Under"},
        "BTTS": {"yes": "Yes", "no": "No"},
        "double_chance": {"1x": "1X", "x2": "X2", "12": "12"},
        "draw_no_bet": {"home": "Home (DNB)", "away": "Away (DNB)"},
        "asian_handicap": {},
        "over_under_25": {"over": "O2.5", "under": "U2.5"},
        "over_under_15": {"over": "O1.5", "under": "U1.5"},
        "over_under_35": {"over": "O3.5", "under": "U3.5"},
    }
    sel_map = mapping.get(market, {})
    return sel_map.get(selection.lower(), f"{market} {selection}")


def main():
    parser = argparse.ArgumentParser(description="Daily picks report for manual betting")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--min-edge", type=float, default=0.03, help="Minimum edge (default: 0.03)")
    parser.add_argument("--bookmaker", default=None, help="Filter by bookmaker name")
    args = parser.parse_args()

    if not DB_URL:
        console.print("[red]DATABASE_URL not set[/red]")
        sys.exit(1)

    console.print(f"\n[bold cyan]Daily Picks — {args.date}[/bold cyan]  [dim](min_edge={args.min_edge:.0%}, bookmaker={args.bookmaker or 'all'})[/dim]\n")

    rows = fetch_picks(args.date, args.min_edge, args.bookmaker)
    breakdown = fetch_bookmaker_breakdown(args.date, args.min_edge)

    if not rows:
        console.print("[yellow]No picks found for this date / filters.[/yellow]")
        return

    # Bookmaker breakdown summary
    if breakdown:
        console.print("[bold]Picks by bookmaker:[/bold]")
        for bk in breakdown:
            edge_pct = float(bk["avg_edge"]) * 100 if bk["avg_edge"] else 0
            console.print(f"  {bk['bookmaker']:15s}  {bk['picks']:3d} picks   avg edge {edge_pct:.1f}%")
        console.print()

    # Main picks table
    table = Table(show_header=True, header_style="bold white", box=None)
    table.add_column("Kickoff", style="dim", width=6)
    table.add_column("Match", width=30)
    table.add_column("League", style="dim", width=20)
    table.add_column("Market", width=14)
    table.add_column("Odds", justify="right", width=6)
    table.add_column("Edge", justify="right", width=6)
    table.add_column("Cal%", justify="right", width=6)
    table.add_column("Bookmaker", width=14)
    table.add_column("Bot", style="dim", width=20)

    for row in rows:
        kickoff_dt = row["kickoff"]
        if isinstance(kickoff_dt, datetime):
            ko_str = kickoff_dt.strftime("%H:%M")
        else:
            ko_str = str(kickoff_dt)[:16]

        edge = float(row["edge_percent"]) if row["edge_percent"] else 0.0
        cal = float(row["calibrated_prob"]) if row["calibrated_prob"] else 0.0
        odds = float(row["odds_at_pick"]) if row["odds_at_pick"] else 0.0
        bm = row["recommended_bookmaker"] or "[dim]unknown[/dim]"
        market_label = _market_display(row["market"], row["selection"])
        edge_col = _edge_color(edge)

        table.add_row(
            ko_str,
            f"{row['home_team_name']} vs {row['away_team_name']}",
            f"{row['league_country'] or ''} / {row['league_name'] or ''}",
            market_label,
            f"{odds:.2f}",
            f"[{edge_col}]{edge:.1%}[/{edge_col}]",
            f"{cal:.1%}",
            bm,
            row["bot_name"],
        )

    console.print(table)
    console.print(f"\n[dim]{len(rows)} picks total[/dim]")


if __name__ == "__main__":
    main()
