#!/usr/bin/env python3
"""
Tennis value-betting backtest — Pinnacle sharp-vs-soft strategy.
Uses tennis-data.co.uk data already in data/raw/tennis/ (zero API calls).

Strategy:
  De-vig Pinnacle (PSW/PSL) → fair probability.
  Compare soft book odds (B365, Max market, Avg market) to fair price.
  Bet when edge ≥ threshold. Track ROI using actual match outcome (Winner/Loser columns).

Run: python3 scripts/tennis/backtest_value.py
"""
from __future__ import annotations
import glob
import pandas as pd
import numpy as np
from pathlib import Path

DATA_GLOB   = "data/raw/tennis/tennis_odds_20*.xlsx"
TRAIN_YEARS = range(2010, 2022)   # informational only — no training needed
TEST_YEARS  = range(2022, 2026)   # out-of-sample period

BOOKS = {
    "Bet365": ("B365W", "B365L"),
    "Max":    ("MaxW",  "MaxL"),
    "Avg":    ("AvgW",  "AvgL"),
}

KELLY_FRAC = 0.25
MAX_STAKE  = 5.0


def load_all() -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(DATA_GLOB)):
        try:
            df = pd.read_excel(f)
            df["year"] = int(Path(f).stem.split("_")[-1])
            frames.append(df)
        except Exception as e:
            print(f"  skip {f}: {e}")
    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df


def devig(pw: float, pl: float) -> tuple[float, float]:
    """Return (fair_prob_winner, fair_prob_loser) after de-vig."""
    if pd.isna(pw) or pd.isna(pl) or pw <= 1 or pl <= 1:
        return float("nan"), float("nan")
    iw, il = 1.0 / pw, 1.0 / pl
    total = iw + il
    return iw / total, il / total


def kelly_stake(edge: float, fair_prob: float, odds: float) -> float:
    b = odds - 1.0
    k = (b * fair_prob - (1 - fair_prob)) / b
    k = max(0.0, k)
    return round(min(k * KELLY_FRAC, MAX_STAKE), 4)


