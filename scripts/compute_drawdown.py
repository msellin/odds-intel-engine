"""
GROWTH-DRAWDOWN-TRANSPARENCY — compute honest drawdown statistics.

Reads `simulated_bets` (settled rows only: result IN ('won','lost')), walks
the chronological daily-aggregated cumulative P&L, and identifies the
worst peak-to-trough drawdown plus monthly + weekly variance.

The goal is to publish honest "this is what value betting actually
looks like" educational content (see /methodology) rather than hiding
volatility behind beta framing.

Usage:
    python scripts/compute_drawdown.py
    python scripts/compute_drawdown.py --since 2026-05-01
    python scripts/compute_drawdown.py --out dev/active/drawdown.md
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


def _conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set")
    return psycopg2.connect(url)


def fetch_daily_pnl(since: str | None) -> list[tuple[date, float, int]]:
    sql = """
    SELECT pick_time::date AS d,
           SUM(COALESCE(pnl, 0)) AS daily_pnl,
           COUNT(*) AS n
    FROM simulated_bets
    WHERE result IN ('won','lost')
    """
    params: list = []
    if since:
        sql += " AND pick_time::date >= %s"
        params.append(since)
    sql += " GROUP BY pick_time::date ORDER BY pick_time::date"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [(r[0], float(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()]


def compute_drawdown(days: list[tuple[date, float, int]]) -> dict:
    """Walks cumulative P&L, tracks peak-to-trough drawdown."""
    cum = 0.0
    peak = 0.0
    peak_date = days[0][0] if days else None
    worst_dd = 0.0
    worst_dd_peak_date = peak_date
    worst_dd_peak = 0.0
    worst_dd_trough_date = peak_date
    worst_dd_trough_cum = 0.0
    series = []
    for d, daily_pnl, _n in days:
        cum += daily_pnl
        if cum > peak:
            peak = cum
            peak_date = d
        dd = cum - peak
        if dd < worst_dd:
            worst_dd = dd
            worst_dd_peak_date = peak_date
            worst_dd_peak = peak
            worst_dd_trough_date = d
            worst_dd_trough_cum = cum
        series.append((d, cum, dd))

    # Recovery: first day where cum reaches the prior peak after the trough
    recovery_date = None
    for d, cum_v, _dd in series:
        if d > worst_dd_trough_date and cum_v >= worst_dd_peak:
            recovery_date = d
            break

    return {
        "series": series,
        "final_cum": cum,
        "peak": peak,
        "peak_date": peak_date,
        "worst_dd_eur": worst_dd,
        "worst_dd_peak_date": worst_dd_peak_date,
        "worst_dd_peak": worst_dd_peak,
        "worst_dd_trough_date": worst_dd_trough_date,
        "worst_dd_trough_cum": worst_dd_trough_cum,
        "worst_dd_duration_days": (worst_dd_trough_date - worst_dd_peak_date).days
            if worst_dd_peak_date else 0,
        "worst_dd_pct_of_peak": (worst_dd / worst_dd_peak * 100.0)
            if worst_dd_peak > 0 else 0.0,
        "recovery_date": recovery_date,
        "currently_below_peak_eur": cum - peak,
    }


def aggregate(days: list[tuple[date, float, int]], group_key) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    for d, pnl, n in days:
        k = group_key(d)
        out[k]["pnl"] += pnl
        out[k]["n"] += n
    return dict(out)


def write_md(out_path: Path, since: str | None, days: list, dd: dict,
             monthly: dict, weekly: dict):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    n_days = len(days)
    n_bets = sum(n for _, _, n in days)
    first, last = days[0][0], days[-1][0]
    worst_wk = min(weekly.items(), key=lambda kv: kv[1]["pnl"])
    best_wk = max(weekly.items(), key=lambda kv: kv[1]["pnl"])
    below_peak_eur = dd["currently_below_peak_eur"]
    currently_line = ("at peak" if below_peak_eur == 0
                      else f"**€{below_peak_eur:+,.2f}** below peak")
    recovery_line = (f"Recovered to prior peak by **{dd['recovery_date']}**"
                     if dd['recovery_date'] else "Not yet recovered to prior peak")
    lines = [
        "# GROWTH-DRAWDOWN-TRANSPARENCY — settled-bet drawdown profile",
        "",
        f"_Generated {now} — sample: {n_bets:,} settled bets across {n_days} days, "
        f"{first} → {last}_",
        "",
        "## Top-line",
        "",
        f"- Final cumulative P&L: **€{dd['final_cum']:+,.2f}**",
        f"- Peak: **€{dd['peak']:+,.2f}** on {dd['peak_date']}",
        f"- Currently {currently_line}",
        "",
        "## Worst drawdown",
        "",
        f"- Magnitude: **€{dd['worst_dd_eur']:+,.2f}**",
        f"- From peak on **{dd['worst_dd_peak_date']}** (€{dd['worst_dd_peak']:+,.2f})",
        f"- To trough on **{dd['worst_dd_trough_date']}** (€{dd['worst_dd_trough_cum']:+,.2f})",
        f"- Duration: **{dd['worst_dd_duration_days']} days**",
        f"- As % of peak at the time: **{dd['worst_dd_pct_of_peak']:+.1f}%**",
        f"- {recovery_line}",
        "",
        "## Best / worst week",
        "",
        f"- Best week: **{best_wk[0]}** = €{best_wk[1]['pnl']:+,.2f} on {best_wk[1]['n']:,} bets",
        f"- Worst week: **{worst_wk[0]}** = €{worst_wk[1]['pnl']:+,.2f} on {worst_wk[1]['n']:,} bets",
        "",
        "## Monthly P&L",
        "",
        "| Month | Settled bets | P&L (€, flat stake) |",
        "|---|---:|---:|",
    ]
    for k in sorted(monthly.keys()):
        lines.append(f"| {k} | {monthly[k]['n']:,} | €{monthly[k]['pnl']:+,.2f} |")
    lines += [
        "",
        "## Honest caveats (publish these too)",
        "",
        f"- Sample is small: {n_days} days of settled bets is far below the 500-bet threshold that gives any drawdown number statistical weight. Numbers above will change materially as the chain extends.",
        "- This is **flat-stake** P&L — kelly-sized bots will diverge from these numbers.",
        "- The chain has one known historical zero-day (2026-05-02) before continuity monitoring was wired. See WORKFLOWS.md.",
        "- Drawdown is the cost of being a +EV bettor, not evidence the model is broken. CLV is the leading indicator; drawdown is the lagging price you pay for variance.",
        "",
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None)
    parser.add_argument("--out", default="dev/active/drawdown.md")
    args = parser.parse_args()

    days = fetch_daily_pnl(args.since)
    if not days:
        print("No settled bets in window — nothing to compute.")
        return 0

    dd = compute_drawdown(days)
    monthly = aggregate(days, lambda d: f"{d.year}-{d.month:02d}")
    weekly = aggregate(days, lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}")

    print(f"Settled bets window: {days[0][0]} → {days[-1][0]} ({len(days)} days w/ bets)")
    print(f"Final cum: €{dd['final_cum']:+,.2f}  Peak: €{dd['peak']:+,.2f} on {dd['peak_date']}")
    print(f"Worst DD: €{dd['worst_dd_eur']:+,.2f} ({dd['worst_dd_pct_of_peak']:+.1f}% of peak), "
          f"{dd['worst_dd_peak_date']} → {dd['worst_dd_trough_date']} "
          f"({dd['worst_dd_duration_days']} days)")
    if dd['recovery_date']:
        print(f"Recovered: {dd['recovery_date']}")
    else:
        print(f"Not yet recovered (€{dd['currently_below_peak_eur']:+,.2f} below peak)")

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_md(out_path, args.since, days, dd, monthly, weekly)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
