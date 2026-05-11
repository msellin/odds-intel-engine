"""
OddsIntel — Odds Timing Analysis

Two questions:

  1. MATCH-RELATIVE: For each game, how many hours before kickoff were the odds
     at their best? e.g. "OU2.5 over peaks 6-8h before KO on average"

  2. ABSOLUTE TIME-OF-DAY: Regardless of kickoff time, is there a window each
     day (e.g. 08:00-10:00 UTC) when odds are systematically higher across all
     games? "Public money flows in from ~10:00 and compresses lines" hypothesis.

Data limitation: the nightly pruner keeps only first/last/closing snapshot per
market per finished match. Run --fix-prune to upgrade to hourly retention so
this analysis accumulates meaningful data over time.

Usage:
    python scripts/odds_timing_analysis.py                  # full report
    python scripts/odds_timing_analysis.py --market 1x2     # 1x2 only
    python scripts/odds_timing_analysis.py --market ou25    # OU2.5 only
    python scripts/odds_timing_analysis.py --days 14        # last 14 days
    python scripts/odds_timing_analysis.py --fix-prune      # show prune upgrade patch
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()
from workers.api_clients.db import execute_query

# ── helpers ───────────────────────────────────────────────────────────────────

W  = "\033[0m"
G  = "\033[32m"
Y  = "\033[33m"
R  = "\033[31m"
B  = "\033[36m"
DIM = "\033[2m"
BOLD = "\033[1m"


def _bar(val: float, max_val: float, width: int = 20) -> str:
    filled = int(round(val / max_val * width)) if max_val > 0 else 0
    return "█" * filled + "░" * (width - filled)


def _pct_color(val: float) -> str:
    if val >= 0.02: return G
    if val >= 0: return Y
    return R


def _h(label: str) -> None:
    print(f"\n{BOLD}{B}{'─'*65}{W}")
    print(f"{BOLD}{B}  {label}{W}")
    print(f"{BOLD}{B}{'─'*65}{W}")


# ── market mapping: simulated_bets → odds_snapshots ──────────────────────────

# simulated_bets.market / selection  →  odds_snapshots.market / selection
# pipeline stores display names ("1X2"/"Home") in simulated_bets

def _to_snapshot_market(market: str, selection: str):
    """Map simulated_bets market+selection to odds_snapshots market+selection."""
    m = (market or "").strip()
    s = (selection or "").strip().lower()
    if m == "1X2":
        return "1x2", s                          # s = "home"/"draw"/"away"
    if m == "O/U":
        # "Over 2.5" → over_under_25, "over"
        import re
        match = re.match(r"(over|under)\s+(\d+\.?\d*)", s)
        if match:
            direction = match.group(1)
            line = match.group(2).replace(".", "")
            return f"over_under_{line}", direction
    if m in ("btts", "BTTS"):
        return "btts", s
    if m == "double_chance":
        return "double_chance", s.replace(" ", "")   # "1x", "x2", "12"
    if m == "asian_handicap":
        return None, None   # skip — complex line matching
    if m == "draw_no_bet":
        return None, None   # not stored in odds_snapshots
    return None, None


MARKET_FILTER_MAP = {
    "1x2":  lambda m, s: m == "1X2",
    "ou25": lambda m, s: m == "O/U" and "2.5" in s,
    "ou15": lambda m, s: m == "O/U" and "1.5" in s,
    "btts": lambda m, s: m in ("btts", "BTTS"),
    "dc":   lambda m, s: m == "double_chance",
    "all":  lambda m, s: True,
}

# ── Part 1: CLV direction by placement window ─────────────────────────────────

def analysis_placement_window(days: int, market_filter: str) -> None:
    """
    For settled prematch bets: how does CLV correlate with how many hours
    before kickoff we placed the bet?

    Uses simulated_bets.odds_at_pick + closing_odds (no snapshot needed).
    CLV = (closing_odds / odds_at_pick - 1) — positive = bet better than close.
    """
    _h("Part 1 — CLV by placement window (hours before kickoff)")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = execute_query("""
        SELECT
            sb.market,
            sb.selection,
            sb.odds_at_pick,
            sb.closing_odds,
            sb.clv,
            sb.pick_time,
            sb.edge_percent,
            m.date AS kickoff
        FROM simulated_bets sb
        JOIN matches m ON m.id = sb.match_id
        WHERE sb.result != 'pending'
          AND sb.xg_source IS NULL
          AND sb.closing_odds IS NOT NULL
          AND sb.odds_at_pick IS NOT NULL
          AND sb.pick_time >= %s
        ORDER BY sb.pick_time
    """, [cutoff])

    if not rows:
        print("  No settled bets with closing odds found.")
        print(f"  (filter: last {days} days, prematch only)")
        return

    mf = MARKET_FILTER_MAP.get(market_filter, MARKET_FILTER_MAP["all"])
    rows = [r for r in rows if mf(r["market"], r["selection"])]

    if not rows:
        print(f"  No rows after market filter '{market_filter}'.")
        return

    # Bucket by hours-before-kickoff
    BUCKETS = [
        ("0–2h",  0,  2),
        ("2–4h",  2,  4),
        ("4–6h",  4,  6),
        ("6–9h",  6,  9),
        ("9–12h", 9, 12),
        ("12h+", 12, 999),
    ]

    bucket_data: dict[str, list] = {b[0]: [] for b in BUCKETS}

    for r in rows:
        try:
            ko = r["kickoff"]
            if hasattr(ko, "replace"):
                ko = ko.replace(tzinfo=timezone.utc) if ko.tzinfo is None else ko
            pt = r["pick_time"]
            if isinstance(pt, str):
                pt = datetime.fromisoformat(pt.replace("Z", "+00:00"))
            if hasattr(ko, "isoformat"):
                pass
            else:
                ko = datetime.fromisoformat(str(ko))
            if ko.tzinfo is None:
                ko = ko.replace(tzinfo=timezone.utc)
            if pt.tzinfo is None:
                pt = pt.replace(tzinfo=timezone.utc)
            hours_before = (ko - pt).total_seconds() / 3600
        except Exception:
            continue

        o_pick = float(r["odds_at_pick"])
        o_close = float(r["closing_odds"])
        clv = (o_pick / o_close - 1) if o_close > 0 else 0.0  # positive = beat close

        for label, lo, hi in BUCKETS:
            if lo <= hours_before < hi:
                bucket_data[label].append({
                    "hours": hours_before,
                    "clv": clv,
                    "odds_at_pick": o_pick,
                    "closing_odds": o_close,
                    "edge": float(r["edge_percent"] or 0),
                })
                break

    print(f"  {len(rows)} settled bets · market={market_filter} · last {days} days\n")
    print(f"  {'Window':<10}  {'N':>5}  {'Beat close':>10}  {'Avg CLV':>9}  {'Avg odds':>9}  {'Avg edge':>9}")
    print(f"  {'─'*10}  {'─'*5}  {'─'*10}  {'─'*9}  {'─'*9}  {'─'*9}")

    all_clvs = []
    for label, lo, hi in BUCKETS:
        items = bucket_data[label]
        if not items:
            print(f"  {label:<10}  {'0':>5}  {'—':>10}  {'—':>9}  {'—':>9}  {'—':>9}")
            continue
        clvs = [i["clv"] for i in items]
        beat_close_pct = sum(1 for c in clvs if c > 0) / len(clvs)
        avg_clv = sum(clvs) / len(clvs)
        avg_odds = sum(i["odds_at_pick"] for i in items) / len(items)
        avg_edge = sum(i["edge"] for i in items) / len(items)
        all_clvs.extend(clvs)

        clv_col = f"{_pct_color(avg_clv)}{avg_clv*100:+.1f}%{W}"
        beat_col = f"{beat_close_pct*100:.0f}%"
        print(f"  {label:<10}  {len(items):>5}  {beat_col:>10}  {clv_col:>20}  {avg_odds:>9.3f}  {avg_edge*100:>8.1f}%")

    if all_clvs:
        overall = sum(all_clvs) / len(all_clvs)
        print(f"\n  Overall avg CLV vs close: {_pct_color(overall)}{overall*100:+.2f}%{W}  ({len(all_clvs)} bets)")
        beat = sum(1 for c in all_clvs if c > 0) / len(all_clvs)
        print(f"  Beat closing line: {beat*100:.0f}% of bets")

    print(f"\n  {DIM}Interpretation: positive CLV = we placed before odds compressed (good).{W}")
    print(f"  {DIM}Negative CLV = odds improved after we placed; we'd have been better later.{W}")


# ── Part 2: absolute time-of-day odds level ────────────────────────────────────

def analysis_time_of_day(days: int, market_filter: str) -> None:
    """
    Using all available intraday odds_snapshots (non-pruned), what hour of day
    tends to have the highest odds across all games?

    This is the 'public money flow' question — does the market open soft and
    compress through the day, or does it open tight and loosen later?
    """
    _h("Part 2 — Absolute time-of-day: when are odds highest?")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Map market filter to odds_snapshots.market values
    SNAP_MARKET_FILTER = {
        "1x2":  ("1x2", ["home", "away"]),
        "ou25": ("over_under_25", ["over", "under"]),
        "ou15": ("over_under_15", ["over", "under"]),
        "btts": ("btts", ["yes"]),
        "dc":   ("double_chance", ["1x", "x2", "12"]),
        "all":  (None, None),
    }
    snap_market, snap_sels = SNAP_MARKET_FILTER.get(market_filter, (None, None))

    where_clauses = [
        "os.timestamp >= %s",
        "os.odds > 1.0",
        "os.odds < 20.0",  # filter outliers
        "m.status = 'finished'",  # only finished matches have full-day data
    ]
    params: list = [cutoff]

    if snap_market:
        where_clauses.append("os.market = %s")
        params.append(snap_market)
    else:
        # For 'all', focus on main markets to avoid OU noise
        where_clauses.append("os.market IN ('1x2', 'over_under_25', 'over_under_15', 'btts')")

    if snap_sels:
        where_clauses.append("os.selection = ANY(%s)")
        params.append(snap_sels)

    where_str = " AND ".join(where_clauses)

    rows = execute_query(f"""
        SELECT
            EXTRACT(HOUR FROM os.timestamp)::int AS hour_utc,
            os.market,
            os.selection,
            AVG(os.odds)::float AS avg_odds,
            COUNT(*)::int AS n_snapshots,
            COUNT(DISTINCT os.match_id)::int AS n_matches
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        WHERE {where_str}
        GROUP BY EXTRACT(HOUR FROM os.timestamp)::int, os.market, os.selection
        ORDER BY os.market, os.selection, hour_utc
    """, params)

    if not rows:
        print("  No intraday snapshot data found for finished matches.")
        print("  This is expected if the pruner has run — it keeps only first/last/closing.")
        print("  → Run --fix-prune to upgrade to hourly retention going forward.")
        return

    # Group by market+selection and normalise odds to day-relative index
    from collections import defaultdict
    market_hours: dict[str, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        key = f"{r['market']} {r['selection']}"
        market_hours[key][r["hour_utc"]].append({
            "avg_odds": r["avg_odds"],
            "n": r["n_snapshots"],
            "n_matches": r["n_matches"],
        })

    total_snapshots = sum(r["n_snapshots"] for r in rows)
    total_matches = len(set(r.get("match_id") for r in rows if "match_id" in r))
    print(f"  {total_snapshots:,} snapshots · market={market_filter} · last {days} days\n")

    for mkt_sel, hours_data in sorted(market_hours.items()):
        # Compute per-hour avg across all data
        hour_avgs: dict[int, float] = {}
        for h in range(7, 23):
            items = hours_data.get(h, [])
            if items:
                hour_avgs[h] = sum(i["avg_odds"] for i in items) / len(items)

        if not hour_avgs:
            continue

        baseline = sum(hour_avgs.values()) / len(hour_avgs)
        peak_hour = max(hour_avgs, key=lambda h: hour_avgs[h])
        trough_hour = min(hour_avgs, key=lambda h: hour_avgs[h])
        spread = hour_avgs[peak_hour] - hour_avgs[trough_hour]

        print(f"  {BOLD}{mkt_sel}{W}  (peak={peak_hour:02d}:00, trough={trough_hour:02d}:00, spread={spread:.4f})")
        print(f"  {'Hour':>5}  {'Avg odds':>9}  {'vs baseline':>12}  Chart")
        print(f"  {'─'*5}  {'─'*9}  {'─'*12}  {'─'*24}")

        max_diff = max(abs(v - baseline) for v in hour_avgs.values()) or 0.001
        for h in range(7, 23):
            if h not in hour_avgs:
                continue
            avg = hour_avgs[h]
            diff = avg - baseline
            diff_color = G if diff > 0.002 else (R if diff < -0.002 else W)
            bar_val = max(0.0, avg - baseline + max_diff)
            bar = _bar(bar_val, max_diff * 2, 20)
            peak_marker = " ← peak" if h == peak_hour else ""
            print(f"  {h:02d}:00  {avg:>9.4f}  {diff_color}{diff:>+11.4f}{W}  {bar}{peak_marker}")
        print()


# ── Part 3: intraday trajectory for individual recent matches ─────────────────

def analysis_recent_intraday(days: int, market_filter: str, n_matches: int = 10) -> None:
    """
    For the N most recent matches that still have intraday snapshot data
    (not yet pruned), show how odds moved through the day.

    Will show 0 if all recent matches have been pruned already.
    """
    _h("Part 3 — Intraday trajectory (recent non-pruned matches)")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    snap_market_map = {
        "1x2":  ("1x2", "home"),
        "ou25": ("over_under_25", "over"),
        "ou15": ("over_under_15", "over"),
        "btts": ("btts", "yes"),
        "all":  ("1x2", "home"),
    }
    snap_m, snap_s = snap_market_map.get(market_filter, ("1x2", "home"))

    # Find matches with 5+ distinct timestamps (indicating non-pruned intraday data)
    matches_with_data = execute_query("""
        SELECT
            os.match_id,
            m.date AS kickoff,
            ht.name AS home_team,
            at.name AS away_team,
            COUNT(DISTINCT os.timestamp) AS n_snapshots,
            MIN(os.timestamp) AS first_snap,
            MAX(os.timestamp) AS last_snap
        FROM odds_snapshots os
        JOIN matches m ON m.id = os.match_id
        JOIN teams ht ON ht.id = m.home_team_id
        JOIN teams at ON at.id = m.away_team_id
        WHERE os.market = %s
          AND os.selection = %s
          AND os.timestamp >= %s
          AND os.odds > 1.0
        GROUP BY os.match_id, m.date, ht.name, at.name
        HAVING COUNT(DISTINCT os.timestamp) >= 5
        ORDER BY m.date DESC
        LIMIT %s
    """, [snap_m, snap_s, cutoff, n_matches])

    if not matches_with_data:
        print(f"  No matches with 5+ intraday snapshots found (last {days} days).")
        print(f"  The pruner has likely run on all finished matches.")
        print(f"  → Run --fix-prune to upgrade to hourly retention.")
        return

    print(f"  {len(matches_with_data)} matches with intraday data · {snap_m} {snap_s}\n")

    for match in matches_with_data[:5]:
        # Get per-hour odds for this match (best bookmaker per hour)
        hourly = execute_query("""
            SELECT
                EXTRACT(HOUR FROM timestamp)::int AS hour_utc,
                MAX(odds)::float AS best_odds,
                COUNT(DISTINCT bookmaker)::int AS n_books
            FROM odds_snapshots
            WHERE match_id = %s
              AND market = %s
              AND selection = %s
              AND odds > 1.0
            GROUP BY EXTRACT(HOUR FROM timestamp)::int
            ORDER BY hour_utc
        """, [match["match_id"], snap_m, snap_s])

        if not hourly:
            continue

        ko = match["kickoff"]
        if isinstance(ko, str):
            ko = datetime.fromisoformat(ko.replace("Z", "+00:00"))
        if ko.tzinfo is None:
            ko = ko.replace(tzinfo=timezone.utc)

        peak_row = max(hourly, key=lambda r: r["best_odds"])
        peak_h = peak_row["hour_utc"]
        peak_odds = peak_row["best_odds"]

        hours = [r["best_odds"] for r in hourly]
        first_odds = hours[0]
        last_odds = hours[-1]
        drift = last_odds - first_odds

        print(f"  {BOLD}{match['home_team']} vs {match['away_team']}{W}  KO {ko.strftime('%H:%M UTC')}")
        print(f"  open {first_odds:.3f} → close {last_odds:.3f}  drift {drift:+.3f}  peak {peak_odds:.3f} at {peak_h:02d}:00")
        print(f"  {'Hour':>5}  {'Best odds':>9}  {'Books':>6}  Chart")
        max_odds = max(r["best_odds"] for r in hourly)
        min_odds = min(r["best_odds"] for r in hourly)
        spread = max_odds - min_odds or 0.001
        for r in hourly:
            h = r["hour_utc"]
            odds = r["best_odds"]
            ko_marker = "← KO" if h == ko.hour else ""
            peak_marker = "← peak" if h == peak_h else ""
            bar = _bar(odds - min_odds, spread, 20)
            diff_from_open = odds - first_odds
            col = G if diff_from_open > 0.005 else (R if diff_from_open < -0.005 else W)
            print(f"  {h:02d}:00  {odds:>9.3f}  {r['n_books']:>6}  {bar}  {col}{diff_from_open:+.3f}{W} {ko_marker}{peak_marker}")
        print()


# ── Part 4: fix-prune recommendation ─────────────────────────────────────────

def show_fix_prune_info() -> None:
    _h("Part 4 — Prune upgrade: hourly retention instead of first/last only")

    print("""
  Current prune strategy: keeps only FIRST + LAST + IS_CLOSING snapshot per
  (match, bookmaker, market, selection) for finished matches.

  Problem: this destroys the intraday time series that this analysis needs.

  Recommended change: keep ONE snapshot per HOUR instead.
  Result: max 16 rows per combination (07-22 UTC) instead of 2-3.
  Storage cost: ~8x more than current (but far less than unpruned).

  To implement, change prune_odds_snapshots.py delete_sql to:

    WITH hourly AS (
      SELECT id,
             ROW_NUMBER() OVER (
               PARTITION BY match_id, bookmaker, market, selection,
                            EXTRACT(HOUR FROM timestamp)
               ORDER BY timestamp ASC   -- keep first snapshot each hour
             ) AS rn_per_hour,
             is_closing
      FROM odds_snapshots
      WHERE match_id = ANY(%(batch)s::uuid[])
    )
    DELETE FROM odds_snapshots
    WHERE id IN (
      SELECT id FROM hourly
      WHERE rn_per_hour > 1 AND NOT is_closing
    )

  This preserves the full shape of intraday movement for all markets.
  The timing analysis will have meaningful data after ~2 weeks of accumulation.
""")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Odds timing analysis")
    parser.add_argument("--market", choices=["all", "1x2", "ou25", "ou15", "btts", "dc"],
                        default="all", help="Market to analyse (default: all)")
    parser.add_argument("--days", type=int, default=30,
                        help="Look back N days (default: 30)")
    parser.add_argument("--fix-prune", action="store_true",
                        help="Show prune upgrade recommendation only")
    args = parser.parse_args()

    print(f"\n{BOLD}{'═'*65}{W}")
    print(f"{BOLD}  OddsIntel — Odds Timing Analysis{W}")
    print(f"{BOLD}  market={args.market}  days={args.days}{W}")
    print(f"{BOLD}{'═'*65}{W}")

    if args.fix_prune:
        show_fix_prune_info()
        return

    analysis_placement_window(args.days, args.market)
    analysis_time_of_day(args.days, args.market)
    analysis_recent_intraday(args.days, args.market)
    show_fix_prune_info()


if __name__ == "__main__":
    main()
