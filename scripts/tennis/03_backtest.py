"""
Tennis Prediction Model & Backtest Engine — v2 (Fixed)

CRITICAL FIX from v1: The backtest now correctly handles the betting logic.
Previously, odds were labeled "Winner/Loser" which leaked outcome info into
the betting decision. Now we randomize player assignment (A vs B) and the model
must identify value without knowing who wins.

Strategy:
1. Join ELO/form features (from Sackmann) to odds data (from tennis-data.co.uk)
2. Randomly assign players as A and B (breaking winner/loser labeling)
3. Model predicts P(A wins), compare against A's odds
4. Bet only when model edge exceeds threshold
"""
import pandas as pd
import numpy as np
import os
import json
import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
import xgboost as xgb
import warnings
warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
PROCESSED_DIR = os.path.join(PROJECT_ROOT, 'data', 'processed', 'tennis')
MODELS_DIR = os.path.join(PROJECT_ROOT, 'data', 'models', 'tennis')
RESULTS_DIR = os.path.join(PROJECT_ROOT, 'data', 'model_results')
os.makedirs(MODELS_DIR, exist_ok=True)


def load_and_join_data():
    """Load odds + features, join by surname + month."""
    print("Loading data...")
    odds = pd.read_csv(os.path.join(PROCESSED_DIR, 'all_odds_matches.csv'), low_memory=False)
    odds['Date'] = pd.to_datetime(odds['Date'], errors='coerce')
    odds = odds[odds['Date'].notna()].copy()

    atp_feats = pd.read_csv(os.path.join(PROCESSED_DIR, 'atp_match_features.csv'), low_memory=False)
    wta_feats = pd.read_csv(os.path.join(PROCESSED_DIR, 'wta_match_features.csv'), low_memory=False)
    atp_feats['tour'] = 'ATP'
    wta_feats['tour'] = 'WTA'
    feats = pd.concat([atp_feats, wta_feats], ignore_index=True)
    feats['date'] = pd.to_datetime(feats['date'], errors='coerce')

    def surname_sack(name):
        if pd.isna(name): return ''
        return str(name).split()[-1].lower()

    def surname_odds(name):
        if pd.isna(name): return ''
        parts = str(name).split()
        if len(parts) >= 2 and (parts[-1].endswith('.') or len(parts[-1]) <= 2):
            return ' '.join(parts[:-1]).lower().rstrip('.')
        return parts[0].lower().rstrip('.')

    # Build lookup: (sorted_surnames_tuple, year-month) -> feature_row
    feat_lookup = {}
    for _, row in feats.iterrows():
        w_sur = surname_sack(row['winner_name'])
        l_sur = surname_sack(row['loser_name'])
        date = row['date']
        if pd.isna(date): continue
        key = (tuple(sorted([w_sur, l_sur])), date.strftime('%Y-%m'))
        if key not in feat_lookup:
            feat_lookup[key] = row

    print(f"  Feature lookup: {len(feat_lookup):,} unique keys")

    # Feature columns to extract
    feat_cols = ['w_elo', 'l_elo', 'elo_diff', 'w_elo_surface', 'l_elo_surface',
                 'elo_surface_diff', 'w_form_10', 'l_form_10', 'w_form_5', 'l_form_5',
                 'w_surface_form', 'l_surface_form', 'w_days_since', 'l_days_since',
                 'w_matches_14d', 'l_matches_14d', 'w_matches_30d', 'l_matches_30d',
                 'w_avg_ace_pct', 'l_avg_ace_pct', 'w_avg_1st_pct', 'l_avg_1st_pct',
                 'w_avg_1st_won', 'l_avg_1st_won', 'w_avg_2nd_won', 'l_avg_2nd_won',
                 'w_avg_bp_saved', 'l_avg_bp_saved', 'w_avg_df_pct', 'l_avg_df_pct',
                 'h2h_w_rate', 'h2h_total', 'w_career_matches', 'l_career_matches',
                 'w_surface_matches', 'l_surface_matches']

    feat_values = {col: [] for col in feat_cols}
    matched = 0
    for _, row in odds.iterrows():
        w_sur = surname_odds(row['Winner'])
        l_sur = surname_odds(row['Loser'])
        date = row['Date']
        key = (tuple(sorted([w_sur, l_sur])), date.strftime('%Y-%m'))
        frow = feat_lookup.get(key)
        if frow is not None:
            matched += 1
            for col in feat_cols:
                feat_values[col].append(frow.get(col, np.nan))
        else:
            for col in feat_cols:
                feat_values[col].append(np.nan)

    for col in feat_cols:
        odds[col] = feat_values[col]

    odds['is_clay'] = (odds['Surface'] == 'Clay').astype(int)
    odds['is_grass'] = (odds['Surface'] == 'Grass').astype(int)
    odds['is_hard'] = (odds['Surface'] == 'Hard').astype(int)
    odds['is_bo5'] = (odds['Best of'] == 5).astype(int)

    print(f"  Matched features: {matched:,}/{len(odds):,} ({matched/len(odds)*100:.1f}%)")
    return odds


