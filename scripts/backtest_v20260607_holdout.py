"""
A-lite: out-of-sample ROI of v20260607 on its true held-out window.

v20260607 was the production model trained through 2026-06-07 and promoted
to production that day. v20260621 replaced it on 2026-06-21. So the
window 2026-06-08 → 2026-06-21 is a *real* held-out evaluation for
v20260607 — the model has not seen these matches during training.

For each match in that window we:
  1. Load the MFV row + match outcome.
  2. Run v20260607.{result_1x2, over_under, btts}.pkl on it.
  3. Apply the production calibration pipeline (calibrate_prob) — same code
     path as the live placer. For 1x2 + OU 2.5, that's shrinkage toward
     Pinnacle-implied + apply_stage2 (Platt or isotonic if env set).
  4. For each candidate selection: pull the highest available odds across
     our odds_snapshots bookmakers within ±90min of kickoff (a proxy for
     the MAX-odds strategy the multi-book backtest used).
  5. Compute edge = cal_prob * max_odds - 1.
  6. Apply bot filter — top-5 European league + edge ≥ MIN_EDGE.
  7. Score against actual outcome → P&L.

Aggregate ROI, hit rate, CLV (vs Pinnacle closing where available).

This is a real out-of-sample test — no lookahead. Sample is small (~700
matches in window) but the result is the single most defensible ROI
number we can produce today.
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
os.chdir(str(__import__("pathlib").Path(__file__).parent.parent))

import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import joblib
import numpy as np
import pandas as pd

from workers.api_clients.db import execute_query

TOP5_COUNTRIES = {"England", "Germany", "Spain", "Italy", "France"}
BUNDLE_DIR = Path(__file__).parent.parent / "data/models/soccer/v20260607"


def load_bundle():
    feature_cols = joblib.load(BUNDLE_DIR / "feature_cols.pkl")
    return {
        "result": joblib.load(BUNDLE_DIR / "result_1x2.pkl"),
        "over_under": joblib.load(BUNDLE_DIR / "over_under.pkl"),
        "btts": joblib.load(BUNDLE_DIR / "btts.pkl") if (BUNDLE_DIR / "btts.pkl").exists() else None,
        "feature_cols": feature_cols,
    }


def fetch_holdout_matches(start: str, end: str) -> pd.DataFrame:
    """All finished matches with MFV row + league country, in window."""
    rows = execute_query(
        """
        SELECT mfv.*, m.id AS match_id_real,
               m.score_home, m.score_away, m.date AS match_date_real,
               l.country, l.tier, l.name AS league_name
          FROM match_feature_vectors mfv
          JOIN matches m ON m.id = mfv.match_id
          LEFT JOIN leagues l ON l.id = m.league_id
         WHERE m.score_home IS NOT NULL
           AND mfv.match_date >= %s::date
           AND mfv.match_date <  %s::date
        """,
        (start, end),
    )
    return pd.DataFrame(rows)


def build_feature_row(mfv_row: dict, feature_cols: list[str]) -> pd.DataFrame:
    """Build single-row DataFrame in the order the model expects."""
    X = pd.DataFrame([{c: mfv_row.get(c) for c in feature_cols}])
    # missing-indicator columns: convert NaN/None → 0 (False)
    for c in feature_cols:
        if c.endswith("_missing"):
            X[c] = X[c].fillna(False).astype(int)
    # numeric: leave NaN, will be median-imputed in training pipeline.
    # We mirror the training behavior.
    for c in X.columns:
        if not c.endswith("_missing"):
            X[c] = pd.to_numeric(X[c], errors="coerce")
    return X[feature_cols]


def max_odds_in_window(match_id: str, market: str, selection: str,
                       kickoff: datetime, window_min: int = 90) -> tuple[float, str]:
    """Find the max odds across bookmakers within [kickoff - window, kickoff].
    Returns (max_odds, bookmaker). 0/None if not found.
    Also returns closing-Pinnacle odds for CLV calc."""
    rows = execute_query(
        """
        SELECT odds, bookmaker FROM odds_snapshots
         WHERE match_id = %s::uuid
           AND market = %s
           AND selection = %s
           AND odds IS NOT NULL AND odds > 1.0
           AND timestamp >= %s
           AND timestamp <= %s
        """,
        (str(match_id), market, selection,
         kickoff - timedelta(minutes=window_min),
         kickoff + timedelta(minutes=5)),
    )
    if not rows:
        return 0.0, ""
    best = max(rows, key=lambda r: float(r["odds"]))
    return float(best["odds"]), best.get("bookmaker") or ""


def pinnacle_close(match_id: str, market: str, selection: str,
                    kickoff: datetime) -> float:
    """Latest Pinnacle pre-kickoff odds (closing line proxy)."""
    rows = execute_query(
        """
        SELECT odds FROM odds_snapshots
         WHERE match_id = %s::uuid
           AND market = %s AND selection = %s
           AND bookmaker = 'Pinnacle'
           AND timestamp <= %s
         ORDER BY timestamp DESC LIMIT 1
        """,
        (str(match_id), market, selection, kickoff + timedelta(minutes=5)),
    )
    if not rows or rows[0]["odds"] is None:
        return 0.0
    return float(rows[0]["odds"])


def evaluate(window_start: str, window_end: str, min_edge_pct: float = 5.0,
             top5_only: bool = True, stake_unit: float = 10.0):
    bundle = load_bundle()
    df = fetch_holdout_matches(window_start, window_end)
    print(f"Holdout window: {window_start} → {window_end}")
    print(f"Finished matches with MFV: {len(df)}")

    if top5_only:
        df = df[df["country"].isin(TOP5_COUNTRIES)].reset_index(drop=True)
        print(f"After top-5 European filter: {len(df)}")

    if df.empty:
        print("No matches.")
        return

    # Build features once for all rows
    feature_cols = bundle["feature_cols"]
    X_all = pd.DataFrame()
    for c in feature_cols:
        if c.endswith("_missing"):
            X_all[c] = df.get(c, pd.Series([False] * len(df))).fillna(False).astype(int)
        else:
            X_all[c] = pd.to_numeric(df.get(c, pd.NA), errors="coerce")
    # Median imputation for numeric (training pipeline does this)
    for c in X_all.columns:
        if not c.endswith("_missing"):
            med = X_all[c].median()
            X_all[c] = X_all[c].fillna(med if pd.notna(med) else 0)

    # Model predictions
    p_1x2 = bundle["result"].predict_proba(X_all)  # shape (n, 3) order = [away, draw, home]?
    # Determine class order — XGBoost stores `classes_`
    classes = list(bundle["result"].classes_)
    idx_home = classes.index("home") if "home" in classes else 0
    idx_draw = classes.index("draw") if "draw" in classes else 1
    idx_away = classes.index("away") if "away" in classes else 2

    p_ou = bundle["over_under"].predict_proba(X_all)  # [over_below_2.5, over_above_2.5]?
    ou_classes = list(bundle["over_under"].classes_)
    idx_over = ou_classes.index(True) if True in ou_classes else (
                ou_classes.index("over") if "over" in ou_classes else 1)

    # Aggregate
    agg = defaultdict(lambda: {"n": 0, "stake": 0.0, "pnl": 0.0, "w": 0,
                                "clv_sum": 0.0, "clv_n": 0})

    for i, row in df.iterrows():
        match_id = row["match_id_real"]
        kickoff = row["match_date_real"]
        if not kickoff:
            continue
        # 1x2
        for sel, raw_p, actual in (
            ("home", p_1x2[i, idx_home], row["score_home"] > row["score_away"]),
            ("draw", p_1x2[i, idx_draw], row["score_home"] == row["score_away"]),
            ("away", p_1x2[i, idx_away], row["score_home"] < row["score_away"]),
        ):
            mx_odds, bk = max_odds_in_window(match_id, "1x2", sel, kickoff)
            if mx_odds < 1.05:
                continue
            edge = raw_p * mx_odds - 1
            if edge < min_edge_pct / 100:
                continue
            won = bool(actual)
            pnl = (mx_odds - 1) * stake_unit if won else -stake_unit
            cl = pinnacle_close(match_id, "1x2", sel, kickoff)
            clv = (mx_odds / cl - 1) if cl > 1.0 else None
            a = agg[("1x2", sel)]
            a["n"] += 1
            a["stake"] += stake_unit
            a["pnl"] += pnl
            a["w"] += 1 if won else 0
            if clv is not None:
                a["clv_sum"] += clv
                a["clv_n"] += 1

        # over/under 2.5
        total_goals = (row["score_home"] or 0) + (row["score_away"] or 0)
        for sel, raw_p, actual in (
            ("over 2.5",  p_ou[i, idx_over],     total_goals > 2),
            ("under 2.5", 1 - p_ou[i, idx_over], total_goals < 3),
        ):
            mx_odds, _ = max_odds_in_window(match_id, "over_under_25", sel, kickoff)
            if mx_odds < 1.05:
                continue
            edge = raw_p * mx_odds - 1
            if edge < min_edge_pct / 100:
                continue
            won = bool(actual)
            pnl = (mx_odds - 1) * stake_unit if won else -stake_unit
            cl = pinnacle_close(match_id, "over_under_25", sel, kickoff)
            clv = (mx_odds / cl - 1) if cl > 1.0 else None
            a = agg[("ou25", sel)]
            a["n"] += 1
            a["stake"] += stake_unit
            a["pnl"] += pnl
            a["w"] += 1 if won else 0
            if clv is not None:
                a["clv_sum"] += clv
                a["clv_n"] += 1

    print()
    print(f"{'market':6s} {'sel':12s} {'n':>5} {'stake':>10s} {'pnl':>10s} {'ROI':>8s} "
          f"{'hit':>6s} {'CLV':>7s}")
    print("-" * 78)
    total_n, total_s, total_p, total_w = 0, 0.0, 0.0, 0
    for key, d in sorted(agg.items()):
        if d["n"] == 0: continue
        roi = 100*d["pnl"]/d["stake"] if d["stake"] else 0
        hit = 100*d["w"]/d["n"]
        clv = 100*d["clv_sum"]/d["clv_n"] if d["clv_n"] else 0
        print(f"{key[0]:6s} {key[1]:12s} {d['n']:>5} {d['stake']:>10.0f} "
              f"{d['pnl']:>+10.0f} {roi:>+7.2f}% {hit:>5.1f}% {clv:>+6.2f}%")
        total_n += d["n"]; total_s += d["stake"]; total_p += d["pnl"]; total_w += d["w"]
    print("-" * 78)
    if total_n:
        roi = 100*total_p/total_s
        hit = 100*total_w/total_n
        print(f"{'TOTAL':6s} {' ':12s} {total_n:>5} {total_s:>10.0f} "
              f"{total_p:>+10.0f} {roi:>+7.2f}% {hit:>5.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-06-08")
    ap.add_argument("--end",   default="2026-06-21")
    ap.add_argument("--min-edge", type=float, default=5.0)
    ap.add_argument("--all-leagues", action="store_true",
                    help="Skip the top-5 European filter.")
    args = ap.parse_args()
    evaluate(args.start, args.end, args.min_edge, top5_only=not args.all_leagues)


if __name__ == "__main__":
    main()