def run_backtest(df: pd.DataFrame, book_label: str, col_w: str, col_l: str,
                 thresholds: list[float]) -> pd.DataFrame:
    """
    For every match: check both sides vs Pinnacle fair price.
    Returns one row per threshold with ROI stats.
    """
    valid = df[
        df["PSW"].notna() & df["PSL"].notna() &
        df[col_w].notna() & df[col_l].notna() &
        (df["PSW"] > 1.01) & (df["PSL"] > 1.01) &
        (df[col_w] > 1.01) & (df[col_l] > 1.01) &
        # Cap outliers — exchange odds can hit 50+; skip those rows for Avg/Max
        (df[col_w] < 30) & (df[col_l] < 30)
    ].copy()
    if len(valid) == 0:
        return pd.DataFrame(columns=["book","threshold","n_bets","win_rate","flat_roi","kelly_roi","total_pnl","avg_edge"])

    fair_w, fair_l = zip(*valid.apply(
        lambda r: devig(r["PSW"], r["PSL"]), axis=1))
    valid["fair_w"] = fair_w
    valid["fair_l"] = fair_l

    valid["edge_w"] = valid[col_w] * valid["fair_w"] - 1.0   # bet winner side
    valid["edge_l"] = valid[col_l] * valid["fair_l"] - 1.0   # bet loser side

    # Pinnacle margin per match (sanity check)
    valid["pin_margin"] = 1.0 / valid["PSW"] + 1.0 / valid["PSL"] - 1.0

    rows = []
    for t in thresholds:
        # Bets on winner side (positive edge, correct prediction → WIN)
        w_bets = valid[valid["edge_w"] >= t]
        # Bets on loser side (positive edge, incorrect prediction → LOSS)
        l_bets = valid[valid["edge_l"] >= t]

        w_pnl  = w_bets[col_w] - 1.0
        l_pnl  = pd.Series([-1.0] * len(l_bets), index=l_bets.index)
        all_pnl = pd.concat([w_pnl, l_pnl])

        n      = len(all_pnl)
        roi    = all_pnl.sum() / n if n > 0 else float("nan")
        wr     = len(w_bets) / n if n > 0 else float("nan")

        # Kelly-sized P&L
        k_w = w_bets.apply(lambda r: kelly_stake(r["edge_w"], r["fair_w"], r[col_w]) * (r[col_w] - 1), axis=1)
        k_l = l_bets.apply(lambda r: -kelly_stake(r["edge_l"], r["fair_l"], r[col_l]), axis=1)
        k_pnl = pd.concat([k_w, k_l])
        k_staked = (
            w_bets.apply(lambda r: kelly_stake(r["edge_w"], r["fair_w"], r[col_w]), axis=1).sum() +
            l_bets.apply(lambda r: kelly_stake(r["edge_l"], r["fair_l"], r[col_l]), axis=1).sum()
        )
        k_roi = k_pnl.sum() / k_staked if k_staked > 0 else float("nan")

        rows.append({
            "book": book_label,
            "threshold": t,
            "n_bets": n,
            "win_rate": wr,
            "flat_roi": roi,
            "kelly_roi": k_roi,
            "total_pnl": all_pnl.sum(),
            "avg_edge": valid[["edge_w", "edge_l"]].clip(lower=t).max(axis=1)[
                valid[["edge_w", "edge_l"]].max(axis=1) >= t
            ].mean() if n > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def segment_breakdown(df: pd.DataFrame, col_w: str, col_l: str,
                      threshold: float, label: str) -> None:
    """Show ROI by Series, Surface, and Favourite vs Underdog."""
    valid = df[
        df["PSW"].notna() & df["PSL"].notna() &
        df[col_w].notna() & df[col_l].notna() &
        (df["PSW"] > 1.01) & (df["PSL"] > 1.01) &
        (df[col_w] > 1.01) & (df[col_l] > 1.01) &
        (df[col_w] < 30) & (df[col_l] < 30)
    ].copy()
    if len(valid) == 0:
        return

    fair_w, fair_l = zip(*valid.apply(lambda r: devig(r["PSW"], r["PSL"]), axis=1))
    valid["fair_w"] = fair_w
    valid["fair_l"] = fair_l
    valid["edge_w"] = valid[col_w] * valid["fair_w"] - 1.0
    valid["edge_l"] = valid[col_l] * valid["fair_l"] - 1.0

    for seg_col in ["Series", "Surface"]:
        if seg_col not in valid.columns:
            continue
        print(f"\n  By {seg_col}:")
        print(f"  {'':20s}  {'n':>5}  {'WR':>7}  {'ROI':>8}")
        for seg_val, grp in valid.groupby(seg_col):
            wb = grp[grp["edge_w"] >= threshold]
            lb = grp[grp["edge_l"] >= threshold]
            w_pnl = wb[col_w] - 1.0
            l_pnl = pd.Series([-1.0] * len(lb), index=lb.index)
            all_p = pd.concat([w_pnl, l_pnl])
            n = len(all_p)
            if n < 5:
                continue
            roi = all_p.sum() / n
            wr  = len(wb) / n
            print(f"  {str(seg_val):20s}  {n:>5}  {wr:>7.1%}  {roi:>+8.1%}")

    # Favourite vs Underdog (who has edge — is it bets on fav side or dog side?)
    print(f"\n  Favourite vs Underdog (value side vs Pinnacle):")
    print(f"  {'':22s}  {'n':>5}  {'WR':>7}  {'ROI':>8}")
    for label_, is_fav_side in [("Value on favourite", True), ("Value on underdog", False)]:
        # fav side = when PSW < PSL (winner was favourite) and we find edge on winner
        # dog side = when PSW > PSL (winner was underdog) and we find edge on winner
        # Also: edge on loser side with loser being fav or dog
        if is_fav_side:
            # Bets where we're backing the lower-priced side (favourite)
            wb = valid[(valid["PSW"] <= valid["PSL"]) & (valid["edge_w"] >= threshold)]  # fav won, edge on fav
            lb = valid[(valid["PSW"] > valid["PSL"]) & (valid["edge_l"] >= threshold)]   # fav lost (is loser), edge on loser
        else:
            wb = valid[(valid["PSW"] > valid["PSL"]) & (valid["edge_w"] >= threshold)]   # underdog won
            lb = valid[(valid["PSW"] <= valid["PSL"]) & (valid["edge_l"] >= threshold)]  # underdog lost
        w_pnl = wb[col_w] - 1.0
        l_pnl = pd.Series([-1.0] * len(lb), index=lb.index)
        all_p = pd.concat([w_pnl, l_pnl])
        n = len(all_p)
        if n < 5:
            continue
        roi = all_p.sum() / n
        wr  = len(wb) / n
        print(f"  {label_:22s}  {n:>5}  {wr:>7.1%}  {roi:>+8.1%}")


def main() -> None:
    print("=" * 70)
    print("TENNIS VALUE BETTING BACKTEST — Pinnacle de-vig vs soft books")
    print("Data: tennis-data.co.uk PSW/PSL/B365/Max/Avg (2010–2025)")
    print("Strategy: de-vig Pinnacle → fair prob → bet when book > fair price")
    print("=" * 70)

    print("\nLoading data...")
    df = load_all()
    print(f"  Total rows loaded: {len(df):,}")

    full  = df[df["year"].isin(range(2010, 2026)) & df["PSW"].notna() & df["PSL"].notna()]
    test  = df[df["year"].isin(TEST_YEARS) & df["PSW"].notna() & df["PSL"].notna()]
    print(f"  Full dataset (2010-2025): {len(full):,} matches")
    print(f"  Test set (2022-2025):     {len(test):,} matches")
    print(f"  Avg Pinnacle margin: {(1/full['PSW'] + 1/full['PSL'] - 1).mean():.2%}")

    thresholds = [0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10]

    # ── Per-book results (test set) ────────────────────────────────────
    print(f"\n{'='*70}")
    print("MAIN RESULTS — Test set 2022-2025 (out-of-sample)")
    print(f"{'='*70}")
    print(f"\n  {'Book':8s}  {'Edge':>6}  {'n':>6}  {'WR':>7}  {'FlatROI':>9}  {'KellyROI':>10}")

    all_results = {}
    for book_label, (cw, cl) in BOOKS.items():
        results = run_backtest(test, book_label, cw, cl, thresholds)
        all_results[book_label] = results
        for _, row in results.iterrows():
            sign = "✅" if row["flat_roi"] > 0 else "  "
            print(f"  {row['book']:8s}  ≥{row['threshold']*100:4.0f}%  "
                  f"{row['n_bets']:>6}  {row['win_rate']:>7.1%}  "
                  f"{row['flat_roi']:>+9.1%}  {row['kelly_roi']:>+10.1%}  {sign}")
        print()

    # ── Year-by-year for best book ─────────────────────────────────────
    best_book, best_cw, best_cl = "Max", "MaxW", "MaxL"
    print(f"\n{'='*70}")
    print(f"YEAR-BY-YEAR ROI — {best_book} market at ≥3% edge threshold")
    print(f"{'='*70}")
    print(f"\n  {'Year':>5}  {'n':>5}  {'WR':>7}  {'ROI':>9}  {'P&L':>8}")

    for yr in sorted(df["year"].unique()):
        grp = df[(df["year"] == yr) & df["PSW"].notna() & df["PSL"].notna()]
        res = run_backtest(grp, best_book, best_cw, best_cl, [0.03])
        if res is None or len(res) == 0 or res.iloc[0]["n_bets"] < 5:
            continue
        r = res.iloc[0]
        trend = "✅" if r["flat_roi"] > 0 else "❌" if r["flat_roi"] < -0.05 else "  "
        print(f"  {yr:>5}  {r['n_bets']:>5}  {r['win_rate']:>7.1%}  "
              f"{r['flat_roi']:>+9.1%}  {r['total_pnl']:>+8.1f}u  {trend}")

    # ── Segment breakdown (test set, Bet365 ≥3%) ──────────────────────
    print(f"\n{'='*70}")
    print("SEGMENT BREAKDOWN — Bet365 at ≥3% edge (test set 2022-2025)")
    print(f"{'='*70}")
    segment_breakdown(test, "B365W", "B365L", 0.03, "Bet365")

    # ── Segment breakdown with Max market ─────────────────────────────
    print(f"\n{'='*70}")
    print("SEGMENT BREAKDOWN — Max market at ≥3% edge (test set 2022-2025)")
    print(f"{'='*70}")
    segment_breakdown(test, "MaxW", "MaxL", 0.03, "Max")

    # ── Long-run on full dataset ───────────────────────────────────────
    print(f"\n{'='*70}")
    print("LONG-RUN RESULTS — Full 2010-2025 (16 years, all data)")
    print(f"{'='*70}")
    print(f"\n  {'Book':8s}  {'Edge':>6}  {'n':>6}  {'WR':>7}  {'FlatROI':>9}  {'KellyROI':>10}")
    for book_label, (cw, cl) in BOOKS.items():
        results = run_backtest(full, book_label, cw, cl, [0.03, 0.05, 0.07])
        for _, row in results.iterrows():
            sign = "✅" if row["flat_roi"] > 0 else "  "
            print(f"  {row['book']:8s}  ≥{row['threshold']*100:4.0f}%  "
                  f"{row['n_bets']:>6}  {row['win_rate']:>7.1%}  "
                  f"{row['flat_roi']:>+9.1%}  {row['kelly_roi']:>+10.1%}  {sign}")
        print()

    # ── Verdict ───────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("VERDICT")
    print(f"{'='*70}")
    best_test = all_results["Max"].set_index("threshold")["flat_roi"]
    best_b365 = all_results["Bet365"].set_index("threshold")["flat_roi"]
    if any(best_test > 0):
        best_t = best_test[best_test > 0].idxmax()
        n_bets = all_results["Max"].set_index("threshold").loc[best_t, "n_bets"]
        print(f"  ✅ Max market: positive ROI at ≥{best_t*100:.0f}% edge "
              f"({best_test[best_t]:+.1%}, n={n_bets})")
    if any(best_b365 > 0):
        best_t = best_b365[best_b365 > 0].idxmax()
        n_bets = all_results["Bet365"].set_index("threshold").loc[best_t, "n_bets"]
        print(f"  ✅ Bet365: positive ROI at ≥{best_t*100:.0f}% edge "
              f"({best_b365[best_t]:+.1%}, n={n_bets})")
    if not any(best_test > 0) and not any(best_b365 > 0):
        print("  ❌ No positive ROI found at any tested threshold.")
        print("  → Consider: Avg and Max market are upper bounds; real execution")
        print("    requires finding the right book per match (OddsPapi).")


if __name__ == "__main__":
    main()
