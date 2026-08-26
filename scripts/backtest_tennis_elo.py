#!/usr/bin/env python3
"""
Tennis Elo model backtest vs Pinnacle closing odds.

Data: tennis-data.co.uk ATP XLS files (data/raw/tennis/tennis_odds_YYYY.xlsx)
      Jeff Sackmann ATP match stats for match stats verification (not needed for elo)

Question: Can a simple surface-weighted Elo model find +EV bets vs Pinnacle?

Training: 2005–2021 (17 seasons) to build ratings
Backtest: 2022–2024 (3 seasons, out-of-sample)
"""

import pandas as pd
import numpy as np
from pathlib import Path

DATA_DIR = Path("data/raw/tennis")
TRAINING_YEARS = range(2005, 2022)
BACKTEST_YEARS = range(2022, 2025)

# K-factors by tournament tier (higher = ratings move faster = faster adaptation)
K_MAP = {
    'grand slam': 40,
    'masters':    32,
    'masters 1000': 32,
    'atp1000':    32,
    'international series gold': 24,
    'atp500':     24,
    'masters cup': 24,
    'atp250':     16,
    'international series': 16,
    'challenger': 10,
}
K_DEFAULT = 16

SURFACE_ELO_WEIGHT = 0.5   # blend: 50% surface-specific + 50% overall
INITIAL_RATING = 1500


def k_factor(series_str: str) -> int:
    s = str(series_str).lower()
    for key, k in K_MAP.items():
        if key in s:
            return k
    return K_DEFAULT


def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def update_elo(wr: float, lr: float, k: int):
    exp_w = elo_expected(wr, lr)
    return wr + k * (1 - exp_w), lr + k * (0 - (1 - exp_w))


def load_odds_files(years):
    dfs = []
    for year in years:
        path = DATA_DIR / f"tennis_odds_{year}.xlsx"
        if not path.exists():
            continue
        df = pd.read_excel(path)
        df['Year'] = year
        dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df.dropna(subset=['Date', 'Winner', 'Loser'])
    # Keep only properly completed matches
    df = df[df['Comment'].str.strip() == 'Completed']
    df = df.sort_values('Date').reset_index(drop=True)
    return df


def build_elo(train_df: pd.DataFrame):
    ratings: dict[str, float] = {}
    surf_ratings: dict[tuple, float] = {}

    for _, row in train_df.iterrows():
        w, l = row['Winner'], row['Loser']
        surface = row.get('Surface', 'Hard')
        k = k_factor(row.get('Series', ''))

        rw = ratings.get(w, INITIAL_RATING)
        rl = ratings.get(l, INITIAL_RATING)
        nw, nl = update_elo(rw, rl, k)
        ratings[w], ratings[l] = nw, nl

        sw = surf_ratings.get((w, surface), INITIAL_RATING)
        sl = surf_ratings.get((l, surface), INITIAL_RATING)
        snw, snl = update_elo(sw, sl, k)
        surf_ratings[(w, surface)] = snw
        surf_ratings[(l, surface)] = snl

    return ratings, surf_ratings


def run_backtest(test_df: pd.DataFrame, ratings: dict, surf_ratings: dict):
    records = []

    for _, row in test_df.iterrows():
        w, l = row['Winner'], row['Loser']
        surface = row.get('Surface', 'Hard')
        series = row.get('Series', '')
        k = k_factor(series)

        psw = row.get('PSW')
        psl = row.get('PSL')
        if pd.isna(psw) or pd.isna(psl) or psw <= 1.0 or psl <= 1.0:
            # Still update ratings even if no odds
            nw, nl = update_elo(ratings.get(w, INITIAL_RATING), ratings.get(l, INITIAL_RATING), k)
            ratings[w], ratings[l] = nw, nl
            snw, snl = update_elo(surf_ratings.get((w, surface), INITIAL_RATING), surf_ratings.get((l, surface), INITIAL_RATING), k)
            surf_ratings[(w, surface)], surf_ratings[(l, surface)] = snw, snl
            continue

        # Pinnacle de-vigged true probabilities
        pin_sum = 1/psw + 1/psl
        pin_true_w = (1/psw) / pin_sum
        pin_true_l = (1/psl) / pin_sum
        pin_margin = pin_sum - 1

        # Blended Elo probability
        ow = ratings.get(w, INITIAL_RATING)
        ol = ratings.get(l, INITIAL_RATING)
        sw = surf_ratings.get((w, surface), INITIAL_RATING)
        sl = surf_ratings.get((l, surface), INITIAL_RATING)
        blended_w = SURFACE_ELO_WEIGHT * sw + (1 - SURFACE_ELO_WEIGHT) * ow
        blended_l = SURFACE_ELO_WEIGHT * sl + (1 - SURFACE_ELO_WEIGHT) * ol
        elo_prob_w = elo_expected(blended_w, blended_l)
        elo_prob_l = 1 - elo_prob_w

        records.append({
            'date': row['Date'],
            'winner': w, 'loser': l,
            'surface': surface,
            'series': series,
            'round': row.get('Round', ''),
            'psw': psw, 'psl': psl,
            'pin_margin': pin_margin,
            'pin_true_w': pin_true_w,
            'pin_true_l': pin_true_l,
            'elo_prob_w': elo_prob_w,
            'elo_prob_l': elo_prob_l,
            'edge_w': elo_prob_w - pin_true_w,
            'edge_l': elo_prob_l - pin_true_l,
        })

        # Update Elo with test results (rolling — model learns as it goes)
        nw, nl = update_elo(ow, ol, k)
        ratings[w], ratings[l] = nw, nl
        snw, snl = update_elo(sw, sl, k)
        surf_ratings[(w, surface)], surf_ratings[(l, surface)] = snw, snl

    return pd.DataFrame(records)


