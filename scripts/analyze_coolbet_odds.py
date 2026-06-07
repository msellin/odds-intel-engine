#!/usr/bin/env python3
"""
Coolbet / Pinnacle / Bet365 odds analysis.

Answers:
  1. Margin by market — how much vig does each book charge?
  2. Coolbet vs Pinnacle closing-line value
  3. Coolbet vs Bet365 comparison
  4. Odds stability pre-kickoff
  5. Lag analysis — does Coolbet follow Pinnacle moves?
  6. Best betting window — when is Coolbet softest?

Usage:
  PYTHONPATH=. python3 scripts/analyze_coolbet_odds.py
"""

import os
import sys
from collections import defaultdict

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATE_FILTER = "2026-05-20"

# Direct connection with statement_timeout=0 — bypasses the pool so long
# analysis queries aren't cancelled by Supabase's role-level timeout.
_conn = None

def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        dsn = os.environ["DATABASE_URL"]
        _conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        _conn.autocommit = False  # keep transactions open so SET LOCAL is respected
    return _conn

MARKET_SEL_COUNTS = {
    "1x2": 3,
    "over_under_25": 2,
    "over_under_35": 2,
    "btts": 2,
    "asian_handicap": 2,
    "double_chance": 2,
}


def pct(v):
    return f"{v*100:.2f}%"


def q(sql, params=None):
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # SET LOCAL scopes the timeout to this transaction only.
            # Overrides the role-level statement_timeout even through PgBouncer.
            cur.execute("SET LOCAL statement_timeout = '300000'")  # 5 min
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    except Exception:
        conn.rollback()
        raise


# ── 1. MARGIN ────────────────────────────────────────────────────────────────
# Use: AVG(1/odds) per bookmaker+market+selection, then sum those averages per
# market — avoids slow GROUP BY timestamp that produced tiny n for Coolbet.