def build_randomized_dataset(odds):
    """
    CRITICAL: Randomly assign Winner as 'pA' or 'pB' to prevent information leakage.
    The model must learn to predict based on features alone, not player labels.

    For each match:
    - 50% chance: pA = Winner, pB = Loser, target = 1, pA_odds = AvgW, pB_odds = AvgL
    - 50% chance: pA = Loser, pB = Winner, target = 0, pA_odds = AvgL, pB_odds = AvgW
    """
    np.random.seed(42)
    rows = []

    for _, row in odds.iterrows():
        if np.random.random() < 0.5:
            # pA = Winner
            r = _make_row(row, winner_is_pA=True)
            r['target'] = 1  # pA (winner) wins
            r['pA_odds'] = row.get('AvgW')
            r['pB_odds'] = row.get('AvgL')
            r['pA_b365'] = row.get('B365W')
            r['pB_b365'] = row.get('B365L')
            r['pA_pin'] = row.get('PSW')
            r['pB_pin'] = row.get('PSL')
            r['pA_max'] = row.get('MaxW')
            r['pB_max'] = row.get('MaxL')
        else:
            # pA = Loser
            r = _make_row(row, winner_is_pA=False)
            r['target'] = 0  # pA (loser) loses
            r['pA_odds'] = row.get('AvgL')
            r['pB_odds'] = row.get('AvgW')
            r['pA_b365'] = row.get('B365L')
            r['pB_b365'] = row.get('B365W')
            r['pA_pin'] = row.get('PSL')
            r['pB_pin'] = row.get('PSW')
            r['pA_max'] = row.get('MaxL')
            r['pB_max'] = row.get('MaxW')

        r['Date'] = row['Date']
        r['tour'] = row['tour']
        r['series'] = row.get('series', '')
        r['Surface'] = row.get('Surface', '')
        r['Round'] = row.get('Round', '')
        r['year'] = row.get('year')
        rows.append(r)

    return pd.DataFrame(rows)


