"""
OddsIntel — Bot Performance Report

Slices 14-day paper trading data to find where edge is positive.
Covers: summary, per-bot, per-market, per-league-tier, top leagues.

Usage:
    python3 scripts/bot_perf_report.py                  # all-time
    python3 scripts/bot_perf_report.py --days 7         # last 7 days only
    python3 scripts/bot_perf_report.py --bot bot_lower_1x2
    python3 scripts/bot_perf_report.py --min-bets 10    # raise significance floor
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich.text import Text

from workers.api_clients.db import execute_query

console = Console()

# Min settled bets before showing a row in the sliced tables.
DEFAULT_MIN_BETS = 5


def _clv_str(clv: float | None) -> Text:
    if clv is None:
        return Text("-", style="dim")
    pct = clv * 100
    s = f"{pct:+.2f}%"
    if pct >= 2.0:
        return Text(s, style="bold green")
    if pct >= 0.5:
        return Text(s, style="green")
    if pct >= -0.5:
        return Text(s, style="yellow")
    return Text(s, style="red")


def _roi_str(roi: float | None) -> Text:
    if roi is None:
        return Text("-", style="dim")
    pct = roi * 100
    s = f"{pct:+.1f}%"
    return Text(s, style="green" if pct >= 0 else "red")


def _where_clause(days: int | None, bot_name: str | None, alias: str = "sb") -> tuple[str, list]:
    clauses, params = [], []
    if days:
        clauses.append(f"{alias}.pick_time >= now() - interval '{days} days'")
    if bot_name:
        clauses.append(f"bo.name = %s")
        params.append(bot_name)
    return (("WHERE " + " AND ".join(clauses)) if clauses else ""), params


def section_summary(days: int | None, bot_name: str | None):
    where, params = _where_clause(days, bot_name, alias="sb")
    if bot_name:
        # need bots join
        rows = execute_query(f"""
            SELECT
                COUNT(*) FILTER (WHERE sb.result NOT IN ('void','pending')) as total,
                COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) as settled,
                COUNT(*) FILTER (WHERE sb.result = 'pending') as pending,
                COUNT(*) FILTER (WHERE sb.result = 'void') as voided,
                COUNT(*) FILTER (WHERE sb.result = 'won') as won,
                SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as total_pnl,
                SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as total_staked,
                AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv,
                COUNT(*) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as clv_n
            FROM simulated_bets sb
            JOIN bots bo ON bo.id = sb.bot_id
            {where}
        """, params)
    else:
        where_no_bot = f"WHERE sb.pick_time >= now() - interval '{days} days'" if days else ""
        rows = execute_query(f"""
            SELECT
                COUNT(*) FILTER (WHERE sb.result NOT IN ('void','pending')) as total,
                COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) as settled,
                COUNT(*) FILTER (WHERE sb.result = 'pending') as pending,
                COUNT(*) FILTER (WHERE sb.result = 'void') as voided,
                COUNT(*) FILTER (WHERE sb.result = 'won') as won,
                SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as total_pnl,
                SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as total_staked,
                AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv,
                COUNT(*) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as clv_n
            FROM simulated_bets sb
            {where_no_bot}
        """, [])

    r = rows[0] if rows else {}
    settled = int(r.get("settled") or 0)
    won = int(r.get("won") or 0)
    staked = float(r.get("total_staked") or 0)
    pnl = float(r.get("total_pnl") or 0)
    avg_clv = float(r["avg_clv"]) if r.get("avg_clv") is not None else None
    clv_n = int(r.get("clv_n") or 0)

    hit = won / settled if settled else None
    roi = pnl / staked if staked > 0 else None

    label = f"last {days}d" if days else "all-time"
    if bot_name:
        label += f" | bot: {bot_name}"

    console.print(f"\n[bold cyan]═══ OddsIntel Bot Performance Report ({label}) ═══[/bold cyan]\n")
    console.print(
        f"  Settled: [bold]{settled}[/bold]  |  "
        f"Pending: {int(r.get('pending') or 0)}  |  "
        f"Voided: {int(r.get('voided') or 0)}  |  "
        f"Hit: {'[green]' if hit and hit > 0.5 else ''}{hit:.1%}{'[/green]' if hit and hit > 0.5 else '' if hit else '-'}  |  "
        f"ROI: {_roi_str(roi)}  |  "
        f"Avg CLV: {_clv_str(avg_clv)} ({clv_n} w/ CLV)\n"
    )


def section_by_bot(days: int | None, bot_name: str | None, min_bets: int):
    time_filter = f"AND sb.pick_time >= now() - interval '{days} days'" if days else ""
    bot_filter = "AND bo.name = %s" if bot_name else ""
    params = [bot_name] if bot_name else []

    rows = execute_query(f"""
        SELECT
            bo.name,
            COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) as settled,
            COUNT(sb.id) FILTER (WHERE sb.result = 'won') as won,
            SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as pnl,
            SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as staked,
            AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv,
            COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as clv_n
        FROM bots bo
        LEFT JOIN simulated_bets sb ON sb.bot_id = bo.id {time_filter}
        WHERE bo.is_active = true AND bo.retired_at IS NULL {bot_filter}
        GROUP BY bo.id, bo.name
        HAVING COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) >= %s
        ORDER BY AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) DESC NULLS LAST
    """, params + [min_bets])

    t = Table(title=f"By Bot (min {min_bets} settled)", show_lines=False)
    t.add_column("Bot", style="cyan", no_wrap=True)
    t.add_column("Bets", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg CLV", justify="right")
    t.add_column("CLV n", justify="right", style="dim")

    for r in rows:
        s = int(r.get("settled") or 0)
        w = int(r.get("won") or 0)
        pnl = float(r.get("pnl") or 0)
        staked = float(r.get("staked") or 0)
        clv = float(r["avg_clv"]) if r.get("avg_clv") is not None else None
        roi = pnl / staked if staked > 0 else None
        t.add_row(
            r["name"],
            str(s),
            f"{w/s:.1%}" if s else "-",
            _roi_str(roi),
            f"{pnl:+.2f}",
            _clv_str(clv),
            str(int(r.get("clv_n") or 0)),
        )

    console.print(t)


def section_by_market(days: int | None, bot_name: str | None, min_bets: int):
    time_filter = f"AND sb.pick_time >= now() - interval '{days} days'" if days else ""
    bot_join = "JOIN bots bo ON bo.id = sb.bot_id" if bot_name else ""
    bot_filter = "AND bo.name = %s" if bot_name else ""
    params = [bot_name] if bot_name else []

    rows = execute_query(f"""
        SELECT
            sb.market,
            sb.selection,
            COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) as settled,
            COUNT(*) FILTER (WHERE sb.result = 'won') as won,
            SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as pnl,
            SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as staked,
            AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv
        FROM simulated_bets sb
        {bot_join}
        WHERE 1=1 {time_filter} {bot_filter}
        GROUP BY sb.market, sb.selection
        HAVING COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) >= %s
        ORDER BY AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) DESC NULLS LAST
    """, params + [min_bets])

    t = Table(title=f"By Market & Selection (min {min_bets} settled, sorted by CLV)", show_lines=False)
    t.add_column("Market", style="cyan")
    t.add_column("Selection")
    t.add_column("Bets", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg CLV", justify="right")

    for r in rows:
        s = int(r.get("settled") or 0)
        w = int(r.get("won") or 0)
        pnl = float(r.get("pnl") or 0)
        staked = float(r.get("staked") or 0)
        clv = float(r["avg_clv"]) if r.get("avg_clv") is not None else None
        roi = pnl / staked if staked > 0 else None
        t.add_row(
            r["market"] or "-",
            r["selection"] or "-",
            str(s),
            f"{w/s:.1%}" if s else "-",
            _roi_str(roi),
            f"{pnl:+.2f}",
            _clv_str(clv),
        )

    console.print(t)


def section_by_tier(days: int | None, bot_name: str | None, min_bets: int):
    time_filter = f"AND sb.pick_time >= now() - interval '{days} days'" if days else ""
    bot_join = "JOIN bots bo ON bo.id = sb.bot_id" if bot_name else ""
    bot_filter = "AND bo.name = %s" if bot_name else ""
    params = [bot_name] if bot_name else []

    rows = execute_query(f"""
        SELECT
            l.tier,
            COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) as settled,
            COUNT(sb.id) FILTER (WHERE sb.result = 'won') as won,
            SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as pnl,
            SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as staked,
            AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        JOIN leagues l ON l.id = m.league_id
        {bot_join}
        WHERE 1=1 {time_filter} {bot_filter}
        GROUP BY l.tier
        HAVING COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) >= %s
        ORDER BY l.tier
    """, params + [min_bets])

    t = Table(title="By League Tier", show_lines=False)
    t.add_column("Tier", justify="center")
    t.add_column("Bets", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg CLV", justify="right")

    for r in rows:
        s = int(r.get("settled") or 0)
        w = int(r.get("won") or 0)
        pnl = float(r.get("pnl") or 0)
        staked = float(r.get("staked") or 0)
        clv = float(r["avg_clv"]) if r.get("avg_clv") is not None else None
        roi = pnl / staked if staked > 0 else None
        t.add_row(
            f"T{r['tier']}",
            str(s),
            f"{w/s:.1%}" if s else "-",
            _roi_str(roi),
            f"{pnl:+.2f}",
            _clv_str(clv),
        )

    console.print(t)


def section_top_leagues(days: int | None, bot_name: str | None, min_bets: int):
    time_filter = f"AND sb.pick_time >= now() - interval '{days} days'" if days else ""
    bot_join = "JOIN bots bo ON bo.id = sb.bot_id" if bot_name else ""
    bot_filter = "AND bo.name = %s" if bot_name else ""
    params = [bot_name] if bot_name else []

    rows = execute_query(f"""
        SELECT
            l.name as league,
            l.country,
            l.tier,
            COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) as settled,
            COUNT(sb.id) FILTER (WHERE sb.result = 'won') as won,
            SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as pnl,
            SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as staked,
            AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) as avg_clv
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        JOIN leagues l ON l.id = m.league_id
        {bot_join}
        WHERE 1=1 {time_filter} {bot_filter}
        GROUP BY l.id, l.name, l.country, l.tier
        HAVING COUNT(sb.id) FILTER (WHERE sb.result IN ('won','lost')) >= %s
        ORDER BY AVG(sb.clv) FILTER (WHERE sb.result IN ('won','lost') AND sb.clv IS NOT NULL) DESC NULLS LAST
        LIMIT 20
    """, params + [min_bets])

    t = Table(title=f"Top Leagues by CLV (min {min_bets} settled)", show_lines=False)
    t.add_column("League", style="cyan")
    t.add_column("Country", style="dim")
    t.add_column("T", justify="center")
    t.add_column("Bets", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg CLV", justify="right")

    for r in rows:
        s = int(r.get("settled") or 0)
        w = int(r.get("won") or 0)
        pnl = float(r.get("pnl") or 0)
        staked = float(r.get("staked") or 0)
        clv = float(r["avg_clv"]) if r.get("avg_clv") is not None else None
        roi = pnl / staked if staked > 0 else None
        t.add_row(
            r["league"],
            r["country"] or "-",
            str(r["tier"]),
            str(s),
            f"{w/s:.1%}" if s else "-",
            _roi_str(roi),
            f"{pnl:+.2f}",
            _clv_str(clv),
        )

    console.print(t)


def main():
    parser = argparse.ArgumentParser(description="OddsIntel bot performance report")
    parser.add_argument("--days", type=int, default=None,
                        help="Restrict to last N days (default: all-time)")
    parser.add_argument("--bot", type=str, default=None,
                        help="Drill down on a single bot by name")
    parser.add_argument("--min-bets", type=int, default=DEFAULT_MIN_BETS,
                        help=f"Min settled bets to show a row (default: {DEFAULT_MIN_BETS})")
    args = parser.parse_args()

    section_summary(args.days, args.bot)
    section_by_bot(args.days, args.bot, args.min_bets)
    section_by_market(args.days, args.bot, args.min_bets)
    section_by_tier(args.days, args.bot, args.min_bets)
    section_top_leagues(args.days, args.bot, args.min_bets)
    console.print()


if __name__ == "__main__":
    main()
