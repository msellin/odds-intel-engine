"""
EDGE-FLOOR-BACKTEST — sweep per-market edge floors at scale, walk-forward.

Answers "what edge floor maximises ROI, robustly, for market X" on the full
history, at two price bases and across time folds, so a single favourable window
can't drive the answer — the exact trap that briefly pushed the 1x2 floor to 15%
before a larger backtest reverted it (BOT-CONFIG-GOLDEN-MIDDLE, 2026-09-08).

TWO PRICE BASES
  executable : simulated_bets — the bots' ACTUAL picks at the price we bet
               (odds_at_pick_live, falling back to odds_at_pick). Smaller, honest,
               and market-agnostic (uses stored edge_percent + result).
  idealized  : fixture-level — for every settled fixture with a Pinnacle anchor,
               edge = best_accessible_price × devig(Pinnacle) − 1 per selection.
               Tens of thousands of bets, BUT best-of-many-books selection bias
               inflates the absolute ROI (the monotonicity is real, the magnitude
               optimistic). Supported for 1x2 and goals O/U.

WALK-FORWARD
  The time range is split into N equal folds; a floor is "robust" only if it is
  positive in EVERY fold, not merely pooled.

STALE-BEST-ODDS GUARD (gotcha 44)
  Best price is latest-per-book THEN max across books — never max(odds) over all
  time, which is a high-water mark that fabricates edge.

Usage:
  python3 scripts/edge_floor_backtest.py [--market 1x2] [--basis both|executable|idealized]
                                         [--folds 3] [--stake 10]
"""
from __future__ import annotations

import argparse
import os
import sys
import statistics as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import timezone

from workers.api_clients.db import execute_query

FLOORS = [0.00, 0.03, 0.05, 0.08, 0.10, 0.12, 0.13, 0.15, 0.18, 0.20, 0.30]
_ACCESSIBLE = ("Coolbet", "Betano", "Unibet", "10Bet", "Betfair", "1xBet",
               "Marathonbet", "Bet365", "William Hill")


