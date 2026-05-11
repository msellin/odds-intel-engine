"""
OddsIntel — Real vs Paper Performance Report

Compares real-money bets (real_bets) against matched paper bets (simulated_bets)
to measure: real ROI, paper ROI on same matches, slippage, and execution friction.

Usage:
    python3 scripts/real_perf_report.py
    python3 scripts/real_perf_report.py --days 30
    python3 scripts/real_perf_report.py --bookmaker Bet365
    python3 scripts/real_perf_report.py --min-bets 3
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


def _roi_str(roi: float | None) -> Text:
    if roi is None:
        return Text("-", style="dim")
    pct = roi * 100
    s = f"{pct:+.1f}%"
    return Text(s, style="bold green" if pct >= 5 else "green" if pct >= 0 else "red")


def _slip_str(slip: float | None) -> Text:
    if slip is None:
        return Text("-", style="dim")
    s = f"{slip:+.2f}%"
    if abs(slip) < 1.0:
        return Text(s, style="green")
    if abs(slip) < 3.0:
        return Text(s, style="yellow")
    return Text(s, style="red")


def _where_clause(days: int | None, bookmaker: str | None) -> tuple[str, list]:
    clauses, params = [], []
    if days:
        clauses.append(f"rb.placed_at >= now() - interval '{days} days'")
    if bookmaker:
        clauses.append("rb.bookmaker = %s")
        params.append(bookmaker)
    return (("WHERE " + " AND ".join(clauses)) if clauses else ""), params


def section_summary(days: int | None, bookmaker: str | None):
    where, params = _where_clause(days, bookmaker)
    rows = execute_query(f"""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE rb.result IN ('won','lost')) as settled,
            COUNT(*) FILTER (WHERE rb.result = 'pending') as pending,
            COUNT(*) FILTER (WHERE rb.result = 'void') as voided,
            COUNT(*) FILTER (WHERE rb.result = 'won') as won,
            SUM(rb.pnl) FILTER (WHERE rb.result IN ('won','lost')) as total_pnl,
            SUM(rb.stake) FILTER (WHERE rb.result IN ('won','lost')) as total_staked,
            AVG(rb.slippage_pct) FILTER (WHERE rb.slippage_pct IS NOT NULL) as avg_slip,
            MIN(rb.slippage_pct) FILTER (WHERE rb.slippage_pct IS NOT NULL) as min_slip,
            MAX(rb.slippage_pct) FILTER (WHERE rb.slippage_pct IS NOT NULL) as max_slip,
            COUNT(*) FILTER (WHERE rb.slippage_pct IS NOT NULL) as slip_n
        FROM real_bets rb
        {where}
    """, params)

    r = rows[0] if rows else {}
    total = int(r.get("total") or 0)
    settled = int(r.get("settled") or 0)
    won = int(r.get("won") or 0)
    staked = float(r.get("total_staked") or 0)
    pnl = float(r.get("total_pnl") or 0)
    avg_slip = float(r["avg_slip"]) if r.get("avg_slip") is not None else None
    min_slip = float(r["min_slip"]) if r.get("min_slip") is not None else None
    max_slip = float(r["max_slip"]) if r.get("max_slip") is not None else None
    slip_n = int(r.get("slip_n") or 0)
    hit = won / settled if settled else None
    roi = pnl / staked if staked > 0 else None

    label = f"last {days}d" if days else "all-time"
    if bookmaker:
        label += f" | {bookmaker}"

    console.print(f"\n[bold cyan]═══ Real Money Performance Report ({label}) ═══[/bold cyan]\n")
    console.print(
        f"  Total: [bold]{total}[/bold]  |  "
        f"Settled: {settled}  |  "
        f"Pending: {int(r.get('pending') or 0)}  |  "
        f"Voided: {int(r.get('voided') or 0)}  |  "
        f"Hit: {'[green]' if hit and hit > 0.5 else ''}"
        f"{f'{hit:.1%}' if hit is not None else '-'}"
        f"{'[/green]' if hit and hit > 0.5 else ''}\n"
    )
    roi_t = _roi_str(roi)
    console.print(
        f"  Staked: [bold]€{staked:.2f}[/bold]  |  "
        f"P&L: [bold]{'[green]' if pnl >= 0 else '[red]'}€{pnl:+.2f}{'[/green]' if pnl >= 0 else '[/red]'}[/bold]  |  "
        f"ROI: {roi_t}\n"
    )
    if slip_n > 0:
        console.print(
            f"  Slippage ({slip_n} bets with captured odds): "
            f"avg {_slip_str(avg_slip)}  min {_slip_str(min_slip)}  max {_slip_str(max_slip)}\n"
        )
    else:
        console.print("  No slippage data yet (captured_odds not set on any bet).\n")


def section_paper_vs_real(days: int | None, bookmaker: str | None):
    """For bets linked to a simulated_bet_id, compare paper ROI vs real ROI."""
    where, params = _where_clause(days, bookmaker)
    extra_and = " AND " + " AND ".join(
        [f"rb.placed_at >= now() - interval '{days} days'" if days else "",
         "rb.bookmaker = %s" if bookmaker else ""]
    ).replace("  AND  ", " ").strip(" AND ") if (days or bookmaker) else ""

    rows = execute_query(f"""
        SELECT
            COUNT(*) FILTER (WHERE rb.result IN ('won','lost')) as real_settled,
            SUM(rb.pnl) FILTER (WHERE rb.result IN ('won','lost')) as real_pnl,
            SUM(rb.stake) FILTER (WHERE rb.result IN ('won','lost')) as real_staked,
            SUM(sb.pnl) FILTER (WHERE rb.result IN ('won','lost') AND sb.result IN ('won','lost')) as paper_pnl,
            SUM(rb.stake) FILTER (WHERE rb.result IN ('won','lost') AND sb.result IN ('won','lost')) as paper_staked_matched,
            AVG(rb.slippage_pct) FILTER (WHERE rb.slippage_pct IS NOT NULL) as avg_slip
        FROM real_bets rb
        JOIN simulated_bets sb ON sb.id = rb.simulated_bet_id
        {where.replace('WHERE', 'WHERE') if where else 'WHERE rb.simulated_bet_id IS NOT NULL'}
        {'AND rb.simulated_bet_id IS NOT NULL' if where else ''}
    """, params)

    r = rows[0] if rows else {}
    real_settled = int(r.get("real_settled") or 0)
    if real_settled == 0:
        console.print("[dim]  Paper vs Real: no matched settled bets yet (need simulated_bet_id links).[/dim]\n")
        return

    real_pnl = float(r.get("real_pnl") or 0)
    real_staked = float(r.get("real_staked") or 0)
    paper_pnl = float(r.get("paper_pnl") or 0)
    paper_staked = float(r.get("paper_staked_matched") or 0)
    real_roi = real_pnl / real_staked if real_staked > 0 else None
    paper_roi = paper_pnl / paper_staked if paper_staked > 0 else None
    avg_slip = float(r["avg_slip"]) if r.get("avg_slip") is not None else None

    console.print("[bold]  Paper vs Real (matched bets only):[/bold]")
    console.print(
        f"    Real ROI:  {_roi_str(real_roi)}  (€{real_pnl:+.2f} on €{real_staked:.2f} staked)\n"
        f"    Paper ROI: {_roi_str(paper_roi)}  (€{paper_pnl:+.2f} on same matches)\n"
        f"    Slippage:  {_slip_str(avg_slip)}\n"
    )


def section_by_bookmaker(days: int | None):
    time_filter = f"AND rb.placed_at >= now() - interval '{days} days'" if days else ""
    rows = execute_query(f"""
        SELECT
            rb.bookmaker,
            COUNT(*) FILTER (WHERE rb.result IN ('won','lost')) as settled,
            COUNT(*) FILTER (WHERE rb.result = 'won') as won,
            SUM(rb.pnl) FILTER (WHERE rb.result IN ('won','lost')) as pnl,
            SUM(rb.stake) FILTER (WHERE rb.result IN ('won','lost')) as staked,
            AVG(rb.slippage_pct) FILTER (WHERE rb.slippage_pct IS NOT NULL) as avg_slip,
            COUNT(*) FILTER (WHERE rb.result = 'pending') as pending
        FROM real_bets rb
        WHERE 1=1 {time_filter}
        GROUP BY rb.bookmaker
        ORDER BY COUNT(*) FILTER (WHERE rb.result IN ('won','lost')) DESC
    """, [])

    if not rows:
        return

    t = Table(title="By Bookmaker", show_lines=False)
    t.add_column("Bookmaker", style="cyan")
    t.add_column("Settled", justify="right")
    t.add_column("Pending", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")
    t.add_column("Avg Slip", justify="right")

    for r in rows:
        s = int(r.get("settled") or 0)
        w = int(r.get("won") or 0)
        pnl = float(r.get("pnl") or 0)
        staked = float(r.get("staked") or 0)
        roi = pnl / staked if staked > 0 else None
        slip = float(r["avg_slip"]) if r.get("avg_slip") is not None else None
        t.add_row(
            r["bookmaker"],
            str(s),
            str(int(r.get("pending") or 0)),
            f"{w/s:.1%}" if s else "-",
            _roi_str(roi),
            f"€{pnl:+.2f}",
            _slip_str(slip),
        )

    console.print(t)


def section_by_market(days: int | None, bookmaker: str | None, min_bets: int):
    where, params = _where_clause(days, bookmaker)
    rows = execute_query(f"""
        SELECT
            rb.market,
            rb.selection,
            COUNT(*) FILTER (WHERE rb.result IN ('won','lost')) as settled,
            COUNT(*) FILTER (WHERE rb.result = 'won') as won,
            SUM(rb.pnl) FILTER (WHERE rb.result IN ('won','lost')) as pnl,
            SUM(rb.stake) FILTER (WHERE rb.result IN ('won','lost')) as staked
        FROM real_bets rb
        {where}
        GROUP BY rb.market, rb.selection
        HAVING COUNT(*) FILTER (WHERE rb.result IN ('won','lost')) >= %s
        ORDER BY SUM(rb.pnl) FILTER (WHERE rb.result IN ('won','lost')) DESC NULLS LAST
    """, params + [min_bets])

    if not rows:
        console.print(f"[dim]  No market breakdown yet (need ≥{min_bets} settled per market).[/dim]\n")
        return

    t = Table(title=f"By Market (min {min_bets} settled)", show_lines=False)
    t.add_column("Market", style="cyan")
    t.add_column("Selection")
    t.add_column("Bets", justify="right")
    t.add_column("Hit%", justify="right")
    t.add_column("ROI", justify="right")
    t.add_column("P&L", justify="right")

    for r in rows:
        s = int(r.get("settled") or 0)
        w = int(r.get("won") or 0)
        pnl = float(r.get("pnl") or 0)
        staked = float(r.get("staked") or 0)
        roi = pnl / staked if staked > 0 else None
        t.add_row(
            r["market"] or "-",
            r["selection"] or "-",
            str(s),
            f"{w/s:.1%}" if s else "-",
            _roi_str(roi),
            f"€{pnl:+.2f}",
        )

    console.print(t)


def section_recent_bets(days: int | None, bookmaker: str | None, limit: int = 15):
    where, params = _where_clause(days, bookmaker)
    rows = execute_query(f"""
        SELECT
            rb.placed_at,
            rb.bookmaker,
            rb.market,
            rb.selection,
            m.date as kickoff,
            ht.name as home_team,
            at.name as away_team,
            rb.actual_odds,
            rb.captured_odds,
            rb.slippage_pct,
            rb.stake,
            rb.result,
            rb.pnl,
            sb.pick_time,
            sb.edge_percent as paper_edge
        FROM real_bets rb
        JOIN matches m ON m.id = rb.match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        LEFT JOIN simulated_bets sb ON sb.id = rb.simulated_bet_id
        {where}
        ORDER BY rb.placed_at DESC
        LIMIT %s
    """, params + [limit])

    if not rows:
        console.print("[dim]  No bets logged yet.[/dim]\n")
        return

    t = Table(title=f"Recent {limit} Bets", show_lines=False)
    t.add_column("Placed", style="dim")
    t.add_column("Delay", justify="right", style="dim")
    t.add_column("Match", no_wrap=True)
    t.add_column("Market")
    t.add_column("Book", style="cyan")
    t.add_column("Odds", justify="right")
    t.add_column("Slip", justify="right")
    t.add_column("Edge", justify="right")
    t.add_column("Result")
    t.add_column("P&L", justify="right")

    for r in rows:
        match = f"{r['home_team']} vs {r['away_team']}"
        result = r["result"]
        result_style = "green" if result == "won" else "red" if result == "lost" else "dim"
        pnl = float(r["pnl"] or 0)

        # Time delay between bot pick and manual placement
        delay_str = "-"
        if r.get("pick_time") and r.get("placed_at"):
            try:
                import datetime
                pick = r["pick_time"]
                placed = r["placed_at"]
                if hasattr(pick, "timestamp") and hasattr(placed, "timestamp"):
                    delta_min = int((placed.timestamp() - pick.timestamp()) / 60)
                    if delta_min < 60:
                        delay_str = f"{delta_min}m"
                    else:
                        delay_str = f"{delta_min // 60}h{delta_min % 60:02d}m"
            except Exception:
                pass

        edge = float(r["paper_edge"]) if r.get("paper_edge") is not None else None
        t.add_row(
            str(r["placed_at"])[:16].replace("T", " "),
            delay_str,
            match[:28],
            f"{r['market']} {r['selection'] or ''}".strip()[:16],
            r["bookmaker"],
            f"{float(r['actual_odds']):.2f}",
            _slip_str(float(r["slippage_pct"]) if r["slippage_pct"] is not None else None),
            f"{edge:.1%}" if edge is not None else "-",
            Text(result, style=result_style),
            Text(f"€{pnl:+.2f}", style="green" if pnl > 0 else "red" if pnl < 0 else "dim"),
        )

    console.print(t)


def main():
    parser = argparse.ArgumentParser(description="Real vs Paper P&L report")
    parser.add_argument("--days", type=int, default=None, help="Limit to last N days")
    parser.add_argument("--bookmaker", type=str, default=None, help="Filter by bookmaker")
    parser.add_argument("--min-bets", type=int, default=3, help="Min settled bets for market breakdown")
    args = parser.parse_args()

    section_summary(args.days, args.bookmaker)
    section_paper_vs_real(args.days, args.bookmaker)
    section_by_bookmaker(args.days)
    section_by_market(args.days, args.bookmaker, args.min_bets)
    section_recent_bets(args.days, args.bookmaker)


if __name__ == "__main__":
    main()
