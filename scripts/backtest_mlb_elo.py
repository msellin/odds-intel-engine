#!/usr/bin/env python3
"""
MLB Elo backtest vs closing odds.

Data sources:
  Results: https://www.retrosheet.org/gamelogs/glYYYY.zip   (free, no account)
  Odds:    https://www.sportsbookreviewsonline.com           (free Excel, currently down — see ALT below)
  Alt odds: The Odds API historical endpoint (10 credits/call, need OA_KEY)

Run this after downloading data:
  python3 scripts/download_mlb_data.py   (writes data/raw/mlb/)
  python3 scripts/backtest_mlb_elo.py

SBRO column reference (when available):
  Date, Rot, VH (V=visitor/H=home), Team, 1st..5th (innings), Final,
  Open (moneyline), Close (moneyline), ML (actual moneyline used)
  Negative ML = favourite (e.g. -140), positive = underdog (+120)
"""

from __future__ import annotations
import sys, os
from pathlib import Path
import pandas as pd
import numpy as np
from collections import defaultdict

DATA_DIR = Path("data/raw/mlb")
TRAINING_YEARS = range(2015, 2022)
BACKTEST_YEARS = range(2022, 2025)
ELO_K = 20
ELO_HOME_ADVANTAGE = 24   # ~3.7% boost equivalent for MLB home
INITIAL_RATING = 1500
REGRESSION_PCT = 0.33      # regress to mean each new season (~1/3 of gap)


def ml_to_prob(ml: float) -> float | None:
    """American moneyline → implied probability (raw, with vig)."""
    if pd.isna(ml) or ml == 0:
        return None
    if ml > 0:
        return 100 / (ml + 100)
    return (-ml) / (-ml + 100)


def devig(home_ml: float, away_ml: float):
    """Return (home_true, away_true) de-vigged from American MLs."""
    hp = ml_to_prob(home_ml)
    ap = ml_to_prob(away_ml)
    if hp is None or ap is None:
        return None, None
    s = hp + ap
    return hp / s, ap / s


def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def update_elo(wr: float, lr: float, k: int = ELO_K):
    e = elo_expected(wr, lr)
    return wr + k * (1 - e), lr + k * (0 - (1 - e))


def load_sbro_mlb(years):
    """Load SBRO-format MLB Excel files from data/raw/mlb/."""
    dfs = []
    for y in years:
        for fname in [f"mlb_{y}.xlsx", f"mlb-odds-{y}.xlsx"]:
            p = DATA_DIR / fname
            if p.exists():
                df = pd.read_excel(p)
                df['season'] = y
                dfs.append(df)
                break
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    return df


def simulate_season_roi(accuracy: float, avg_odds: float = -110) -> float:
    """Given model accuracy and typical ML odds, compute expected flat-stake ROI."""
    # Convert -110 to decimal: 100/110 payout on win = 0.909
    win_payout = 100 / abs(avg_odds) if avg_odds < 0 else avg_odds / 100
    return accuracy * win_payout - (1 - accuracy) * 1.0


