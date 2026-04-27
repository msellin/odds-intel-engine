"""
Tennis Data Processing Pipeline
Two data tracks:
  1. Odds-primary: tennis-data.co.uk data (has odds + results + rankings) — for backtesting
  2. Sackmann-primary: detailed serve stats + rankings — for feature engineering (ELO, form)
We compute ELO and form from Sackmann data, then join to odds data for backtesting.
"""
import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings('ignore')

RAW_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'raw', 'tennis')
PROCESSED_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'processed', 'tennis')
os.makedirs(PROCESSED_DIR, exist_ok=True)


def load_sackmann(tour='atp', include_challenger=False):
    """Load Jeff Sackmann match data."""
    frames = []
    for year in range(2000, 2026):
        fpath = os.path.join(RAW_DIR, f'{tour}_matches_{year}.csv')
        if os.path.exists(fpath):
            df = pd.read_csv(fpath, low_memory=False)
            df['year'] = year
            df['level'] = 'main'
            frames.append(df)

    if include_challenger and tour == 'atp':
        for year in range(2000, 2026):
            fpath = os.path.join(RAW_DIR, f'atp_matches_qual_chall_{year}.csv')
            if os.path.exists(fpath):
                df = pd.read_csv(fpath, low_memory=False)
                df['year'] = year
                df['level'] = 'challenger'
                frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    combined['tourney_date'] = pd.to_datetime(combined['tourney_date'].astype(str), format='%Y%m%d', errors='coerce')
    print(f"  {tour.upper()} Sackmann: {len(combined):,} matches ({combined['year'].min()}-{combined['year'].max()})")
    return combined


def load_odds(tour='atp'):
    """Load tennis-data.co.uk odds data."""
    frames = []
    start_year = 2005 if tour == 'atp' else 2007
    for year in range(start_year, 2026):
        fname = f'{"tennis" if tour == "atp" else "wta"}_odds_{year}.xlsx'
        fpath = os.path.join(RAW_DIR, fname)
        if os.path.exists(fpath):
            try:
                df = pd.read_excel(fpath)
                df['year'] = year
                frames.append(df)
            except Exception:
                pass

    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined['Date'] = pd.to_datetime(combined['Date'], errors='coerce')
    print(f"  {tour.upper()} Odds: {len(combined):,} matches ({combined['year'].min()}-{combined['year'].max()})")
    return combined


def compute_serve_pcts(df):
    """Compute serve percentages from Sackmann raw stats."""
    for p in ['w', 'l']:
        svpt = df[f'{p}_svpt']
        first_in = df[f'{p}_1stIn']
        df[f'{p}_1st_pct'] = first_in / svpt
        df[f'{p}_1st_won_pct'] = df[f'{p}_1stWon'] / first_in
        df[f'{p}_2nd_won_pct'] = df[f'{p}_2ndWon'] / (svpt - first_in)
        df[f'{p}_bp_saved_pct'] = df[f'{p}_bpSaved'] / df[f'{p}_bpFaced']
        df[f'{p}_ace_pct'] = df[f'{p}_ace'] / svpt
        df[f'{p}_df_pct'] = df[f'{p}_df'] / svpt
    return df


def standardize_odds_data(atp_odds, wta_odds):
    """Standardize both ATP and WTA odds data into unified format."""
    # ATP has 'Series' column, WTA has 'Tier' column
    all_frames = []

    if not atp_odds.empty:
        atp = atp_odds.copy()
        atp['tour'] = 'ATP'
        # Standardize series column
        series_col = 'Series' if 'Series' in atp.columns else None
        if series_col:
            atp['series'] = atp[series_col]
        else:
            atp['series'] = 'Unknown'
        all_frames.append(atp)

    if not wta_odds.empty:
        wta = wta_odds.copy()
        wta['tour'] = 'WTA'
        tier_col = 'Tier' if 'Tier' in wta.columns else None
        if tier_col:
            wta['series'] = wta[tier_col]
        else:
            wta['series'] = 'Unknown'
        all_frames.append(wta)

    if not all_frames:
        return pd.DataFrame()

    # Select common columns
    common_cols = ['Date', 'Location', 'Tournament', 'Surface', 'Court', 'Round',
                   'Best of', 'Winner', 'Loser', 'WRank', 'LRank', 'WPts', 'LPts',
                   'W1', 'L1', 'W2', 'L2', 'W3', 'L3', 'Wsets', 'Lsets', 'Comment',
                   'B365W', 'B365L', 'PSW', 'PSL', 'MaxW', 'MaxL', 'AvgW', 'AvgL',
                   'tour', 'series', 'year']

    combined = pd.concat(all_frames, ignore_index=True)

    # Keep only columns that exist
    cols_to_keep = [c for c in common_cols if c in combined.columns]
    # Also keep W4, L4, W5, L5 if they exist (for BO5)
    for c in ['W4', 'L4', 'W5', 'L5']:
        if c in combined.columns:
            cols_to_keep.append(c)

    combined = combined[cols_to_keep].copy()

    # Filter completed matches
    if 'Comment' in combined.columns:
        combined = combined[combined['Comment'].str.lower().isin(['completed', 'retired']) |
                           combined['Comment'].isna()].copy()

    # Clean rankings
    combined['WRank'] = pd.to_numeric(combined['WRank'], errors='coerce')
    combined['LRank'] = pd.to_numeric(combined['LRank'], errors='coerce')

    # Clean odds
    for col in ['B365W', 'B365L', 'PSW', 'PSL', 'MaxW', 'MaxL', 'AvgW', 'AvgL']:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors='coerce')

    # Calculate implied probabilities from odds
    combined['implied_prob_w'] = 1 / combined['AvgW']
    combined['implied_prob_l'] = 1 / combined['AvgL']
    combined['overround'] = combined['implied_prob_w'] + combined['implied_prob_l']

    # Pinnacle implied (sharpest)
    combined['pin_implied_w'] = 1 / combined['PSW']
    combined['pin_implied_l'] = 1 / combined['PSL']

    print(f"\n  Combined odds dataset: {len(combined):,} matches")
    print(f"    ATP: {(combined['tour'] == 'ATP').sum():,} | WTA: {(combined['tour'] == 'WTA').sum():,}")
    print(f"    With Pinnacle: {combined['PSW'].notna().sum():,} | With B365: {combined['B365W'].notna().sum():,}")
    print(f"    Surfaces: {combined['Surface'].value_counts().to_dict()}")
    print(f"    Date range: {combined['Date'].min()} to {combined['Date'].max()}")
    print(f"    Avg overround: {combined['overround'].mean():.3f} ({(combined['overround'].mean()-1)*100:.1f}% margin)")

    return combined


