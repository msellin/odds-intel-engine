#!/usr/bin/env python3
"""TRIGGER-ENGINE-BACKTEST — would the book-agnostic trigger selection have made money?

Answers the question the owner asked before any real bet: on SETTLED history, if
we had selected Coolbet bets via the trigger window (Stage A/B) — edge evaluated at
COOLBET's own odds — what would the realized ROI have been, and how many bets?

Held-out OOS discipline: calibration is fit on the TRAIN split only and applied to
the untouched TEST split, where the windows are formed, Coolbet's historical odds
are matched, and picks are graded against the final result. Read-only.

    python3 scripts/trigger_engine_backtest.py [--split 0.7] [--folds 3]
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import numpy as np
from sklearn.isotonic import IsotonicRegression

sys.path.insert(0, str(Path(__file__).parent.parent))
from workers.api_clients.db import execute_query  # noqa: E402
from workers.automation.coolbet_placer import _min_edge_for, _min_odds_for  # noqa: E402

OUTLIER_MULT = 1.6


def _load(market: str):
    """Settled matches with a Coolbet price + model prediction for `market`.
    Returns rows: date, raw model prob of the selection, coolbet odds, won(0/1)."""
    if market == "1x2":
        sql = """
        WITH cb AS (
          SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid,o.selection,o.odds::float odds
            FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
           WHERE o.market='1x2' AND o.bookmaker='Coolbet' AND o.timestamp<=m.date
             AND m.status='finished' AND m.result IS NOT NULL
           ORDER BY o.match_id,o.selection,o.timestamp DESC)
        SELECT m.date, cb.selection, cb.odds,
               p.model_probability::float praw,
               (cb.selection = m.result::text)::int won
          FROM cb JOIN matches m ON m.id::text=cb.mid
          -- LATEST model_version only: predictions holds ~16 versions per fixture,
          -- and a plain JOIN counted each match ~1.9x (inflated n + cross-fold match
          -- leakage). Pick one row per (match, selection). [TRIGGER-WIDEN-AUDIT 2026-09-10]
          JOIN LATERAL (SELECT model_probability FROM predictions
                         WHERE match_id=m.id AND market='1x2_'||cb.selection
                         ORDER BY model_version DESC LIMIT 1) p ON true
        """
    else:  # over_under_25
        sql = """
        WITH cb AS (
          SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid,o.selection,o.odds::float odds
            FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
           WHERE o.market='over_under_25' AND o.bookmaker='Coolbet' AND o.timestamp<=m.date
             AND m.status='finished' AND m.score_home IS NOT NULL
           ORDER BY o.match_id,o.selection,o.timestamp DESC)
        SELECT m.date, cb.selection, cb.odds,
               po.model_probability::float praw_over,
               ((m.score_home+m.score_away) > 2.5)::int over_hit
          FROM cb JOIN matches m ON m.id::text=cb.mid
          JOIN LATERAL (SELECT model_probability FROM predictions
                        WHERE match_id=m.id AND market='over25' ORDER BY model_version DESC LIMIT 1) po ON true
        """
    return execute_query(sql)


def _cells(market, recs, split, folds, sel_filter, edge, odds_lo, odds_hi):
    """One sweep cell: (edge floor, odds band [lo,hi], selection) -> (n, roi, robust).
    Calibrator fit on TRAIN (all selections); the cell only narrows what we BET."""
    n = len(recs)
    cut = int(n * split)
    train, test = recs[:cut], recs[cut:]
    iso = IsotonicRegression(out_of_bounds="clip").fit([r[1] for r in train], [r[3] for r in train])
    of = float(_min_odds_for("1x2" if market == "1x2" else "o/u"))
    rets = []
    for d, praw, odds, won, sel in test:
        if sel_filter and sel != sel_filter:
            continue
        if not (odds_lo <= odds <= odds_hi):
            continue
        cal = float(iso.predict([praw])[0])
        if cal <= edge or cal >= 1.0:
            continue
        min_odds = max(1.0 / (cal - edge), of)
        if odds >= min_odds:                       # cleared the edge at this price
            rets.append((odds - 1.0) if won else -1.0)
    if len(rets) < 20:
        return len(rets), None, "underpowered"
    arr = np.array(rets)
    roi = arr.mean() * 100
    fsz = max(1, len(arr) // folds)
    fr = [arr[i:i + fsz].mean() * 100 for i in range(0, len(arr), fsz) if len(arr[i:i + fsz]) >= 15]
    robust = "ROBUST+" if fr and all(x > 0 for x in fr) else ("pos" if roi > 0 else "neg")
    return len(rets), roi, robust


def sweep(market: str, split: float, folds: int, sel_filter: str | None):
    """2D search for a fold-robust profitable cell: edge floor × odds band."""
    rows = _load(market)
    recs = []
    for r in rows:
        if market == "1x2":
            if r["praw"] is None:
                continue
            recs.append((r["date"], float(r["praw"]), float(r["odds"]), int(r["won"]), r["selection"]))
        else:
            if r["praw_over"] is None:
                continue
            praw = float(r["praw_over"]) if r["selection"] == "over" else 1.0 - float(r["praw_over"])
            won = int(r["over_hit"]) if r["selection"] == "over" else 1 - int(r["over_hit"])
            recs.append((r["date"], praw, float(r["odds"]), won, r["selection"]))
    recs.sort(key=lambda x: x[0])
    edges = [0.05, 0.08, 0.10, 0.13, 0.16, 0.20]
    bands = [(2.80, 3.30), (3.30, 4.00), (4.00, 5.50), (5.50, 12.0), (2.80, 12.0)]
    print(f"\nSWEEP {market} [{sel_filter or 'all'}] — cell = ROI%(n){{robust}}, blank if <20 picks")
    print("  band \\ edge   " + "".join(f"{int(e*100):>13d}%" for e in edges))
    for lo, hi in bands:
        cells = []
        for e in edges:
            nn, roi, rob = _cells(market, recs, split, folds, sel_filter, e, lo, hi)
            cells.append("            ." if roi is None else f"{roi:+6.0f}%({nn}){rob[:4]}")
        print(f"  [{lo:>4.1f},{hi:>4.1f}]  " + "".join(f"{c:>14s}" for c in cells))


def _load_sharp():
    """Settled matches with Coolbet 1x2 (per selection) + Pinnacle home/draw/away —
    for the SHARP anchor (edge = de-vigged Pinnacle P_sharp − 1/Coolbet_odds). No model,
    no calibration/train-test needed (P_sharp is not fit to results)."""
    return execute_query("""
      WITH cb AS (
        SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid, o.selection, o.odds::float odds
          FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
         WHERE o.market='1x2' AND o.bookmaker='Coolbet' AND o.timestamp<=m.date
           AND m.status='finished' AND m.result IS NOT NULL
         ORDER BY o.match_id,o.selection,o.timestamp DESC),
      pinraw AS (
        SELECT DISTINCT ON (o.match_id,o.selection) o.match_id::text mid, o.selection, o.odds::float odds
          FROM odds_snapshots o JOIN matches m ON m.id=o.match_id
         WHERE o.market='1x2' AND o.bookmaker='Pinnacle' AND o.timestamp<=m.date AND m.status='finished'
         ORDER BY o.match_id,o.selection,o.timestamp DESC),
      pin AS (
        SELECT mid, max(odds) FILTER (WHERE selection='home') ph,
                    max(odds) FILTER (WHERE selection='draw') pd,
                    max(odds) FILTER (WHERE selection='away') pa
          FROM pinraw GROUP BY mid)
      SELECT m.date, cb.selection, cb.odds, pin.ph, pin.pd, pin.pa, m.result::text AS result
        FROM cb JOIN matches m ON m.id::text=cb.mid JOIN pin ON pin.mid=cb.mid
       WHERE pin.ph IS NOT NULL AND pin.pd IS NOT NULL AND pin.pa IS NOT NULL
    """)


def sweep_sharp(folds: int, sel_filter: str | None):
    """SHARP-anchor sweep: edge = P_sharp(de-vig Pinnacle) − 1/Coolbet_odds, over
    edge floor × odds band × bet-type. This is where §57 says the draw edge lives."""
    from workers.model.devig import devig
    rows = _load_sharp()
    recs = []  # (date, selection, coolbet_odds, won, sharp_edge)
    for r in rows:
        probs = devig([float(r["ph"]), float(r["pd"]), float(r["pa"])])  # [home,draw,away]
        if not probs:
            continue
        idx = {"home": 0, "draw": 1, "away": 2}[r["selection"]]
        p_sharp = probs[idx]
        odds = float(r["odds"])
        recs.append((r["date"], r["selection"], odds, int(r["selection"] == r["result"]),
                     p_sharp - 1.0 / odds))
    recs.sort(key=lambda x: x[0])
    edges = [0.02, 0.03, 0.05, 0.08, 0.12]           # sharp floors are LOW (~3%)
    bands = [(1.01, 3.30), (3.30, 4.00), (4.00, 5.50), (5.50, 12.0), (1.01, 12.0)]
    print(f"\nSHARP-SWEEP 1x2 [{sel_filter or 'all'}] — edge = P_sharp(devig Pinnacle) − 1/coolbet_odds")
    print("  band \\ edge   " + "".join(f"{int(e*100):>13d}%" for e in edges))
    for lo, hi in bands:
        cells = []
        for e in edges:
            rr = [(-1.0 if not w else o - 1.0) for (d, s, o, w, se) in recs
                  if (not sel_filter or s == sel_filter) and lo <= o <= hi and se >= e]
            if len(rr) < 20:
                cells.append("            ."); continue
            arr = np.array(rr); roi = arr.mean() * 100
            fsz = max(1, len(arr) // folds)
            fr = [arr[i:i+fsz].mean()*100 for i in range(0, len(arr), fsz) if len(arr[i:i+fsz]) >= 15]
            rob = "ROBUST+" if fr and all(x > 0 for x in fr) else ("pos" if roi > 0 else "neg")
            cells.append(f"{roi:+6.0f}%({len(arr)}){rob[:4]}")
        print(f"  [{lo:>4.1f},{hi:>4.1f}]  " + "".join(f"{c:>14s}" for c in cells))


def backtest_market(market: str, split: float, folds: int,
                    sel_filter: str | None = None, edge_override: float | None = None,
                    odds_cap: float | None = None):
    rows = _load(market)
    # normalise to (date, praw_of_selection, odds, won, selection)
    recs = []
    for r in rows:
        if market == "1x2":
            if r["praw"] is None:
                continue
            recs.append((r["date"], float(r["praw"]), float(r["odds"]), int(r["won"]), r["selection"]))
        else:
            if r["praw_over"] is None:
                continue
            praw = float(r["praw_over"]) if r["selection"] == "over" else 1.0 - float(r["praw_over"])
            won = int(r["over_hit"]) if r["selection"] == "over" else 1 - int(r["over_hit"])
            recs.append((r["date"], praw, float(r["odds"]), won, r["selection"]))
    recs.sort(key=lambda x: x[0])
    n = len(recs)
    if n < 400:
        print(f"  {market}: only {n} rows — skip"); return
    cut = int(n * split)
    train, test = recs[:cut], recs[cut:]
    # calibrate on TRAIN (raw prob of the SELECTION → did it happen) — on ALL selections,
    # so the calibrator is unchanged; the selection filter only narrows what we BET.
    iso = IsotonicRegression(out_of_bounds="clip").fit([r[1] for r in train], [r[3] for r in train])
    ef = edge_override if edge_override is not None else float(_min_edge_for("1x2" if market == "1x2" else "o/u"))
    of = float(_min_odds_for("1x2" if market == "1x2" else "o/u"))

    picks = []  # (date, odds, ret)
    for d, praw, odds, won, sel in test:
        if sel_filter and sel != sel_filter:
            continue
        cal = float(iso.predict([praw])[0])
        if cal <= ef or cal >= 1.0:
            continue
        min_odds = max(1.0 / (cal - ef), of)
        max_odds = min_odds * OUTLIER_MULT
        if odds_cap is not None:
            max_odds = min(max_odds, odds_cap)
        if min_odds <= odds <= max_odds:
            ret = (odds - 1.0) if won else -1.0
            picks.append((d, odds, ret))

    if not picks:
        print(f"  {market}: 0 picks fired on TEST"); return
    rets = np.array([p[2] for p in picks])
    roi = rets.mean() * 100
    wr = (rets > 0).mean() * 100
    avg_odds = np.mean([p[1] for p in picks])
    # fold-robustness (chronological)
    fr = []
    fsz = max(1, len(picks) // folds)
    for i in range(0, len(picks), fsz):
        f = rets[i:i + fsz]
        if len(f) >= 15:
            fr.append(f.mean() * 100)
    robust = "ROBUST" if fr and all(x > 0 for x in fr) else "not-robust"
    label = f"{market}[{sel_filter or 'all'}]" + (f"≤{odds_cap}" if odds_cap else "")
    print(f"  {label:22s} edge≥{ef:.0%}/odds≥{of:.1f}: "
          f"n={len(picks):5d}  ROI {roi:+.1f}%  win {wr:.0f}%  avg_odds {avg_odds:.2f}  "
          f"folds={[f'{x:+.0f}' for x in fr]} {robust}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", type=float, default=0.7)
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--sharp", action="store_true",
                    help="run the SHARP-anchor sweep (edge = P_sharp[devig Pinnacle] - 1/coolbet_odds) instead of the model sweep")
    a = ap.parse_args()
    if a.sharp:
        print("SHARP-ANCHOR SWEEP — edge vs Shin-de-vigged Pinnacle at Coolbet's own odds, held-out OOS")
        print("WARNING: longshot bands (odds>=4.0, esp >=5.5) show absurd ROI = VARIANCE, not edge.")
        print("Trust only moderate-odds ([1.0,3.3]) fold-ROBUST cells with adequate n.\n")
        for sel in (None, "home", "away", "draw"):
            sweep_sharp(a.folds, sel)
        return 0
    print("TRIGGER-ENGINE-BACKTEST — Coolbet-native selection at Coolbet's own odds, held-out OOS")
    print("(calibration fit on TRAIN only; windows + grading on untouched TEST)\n")
    print("BOOK-AGNOSTIC verdict — does the FAVLONG restriction rescue the wide trigger selection?")
    backtest_market("1x2", a.split, a.folds)                                  # wide, pooled 13% (the -21% baseline)
    backtest_market("1x2", a.split, a.folds, sel_filter="home")               # home only, 13%
    backtest_market("1x2", a.split, a.folds, sel_filter="home", edge_override=0.10)  # home-underdogs @10% (FAVLONG)
    backtest_market("1x2", a.split, a.folds, sel_filter="home", edge_override=0.10, odds_cap=3.8)  # moderate home-dogs (mirror universe)
    backtest_market("1x2", a.split, a.folds, sel_filter="away")               # away only (should be weak)
    print()
    backtest_market("over_under_25", a.split, a.folds)
    # THE ACTUAL SEARCH: sweep edge × odds band × selection for a fold-robust profitable cell.
    for sel in (None, "home", "away", "draw"):
        sweep("1x2", a.split, a.folds, sel)
    sweep("over_under_25", a.split, a.folds, None)
    print("\nNote: cell = OOS ROI%(n){robust}. ROBUST+ = positive in every chronological fold."
          "\nLooking for ANY fold-robust positive cell with adequate n — that would be a viable trigger setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
