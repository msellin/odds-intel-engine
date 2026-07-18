"""ROI + CLV breakdown per league tier + per-league leaderboards.

Diagnostic report that answers "which slice of the market is actually
edge-positive vs pure hallucination". Filed 2026-07-18 after the
Torneo Federal A / Serie D / NWSL Women audit surfaced tier-4 as a
model-failure zone (ROI -34.7%, CLV +1.7% over 42d, n=146).

Interpretation guide:
- **CLV > 5%** and ROI negative → real edge, unlucky short-term variance.
- **CLV near 0% and ROI negative** → model is wrong, market later agrees
  with the book. Candidate for filtering.
- **avg_edge > 0.25** on lower tiers → calibration failure (real edges on
  efficient markets top out around 0.10-0.20).

Usage:
    python3 scripts/roi_by_tier_report.py                 # last 42 days
    python3 scripts/roi_by_tier_report.py --days 90       # longer window
    python3 scripts/roi_by_tier_report.py --market o_u25  # single market
    python3 scripts/roi_by_tier_report.py --top-n 20      # more leagues
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.table import Table

from workers.api_clients.db import execute_query

console = Console()


def _fetch(days: int, market: str | None) -> tuple[list, list, list]:
    market_clause = "AND b.market ILIKE %s" if market else ""
    market_arg = (f"%{market}%",) if market else ()

    base_filter = f"""
        b.pick_time > now() - interval '{days} days'
        AND b.result IN ('won','lost')
        {market_clause}
    """

    by_tier = execute_query(f"""
        SELECT
          COALESCE(l.tier::text, 'NA') tier,
          count(*) picks,
          sum((b.result='won')::int) w,
          sum((b.result='lost')::int) l,
          round(sum(b.pnl)::numeric, 0) pnl,
          round(sum(b.pnl)::numeric*100/nullif(sum(b.stake),0), 1) roi_pct,
          round(avg(b.edge_percent)::numeric, 2) avg_edge,
          round(avg(b.clv)::numeric,3) avg_clv
        FROM simulated_bets b
        JOIN matches m ON m.id = b.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE {base_filter}
        GROUP BY l.tier
        ORDER BY tier NULLS LAST
    """, market_arg)

    per_league = execute_query(f"""
        SELECT
          l.name league, l.tier,
          count(*) picks,
          sum((b.result='won')::int) w,
          sum((b.result='lost')::int) l,
          round(sum(b.pnl)::numeric, 0) pnl,
          round(sum(b.pnl)::numeric*100/nullif(sum(b.stake),0), 0) roi_pct,
          round(avg(b.edge_percent)::numeric, 2) avg_edge,
          round(avg(b.clv)::numeric,2) avg_clv
        FROM simulated_bets b
        JOIN matches m ON m.id = b.match_id
        LEFT JOIN leagues l ON l.id = m.league_id
        WHERE {base_filter}
        GROUP BY l.id, l.name, l.tier
    """, market_arg)

    return by_tier, per_league, market_arg


def _print_tier_table(rows: list, days: int, market: str | None):
    tag = f" · market='{market}'" if market else ""
    tbl = Table(title=f"ROI + CLV by tier · last {days}d{tag}", show_lines=False)
    tbl.add_column("tier", justify="right")
    tbl.add_column("picks", justify="right")
    tbl.add_column("W", justify="right")
    tbl.add_column("L", justify="right")
    tbl.add_column("PnL", justify="right")
    tbl.add_column("ROI%", justify="right")
    tbl.add_column("avg edge", justify="right")
    tbl.add_column("avg CLV", justify="right")
    tbl.add_column("reading", justify="left")
    for r in rows:
        roi = r["roi_pct"]
        clv = r["avg_clv"] or 0
        reading = "✓ real edge" if clv > 0.05 else "⚠ marginal" if clv > 0.02 else "✗ no edge"
        tbl.add_row(
            str(r["tier"]), str(r["picks"]), str(r["w"]), str(r["l"]),
            f"{r['pnl']}", f"{roi:.1f}" if roi else "—",
            f"{r['avg_edge']}", f"{clv:.3f}", reading,
        )
    console.print(tbl)


def _print_league_table(rows: list, title: str, ascending: bool, top_n: int):
    sorted_rows = sorted(rows, key=lambda r: r["pnl"] or 0, reverse=not ascending)[:top_n]
    tbl = Table(title=title, show_lines=False)
    tbl.add_column("league", overflow="fold")
    tbl.add_column("tier", justify="right")
    tbl.add_column("picks", justify="right")
    tbl.add_column("W", justify="right")
    tbl.add_column("L", justify="right")
    tbl.add_column("PnL", justify="right")
    tbl.add_column("ROI%", justify="right")
    tbl.add_column("avg edge", justify="right")
    tbl.add_column("avg CLV", justify="right")
    for r in sorted_rows:
        tbl.add_row(
            (r["league"] or "—")[:30], str(r["tier"] or "—"),
            str(r["picks"]), str(r["w"]), str(r["l"]),
            f"{r['pnl']}", f"{r['roi_pct']}",
            f"{r['avg_edge']}", f"{r['avg_clv']}",
        )
    console.print(tbl)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=42, help="lookback window (default 42)")
    ap.add_argument("--market", help="filter to matching market (ILIKE '%%<market>%%')")
    ap.add_argument("--top-n", type=int, default=15, help="top-N leagues per table (default 15)")
    ap.add_argument("--min-picks", type=int, default=3, help="skip leagues with fewer picks")
    args = ap.parse_args()

    by_tier, per_league, _ = _fetch(args.days, args.market)
    if not by_tier:
        console.print("[yellow]No settled bets in window.[/yellow]")
        return

    _print_tier_table(by_tier, args.days, args.market)
    filtered = [r for r in per_league if r["picks"] >= args.min_picks]
    console.print()
    _print_league_table(filtered, f"Worst {args.top_n} leagues by PnL", ascending=True, top_n=args.top_n)
    console.print()
    _print_league_table(filtered, f"Best {args.top_n} leagues by PnL", ascending=False, top_n=args.top_n)


if __name__ == "__main__":
    main()