def main():
    print("=" * 60)
    print("TENNIS DATA PROCESSING PIPELINE")
    print("=" * 60)

    # Track 1: Sackmann data (for ELO computation and serve stats)
    print("\n--- TRACK 1: Sackmann Data (serve stats, ELO base) ---")
    atp_sack = load_sackmann('atp', include_challenger=True)
    wta_sack = load_sackmann('wta')

    atp_main = atp_sack[atp_sack['level'] == 'main'].copy()
    atp_chall = atp_sack[atp_sack['level'] == 'challenger'].copy()
    atp_main = compute_serve_pcts(atp_main)
    wta_sack = compute_serve_pcts(wta_sack)

    print(f"  ATP main: {len(atp_main):,} | Challenger: {len(atp_chall):,} | WTA: {len(wta_sack):,}")

    # Save Sackmann data (will be used for ELO and feature engineering)
    atp_sack.to_csv(os.path.join(PROCESSED_DIR, 'sackmann_atp_all.csv'), index=False)
    wta_sack.to_csv(os.path.join(PROCESSED_DIR, 'sackmann_wta.csv'), index=False)
    print(f"  Saved sackmann_atp_all.csv and sackmann_wta.csv")

    # Track 2: Odds data (for backtesting)
    print("\n--- TRACK 2: Odds Data (backtesting) ---")
    atp_odds = load_odds('atp')
    wta_odds = load_odds('wta')

    combined_odds = standardize_odds_data(atp_odds, wta_odds)
    combined_odds.to_csv(os.path.join(PROCESSED_DIR, 'all_odds_matches.csv'), index=False)
    print(f"\n  Saved all_odds_matches.csv ({len(combined_odds):,} matches)")

    # Summary stats per tour and series
    print("\n--- MATCH COUNTS BY TOUR & SERIES ---")
    if 'series' in combined_odds.columns:
        summary = combined_odds.groupby(['tour', 'series']).agg(
            matches=('Date', 'count'),
            avg_overround=('overround', 'mean'),
            pct_with_pinnacle=('PSW', lambda x: x.notna().mean() * 100)
        ).round(2)
        print(summary.to_string())

    # Summary stats per surface
    print("\n--- MATCH COUNTS BY SURFACE ---")
    surface_summary = combined_odds.groupby(['tour', 'Surface']).agg(
        matches=('Date', 'count'),
        avg_overround=('overround', 'mean'),
        avg_wrank=('WRank', 'mean'),
        avg_lrank=('LRank', 'mean')
    ).round(1)
    print(surface_summary.to_string())

    # Favorite win rate by tour
    print("\n--- FAVORITE WIN RATE (lower rank = higher rated) ---")
    valid = combined_odds[combined_odds['WRank'].notna() & combined_odds['LRank'].notna()].copy()
    valid['fav_won'] = valid['WRank'] < valid['LRank']
    for tour in ['ATP', 'WTA']:
        t = valid[valid['tour'] == tour]
        print(f"  {tour}: Favorite (lower rank) wins {t['fav_won'].mean()*100:.1f}% of the time ({len(t):,} matches)")

    # Upset rate by series
    print("\n--- UPSET RATE BY SERIES ---")
    for tour in ['ATP', 'WTA']:
        t = valid[valid['tour'] == tour]
        upset_by_series = t.groupby('series')['fav_won'].agg(['mean', 'count'])
        upset_by_series['upset_rate'] = (1 - upset_by_series['mean']) * 100
        upset_by_series = upset_by_series.sort_values('upset_rate', ascending=False)
        print(f"\n  {tour}:")
        for idx, row in upset_by_series.iterrows():
            if row['count'] >= 50:
                print(f"    {idx}: {row['upset_rate']:.1f}% upset rate ({int(row['count'])} matches)")


if __name__ == '__main__':
    main()