def _make_row(row, winner_is_pA):
    """Build feature row for pA vs pB."""
    if winner_is_pA:
        a, b = 'w_', 'l_'
        a_rank, b_rank = row.get('WRank'), row.get('LRank')
    else:
        a, b = 'l_', 'w_'
        a_rank, b_rank = row.get('LRank'), row.get('WRank')

    r = {}

    # ELO features (differences: pA - pB)
    a_elo = row.get(f'{a}elo', np.nan)
    b_elo = row.get(f'{b}elo', np.nan)
    a_elo_s = row.get(f'{a}elo_surface', np.nan)
    b_elo_s = row.get(f'{b}elo_surface', np.nan)

    r['elo_diff'] = (a_elo or 1500) - (b_elo or 1500)
    r['elo_surface_diff'] = (a_elo_s or 1500) - (b_elo_s or 1500)
    r['pA_elo'] = a_elo or 1500
    r['pB_elo'] = b_elo or 1500

    # Ranking features
    a_rank = a_rank if pd.notna(a_rank) else 500
    b_rank = b_rank if pd.notna(b_rank) else 500
    r['rank_diff'] = b_rank - a_rank  # positive means pA is higher ranked
    r['rank_ratio'] = np.log(b_rank / a_rank) if a_rank > 0 and b_rank > 0 else 0

    # Form features
    r['pA_form_10'] = row.get(f'{a}form_10')
    r['pB_form_10'] = row.get(f'{b}form_10')
    r['form_diff'] = (row.get(f'{a}form_10') or 0.5) - (row.get(f'{b}form_10') or 0.5)
    r['pA_form_5'] = row.get(f'{a}form_5')
    r['pB_form_5'] = row.get(f'{b}form_5')
    r['pA_surface_form'] = row.get(f'{a}surface_form')
    r['pB_surface_form'] = row.get(f'{b}surface_form')

    # Fatigue
    r['pA_days_since'] = row.get(f'{a}days_since')
    r['pB_days_since'] = row.get(f'{b}days_since')
    r['pA_matches_14d'] = row.get(f'{a}matches_14d', 0)
    r['pB_matches_14d'] = row.get(f'{b}matches_14d', 0)
    r['pA_matches_30d'] = row.get(f'{a}matches_30d', 0)
    r['pB_matches_30d'] = row.get(f'{b}matches_30d', 0)

    # Serve stats (rolling averages)
    r['pA_ace_pct'] = row.get(f'{a}avg_ace_pct')
    r['pB_ace_pct'] = row.get(f'{b}avg_ace_pct')
    r['pA_1st_won'] = row.get(f'{a}avg_1st_won')
    r['pB_1st_won'] = row.get(f'{b}avg_1st_won')
    r['pA_2nd_won'] = row.get(f'{a}avg_2nd_won')
    r['pB_2nd_won'] = row.get(f'{b}avg_2nd_won')
    r['pA_bp_saved'] = row.get(f'{a}avg_bp_saved')
    r['pB_bp_saved'] = row.get(f'{b}avg_bp_saved')
    r['pA_df_pct'] = row.get(f'{a}avg_df_pct')
    r['pB_df_pct'] = row.get(f'{b}avg_df_pct')

    # H2H
    h2h_rate = row.get('h2h_w_rate')
    if pd.notna(h2h_rate):
        r['h2h_pA_rate'] = h2h_rate if winner_is_pA else (1 - h2h_rate)
    else:
        r['h2h_pA_rate'] = np.nan
    r['h2h_total'] = row.get('h2h_total', 0)

    # Experience
    r['pA_career_matches'] = row.get(f'{a}career_matches', 0)
    r['pB_career_matches'] = row.get(f'{b}career_matches', 0)

    # Context
    r['is_clay'] = row.get('is_clay', 0)
    r['is_grass'] = row.get('is_grass', 0)
    r['is_hard'] = row.get('is_hard', 0)
    r['is_bo5'] = row.get('is_bo5', 0)

    return r


# =============================================================================
# FEATURE SETS
# =============================================================================

FEATURE_SETS = {
    'elo_only': ['elo_diff', 'elo_surface_diff'],
    'elo_rank': ['elo_diff', 'elo_surface_diff', 'rank_diff', 'rank_ratio'],
    'elo_form': ['elo_diff', 'elo_surface_diff', 'rank_diff', 'rank_ratio',
                 'form_diff', 'pA_form_5', 'pB_form_5', 'pA_surface_form', 'pB_surface_form'],
    'full': ['elo_diff', 'elo_surface_diff', 'pA_elo', 'pB_elo',
             'rank_diff', 'rank_ratio',
             'form_diff', 'pA_form_5', 'pB_form_5', 'pA_surface_form', 'pB_surface_form',
             'pA_days_since', 'pB_days_since', 'pA_matches_14d', 'pB_matches_14d',
             'pA_ace_pct', 'pB_ace_pct', 'pA_1st_won', 'pB_1st_won',
             'pA_2nd_won', 'pB_2nd_won', 'pA_bp_saved', 'pB_bp_saved',
             'pA_df_pct', 'pB_df_pct',
             'h2h_pA_rate', 'h2h_total',
             'pA_career_matches', 'pB_career_matches',
             'is_clay', 'is_grass', 'is_bo5'],
}


# =============================================================================
# BACKTEST ENGINE
# =============================================================================

