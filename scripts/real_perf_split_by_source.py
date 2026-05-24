"""Split real_bets performance by source: placer (--record) vs manual /admin/place.

Placer rows are identified by notes LIKE 'auto%' (set in coolbet_placer.py:1311).
Manual rows come from the /admin/place modal — notes is user-typed or blank.

Usage:
    python3 scripts/real_perf_split_by_source.py [--days 60]
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]


def summarise(label, rows):
    settled = [r for r in rows if r["result"] in ("won", "lost", "half_won", "half_lost")]
    pending = [r for r in rows if r["result"] == "pending"]
    void = [r for r in rows if r["result"] == "void"]

    staked = sum(float(r["stake"]) for r in settled)
    pnl = sum(float(r["pnl"] or 0) for r in settled)
    won = [r for r in settled if r["result"] in ("won", "half_won")]
    hit = len(won) / len(settled) * 100 if settled else 0
    roi = pnl / staked * 100 if staked else 0

    slips = [float(r["slippage_pct"]) for r in rows if r["slippage_pct"] is not None]
    avg_slip = sum(slips) / len(slips) if slips else 0

    print(f"\n=== {label} ===")
    print(f"  Total: {len(rows)}  |  Settled: {len(settled)}  |  Pending: {len(pending)}  |  Void: {len(void)}  |  Hit: {hit:.1f}%")
    print(f"  Staked: €{staked:.2f}  |  P&L: €{pnl:+.2f}  |  ROI: {roi:+.2f}%")
    print(f"  Avg slippage: {avg_slip:+.2f}%")


def by_market(label, rows):
    settled = [r for r in rows if r["result"] in ("won", "lost", "half_won", "half_lost")]
    if not settled:
        return
    print(f"\n  By market ({label}, min 3 settled):")
    groups = defaultdict(list)
    for r in settled:
        # Normalise case so 1X2/1x2 etc collapse
        k = (str(r["market"]).lower(), str(r["selection"]).lower())
        groups[k].append(r)
    rows_out = []
    for (m, s), bs in groups.items():
        if len(bs) < 3:
            continue
        staked = sum(float(b["stake"]) for b in bs)
        pnl = sum(float(b["pnl"] or 0) for b in bs)
        roi = pnl / staked * 100 if staked else 0
        rows_out.append((m, s, len(bs), roi, pnl))
    rows_out.sort(key=lambda r: -r[3])
    for m, s, n, roi, pnl in rows_out:
        print(f"    {m:<16} {s:<14} n={n:<4} ROI {roi:+7.1f}%  P&L €{pnl:+.2f}")


def by_bot(label, rows):
    settled = [r for r in rows if r["result"] in ("won", "lost", "half_won", "half_lost")]
    if not settled:
        return
    print(f"\n  By bot ({label}, min 5 settled):")
    groups = defaultdict(list)
    for r in settled:
        groups[r["bot_name"] or "unknown"].append(r)
    rows_out = []
    for bot, bs in groups.items():
        if len(bs) < 5:
            continue
        staked = sum(float(b["stake"]) for b in bs)
        pnl = sum(float(b["pnl"] or 0) for b in bs)
        roi = pnl / staked * 100 if staked else 0
        rows_out.append((bot, len(bs), roi, pnl))
    rows_out.sort(key=lambda r: -r[2])
    for bot, n, roi, pnl in rows_out:
        print(f"    {bot:<32} n={n:<4} ROI {roi:+7.1f}%  P&L €{pnl:+.2f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=60)
    args = p.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """
        SELECT rb.id, rb.market, rb.selection, rb.bookmaker, rb.captured_odds,
               rb.actual_odds, rb.slippage_pct, rb.stake, rb.result, rb.pnl,
               rb.placed_at, rb.notes, rb.bot_id, b.name AS bot_name
        FROM real_bets rb
        LEFT JOIN bots b ON b.id = rb.bot_id
        WHERE rb.placed_at >= NOW() - INTERVAL '%s days'
        ORDER BY rb.placed_at DESC
        """,
        (args.days,),
    )
    rows = cur.fetchall()

    placer = [r for r in rows if (r["notes"] or "").startswith("auto")]
    manual = [r for r in rows if not (r["notes"] or "").startswith("auto")]

    print(f"Loaded {len(rows)} real_bets from last {args.days}d")
    print(f"  Placer rows (--record, notes LIKE 'auto%'): {len(placer)}")
    print(f"  Manual rows (/admin/place): {len(manual)}")

    summarise("ALL ROWS", rows)
    summarise("PLACER ROWS — rule-driven, real-time Coolbet odds", placer)
    summarise("MANUAL ROWS — /admin/place, user-selected", manual)

    by_bot("placer", placer)
    by_bot("manual", manual)

    by_market("placer", placer)
    by_market("manual", manual)

    conn.close()


if __name__ == "__main__":
    main()