def simulate_flat_stake(df_bets: pd.DataFrame, edge_col: str, odds_col: str, won_col: str, label: str):
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(f"  {'Edge':>6}  {'n':>6}  {'WR':>7}  {'ROI':>8}  {'P&L':>8}  {'AvgOdds':>8}")
    for threshold in [0.00, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
        bets = df_bets[df_bets[edge_col] >= threshold]
        if len(bets) < 10:
            continue
        pnl = bets.apply(lambda r: r[odds_col] - 1 if r[won_col] else -1, axis=1)
        roi = pnl.sum() / len(bets)
        wr  = bets[won_col].mean()
        avg_odds = bets[odds_col].mean()
        print(f"  ≥{threshold*100:4.0f}%  {len(bets):>6}  {wr:>7.1%}  {roi:>+8.1%}  {pnl.sum():>+8.1f}u  {avg_odds:>8.2f}")


def main():
    print("=" * 70)
    print("TENNIS ELO BACKTEST — OddsIntel expansion feasibility")
    print("=" * 70)

    print(f"\nLoading training data ({TRAINING_YEARS.start}–{TRAINING_YEARS.stop-1})...")
    train_df = load_odds_files(TRAINING_YEARS)
    print(f"  {len(train_df):,} completed ATP matches")

    print("Building Elo ratings...")
    ratings, surf_ratings = build_elo(train_df)
    print(f"  {len(ratings):,} unique players rated")

    print(f"\nLoading backtest data ({BACKTEST_YEARS.start}–{BACKTEST_YEARS.stop-1})...")
    test_df = load_odds_files(BACKTEST_YEARS)
    print(f"  {len(test_df):,} completed ATP matches")

    print("Running backtest...")
    results = run_backtest(test_df, ratings, surf_ratings)
    print(f"  {len(results):,} matches with valid Pinnacle odds")

    if results.empty:
        print("No results — check data files.")
        return

    # ── Build a flat bet candidate per side ──
    w_bets = results[['date','surface','series','round','psw','pin_true_w','elo_prob_w','edge_w']].copy()
    w_bets.columns = ['date','surface','series','round','bet_odds','pin_true','elo_prob','edge']
    w_bets['won'] = True

    l_bets = results[['date','surface','series','round','psl','pin_true_l','elo_prob_l','edge_l']].copy()
    l_bets.columns = ['date','surface','series','round','bet_odds','pin_true','elo_prob','edge']
    l_bets['won'] = False

    all_bets = pd.concat([w_bets, l_bets], ignore_index=True).sort_values('date')

    # ── MAIN RESULTS ──
    print("\n" + "=" * 70)
    print("MAIN RESULTS — bet any side where Elo edge > threshold vs Pinnacle")
    print("=" * 70)
    simulate_flat_stake(all_bets, 'edge', 'bet_odds', 'won', "ALL matches, both sides")

    # ── SURFACE BREAKDOWN ──
    print("\n\n--- SURFACE BREAKDOWN (edge ≥ 5%) ---")
    subset_5 = all_bets[all_bets['edge'] >= 0.05]
    for surf in ['Hard', 'Clay', 'Grass']:
        sub = subset_5[subset_5['surface'] == surf]
        if len(sub) < 5:
            continue
        pnl = sub.apply(lambda r: r['bet_odds'] - 1 if r['won'] else -1, axis=1)
        roi = pnl.sum() / len(sub)
        wr  = sub['won'].mean()
        print(f"  {surf:8s}  n={len(sub):4d}  WR={wr:.1%}  ROI={roi:+.1%}  P&L={pnl.sum():+.1f}u")

    # ── TOURNAMENT TIER BREAKDOWN ──
    print("\n--- TOURNAMENT TIER BREAKDOWN (edge ≥ 5%) ---")
    tier_keywords = [('Grand Slam','Grand Slam'), ('Masters','Masters'), ('ATP500','ATP500'), ('ATP250','ATP250')]
    for label, kw in tier_keywords:
        sub = subset_5[subset_5['series'].str.contains(kw, case=False, na=False)]
        if len(sub) < 5:
            continue
        pnl = sub.apply(lambda r: r['bet_odds'] - 1 if r['won'] else -1, axis=1)
        roi = pnl.sum() / len(sub)
        wr  = sub['won'].mean()
        print(f"  {label:20s}  n={len(sub):4d}  WR={wr:.1%}  ROI={roi:+.1%}  P&L={pnl.sum():+.1f}u")

    # ── UNDERDOG FOCUS ──
    print("\n--- BETTING ONLY UNDERDOGS (odds > 2.00, edge ≥ 5%) ---")
    dogs = all_bets[(all_bets['edge'] >= 0.05) & (~all_bets['won']) & (all_bets['bet_odds'] > 2.0)]
    if len(dogs) >= 10:
        pnl = dogs.apply(lambda r: r['bet_odds'] - 1 if r['won'] else -1, axis=1)
        roi = pnl.sum() / len(dogs)
        wr  = dogs['won'].mean()
        print(f"  n={len(dogs)}  WR={wr:.1%}  ROI={roi:+.1%}  P&L={pnl.sum():+.1f}u")

    # ── ACCURACY CHECK ──
    print("\n--- ELO MODEL ACCURACY ---")
    elo_correct = (results['elo_prob_w'] > 0.5).mean()
    pin_correct = (results['pin_true_w'] > 0.5).mean()
    print(f"  Elo picks favourite correctly:        {elo_correct:.1%}")
    print(f"  Pinnacle prices favourite correctly:  {pin_correct:.1%}")

    disagree = results[(results['elo_prob_w'] > 0.5) != (results['pin_true_w'] > 0.5)]
    if len(disagree) > 0:
        elo_wins_disagree = (disagree['elo_prob_w'] > 0.5).mean()
        print(f"  Elo disagrees with Pinnacle on:       {len(disagree)} matches")
        print(f"  Of disagreements, Elo is correct:     {elo_wins_disagree:.1%}")

    # ── PINNACLE MARGIN ──
    print(f"\n  Avg Pinnacle margin:  {results['pin_margin'].mean():.2%}")
    print(f"  Median Pinnacle odds: W={results['psw'].median():.2f}  L={results['psl'].median():.2f}")

    # ── YEAR-BY-YEAR ──
    print("\n--- YEAR-BY-YEAR ROI (edge ≥ 5%) ---")
    subset_5 = all_bets[all_bets['edge'] >= 0.05].copy()
    subset_5['year'] = pd.to_datetime(subset_5['date']).dt.year
    for yr in sorted(subset_5['year'].unique()):
        yb = subset_5[subset_5['year'] == yr]
        pnl = yb.apply(lambda r: r['bet_odds'] - 1 if r['won'] else -1, axis=1)
        roi = pnl.sum() / len(yb)
        wr  = yb['won'].mean()
        print(f"  {yr}  n={len(yb):4d}  WR={wr:.1%}  ROI={roi:+.1%}  P&L={pnl.sum():+.1f}u")

    # ── CLV SIMULATION ──
    print("\n--- CLV SIMULATION (how much do we beat Pinnacle closing line?) ---")
    subset_5 = all_bets[all_bets['edge'] >= 0.05]
    if len(subset_5) > 0:
        clv = subset_5['elo_prob'] - subset_5['pin_true']
        print(f"  Avg CLV (Elo prob − Pinnacle de-vigged prob):  {clv.mean():+.2%}")
        print(f"  % bets with positive CLV:                      {(clv > 0).mean():.1%}")
        print(f"  Bets beating closing line = real edge signal")

    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    # Auto-verdict
    best_roi = {}
    for t in [0.05, 0.08, 0.10]:
        bets = all_bets[all_bets['edge'] >= t]
        if len(bets) > 10:
            pnl = bets.apply(lambda r: r['bet_odds'] - 1 if r['won'] else -1, axis=1)
            best_roi[t] = (pnl.sum() / len(bets), len(bets))

    if any(v[0] > 0 for v in best_roi.values()):
        print("  ✅ POSITIVE ROI found at one or more edge thresholds")
        for t, (roi, n) in best_roi.items():
            sign = '✅' if roi > 0 else '❌'
            print(f"  {sign} Edge ≥ {t*100:.0f}%: ROI={roi:+.1%} (n={n})")
    else:
        print("  ❌ No profitable threshold found with simple Elo model")
        print("  → Needs surface-adjusted features, serve/return stats, or rankings model")


if __name__ == '__main__':
    main()