def backtest(model_df, version_name, feature_set_name, model_type='xgb',
             edge_threshold=0.03, min_odds=1.10, max_odds=5.0,
             min_prob=0.0, calibrate=True,
             tour_filter=None, series_filter=None):
    """Train model on past data, bet on test year."""
    feature_cols = FEATURE_SETS[feature_set_name]

    df = model_df.copy()
    if tour_filter:
        df = df[df['tour'] == tour_filter]
    if series_filter:
        df = df[df['series'].isin(series_filter) if isinstance(series_filter, list) else df['series'] == series_filter]

    df = df[df['pA_odds'].notna() & df['pB_odds'].notna()].copy()

    # Fill NaN features
    for col in feature_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    results = {}

    for test_year in [2022, 2023, 2024]:
        train = df[df['Date'] < f'{test_year}-01-01'].copy()
        test = df[(df['Date'] >= f'{test_year}-01-01') & (df['Date'] < f'{test_year+1}-01-01')].copy()

        if len(train) < 500 or len(test) < 100:
            continue

        # Fill NaN with training median
        for col in feature_cols:
            med = train[col].median()
            train[col] = train[col].fillna(med)
            test[col] = test[col].fillna(med)

        X_train = train[feature_cols].values
        y_train = train['target'].values
        X_test = test[feature_cols].values
        y_test = test['target'].values

        # Train
        if model_type == 'xgb':
            model = xgb.XGBClassifier(
                n_estimators=200, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
                eval_metric='logloss', random_state=42
            )
        else:
            model = LogisticRegression(max_iter=1000, C=1.0, random_state=42)
        model.fit(X_train, y_train)

        if calibrate:
            cal_model = CalibratedClassifierCV(model, method='isotonic', cv=5)
            cal_model.fit(X_train, y_train)
            pA_probs = cal_model.predict_proba(X_test)[:, 1]
        else:
            pA_probs = model.predict_proba(X_test)[:, 1]

        # Accuracy check
        preds = (pA_probs >= 0.5).astype(int)
        accuracy = (preds == y_test).mean()

        # Generate bets
        bets = []
        for i, (idx, row) in enumerate(test.iterrows()):
            pA_prob = pA_probs[i]
            pB_prob = 1 - pA_prob

            pA_odds = row['pA_odds']
            pB_odds = row['pB_odds']
            pA_implied = 1 / pA_odds
            pB_implied = 1 / pB_odds

            actual_pA_won = row['target']  # 1 if pA won, 0 if pB won

            # Check value on pA
            edge_A = pA_prob - pA_implied
            if (edge_A >= edge_threshold and min_odds <= pA_odds <= max_odds
                    and pA_prob >= min_prob):
                won = actual_pA_won == 1
                pnl = (pA_odds - 1) * 10 if won else -10
                bets.append({
                    'date': row['Date'], 'tour': row['tour'],
                    'series': row.get('series', ''), 'surface': row.get('Surface', ''),
                    'round': row.get('Round', ''),
                    'pick': 'pA', 'odds': pA_odds,
                    'model_prob': pA_prob, 'implied_prob': pA_implied,
                    'edge': edge_A, 'won': int(won), 'pnl': pnl,
                })

            # Check value on pB
            edge_B = pB_prob - pB_implied
            if (edge_B >= edge_threshold and min_odds <= pB_odds <= max_odds
                    and pB_prob >= min_prob):
                won = actual_pA_won == 0  # pB won
                pnl = (pB_odds - 1) * 10 if won else -10
                bets.append({
                    'date': row['Date'], 'tour': row['tour'],
                    'series': row.get('series', ''), 'surface': row.get('Surface', ''),
                    'round': row.get('Round', ''),
                    'pick': 'pB', 'odds': pB_odds,
                    'model_prob': pB_prob, 'implied_prob': pB_implied,
                    'edge': edge_B, 'won': int(won), 'pnl': pnl,
                })

        if not bets:
            results[test_year] = {'bets': 0, 'roi': np.nan, 'accuracy': round(accuracy * 100, 1)}
            continue

        bets_df = pd.DataFrame(bets)
        n_bets = len(bets_df)
        wins = bets_df['won'].sum()
        total_pnl = bets_df['pnl'].sum()
        roi = total_pnl / (n_bets * 10) * 100
        hit_rate = wins / n_bets * 100
        avg_odds = bets_df['odds'].mean()
        avg_edge = bets_df['edge'].mean() * 100

        # Calibration
        cal_bins = []
        for lo, hi in [(0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9)]:
            b = bets_df[(bets_df['model_prob'] >= lo) & (bets_df['model_prob'] < hi)]
            if len(b) >= 10:
                cal_bins.append({
                    'range': f'{lo:.0%}-{hi:.0%}',
                    'predicted': round(b['model_prob'].mean() * 100, 1),
                    'actual': round(b['won'].mean() * 100, 1),
                    'count': len(b),
                })

        r = {
            'bets': n_bets, 'wins': int(wins), 'hit_rate': round(hit_rate, 1),
            'roi': round(roi, 1), 'pnl': round(total_pnl, 2),
            'avg_odds': round(avg_odds, 2), 'avg_edge': round(avg_edge, 1),
            'accuracy': round(accuracy * 100, 1),
            'calibration': cal_bins,
        }

        # Breakdowns
        if tour_filter is None:
            for tour in bets_df['tour'].unique():
                t = bets_df[bets_df['tour'] == tour]
                r[f'{tour}_bets'] = len(t)
                r[f'{tour}_roi'] = round(t['pnl'].sum() / (len(t) * 10) * 100, 1)
                r[f'{tour}_hit'] = round(t['won'].mean() * 100, 1)

        for series in bets_df['series'].unique():
            s = bets_df[bets_df['series'] == series]
            if len(s) >= 20:
                r[f'series_{series}_bets'] = len(s)
                r[f'series_{series}_roi'] = round(s['pnl'].sum() / (len(s) * 10) * 100, 1)

        # Save bets log
        bets_df.to_csv(os.path.join(RESULTS_DIR, f'tennis_{version_name}_{test_year}.csv'), index=False)

        results[test_year] = r

    return results


