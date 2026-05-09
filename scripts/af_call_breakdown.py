"""
AF call breakdown — answers "where did the 26K go?"

Reads api_budget_log rows (with endpoint_breakdown_today JSONB) for the last 24h,
prints a sorted endpoint × hour matrix and a daily total.

Run: venv/bin/python3 scripts/af_call_breakdown.py [--days N]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from workers.api_clients.db import execute_query  # noqa: E402


def fetch(days: int):
    return execute_query(
        """
        SELECT logged_at, log_date, calls_today, remaining,
               endpoint_breakdown, endpoint_breakdown_today
        FROM api_budget_log
        WHERE logged_at > now() - (%s || ' days')::interval
        ORDER BY logged_at ASC
        """,
        [str(days)],
    )


def latest_per_day(rows):
    """For each calendar day, pick the row with the highest calls_today (= latest)."""
    by_day = {}
    for r in rows:
        d = r["log_date"]
        if d not in by_day or r["calls_today"] > by_day[d]["calls_today"]:
            by_day[d] = r
    return [by_day[d] for d in sorted(by_day)]


def hourly_increments(rows):
    """Hourly delta of endpoint_breakdown (since-last-sync values)."""
    out = []
    for r in rows:
        bk = r.get("endpoint_breakdown") or {}
        out.append((r["logged_at"], bk))
    return out


def print_daily_totals(daily_rows):
    print()
    print("=" * 80)
    print("  Daily totals (latest snapshot per day)")
    print("=" * 80)
    print(f"  {'Date':<12} {'calls':>8} {'remaining':>10}")
    for r in daily_rows:
        print(f"  {str(r['log_date']):<12} {r['calls_today']:>8} {r['remaining']:>10}")


def print_endpoint_breakdown(daily_rows):
    print()
    print("=" * 80)
    print("  Endpoint breakdown (cumulative day-to-date, latest row per day)")
    print("=" * 80)
    for r in daily_rows:
        bk = r.get("endpoint_breakdown_today") or {}
        if not bk:
            print(f"\n  {r['log_date']}: no endpoint_breakdown_today (pre-migration row?)")
            continue
        total = sum(bk.values())
        print(f"\n  {r['log_date']}  total={r['calls_today']}  attributed={total}  "
              f"unattributed={r['calls_today'] - total}")
        for ep, n in sorted(bk.items(), key=lambda x: -x[1]):
            pct = (n / total * 100) if total else 0
            print(f"    {ep:<32} {n:>7}  {pct:5.1f}%")


def print_hourly_endpoint_matrix(rows):
    """For the most recent 24h, show endpoint × hour heatmap (since-last-sync values)."""
    if not rows:
        return
    recent = rows[-24:]  # last 24 sync rows ≈ 24h at hourly cadence
    print()
    print("=" * 80)
    print("  Hourly increments (since-last-sync per endpoint)  — last 24h")
    print("=" * 80)
    # Collect all endpoints seen
    all_eps = set()
    for r in recent:
        if r.get("endpoint_breakdown"):
            all_eps.update(r["endpoint_breakdown"].keys())
    if not all_eps:
        print("  No endpoint_breakdown values yet — wait for next hourly sync after migration 086.")
        return
    eps_sorted = sorted(all_eps)
    # Header
    times = [r["logged_at"].strftime("%H") for r in recent]
    print(f"  {'endpoint':<28} {' '.join(f'{t:>4}' for t in times)}")
    for ep in eps_sorted:
        row_total = 0
        cells = []
        for r in recent:
            bk = r.get("endpoint_breakdown") or {}
            n = bk.get(ep, 0)
            row_total += n
            cells.append(f"{n:>4}" if n else f"{'.':>4}")
        print(f"  {ep:<28} {' '.join(cells)}  total={row_total}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()

    rows = fetch(args.days)
    if not rows:
        print(f"No api_budget_log rows in the last {args.days} day(s).")
        return

    print(f"Loaded {len(rows)} api_budget_log rows over {args.days}d.")
    daily = latest_per_day(rows)

    print_daily_totals(daily)
    print_endpoint_breakdown(daily)
    print_hourly_endpoint_matrix(rows)


if __name__ == "__main__":
    main()