def analyze_margins():
    print("\n" + "="*60)
    print("1. BOOK MARGIN BY MARKET (since May 20, pre-KO, non-live)")
    print("="*60)
    print("  Method: avg implied-prob per selection, summed to get total vig.\n")

    for market, n_sel in MARKET_SEL_COUNTS.items():
        results = {}
        for book in ["Coolbet", "Pinnacle", "Bet365"]:
            rows = q("""
                SELECT selection, AVG(1.0 / odds) as avg_imp, COUNT(*) as n
                FROM odds_snapshots
                WHERE bookmaker = %(book)s
                  AND market = %(market)s
                  AND is_live = FALSE
                  AND odds > 1.01
                  AND minutes_to_kickoff > 0
                  AND timestamp >= %(since)s
                GROUP BY selection
            """, {"book": book, "market": market, "since": DATE_FILTER})

            if len(rows) >= n_sel:
                total_imp = sum(float(r['avg_imp']) for r in rows)
                margin = total_imp - 1.0
                n_snapshots = sum(r['n'] for r in rows)
                results[book] = (margin, n_snapshots // n_sel)

        if not results:
            continue

        print(f"  Market: {market}")
        for book, (margin, n) in sorted(results.items(), key=lambda x: x[1][0]):
            print(f"    {book:<10} margin={pct(margin)}  (n≈{n} snapshots)")
        print()


# ── 2. CLOSING-LINE vs PINNACLE ──────────────────────────────────────────────

def analyze_vs_pinnacle():
    print("="*60)
    print("2. CLOSING-LINE: Coolbet vs Pinnacle (0-60 min pre-KO)")
    print("="*60)
    print("  Positive = Coolbet gives better odds (higher payout) than Pinnacle.\n")

    for market in list(MARKET_SEL_COUNTS.keys()):
        # Avg closing odds per match+selection for each book separately, then join in Python
        cb_rows = q("""
            SELECT match_id::text, selection, handicap_line::text, AVG(odds) as odds
            FROM odds_snapshots
            WHERE bookmaker = 'Coolbet'
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 60
              AND odds > 1.01
              AND timestamp >= %(since)s
            GROUP BY match_id, selection, handicap_line
        """, {"market": market, "since": DATE_FILTER})

        pin_rows = q("""
            SELECT match_id::text, selection, handicap_line::text, AVG(odds) as odds
            FROM odds_snapshots
            WHERE bookmaker = 'Pinnacle'
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 60
              AND odds > 1.01
              AND timestamp >= %(since)s
            GROUP BY match_id, selection, handicap_line
        """, {"market": market, "since": DATE_FILTER})

        if not cb_rows or not pin_rows:
            print(f"  {market}: no paired data\n")
            continue

        pin_map = {(r['match_id'], r['selection'], r['handicap_line']): float(r['odds']) for r in pin_rows}

        diffs_by_sel: dict = defaultdict(list)
        for r in cb_rows:
            key = (r['match_id'], r['selection'], r['handicap_line'])
            if key in pin_map:
                diff = float(r['odds']) - pin_map[key]
                diffs_by_sel[r['selection']].append(diff)

        if not diffs_by_sel:
            print(f"  {market}: no matched pairs\n")
            continue

        print(f"  Market: {market}")
        for sel in sorted(diffs_by_sel):
            vals = diffs_by_sel[sel]
            avg = sum(vals) / len(vals)
            avg_pct = avg / (sum(pin_map.get((r['match_id'], r['selection'], r['handicap_line']), 1) for r in cb_rows if r['selection'] == sel) / max(len(vals), 1)) * 100
            sign = "+" if avg >= 0 else ""
            verdict = "Coolbet BETTER" if avg > 0.01 else ("Pinnacle BETTER" if avg < -0.01 else "≈ equal")
            print(f"    {sel:<20} {sign}{avg:.4f} odds avg diff  [{verdict}]  n={len(vals)}")
        print()


# ── 3. CLOSING-LINE vs BET365 ────────────────────────────────────────────────

def analyze_vs_bet365():
    print("="*60)
    print("3. CLOSING-LINE: Coolbet vs Bet365 (0-60 min pre-KO)")
    print("="*60)
    print("  Positive = Coolbet gives better odds than Bet365.\n")

    for market in list(MARKET_SEL_COUNTS.keys()):
        cb_rows = q("""
            SELECT match_id::text, selection, handicap_line::text, AVG(odds) as odds
            FROM odds_snapshots
            WHERE bookmaker = 'Coolbet'
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 60
              AND odds > 1.01
              AND timestamp >= %(since)s
            GROUP BY match_id, selection, handicap_line
        """, {"market": market, "since": DATE_FILTER})

        b3_rows = q("""
            SELECT match_id::text, selection, handicap_line::text, AVG(odds) as odds
            FROM odds_snapshots
            WHERE bookmaker = 'Bet365'
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 60
              AND odds > 1.01
              AND timestamp >= %(since)s
            GROUP BY match_id, selection, handicap_line
        """, {"market": market, "since": DATE_FILTER})

        if not cb_rows or not b3_rows:
            print(f"  {market}: no paired data\n")
            continue

        b3_map = {(r['match_id'], r['selection'], r['handicap_line']): float(r['odds']) for r in b3_rows}

        diffs_by_sel: dict = defaultdict(list)
        for r in cb_rows:
            key = (r['match_id'], r['selection'], r['handicap_line'])
            if key in b3_map:
                diff = float(r['odds']) - b3_map[key]
                diffs_by_sel[r['selection']].append(diff)

        if not diffs_by_sel:
            print(f"  {market}: no matched pairs\n")
            continue

        print(f"  Market: {market}")
        for sel in sorted(diffs_by_sel):
            vals = diffs_by_sel[sel]
            avg = sum(vals) / len(vals)
            sign = "+" if avg >= 0 else ""
            verdict = "Coolbet BETTER" if avg > 0.01 else ("Bet365 BETTER" if avg < -0.01 else "≈ equal")
            print(f"    {sel:<20} {sign}{avg:.4f} odds avg diff  [{verdict}]  n={len(vals)}")
        print()


# ── 4. PRE-KICKOFF STABILITY ─────────────────────────────────────────────────

def analyze_stability():
    print("="*60)
    print("4. COOLBET ODDS STABILITY (move vs final 30-min price)")
    print("="*60)
    print("  How much do prices change between a window and closing?\n")

    windows = [(480, 240), (240, 120), (120, 60), (60, 30)]
    labels   = ["480-240 min", "240-120 min", "120-60 min", "60-30 min"]

    for market in ["1x2", "over_under_25", "over_under_35"]:
        # Get closing odds first
        closing = q("""
            SELECT match_id::text, selection, AVG(odds) as c_odds
            FROM odds_snapshots
            WHERE bookmaker = 'Coolbet'
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 30
              AND odds > 1.01
              AND timestamp >= %(since)s
            GROUP BY match_id, selection
        """, {"market": market, "since": DATE_FILTER})

        if not closing:
            continue

        closing_map = {(r['match_id'], r['selection']): float(r['c_odds']) for r in closing}

        print(f"  Market: {market}")
        for (w_hi, w_lo), label in zip(windows, labels):
            window_rows = q("""
                SELECT match_id::text, selection, AVG(odds) as w_odds
                FROM odds_snapshots
                WHERE bookmaker = 'Coolbet'
                  AND market = %(market)s
                  AND is_live = FALSE
                  AND minutes_to_kickoff BETWEEN %(w_lo)s AND %(w_hi)s
                  AND odds > 1.01
                  AND timestamp >= %(since)s
                GROUP BY match_id, selection
            """, {"market": market, "w_lo": w_lo, "w_hi": w_hi, "since": DATE_FILTER})

            diffs = []
            for r in window_rows:
                key = (r['match_id'], r['selection'])
                if key in closing_map:
                    move = abs(float(r['w_odds']) - closing_map[key])
                    if move < 2.0:
                        diffs.append((move, closing_map[key]))

            if diffs:
                avg_move = sum(d[0] for d in diffs) / len(diffs)
                avg_pct  = sum(d[0] / d[1] for d in diffs) / len(diffs) * 100
                print(f"    {label}: avg move {avg_move:.4f} odds ({avg_pct:.2f}%)  n={len(diffs)}")
        print()


# ── 5. LAG ANALYSIS ──────────────────────────────────────────────────────────

def analyze_lag():
    print("="*60)
    print("5. COOLBET LAG BEHIND PINNACLE")
    print("="*60)
    print("  Minutes between a Pinnacle move and Coolbet following.\n")

    for market in ["1x2", "over_under_25"]:
        rows = q("""
            SELECT
                match_id::text,
                bookmaker,
                selection,
                odds,
                timestamp,
                minutes_to_kickoff
            FROM odds_snapshots
            WHERE bookmaker IN ('Coolbet', 'Pinnacle')
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 600
              AND odds > 1.01
              AND timestamp >= %(since)s
              AND match_id IN (
                  SELECT DISTINCT match_id FROM odds_snapshots
                  WHERE bookmaker = 'Coolbet' AND market = %(market)s AND timestamp >= %(since)s
              )
            ORDER BY match_id, selection, bookmaker, timestamp
        """, {"market": market, "since": DATE_FILTER})

        if not rows:
            print(f"  {market}: no data\n")
            continue

        series: dict = defaultdict(list)
        for r in rows:
            key = (r['match_id'], r['selection'], r['bookmaker'])
            series[key].append((r['timestamp'], float(r['odds'])))

        lags = []
        match_ids = {k[0] for k in series}
        for mid in match_ids:
            for sel in {k[1] for k in series if k[0] == mid}:
                pin_key = (mid, sel, 'Pinnacle')
                cb_key  = (mid, sel, 'Coolbet')
                if pin_key not in series or cb_key not in series:
                    continue

                pin_ts = sorted(series[pin_key])
                cb_ts  = sorted(series[cb_key])

                for i in range(1, len(pin_ts)):
                    move = pin_ts[i][1] - pin_ts[i-1][1]
                    if abs(move) < 0.02:
                        continue
                    move_time = pin_ts[i][0]
                    move_sign = 1 if move > 0 else -1

                    cb_before = [o for t, o in cb_ts if t < move_time]
                    if not cb_before:
                        continue
                    cb_base = cb_before[-1]

                    for ts, odds in cb_ts:
                        if ts <= move_time:
                            continue
                        cb_move = odds - cb_base
                        if abs(cb_move) >= 0.01 and (cb_move * move_sign > 0):
                            lag_min = (ts - move_time).total_seconds() / 60
                            if 0 < lag_min < 300:
                                lags.append(lag_min)
                            break

        if lags:
            lags.sort()
            p50 = lags[len(lags)//2]
            p25 = lags[len(lags)//4]
            p75 = lags[len(lags)*3//4]
            avg = sum(lags) / len(lags)
            print(f"  {market}: {len(lags)} correlated pin→cb moves")
            print(f"    median lag = {p50:.0f} min  (p25={p25:.0f}, p75={p75:.0f}, avg={avg:.0f})")
        else:
            print(f"  {market}: too few Coolbet snapshots for reliable lag detection")
        print()


# ── 6. BEST BETTING WINDOW ───────────────────────────────────────────────────

def analyze_best_window():
    print("="*60)
    print("6. BEST BETTING WINDOW (Coolbet / Pinnacle ratio by time-to-KO)")
    print("="*60)
    print("  Ratio > 1 = Coolbet better than Pinnacle at that time.\n")

    window_defs = [
        ("360+ min",    360, 9999),
        ("240-360 min", 240, 360),
        ("120-240 min", 120, 240),
        ("60-120 min",   60, 120),
        ("30-60 min",    30,  60),
        ("0-30 min",      0,  30),
    ]

    for market in ["1x2", "over_under_25", "over_under_35", "btts"]:
        # Fetch Coolbet and Pinnacle separately for the date window, join in Python
        cb_rows = q("""
            SELECT match_id::text, selection, handicap_line::text,
                   (minutes_to_kickoff / 30) * 30 as bucket,
                   AVG(odds) as odds
            FROM odds_snapshots
            WHERE bookmaker = 'Coolbet'
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 700
              AND odds > 1.01
              AND timestamp >= %(since)s
            GROUP BY match_id, selection, handicap_line,
                     (minutes_to_kickoff / 30) * 30
        """, {"market": market, "since": DATE_FILTER})

        pin_rows = q("""
            SELECT match_id::text, selection, handicap_line::text,
                   (minutes_to_kickoff / 30) * 30 as bucket,
                   AVG(odds) as odds
            FROM odds_snapshots
            WHERE bookmaker = 'Pinnacle'
              AND market = %(market)s
              AND is_live = FALSE
              AND minutes_to_kickoff BETWEEN 0 AND 700
              AND odds > 1.01
              AND timestamp >= %(since)s
            GROUP BY match_id, selection, handicap_line,
                     (minutes_to_kickoff / 30) * 30
        """, {"market": market, "since": DATE_FILTER})

        if not cb_rows or not pin_rows:
            print(f"  {market}: no data\n")
            continue

        # Build pinnacle lookup: (match_id, sel, handicap, 30min-bucket) → odds
        pin_map: dict = {}
        for r in pin_rows:
            bucket = int(r['bucket'])
            key = (r['match_id'], r['selection'], r['handicap_line'], bucket)
            pin_map[key] = float(r['odds'])

        # Accumulate ratios by window
        window_ratios: dict = defaultdict(list)
        for r in cb_rows:
            bucket = int(r['bucket'])
            mins = bucket  # use bucket midpoint as representative minutes
            key = (r['match_id'], r['selection'], r['handicap_line'], bucket)
            if key not in pin_map:
                continue
            ratio = float(r['odds']) / pin_map[key]
            if not (0.7 < ratio < 1.5):
                continue
            for wlabel, w_lo, w_hi in window_defs:
                if w_lo <= mins < w_hi:
                    window_ratios[wlabel].append(ratio)
                    break

        if not window_ratios:
            print(f"  {market}: no paired data\n")
            continue

        print(f"  Market: {market}")
        for wlabel, _, _ in window_defs:
            ratios = window_ratios.get(wlabel, [])
            if not ratios:
                continue
            avg_r = sum(ratios) / len(ratios)
            diff_pct = (avg_r - 1) * 100
            sign = "+" if diff_pct >= 0 else ""
            label = "↑ Coolbet BETTER" if diff_pct > 0.3 else ("↓ Coolbet WORSE" if diff_pct < -0.3 else "≈ equal")
            print(f"    {wlabel:<14}: ratio={avg_r:.4f} ({sign}{diff_pct:.2f}%)  {label}  n={len(ratios)}")
        print()


if __name__ == "__main__":
    print("Coolbet / Pinnacle / Bet365 Odds Analysis")
    print(f"Period: {DATE_FILTER} onwards (Coolbet coverage window)")
    print("Coolbet: ~17 days of data — directional signal, not definitive stats.\n")

    analyze_margins()
    analyze_vs_pinnacle()
    analyze_vs_bet365()
    analyze_stability()
    analyze_lag()
    analyze_best_window()

    print("="*60)
    print("DONE")
    print("="*60)
