"""
Edge-threshold backtest — answer the question:
   For bets recorded in simulated_bets, how does ROI/CLV vary by edge bucket
   (overall, per market, and on the recent calibrated cohort)?

Drives the auto-place threshold decision (currently UI 5%, placer 3%).

Note: edge_percent is stored as a FRACTION (0.05 = 5%). Bots never log
sub-3% picks (placer's MIN_EDGE floor) so we can't backtest "below 3%".
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg2.extras
from workers.api_clients.db import get_pool

BUCKETS = [
    ("03-05%",  0.03, 0.05),
    ("05-07%",  0.05, 0.07),
    ("07-10%",  0.07, 0.10),
    ("10-15%",  0.10, 0.15),
    ("15-25%",  0.15, 0.25),
    ("25%+",    0.25, 999.0),
]

THRESHOLDS = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.15, 0.20]


def fetch_bets() -> list[dict]:
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT pick_time, market, edge_percent, stake, pnl, result, clv
                FROM simulated_bets
                WHERE result <> 'pending' AND edge_percent IS NOT NULL
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        pool.putconn(conn)


def aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "roi_pct": None, "win_rate_pct": None, "avg_clv_pct": None,
                "total_pnl": 0.0, "total_stake": 0.0, "avg_edge_pct": None}
    n = len(rows)
    stake_sum = sum(float(r["stake"]) for r in rows)
    pnl_sum = sum(float(r["pnl"] or 0) for r in rows)
    won = sum(1 for r in rows if r["result"] == "won")
    settled_wl = sum(1 for r in rows if r["result"] in ("won", "lost"))
    clv_rows = [float(r["clv"]) for r in rows if r["clv"] is not None]
    avg_clv = (sum(clv_rows) / len(clv_rows) * 100) if clv_rows else None
    avg_edge = sum(float(r["edge_percent"]) for r in rows) / n * 100
    return {
        "n": n,
        "win_rate_pct": (won / settled_wl * 100) if settled_wl else None,
        "roi_pct": (pnl_sum / stake_sum * 100) if stake_sum else None,
        "total_pnl": pnl_sum,
        "total_stake": stake_sum,
        "avg_edge_pct": avg_edge,
        "avg_clv_pct": avg_clv,
    }


def _fmt(v, spec):
    return format(v, spec) if v is not None else "—"


def print_buckets(label: str, rows: list[dict]) -> None:
    print(f"\n=== {label} (n={len(rows)}) ===")
    print(f"{'bucket':<8} {'n':>5} {'edge%':>6} {'win%':>6} {'ROI%':>7} "
          f"{'PnL':>9} {'stake':>9} {'CLV%':>7}")
    for name, lo, hi in BUCKETS:
        sub = [r for r in rows if lo <= float(r["edge_percent"]) < hi]
        a = aggregate(sub)
        if a["n"] == 0:
            continue
        print(f"{name:<8} {a['n']:>5} "
              f"{_fmt(a['avg_edge_pct'], '5.1f'):>6} "
              f"{_fmt(a['win_rate_pct'], '5.1f'):>6} "
              f"{_fmt(a['roi_pct'], '+6.2f'):>7} "
              f"{a['total_pnl']:>+9.2f} {a['total_stake']:>9.2f} "
              f"{_fmt(a['avg_clv_pct'], '+6.2f'):>7}")


def print_cumulative(label: str, rows: list[dict]) -> None:
    print(f"\n--- {label}: cumulative (take ALL bets where edge >= threshold) ---")
    print(f"{'thresh':>6} {'n':>5} {'cov':>4} {'win%':>6} {'ROI%':>7} {'PnL':>9} {'CLV%':>7}")
    total = len(rows)
    for t in THRESHOLDS:
        sub = [r for r in rows if float(r["edge_percent"]) >= t]
        a = aggregate(sub)
        if a["n"] == 0:
            continue
        coverage = a["n"] / total * 100 if total else 0
        print(f"{t*100:>5.0f}% {a['n']:>5} {coverage:>3.0f}% "
              f"{_fmt(a['win_rate_pct'], '5.1f'):>6} "
              f"{_fmt(a['roi_pct'], '+6.2f'):>7} "
              f"{a['total_pnl']:>+9.2f} "
              f"{_fmt(a['avg_clv_pct'], '+6.2f'):>7}")


def main():
    all_rows = fetch_bets()
    print_buckets("ALL bets, all markets, all time (since 2026-05-01)", all_rows)
    print_cumulative("ALL bets, all markets", all_rows)

    markets = ["1x2", "o/u", "asian_handicap", "btts", "double_chance"]
    for m in markets:
        mrows = [r for r in all_rows if r["market"] == m]
        print_buckets(f"MARKET = {m}", mrows)
        if len(mrows) >= 100:
            print_cumulative(f"market={m}", mrows)

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    recent = [r for r in all_rows if r["pick_time"] >= cutoff]
    print_buckets("LAST 14 DAYS — all markets (modern calibrated cohort)", recent)
    print_cumulative("LAST 14 DAYS — all markets", recent)

    for m in markets:
        mrows = [r for r in recent if r["market"] == m]
        if len(mrows) >= 30:
            print_buckets(f"LAST 14 DAYS — market={m}", mrows)


if __name__ == "__main__":
    main()