def _folds(items, key, n):
    """Split items into n equal-size time folds by `key` (a datetime)."""
    xs = sorted(items, key=key)
    if not xs:
        return []
    size = max(1, len(xs) // n)
    return [xs[i:i + size] for i in range(0, len(xs), size)][:n]


def _roi(returns):
    return 100 * st.mean(returns) if returns else None


def _sweep(bets, n_folds, stake):
    """bets: list of (edge, ret, when). Print floor -> pooled ROI + per-fold +
    robust flag + profit."""
    folds = _folds(bets, key=lambda b: b[2], n=n_folds)
    print(f"  {'floor':<6}{'bets':>7}{'ROI%':>8}{'profit€':>9}  " +
          "  ".join(f"f{i+1}ROI" for i in range(len(folds))) + "  robust")
    for f in FLOORS:
        kept = [(e, r, w) for (e, r, w) in bets if e >= f]
        if not kept:
            continue
        rets = [r for _, r, _ in kept]
        pooled = _roi(rets)
        profit = sum(rets) * stake
        fold_rois = []
        for fold in folds:
            fr = [r for (e, r, w) in fold if e >= f]
            fold_rois.append(_roi(fr))
        robust = all(fr is not None and fr > 0 for fr in fold_rois) and len(folds) == n_folds
        cells = "  ".join(f"{(fr if fr is not None else 0):>5.1f}" for fr in fold_rois)
        print(f"  ≥{f*100:>2.0f}% {len(kept):>7}{pooled:>8.1f}{profit:>9.0f}  {cells}  "
              f"{'✓' if robust else '·'}")


# ── executable basis: the bots' actual picks ─────────────────────────────────
def backtest_executable(market, n_folds, stake, active_only=False):
    mat = "AND b.maturity_label IN ('calibrated','beta','active')" if active_only else ""
    rows = execute_query(f"""
        SELECT sb.edge_percent::float ep, sb.pick_time,
               (CASE WHEN sb.result='won'
                     THEN (COALESCE(sb.odds_at_pick_live, sb.odds_at_pick) - 1)
                     ELSE -1 END)::float ret
          FROM simulated_bets sb JOIN bots b ON b.id = sb.bot_id
         WHERE lower(sb.market) = %s AND sb.result IN ('won','lost')
           AND sb.edge_percent IS NOT NULL {mat}
    """, (market.lower(),))
    bets = [(r["ep"], r["ret"], r["pick_time"]) for r in rows]
    label = "EXECUTABLE (bots' actual picks" + (", active/calibrated only" if active_only else "") + ")"
    print(f"\n=== {market} — {label} — n={len(bets)} ===")
    if bets:
        _sweep(bets, n_folds, stake)


# ── idealized basis: fixture-level best-accessible vs de-vig Pinnacle ─────────
def _devig(odds_by_sel):
    inv = {s: 1.0 / o for s, o in odds_by_sel.items() if o and o > 1}
    tot = sum(inv.values())
    return {s: v / tot for s, v in inv.items()} if tot else {}


def backtest_idealized(market, n_folds, stake):
    m = market.lower()
    if m == "1x2":
        sels = ("home", "draw", "away")
        sel_filter = "o.selection IN ('home','draw','away')"
    elif m in ("o/u", "over_under") or m.startswith("over_under"):
        sels = ("over", "under")
        sel_filter = "o.selection IN ('over','under')"
        m = "over_under"  # match any over_under_NN below
    else:
        print(f"\n=== {market} — IDEALIZED not supported (only 1x2 / goals O/U) ===")
        return

    market_pred = "o.market='1x2'" if market.lower() == "1x2" else "o.market ~ '^over_under_[0-9]+$'"
    rows = execute_query(f"""
        WITH fin AS (
          SELECT id, date, score_home, score_away,
                 CASE WHEN score_home>score_away THEN 'home'
                      WHEN score_home<score_away THEN 'away' ELSE 'draw' END winner,
                 (score_home+score_away) total
            FROM matches WHERE status='finished'
              AND score_home IS NOT NULL AND score_away IS NOT NULL),
        lat AS (
          SELECT DISTINCT ON (o.match_id,o.market,o.selection,o.bookmaker)
                 o.match_id, o.market, o.selection, o.bookmaker, o.odds
            FROM odds_snapshots o
           WHERE {market_pred} AND {sel_filter}
             AND o.bookmaker IN ('Pinnacle',{','.join("'%s'" % b for b in _ACCESSIBLE)})
           ORDER BY o.match_id,o.market,o.selection,o.bookmaker,o."timestamp" DESC),
        agg AS (
          SELECT match_id, market, selection,
                 max(odds) FILTER (WHERE bookmaker='Pinnacle') pin,
                 max(odds) FILTER (WHERE bookmaker!='Pinnacle') best
            FROM lat GROUP BY 1,2,3)
        SELECT a.match_id::text mid, a.market, a.selection, a.pin::float pin, a.best::float best,
               f.date, f.winner, f.total
          FROM agg a JOIN fin f ON f.id=a.match_id
         WHERE a.pin IS NOT NULL AND a.best IS NOT NULL AND a.pin>1 AND a.best>1
    """)
    # group per (match, market) — a match may carry several O/U lines
    from collections import defaultdict
    grp = defaultdict(dict)
    meta = {}
    for r in rows:
        grp[(r["mid"], r["market"])][r["selection"]] = {"pin": r["pin"], "best": r["best"]}
        meta[(r["mid"], r["market"])] = r
    bets = []
    for key, od in grp.items():
        if not all(s in od for s in sels):
            continue
        truep = _devig({s: od[s]["pin"] for s in sels})
        r = meta[key]
        when = r["date"];  when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
        for s in sels:
            edge = od[s]["best"] * truep.get(s, 0) - 1
            if market.lower() == "1x2":
                won = (r["winner"] == s)
            else:  # goals O/U
                line = int(r["market"].split("_")[-1]) / 10.0
                won = (r["total"] > line) if s == "over" else (r["total"] < line)
            ret = (od[s]["best"] - 1) if won else -1
            bets.append((edge, ret, when))
    print(f"\n=== {market} — IDEALIZED (fixture-level, best-accessible vs de-vig Pinnacle) — n={len(bets)} ===")
    print("  (NB best-of-books selection bias inflates absolute ROI; the monotonicity is the signal)")
    if bets:
        _sweep(bets, n_folds, stake)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="1x2")
    ap.add_argument("--basis", choices=("both", "executable", "idealized"), default="both")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--stake", type=float, default=10.0)
    a = ap.parse_args()
    print(f"EDGE-FLOOR-BACKTEST — market={a.market} folds={a.folds} stake=€{a.stake:.0f}")
    print("A floor is 'robust' (✓) only if positive in EVERY time fold.")
    if a.basis in ("both", "executable"):
        backtest_executable(a.market, a.folds, a.stake, active_only=False)
        backtest_executable(a.market, a.folds, a.stake, active_only=True)
    if a.basis in ("both", "idealized"):
        backtest_idealized(a.market, a.folds, a.stake)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