def run_all_versions(model_df):
    """Run all model versions."""
    versions = [
        {'name': 'v0_elo_baseline', 'desc': 'ELO-only, 3% edge',
         'feature_set': 'elo_only', 'edge_threshold': 0.03},
        {'name': 'v1_elo_rank', 'desc': 'ELO + rank, 3% edge',
         'feature_set': 'elo_rank', 'edge_threshold': 0.03},
        {'name': 'v2_elo_form', 'desc': 'ELO + rank + form, 3% edge',
         'feature_set': 'elo_form', 'edge_threshold': 0.03},
        {'name': 'v3_full', 'desc': 'All features, 3% edge',
         'feature_set': 'full', 'edge_threshold': 0.03},
        {'name': 'v4_selective', 'desc': 'All features, 5% edge, odds 1.20-3.50',
         'feature_set': 'full', 'edge_threshold': 0.05, 'min_odds': 1.20, 'max_odds': 3.50, 'min_prob': 0.40},
        {'name': 'v5_very_selective', 'desc': 'All features, 8% edge, odds 1.30-3.00',
         'feature_set': 'full', 'edge_threshold': 0.08, 'min_odds': 1.30, 'max_odds': 3.00, 'min_prob': 0.45},
        {'name': 'v6_wta_only', 'desc': 'WTA only, all features, 5% edge',
         'feature_set': 'full', 'edge_threshold': 0.05, 'min_odds': 1.20, 'max_odds': 3.50,
         'tour_filter': 'WTA'},
        {'name': 'v7_atp_only', 'desc': 'ATP only, all features, 5% edge',
         'feature_set': 'full', 'edge_threshold': 0.05, 'min_odds': 1.20, 'max_odds': 3.50,
         'tour_filter': 'ATP'},
        {'name': 'v8_logistic', 'desc': 'Logistic regression, all features, 5% edge',
         'feature_set': 'full', 'model_type': 'logistic', 'edge_threshold': 0.05,
         'min_odds': 1.20, 'max_odds': 3.50},
        {'name': 'v9_wta_soft', 'desc': 'WTA 250/International, 5% edge',
         'feature_set': 'full', 'edge_threshold': 0.05, 'min_odds': 1.20, 'max_odds': 3.50,
         'tour_filter': 'WTA', 'series_filter': ['WTA250', 'International', 'Tier 3', 'Tier 4']},
        {'name': 'v10_atp_250', 'desc': 'ATP 250/International, 5% edge',
         'feature_set': 'full', 'edge_threshold': 0.05, 'min_odds': 1.20, 'max_odds': 3.50,
         'tour_filter': 'ATP', 'series_filter': ['ATP250', 'International', 'International Gold']},
    ]

    iterations = []
    for v in versions:
        print(f"\n{'='*60}")
        print(f"  {v['name']}: {v['desc']}")
        print(f"{'='*60}")

        results = backtest(
            model_df, v['name'], v['feature_set'],
            model_type=v.get('model_type', 'xgb'),
            edge_threshold=v.get('edge_threshold', 0.03),
            min_odds=v.get('min_odds', 1.10),
            max_odds=v.get('max_odds', 5.0),
            min_prob=v.get('min_prob', 0.0),
            calibrate=v.get('calibrate', True),
            tour_filter=v.get('tour_filter'),
            series_filter=v.get('series_filter'),
        )

        iteration = {'version': v['name'], 'description': v['desc'], 'results': {}}

        for year, r in sorted(results.items()):
            acc = r.get('accuracy', 'N/A')
            print(f"\n  {year}: {r.get('bets', 0)} bets | Hit: {r.get('hit_rate', 'N/A')}% | "
                  f"ROI: {r.get('roi', 'N/A')}% | P&L: EUR {r.get('pnl', 0):.0f} | "
                  f"Model accuracy: {acc}%")

            if r.get('avg_odds'):
                print(f"    Avg odds: {r['avg_odds']}, Avg edge: {r['avg_edge']}%")

            if r.get('calibration'):
                for cb in r['calibration']:
                    print(f"    Cal {cb['range']}: pred {cb['predicted']:.0f}% → actual {cb['actual']:.0f}% ({cb['count']} bets)")

            for key in ['ATP_bets', 'ATP_roi', 'ATP_hit', 'WTA_bets', 'WTA_roi', 'WTA_hit']:
                if key in r:
                    print(f"    {key}: {r[key]}")

            for key in sorted(r.keys()):
                if key.startswith('series_') and key.endswith('_roi'):
                    sname = key.replace('series_', '').replace('_roi', '')
                    bkey = key.replace('_roi', '_bets')
                    if bkey in r:
                        print(f"    {sname}: {r[bkey]} bets, {r[key]}% ROI")

            iteration['results'][str(year)] = r

        iterations.append(iteration)

    return iterations