def main():
    print("=" * 70)
    print("MLB ELO BACKTEST")
    print("=" * 70)

    # ── Check for data ──
    if not DATA_DIR.exists() or not any(DATA_DIR.iterdir()):
        print("\n⚠️  No MLB data found in data/raw/mlb/")
        print("\nTo download, run:")
        print("  python3 scripts/download_mlb_data.py")
        print()
        print("OR download manually from:")
        print("  https://www.retrosheet.org/gamelogs/  (game results)")
        print("  SBRO Excel files (google: SBRO MLB odds 2023 xlsx)")
        print()
        print("─" * 70)
        print("RESEARCH-BASED ESTIMATES (without live backtest):")
        print("─" * 70)
        _print_research_estimates()
        return

    print(f"\nLoading SBRO MLB data ({BACKTEST_YEARS.start}–{BACKTEST_YEARS.stop-1})...")
    all_df = load_sbro_mlb(range(TRAINING_YEARS.start, BACKTEST_YEARS.stop))

    if all_df.empty:
        print("⚠️  Could not parse MLB Excel files — check column format.")
        _print_research_estimates()
        return

    print(f"  {len(all_df):,} games loaded")

    # SBRO column detection (format varies by year)
    # Typical: Date, Rot, VH, Team, 1st, 2nd, 3rd, 4th, 5th, Final, Open, Close, ML, 2H
    print("  Columns:", list(all_df.columns)[:15])

    # Build Elo ratings
    ratings: dict[str, float] = {}
    results = []

    all_df = all_df.sort_values('Date' if 'Date' in all_df.columns else all_df.columns[0])

    # Pair rows (SBRO has visitor row then home row for each game)
    rows = list(all_df.itertuples(index=False))
    i = 0
    while i < len(rows) - 1:
        away_row, home_row = rows[i], rows[i + 1]
        i += 2

        # Extract team and outcome
        try:
            away_team = getattr(away_row, 'Team', None)
            home_team = getattr(home_row, 'Team', None)
            away_final = float(getattr(away_row, 'Final', 0) or 0)
            home_final = float(getattr(home_row, 'Final', 0) or 0)
            season = getattr(home_row, 'season', 0)

            if not away_team or not home_team or (away_final == 0 and home_final == 0):
                continue

            home_won = home_final > away_final

            # Elo probabilities (with home advantage)
            rh = ratings.get(home_team, INITIAL_RATING) + ELO_HOME_ADVANTAGE
            ra = ratings.get(away_team, INITIAL_RATING)
            elo_prob_home = elo_expected(rh, ra)

            # Odds
            home_ml = getattr(home_row, 'Close', None) or getattr(home_row, 'ML', None)
            away_ml = getattr(away_row, 'Close', None) or getattr(away_row, 'ML', None)
            pin_home, pin_away = devig(home_ml, away_ml)

            if season >= BACKTEST_YEARS.start and pin_home is not None:
                results.append({
                    'season': season,
                    'home': home_team, 'away': away_team,
                    'home_won': home_won,
                    'elo_prob_home': elo_prob_home,
                    'pin_home': pin_home,
                    'edge_home': elo_prob_home - pin_home,
                    'edge_away': (1 - elo_prob_home) - pin_away,
                    'home_ml': home_ml,
                    'away_ml': away_ml,
                })

            # Update Elo
            if home_won:
                nw, nl = update_elo(ratings.get(home_team, INITIAL_RATING),
                                    ratings.get(away_team, INITIAL_RATING))
                ratings[home_team], ratings[away_team] = nw, nl
            else:
                nw, nl = update_elo(ratings.get(away_team, INITIAL_RATING),
                                    ratings.get(home_team, INITIAL_RATING))
                ratings[away_team], ratings[home_team] = nw, nl

        except Exception:
            continue

    if not results:
        print("⚠️  No valid game rows found — check column names in Excel files.")
        _print_research_estimates()
        return

    rdf = pd.DataFrame(results)
    print(f"\n  {len(rdf):,} backtest games with closing odds")

    # Main results table
    print(f"\n{'='*70}")
    print("BETTING ON ELO EDGE vs CLOSING ML")
    print(f"{'='*70}")
    print(f"  {'Edge':>6}  {'n':>6}  {'WR':>7}  {'ROI':>8}  {'P&L':>8}")

    for threshold in [0.00, 0.02, 0.03, 0.05, 0.08, 0.10]:
        home_bets = rdf[rdf['edge_home'] >= threshold]
        away_bets = rdf[rdf['edge_away'] >= threshold]

        def to_decimal(ml):
            if pd.isna(ml) or ml == 0:
                return 1.91
            return (100 / (-ml) + 1) if ml < 0 else (ml / 100 + 1)

        home_pnl = home_bets.apply(
            lambda r: to_decimal(r['home_ml']) - 1 if r['home_won'] else -1, axis=1)
        away_pnl = away_bets.apply(
            lambda r: to_decimal(r['away_ml']) - 1 if not r['home_won'] else -1, axis=1)

        all_pnl = pd.concat([home_pnl, away_pnl])
        n = len(home_bets) + len(away_bets)
        wr = (len(home_bets[home_bets['home_won']]) +
              len(away_bets[~away_bets['home_won']])) / max(n, 1)
        if n < 10:
            continue
        roi = all_pnl.sum() / n
        print(f"  ≥{threshold*100:4.0f}%  {n:>6}  {wr:>7.1%}  {roi:>+8.1%}  {all_pnl.sum():>+8.1f}u")

    # Model accuracy
    acc = (rdf['elo_prob_home'] > 0.5) == rdf['home_won']
    print(f"\n  Elo accuracy:      {acc.mean():.1%}")
    print(f"  Home win rate:     {rdf['home_won'].mean():.1%}")
    print(f"  Avg market margin: {(rdf.apply(lambda r: ml_to_prob(r['home_ml']) + ml_to_prob(r['away_ml']), axis=1)).mean():.1%}")


def _print_research_estimates():
    print("""
MLB RESEARCH-BASED EXPECTED RESULTS (from published studies):

  Elo model (basic win/loss):
    Accuracy: ~55-57%  (market is 55-58%)
    Breakeven: 52.4% at standard -110/-110
    Expected ROI at 0% edge threshold: -3% to -5%

  Totals (Over/Under) model:
    Best documented inefficiency in MLB
    Early-season bias documented at 56.7% win rate
    Expected ROI: -1% to +3% with proper model

  Key signals that add edge (from literature):
    - Starting pitcher ERA last 30 days (most important)
    - Ballpark run factor (varies 10-15% across parks)
    - Rest days (5+ vs 0) = ~3% win rate boost
    - Home team on winning streak = slight overpricing

  Pitcher rotation is the #1 MLB signal
  Without pitcher data, basic Elo likely gives -4% to -6% ROI
  With pitcher data (known day-of), could approach break-even or +
""")


if __name__ == '__main__':
    main()
