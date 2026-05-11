"""
OddsIntel — Inplay Bot Report

Shows per-strategy tried/fired funnel from inplay_bot_stats, combined with
actual bet P&L from simulated_bets. Replaces hunting through Railway logs.

Usage:
    python3 scripts/inplay_bot_report.py
    python3 scripts/inplay_bot_report.py --days 7
    python3 scripts/inplay_bot_report.py --strategy inplay_a
    python3 scripts/inplay_bot_report.py --recent 30
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

console = Console(width=160)

# Strategy descriptions embedded here so we don't import inplay_bot.py
_DESCRIPTIONS: dict[str, str] = {
    "inplay_a":   "xG Divergence O2.5 — goals≤1, min 25-35",
    "inplay_b":   "BTTS Momentum — trailing team pressure, min 15-40",
    "inplay_c":   "Favourite Comeback — fav trailing by 1",
    "inplay_d":   "Late Goals Compression O2.5 — min 55-75",
    "inplay_e":   "Dead Game Unders — tempo collapse, min 25-50",
    "inplay_g":   "Corner Cluster O2.5 — ≥3 corners/10min, min 30-70",
    "inplay_h":   "HT Restart Surge O2.5 — 0-0 at HT, attacking first half",
    "inplay_i":   "Favourite Stall — strong fav 0-0, live odds drifted ≥3.0",
    "inplay_j":   "Goal Debt O1.5 — 0-0 min 30-52, O25 prematch ≥0.62",
    "inplay_l":   "Goal Contagion — first goal min 15-35 in high-λ match",
    "inplay_m":   "Equalizer Magnet — 1-goal game min 30-60, BTTS≥0.48",
    "inplay_n":   "Late Favourite Push — 0-0/1-1 min 72-80, home fav drifted",
    "inplay_q":   "Red Card Overreaction — red 15-55, goals≤1, possession≥55%",
}


def _roi_str(roi: float | None, settled: int = 1) -> Text:
    if roi is None or settled == 0:
        return Text("—", style="dim")
    pct = roi * 100
    s = f"{pct:+.1f}%"
    style = "bold green" if pct >= 5 else "green" if pct >= 0 else "red"
    return Text(s, style=style)


def _pct_str(n: int, d: int) -> Text:
    if d == 0:
        return Text("—", style="dim")
    p = n / d * 100
    s = f"{p:.1f}%"
    style = "green" if p >= 2 else "yellow" if p >= 0.5 else "dim"
    return Text(s, style=style)


def section_summary(days: int, strategy: str | None):
    day_filter = f"WHERE stat_date >= current_date - interval '{days} days'" if days else ""
    strat_and = f"AND strategy = %s" if strategy else ""
    params = [strategy] if strategy else []

    rows = execute_query(f"""
        SELECT
            COUNT(DISTINCT stat_date) as active_days,
            SUM(tried) as total_tried,
            SUM(fired) as total_fired
        FROM inplay_bot_stats
        {day_filter} {strat_and}
    """, params)

    r = rows[0] if rows else {}
    tried = int(r.get("total_tried") or 0)
    fired = int(r.get("total_fired") or 0)
    days_active = int(r.get("active_days") or 0)

    # Overall live P&L from simulated_bets
    pb_rows = execute_query(f"""
        SELECT
            COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) as settled,
            COUNT(*) FILTER (WHERE sb.result = 'won') as won,
            COUNT(*) FILTER (WHERE sb.result = 'pending') as pending,
            SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as pnl,
            SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as staked,
            AVG(sb.odds_at_pick) FILTER (WHERE sb.result IN ('won','lost')) as avg_odds
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.xg_source IS NOT NULL
          {'AND sb.pick_time >= now() - interval ' + f"'{days} days'" if days else ''}
          {'AND b.name = %s' if strategy else ''}
    """, [strategy] if strategy else [])

    pb = pb_rows[0] if pb_rows else {}
    settled = int(pb.get("settled") or 0)
    won = int(pb.get("won") or 0)
    pending = int(pb.get("pending") or 0)
    pnl = float(pb.get("pnl") or 0)
    staked = float(pb.get("staked") or 0)
    avg_odds = float(pb["avg_odds"]) if pb.get("avg_odds") else None
    roi = pnl / staked if staked > 0 else None
    hit = won / settled if settled else None

    label = f"last {days}d" if days else "all-time"
    if strategy:
        label += f" | {strategy}"

    console.print(f"\n[bold cyan]═══ Inplay Bot Report ({label}) ═══[/bold cyan]\n")
    console.print(
        f"  Stats DB: [bold]{days_active}[/bold] days recorded  |  "
        f"Tried: [bold]{tried:,}[/bold]  |  "
        f"Fired: [bold]{fired:,}[/bold]  |  "
        f"Fire rate: {_pct_str(fired, tried)}\n"
    )
    if settled > 0 or pending > 0:
        console.print(
            f"  Bets: Settled [bold]{settled}[/bold]  |  "
            f"Pending [bold]{pending}[/bold]  |  "
            f"Hit {'[green]' if hit and hit > 0.5 else ''}"
            f"{f'{hit:.1%}' if hit else '—'}"
            f"{'[/green]' if hit and hit > 0.5 else ''}  |  "
            f"Avg odds {f'{avg_odds:.2f}' if avg_odds else '—'}  |  "
            f"ROI {_roi_str(roi, settled)}\n"
        )
    else:
        console.print("  [dim]No live bets settled yet in this window.[/dim]\n")


def section_strategy_table(days: int, strategy: str | None):
    day_filter = f"AND s.stat_date >= current_date - interval '{days} days'" if days else ""
    strat_filter = "AND s.strategy = %s" if strategy else ""
    params = [strategy] if strategy else []

    # Stats from inplay_bot_stats
    stat_rows = execute_query(f"""
        SELECT
            strategy,
            COUNT(DISTINCT stat_date) as active_days,
            SUM(tried) as tried,
            SUM(fired) as fired,
            MAX(updated_at) as last_seen
        FROM inplay_bot_stats s
        WHERE 1=1 {day_filter} {strat_filter}
        GROUP BY strategy
        ORDER BY SUM(fired) DESC, SUM(tried) DESC
    """, params)

    # P&L from simulated_bets per bot
    pnl_rows = execute_query(f"""
        SELECT
            b.name as bot_name,
            COUNT(*) FILTER (WHERE sb.result IN ('won','lost')) as settled,
            COUNT(*) FILTER (WHERE sb.result = 'won') as won,
            COUNT(*) FILTER (WHERE sb.result = 'pending') as pending,
            SUM(sb.pnl) FILTER (WHERE sb.result IN ('won','lost')) as pnl,
            SUM(sb.stake) FILTER (WHERE sb.result IN ('won','lost')) as staked,
            AVG(sb.odds_at_pick) as avg_odds,
            MAX(sb.pick_time) as last_bet
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        WHERE sb.xg_source IS NOT NULL
          {'AND sb.pick_time >= now() - interval ' + f"'{days} days'" if days else ''}
          {'AND b.name = %s' if strategy else ''}
        GROUP BY b.name
    """, [strategy] if strategy else [])

    pnl_map = {r["bot_name"]: r for r in pnl_rows}

    # Merge: include all strategies that appear in either source
    all_strategies = set(r["strategy"] for r in stat_rows) | set(pnl_map.keys())
    # Filter to known inplay strategies only
    all_strategies = {s for s in all_strategies if s.startswith("inplay_")}

    if not all_strategies:
        console.print("[dim]  No strategy data yet — inplay_bot_stats populates on first heartbeat (every 10 cycles = ~5 min after bot starts).[/dim]\n")
        return

    stat_map = {r["strategy"]: r for r in stat_rows}

    t = Table(title=f"Strategy Funnel + P&L (last {days}d)", show_lines=False)
    t.add_column("Strategy", style="cyan", no_wrap=True)
    t.add_column("Description", style="dim", no_wrap=True, max_width=44)
    t.add_column("Tried", justify="right", no_wrap=True)
    t.add_column("Fired", justify="right", no_wrap=True)
    t.add_column("Fire%", justify="right", no_wrap=True)
    t.add_column("Bets", justify="right", no_wrap=True)
    t.add_column("Settled", justify="right", no_wrap=True)
    t.add_column("Hit%", justify="right", no_wrap=True)
    t.add_column("Odds", justify="right", no_wrap=True)
    t.add_column("ROI", justify="right", no_wrap=True)
    t.add_column("Last bet", justify="right", style="dim", no_wrap=True)

    # Sort: most tried first, then alphabetically for those without stats
    sorted_strats = sorted(
        all_strategies,
        key=lambda s: (-(stat_map[s]["tried"] if s in stat_map else 0), s),
    )

    for sname in sorted_strats:
        sr = stat_map.get(sname, {})
        pr = pnl_map.get(sname, {})

        tried = int(sr.get("tried") or 0)
        fired = int(sr.get("fired") or 0)
        days_act = int(sr.get("active_days") or 0)
        settled = int(pr.get("settled") or 0)
        won = int(pr.get("won") or 0)
        pending = int(pr.get("pending") or 0)
        pnl = float(pr.get("pnl") or 0)
        staked = float(pr.get("staked") or 0)
        avg_odds = float(pr["avg_odds"]) if pr.get("avg_odds") else None
        roi = pnl / staked if staked > 0 else None
        hit = won / settled if settled else None
        last_bet = str(pr["last_bet"])[:10] if pr.get("last_bet") else "—"
        total_bets = settled + pending

        desc = _DESCRIPTIONS.get(sname, "—")
        # Truncate description to fit without wrapping
        if len(desc) > 44:
            desc = desc[:41] + "…"
        t.add_row(
            sname,
            desc,
            f"{tried:,}" if tried else "—",
            # Show "0" when we have data (tried>0) but nothing fired; "—" when no data at all
            str(fired) if tried else "—",
            _pct_str(fired, tried),
            str(total_bets) if total_bets else "—",
            str(settled) if settled else "—",
            f"{hit:.1%}" if hit is not None else "—",
            f"{avg_odds:.2f}" if avg_odds else "—",
            _roi_str(roi, settled),
            last_bet,
        )

    console.print(t)
    console.print()


def section_daily_activity(days: int, strategy: str | None):
    """Last 7 days of fired counts per strategy — quick activity heatmap."""
    show_days = min(days, 7)
    strat_filter = "AND strategy = %s" if strategy else ""
    params_extra = [strategy] if strategy else []

    rows = execute_query(f"""
        SELECT stat_date, strategy, tried, fired
        FROM inplay_bot_stats
        WHERE stat_date >= current_date - interval '{show_days} days'
          {strat_filter}
        ORDER BY stat_date DESC, strategy
    """, params_extra)

    if not rows:
        return

    # Collect unique dates and strategies
    dates = sorted(set(str(r["stat_date"])[:10] for r in rows), reverse=True)
    strategies = sorted(set(r["strategy"] for r in rows if r["strategy"].startswith("inplay_")))

    # Build lookup: (date, strategy) → fired
    lookup: dict[tuple, int] = {}
    tried_lookup: dict[tuple, int] = {}
    for r in rows:
        k = (str(r["stat_date"])[:10], r["strategy"])
        lookup[k] = int(r.get("fired") or 0)
        tried_lookup[k] = int(r.get("tried") or 0)

    t = Table(title=f"Daily Activity — fired counts (last {show_days} days)", show_lines=False)
    t.add_column("Strategy", style="cyan", no_wrap=True)
    for d in dates:
        t.add_column(d[5:], justify="right", no_wrap=True)  # show MM-DD
    t.add_column("Σ fired", justify="right", style="bold", no_wrap=True)
    t.add_column("Σ tried", justify="right", style="dim", no_wrap=True)

    for sname in strategies:
        vals = []
        total_fired = 0
        total_tried = 0
        for d in dates:
            fired = lookup.get((d, sname), 0)
            tried = tried_lookup.get((d, sname), 0)
            total_fired += fired
            total_tried += tried
            if fired == 0:
                vals.append(Text("·", style="dim"))
            elif fired < 3:
                vals.append(Text(str(fired), style="yellow"))
            else:
                vals.append(Text(str(fired), style="green"))
        t.add_row(sname, *vals, Text(str(total_fired), style="bold"), Text(f"{total_tried:,}", style="dim"))

    console.print(t)
    console.print()


def section_recent_bets(days: int, strategy: str | None, limit: int = 20):
    strat_filter = "AND b.name = %s" if strategy else ""
    day_filter = f"AND sb.pick_time >= now() - interval '{days} days'" if days else ""
    params = []
    if strategy:
        params.append(strategy)

    rows = execute_query(f"""
        SELECT
            sb.pick_time,
            b.name as bot_name,
            m.date as kickoff,
            ht.name as home_team,
            at.name as away_team,
            sb.market,
            sb.selection,
            sb.odds_at_pick,
            sb.edge_percent,
            sb.xg_source,
            sb.result,
            sb.pnl,
            sb.stake
        FROM simulated_bets sb
        JOIN bots b ON b.id = sb.bot_id
        JOIN matches m ON m.id = sb.match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE sb.xg_source IS NOT NULL
          {day_filter} {strat_filter}
        ORDER BY sb.pick_time DESC
        LIMIT %s
    """, params + [limit])

    if not rows:
        console.print("[dim]  No live bets in this window.[/dim]\n")
        return

    t = Table(title=f"Recent {limit} Live Bets", show_lines=False)
    t.add_column("Pick time", style="dim", no_wrap=True)
    t.add_column("Strategy", style="cyan", no_wrap=True)
    t.add_column("Match", no_wrap=True)
    t.add_column("Bet", no_wrap=True)
    t.add_column("Odds", justify="right", no_wrap=True)
    t.add_column("Edge", justify="right", no_wrap=True)
    t.add_column("xG", justify="right", style="dim", no_wrap=True)
    t.add_column("Result", no_wrap=True)
    t.add_column("P&L", justify="right", no_wrap=True)

    for r in rows:
        result = r.get("result") or "pending"
        result_style = "green" if result == "won" else "red" if result == "lost" else "dim"
        pnl = float(r.get("pnl") or 0)
        edge = float(r["edge_percent"]) if r.get("edge_percent") is not None else None
        match = f"{r['home_team']} v {r['away_team']}"
        bet = f"{r['market']} {r['selection'] or ''}".strip()

        t.add_row(
            str(r["pick_time"])[:16].replace("T", " "),
            r["bot_name"],
            match[:26],
            bet[:18],
            f"{float(r['odds_at_pick']):.2f}",
            f"{edge:.1%}" if edge is not None else "—",
            r.get("xg_source") or "—",
            Text(result, style=result_style),
            Text(f"€{pnl:+.2f}", style="green" if pnl > 0 else "red" if pnl < 0 else "dim"),
        )

    console.print(t)


def main():
    parser = argparse.ArgumentParser(description="Inplay bot strategy report")
    parser.add_argument("--days", type=int, default=14, help="Window in days (default 14)")
    parser.add_argument("--strategy", type=str, default=None, help="Filter to one strategy (e.g. inplay_a)")
    parser.add_argument("--recent", type=int, default=20, help="Number of recent bets to show (default 20)")
    args = parser.parse_args()

    section_summary(args.days, args.strategy)
    section_strategy_table(args.days, args.strategy)
    section_daily_activity(args.days, args.strategy)
    section_recent_bets(args.days, args.strategy, args.recent)


if __name__ == "__main__":
    main()