def main():
    print("=" * 60)
    print("TENNIS BACKTEST ENGINE (v2 — Fixed)")
    print("=" * 60)

    odds = load_and_join_data()

    print("\nBuilding randomized dataset...")
    model_df = build_randomized_dataset(odds)
    print(f"  Dataset: {len(model_df):,} matches")
    print(f"  Target balance: {model_df['target'].mean():.3f} (should be ~0.50)")

    iterations = run_all_versions(model_df)

    # Save
    ipath = os.path.join(RESULTS_DIR, 'tennis_iterations.json')
    with open(ipath, 'w') as f:
        json.dump(iterations, f, indent=2, default=str)
    print(f"\nSaved to {ipath}")

    # Summary
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(f"{'Version':<25} {'Accuracy':>8} {'2022':>10} {'2023':>10} {'2024':>10} {'Bets/yr':>8}")
    print("-" * 71)
    for it in iterations:
        rois, bets_list, accs = [], [], []
        for y in ['2022', '2023', '2024']:
            r = it['results'].get(y, {})
            rois.append(f"{r.get('roi', 'N/A')}%" if r.get('roi') is not None else 'N/A')
            bets_list.append(r.get('bets', 0))
            accs.append(r.get('accuracy', 0))
        avg_bets = np.mean(bets_list) if bets_list else 0
        avg_acc = np.mean(accs) if accs else 0
        print(f"{it['version']:<25} {avg_acc:>7.1f}% {rois[0]:>10} {rois[1]:>10} {rois[2]:>10} {avg_bets:>8.0f}")

    # Save models for best version
    print("\nSaving models...")
    best_version = 'v4_selective'
    feature_cols = FEATURE_SETS['full']
    df = model_df[model_df['pA_odds'].notna() & model_df['pB_odds'].notna()].copy()
    for col in feature_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(df[col].median() if col in df else 0)

    for train_end in [2022, 2023, 2024]:
        train = df[df['Date'] < f'{train_end}-01-01']
        X = train[feature_cols].values
        y = train['target'].values
        model = xgb.XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
            eval_metric='logloss', random_state=42
        )
        model.fit(X, y)
        cal = CalibratedClassifierCV(model, method='isotonic', cv=5)
        cal.fit(X, y)
        joblib.dump(cal, os.path.join(MODELS_DIR, f'tennis_v4_trained_to_{train_end}.joblib'))
        print(f"  Saved tennis_v4_trained_to_{train_end}.joblib")


if __name__ == '__main__':
    main()
