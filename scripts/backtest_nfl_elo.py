#!/usr/bin/env python3
"""
NFL Elo backtest vs nflverse closing odds (spread + moneyline).

Data: nflverse/nfldata games.csv — free, includes spread_line, home_moneyline,
      away_moneyline, over/under odds. Downloaded to data/raw/nfl/games.csv.

Training: 2006-2019 (build Elo ratings)
Backtest: 2020-2024 (out-of-sample vs actual lines)

Markets tested:
  1. Moneyline: who wins outright
  2. Spread: beat the spread (cover)
  3. Totals: over/under (using historical points for Poisson estimates)
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path

DATA_FILE = Path("data/raw/nfl/games.csv")
TRAINING_SEASONS = range(2006, 2020)
BACKTEST_SEASONS = range(2020, 2025)

ELO_K             = 20     # NFL — teams change slowly, moderate K
HOME_ADV          = 55     # Elo points added to home team (~3.5pt spread edge)
INITIAL_RATING    = 1500
REGRESSION_PCT    = 0.33   # regress to mean each new season
NEUTRAL_SITE_ADJ  = 0      # playoffs at neutral sites: no home advantage


def ml_to_decimal(ml: float) -> float | None:
    """American moneyline → decimal odds (returns None for invalid)."""
    if pd.isna(ml) or ml == 0:
        return None
    if ml > 0:
        return ml / 100 + 1
    return 100 / abs(ml) + 1


def ml_to_prob(ml: float) -> float | None:
    """American moneyline → raw implied probability (with vig)."""
    d = ml_to_decimal(ml)
    return (1 / d) if d else None


def devig_ml(home_ml: float, away_ml: float):
    """De-vig American moneylines → (home_true, away_true)."""
    hp = ml_to_prob(home_ml)
    ap = ml_to_prob(away_ml)
    if hp is None or ap is None:
        return None, None
    s = hp + ap
    return hp / s, ap / s


def elo_expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def elo_prob_to_spread(elo_prob_home: float) -> float:
    """Convert Elo win probability → expected point spread for home team."""
    # Approximate: each 3pp of win prob ≈ 1 point of spread
    # At 50% → 0 spread, at 70% → ~6.7pt spread
    if elo_prob_home <= 0 or elo_prob_home >= 1:
        return 0.0
    return -np.log(1 / elo_prob_home - 1) / np.log(10) * 400 / 3.0 / 10


def update_elo(winner_r: float, loser_r: float, margin: float = 1) -> tuple:
    """Update Elo for winner and loser. margin can be used for margin-weighted K."""
    e = elo_expected(winner_r, loser_r)
    # Margin of victory multiplier (log-based, standard for NFL)
    mov_mult = np.log(abs(margin) + 1) if margin > 0 else 1.0
    k_adj = ELO_K * min(mov_mult, 2.0)  # cap at 2x
    return winner_r + k_adj * (1 - e), loser_r + k_adj * (0 - (1 - e))


def main():
    print("=" * 70)
    print("NFL ELO BACKTEST vs nflverse closing lines")
    print("=" * 70)

    df = pd.read_csv(DATA_FILE, low_memory=False)
    df['gameday'] = pd.to_datetime(df['gameday'], errors='coerce')

    # Regular season + playoffs, filter to completed games
    df = df[df['home_score'].notna() & df['away_score'].notna()].copy()
    df['result'] = df['home_score'] - df['away_score']  # positive = home won
    df['home_won'] = df['result'] > 0
    df['neutral'] = df['location'] != 'Home'
    df = df.sort_values('gameday').reset_index(drop=True)

    print(f"\n  {len(df):,} total finished games (1999-2026)")
    print(f"  Training: {TRAINING_SEASONS.start}-{TRAINING_SEASONS.stop-1}")
    print(f"  Backtest: {BACKTEST_SEASONS.start}-{BACKTEST_SEASONS.stop-1}")

    # ─── Build Elo ratings ───────────────────────────────────────────
    ratings: dict[str, float] = {}

    def get_rating(team: str) -> float:
        return ratings.get(team, INITIAL_RATING)

    def regress_season(season: int):
        """Pull all ratings 33% toward mean at start of each new season."""
        for team in list(ratings.keys()):
            ratings[team] = ratings[team] * (1 - REGRESSION_PCT) + INITIAL_RATING * REGRESSION_PCT

    results = []
    prev_season = None

    for _, row in df.iterrows():
        season = int(row['season'])
        home = row['home_team']
        away = row['away_team']
        home_won = bool(row['home_won'])
        margin = abs(float(row['result']))
        is_neutral = bool(row['neutral'])

        # Regress at season boundary
        if prev_season is not None and season != prev_season:
            regress_season(season)
        prev_season = season

        # Elo probability with home advantage (unless neutral site)
        rh = get_rating(home) + (0 if is_neutral else HOME_ADV)
        ra = get_rating(away)
        elo_prob_home = elo_expected(rh, ra)

        # Moneyline and spread from data
        h_ml = row.get('home_moneyline')
        a_ml = row.get('away_moneyline')
        spread = row.get('spread_line')        # negative = home favoured
        h_sp_odds = row.get('home_spread_odds')
        a_sp_odds = row.get('away_spread_odds')
        total_line = row.get('total_line')
        over_odds = row.get('over_odds')
        under_odds = row.get('under_odds')

        if season in BACKTEST_SEASONS:
            pin_home, pin_away = devig_ml(h_ml, a_ml)

            if pin_home is not None:
                results.append({
                    'season': season,
                    'home': home, 'away': away,
                    'home_won': home_won,
                    'result': row['result'],
                    'neutral': is_neutral,
                    'elo_prob_home': elo_prob_home,
                    'pin_home': pin_home,
                    'edge_home': elo_prob_home - pin_home,
                    'edge_away': (1 - elo_prob_home) - pin_away,
                    'h_ml': h_ml, 'a_ml': a_ml,
                    'spread_line': spread,
                    'h_sp_odds': h_sp_odds, 'a_sp_odds': a_sp_odds,
                    'total_line': total_line,
                    'over_odds': over_odds, 'under_odds': under_odds,
                    'home_rest': row.get('home_rest'),
                    'away_rest': row.get('away_rest'),
                    'div_game': row.get('div_game'),
                    'roof': row.get('roof'),
                    'temp': row.get('temp'),
                    'wind': row.get('wind'),
                })

        # Update Elo
        if home_won:
            rw, rl = update_elo(get_rating(home), get_rating(away), margin)
            ratings[home], ratings[away] = rw, rl
        else:
            rw, rl = update_elo(get_rating(away), get_rating(home), margin)
            ratings[away], ratings[home] = rw, rl

    if not results:
        print("No backtest rows — check data")
        return

    rdf = pd.DataFrame(results)
    print(f"\n  {len(rdf):,} backtest games with moneyline data")

    def to_dec(ml):
        d = ml_to_decimal(ml)
        return d if d else 1.91

    # ─── MARKET 1: MONEYLINE ─────────────────────────────────────────
    print(f"\n{'='*70}")
    print("MARKET 1 — MONEYLINE (bet home or away vs Elo edge)")
    print(f"{'='*70}")
    print(f"  Avg market margin: {(rdf.apply(lambda r: ml_to_prob(r['h_ml']) + ml_to_prob(r['a_ml']), axis=1)).mean():.2%}")
    print(f"\n  {'Edge':>6}  {'n':>5}  {'WR':>7}  {'ROI':>8}  {'P&L':>8}")

    for threshold in [0.00, 0.02, 0.03, 0.05, 0.07, 0.10]:
        h_bets = rdf[rdf['edge_home'] >= threshold]
        a_bets = rdf[rdf['edge_away'] >= threshold]
        h_pnl = h_bets.apply(lambda r: to_dec(r['h_ml']) - 1 if r['home_won'] else -1, axis=1)
        a_pnl = a_bets.apply(lambda r: to_dec(r['a_ml']) - 1 if not r['home_won'] else -1, axis=1)
        all_pnl = pd.concat([h_pnl, a_pnl])
        n = len(h_pnl) + len(a_pnl)
        if n < 10:
            continue
        wr = (h_bets['home_won'].sum() + (~a_bets['home_won']).sum()) / n
        roi = all_pnl.sum() / n
        print(f"  ≥{threshold*100:4.0f}%  {n:>5}  {wr:>7.1%}  {roi:>+8.1%}  {all_pnl.sum():>+8.1f}u")

    acc = ((rdf['elo_prob_home'] > 0.5) == rdf['home_won']).mean()
    print(f"\n  Elo accuracy: {acc:.1%}")
    print(f"  Home win rate: {rdf['home_won'].mean():.1%}")

    # ─── MARKET 2: SPREAD ────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("MARKET 2 — SPREAD (does Elo predict ATS covers?)")
    print(f"{'='*70}")

    spread_df = rdf[
        rdf['spread_line'].notna() &
        rdf['h_sp_odds'].notna() &
        rdf['a_sp_odds'].notna()
    ].copy()

    # nflverse convention: spread_line > 0 = home favored, < 0 = away favored (home underdog)
    # home_covered: home result > spread_line (cover if wins by more than spread, or loses by less)
    spread_df['home_covered'] = spread_df['result'] > spread_df['spread_line']
    # Elo-implied home margin (points, positive = Elo predicts home win)
    spread_df['elo_margin'] = spread_df['elo_prob_home'].apply(elo_prob_to_spread)
    # Bet home to cover if Elo-predicted margin exceeds the spread
    spread_df['elo_pick_home'] = spread_df['elo_margin'] > spread_df['spread_line']
    # Was Elo correct?
    spread_df['elo_correct'] = spread_df['elo_pick_home'] == spread_df['home_covered']

    # Sanity check
    total_sp = len(spread_df)
    elo_sp_acc = spread_df['elo_correct'].mean()
    home_cov_pct = spread_df['home_covered'].mean()
    elo_bets_home = spread_df['elo_pick_home'].sum()
    print(f"  {total_sp:,} games with spread data")
    print(f"  Home covers: {home_cov_pct:.1%}  (expected ~50%)")
    print(f"  Elo picks home: {elo_bets_home:,} / {total_sp:,} ({elo_bets_home/total_sp:.1%})")
    print(f"  Elo ATS accuracy: {elo_sp_acc:.1%}  (expected ~52-53% for a weak signal)")

    # For each game: bet the Elo-favoured side, use that side's spread odds
    def spread_roi(df: pd.DataFrame) -> tuple:
        pnl = df.apply(
            lambda r: (to_dec(r['h_sp_odds']) - 1 if r['elo_pick_home'] else to_dec(r['a_sp_odds']) - 1)
            if r['elo_correct'] else -1.0,
            axis=1
        )
        n = len(pnl)
        roi = pnl.sum() / n if n > 0 else 0
        return pnl.sum(), n, roi

    print(f"\n  {'Filter':>30}  {'n':>5}  {'ATS Acc':>8}  {'ROI':>8}  {'P&L':>8}")
    total_pnl, n, roi = spread_roi(spread_df)
    acc = spread_df['elo_correct'].mean()
    print(f"  {'All games':>30}  {n:>5}  {acc:>8.1%}  {roi:>+8.1%}  {total_pnl:>+8.1f}u")

    # Filter by |elo_margin - market_margin| (how much Elo disagrees with line)
    for edge_pt in [1.0, 2.0, 3.0, 5.0]:
        sub = spread_df[abs(spread_df['elo_margin'] - spread_df['spread_line']) >= edge_pt]
        if len(sub) < 10: continue
        p, n, roi = spread_roi(sub)
        acc = sub['elo_correct'].mean()
        print(f"  {'|Elo−line| ≥ '+str(edge_pt)+'pt':>30}  {n:>5}  {acc:>8.1%}  {roi:>+8.1%}  {p:>+8.1f}u")

    # ─── REST ADVANTAGE SIGNAL ───────────────────────────────────────
    print(f"\n{'='*70}")
    print("REST ADVANTAGE — short-rest teams (≤5 days) vs fresh opponents")
    print(f"{'='*70}")

    rest_df = rdf[rdf['home_rest'].notna() & rdf['away_rest'].notna()].copy()
    rest_df['home_rest'] = rest_df['home_rest'].astype(float)
    rest_df['away_rest'] = rest_df['away_rest'].astype(float)

    # Short rest = ≤5 days (Thursday Night Football = 4 days)
    short_home = rest_df[rest_df['home_rest'] <= 5]
    short_away = rest_df[rest_df['away_rest'] <= 5]
    # Bet AGAINST short-rest team (bet on the fresh opponent)
    # Bet on away when home has short rest
    if len(short_home) > 5:
        pnl = short_home.apply(lambda r: to_dec(r['a_ml']) - 1 if not r['home_won'] else -1, axis=1)
        print(f"  Bet AWAY when home has ≤5 days rest: n={len(short_home)} WR={pnl[pnl>0].count()/len(pnl):.1%} ROI={pnl.sum()/len(pnl):+.1%}")
    if len(short_away) > 5:
        pnl = short_away.apply(lambda r: to_dec(r['h_ml']) - 1 if r['home_won'] else -1, axis=1)
        print(f"  Bet HOME when away has ≤5 days rest: n={len(short_away)} WR={pnl[pnl>0].count()/len(pnl):.1%} ROI={pnl.sum()/len(pnl):+.1%}")

    # ─── DIVISIONAL GAME SIGNAL ──────────────────────────────────────
    print(f"\n{'='*70}")
    print("DIVISIONAL GAMES — tighter results, covers trend different")
    print(f"{'='*70}")

    div_df = rdf[rdf['div_game'].notna()].copy()
    div_df['div_game'] = div_df['div_game'].astype(float)
    for div_flag, label in [(1.0, 'Divisional'), (0.0, 'Non-divisional')]:
        sub = div_df[div_df['div_game'] == div_flag]
        if len(sub) < 10: continue
        h_pnl = sub.apply(lambda r: to_dec(r['h_ml']) - 1 if r['home_won'] else -1, axis=1)
        a_pnl = sub.apply(lambda r: to_dec(r['a_ml']) - 1 if not r['home_won'] else -1, axis=1)
        # Flat bet on home
        h_roi = h_pnl.sum() / len(sub)
        hw_rate = sub['home_won'].mean()
        print(f"  {label:20s}  n={len(sub):4d}  HomeWR={hw_rate:.1%}  FlatHomeROI={h_roi:+.1%}")

    # ─── WEATHER SIGNAL (outdoor games) ──────────────────────────────
    print(f"\n{'='*70}")
    print("WEATHER — wind effect on totals (outdoor games)")
    print(f"{'='*70}")

    total_df = rdf[rdf['total_line'].notna() & rdf['over_odds'].notna()].copy()
    raw = pd.read_csv(DATA_FILE, low_memory=False)
    raw = raw[raw['home_score'].notna()].copy()
    raw['actual_total'] = raw['home_score'] + raw['away_score']
    total_df2 = total_df.merge(
        raw[['season','home_team','away_team','actual_total']].drop_duplicates(),
        left_on=['season','home','away'], right_on=['season','home_team','away_team'],
        how='left'
    ).drop(columns=['home_team','away_team'], errors='ignore')
    total_df2 = total_df2[total_df2['actual_total'].notna()].copy()
    total_df2['went_over'] = total_df2['actual_total'] > total_df2['total_line']

    # Wind threshold
    w_df = total_df2[total_df2['wind'].notna()].copy()
    w_df['wind'] = w_df['wind'].astype(float)
    w_df['roof'] = w_df['roof'].astype(str).str.lower()
    outdoor = w_df[~w_df['roof'].isin(['dome', 'closed', 'retractable'])]

    for wind_min in [0, 10, 15, 20]:
        high_wind = outdoor[outdoor['wind'] >= wind_min]
        if len(high_wind) < 10: continue
        # Bet under in high wind
        pnl = high_wind.apply(lambda r: to_dec(r['under_odds']) - 1 if not r['went_over'] else -1, axis=1)
        print(f"  Wind ≥ {wind_min:2d}mph → bet UNDER:  n={len(high_wind):4d}  "
              f"Cover={pnl[pnl>0].count()/len(pnl):.1%}  ROI={pnl.sum()/len(pnl):+.1%}")

    # ─── YEAR-BY-YEAR ────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("YEAR-BY-YEAR MONEYLINE ROI (edge ≥ 3%)")
    print(f"{'='*70}")

    for yr in sorted(rdf['season'].unique()):
        sub = rdf[rdf['season'] == yr]
        h_bets = sub[sub['edge_home'] >= 0.03]
        a_bets = sub[sub['edge_away'] >= 0.03]
        h_pnl = h_bets.apply(lambda r: to_dec(r['h_ml']) - 1 if r['home_won'] else -1, axis=1)
        a_pnl = a_bets.apply(lambda r: to_dec(r['a_ml']) - 1 if not r['home_won'] else -1, axis=1)
        all_pnl = pd.concat([h_pnl, a_pnl])
        n = len(all_pnl)
        if n < 5: continue
        roi = all_pnl.sum() / n
        wr = (h_bets['home_won'].sum() + (~a_bets['home_won']).sum()) / n
        print(f"  {yr}  n={n:4d}  WR={wr:.1%}  ROI={roi:+.1%}  P&L={all_pnl.sum():+.1f}u")

    # ─── VERDICT ─────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("VERDICT")
    print(f"{'='*70}")

    best = {}
    for t in [0.02, 0.03, 0.05, 0.07, 0.10]:
        h_b = rdf[rdf['edge_home'] >= t]
        a_b = rdf[rdf['edge_away'] >= t]
        h_p = h_b.apply(lambda r: to_dec(r['h_ml']) - 1 if r['home_won'] else -1, axis=1)
        a_p = a_b.apply(lambda r: to_dec(r['a_ml']) - 1 if not r['home_won'] else -1, axis=1)
        all_p = pd.concat([h_p, a_p])
        n = len(all_p)
        if n >= 10:
            best[t] = (all_p.sum() / n, n)

    if any(v[0] > 0 for v in best.values()):
        print("  ✅ POSITIVE ROI found on NFL moneyline!")
        for t, (roi, n) in best.items():
            sign = '✅' if roi > 0 else '❌'
            print(f"  {sign} Edge ≥ {t*100:.0f}%: ROI={roi:+.1%}  n={n}")
    else:
        print("  ❌ No profitable moneyline threshold")
        for t, (roi, n) in best.items():
            print(f"     Edge ≥ {t*100:.0f}%: ROI={roi:+.1%}  n={n}")
        print()
        print("  NOTE: NFL spread market may be more profitable than moneyline.")
        print("  See spread results above for ATS (against the spread) signals.")


if __name__ == '__main__':
    main()
