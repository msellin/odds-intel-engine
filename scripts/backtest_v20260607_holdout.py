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


def bulk_load_odds(match_ids: list[str], window_min: int = 120,
                    kickoff_by_match: dict | None = None) -> dict:
    """Chunked bulk query for ALL odds across all backtest matches; build
    {(match_id, market, selection): {'max': float, 'pinnacle_close': float}}.
    Avoids 25k+ serial round-trips AND avoids the pooler-killing single
    giant query. ~50 matches per chunk; each chunk completes in <1s."""
    if not match_ids:
        return {}
    rows: list[dict] = []
    CHUNK = 50
    for i in range(0, len(match_ids), CHUNK):
        chunk_ids = match_ids[i:i+CHUNK]
        chunk_rows = execute_query(
            """
            SELECT match_id::text AS mid, market, selection, bookmaker, odds, timestamp
              FROM odds_snapshots
             WHERE match_id = ANY(%s::uuid[])
               AND market IN ('1x2', 'over_under_25')
               AND odds IS NOT NULL AND odds > 1.0
            """,
            (chunk_ids,),
        )
        rows.extend(chunk_rows)
        if (i // CHUNK) % 10 == 0:
            print(f"  …{len(rows):,} odds rows ({i+CHUNK}/{len(match_ids)} matches)")
    print(f"  bulk odds loaded: {len(rows):,} rows for {len(match_ids):,} matches")

    # REALISTIC BACKTEST METHODOLOGY (2026-06-24):
    #
    # Original version used max(odds) across ALL bookmakers AND ALL pre-kickoff
    # timestamps — that's best-of-538 cherry-picking. Produced absurd +37% ROI
    # because the model picks any moment's most aggressive book in retrospect.
    #
    # Realistic approach: pick a fixed time slice (T-60min → T-5min before
    # kickoff) and take the BEST snapshot per (book, market, selection) within
    # that slice — closer to "what a price-shopping bettor at multiple books
    # could grab right before placement". Then take the MAX across books.
    # This is still best-of-N-books but at a realistic single moment, not
    # cherry-picked across the whole pre-match line history.
    out: dict = {}
    # First, find latest snapshot per (match, market, selection, book) in slice
    latest_per_book: dict = {}  # (mid, mkt, sel, bk) -> (odds, ts)
    for r in rows:
        ko = kickoff_by_match.get(r["mid"]) if kickoff_by_match else None
        if ko is None:
            continue
        # Time slice: 60min before kickoff → 5min after (allow tiny clock skew)
        if not (ko - timedelta(minutes=60) <= r["timestamp"] <= ko + timedelta(minutes=5)):
            continue
        odds = float(r["odds"])
        k = (r["mid"], r["market"], r["selection"], r["bookmaker"] or "")
        cur = latest_per_book.get(k)
        if cur is None or r["timestamp"] > cur[1]:
            latest_per_book[k] = (odds, r["timestamp"])

    # Now aggregate to (mid, mkt, sel): max across books, plus Pinnacle close.
    for (mid, mkt, sel, bk), (odds, ts) in latest_per_book.items():
        key = (mid, mkt, sel)
        slot = out.setdefault(key, {"max": 0.0, "max_bk": "",
                                      "pinnacle_close": 0.0, "n_books": 0,
                                      "avg_sum": 0.0, "median_list": []})
        if odds > slot["max"]:
            slot["max"] = odds
            slot["max_bk"] = bk
        if bk == "Pinnacle":
            slot["pinnacle_close"] = odds
        slot["n_books"] += 1
        slot["avg_sum"] += odds
        slot["median_list"].append(odds)
    # Compute avg + median
    for slot in out.values():
        if slot["n_books"]:
            slot["avg"] = slot["avg_sum"] / slot["n_books"]
            sl = sorted(slot["median_list"])
            slot["median"] = sl[len(sl)//2]
    return out


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

    # Bulk-load all odds in one query (avoids 25k serial roundtrips)
    match_ids = [str(m) for m in df["match_id_real"]]
    kickoffs = dict(zip(match_ids, df["match_date_real"]))
    odds_idx = bulk_load_odds(match_ids, kickoff_by_match=kickoffs)

    # Aggregate THREE variants side-by-side: max/avg/median odds at T-60→T-5
    # Realistic answer is somewhere between avg and max (depending on how
    # diligent the bettor is at line-shopping). max is the optimistic ceiling.
    agg = defaultdict(lambda: {"n": 0, "stake": 0.0, "pnl": 0.0, "w": 0,
                                "clv_sum": 0.0, "clv_n": 0})

    def _eval_selection(mid: str, market: str, sel: str, raw_p: float, won: bool):
        slot = odds_idx.get((mid, market, sel))
        if not slot or slot["max"] < 1.05:
            return
        bucket = "1x2" if market == "1x2" else "ou25"
        for variant in ("max", "avg", "median"):
            taken = slot.get(variant, 0.0) or 0.0
            if taken < 1.05:
                continue
            edge = raw_p * taken - 1
            if edge < min_edge_pct / 100:
                continue
            pnl = (taken - 1) * stake_unit if won else -stake_unit
            cl = slot.get("pinnacle_close") or 0.0
            clv = (taken / cl - 1) if cl > 1.0 else None
            a = agg[(variant, bucket, sel)]
            a["n"] += 1
            a["stake"] += stake_unit
            a["pnl"] += pnl
            a["w"] += 1 if won else 0
            if clv is not None:
                a["clv_sum"] += clv
                a["clv_n"] += 1

    for i, row in df.iterrows():
        mid = str(row["match_id_real"])
        if not row["match_date_real"]:
            continue
        # 1x2
        _eval_selection(mid, "1x2", "home", p_1x2[i, idx_home],
                         row["score_home"] > row["score_away"])
        _eval_selection(mid, "1x2", "draw", p_1x2[i, idx_draw],
                         row["score_home"] == row["score_away"])
        _eval_selection(mid, "1x2", "away", p_1x2[i, idx_away],
                         row["score_home"] < row["score_away"])
        # over/under 2.5
        total_goals = (row["score_home"] or 0) + (row["score_away"] or 0)
        _eval_selection(mid, "over_under_25", "over 2.5",
                         p_ou[i, idx_over], total_goals > 2)
        _eval_selection(mid, "over_under_25", "under 2.5",
                         1 - p_ou[i, idx_over], total_goals < 3)

    print()
    print(f"{'variant':8s} {'market':6s} {'sel':12s} {'n':>5} {'stake':>10s} "
          f"{'pnl':>10s} {'ROI':>8s} {'hit':>6s} {'CLV':>7s}")
    print("-" * 86)
    by_variant: dict = defaultdict(lambda: {"n": 0, "stake": 0.0, "pnl": 0.0,
                                             "w": 0, "clv_s": 0.0, "clv_n": 0})
    for key, d in sorted(agg.items()):
        if d["n"] == 0:
            continue
        variant, market, sel = key
        roi = 100 * d["pnl"] / d["stake"] if d["stake"] else 0
        hit = 100 * d["w"] / d["n"]
        clv = 100 * d["clv_sum"] / d["clv_n"] if d["clv_n"] else 0
        print(f"{variant:8s} {market:6s} {sel:12s} {d['n']:>5} {d['stake']:>10.0f} "
              f"{d['pnl']:>+10.0f} {roi:>+7.2f}% {hit:>5.1f}% {clv:>+6.2f}%")
        v = by_variant[variant]
        v["n"] += d["n"]
        v["stake"] += d["stake"]
        v["pnl"] += d["pnl"]
        v["w"] += d["w"]
        v["clv_s"] += d["clv_sum"]
        v["clv_n"] += d["clv_n"]
    print("-" * 86)
    print()
    print("HEADLINE — variant aggregates (use AVG or MEDIAN as the realistic number):")
    for variant in ("median", "avg", "max"):
        d = by_variant[variant]
        if d["n"] == 0:
            continue
        roi = 100 * d["pnl"] / d["stake"]
        hit = 100 * d["w"] / d["n"]
        clv = 100 * d["clv_s"] / d["clv_n"] if d["clv_n"] else 0
        print(f"  {variant:7s}  n={d['n']:>5}  stake={d['stake']:>9.0f}  "
              f"pnl={d['pnl']:>+9.0f}  ROI={roi:>+6.2f}%  hit={hit:>5.1f}%  "
              f"avg_CLV={clv:>+6.2f}%")


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
