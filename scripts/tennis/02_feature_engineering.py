"""
Tennis Feature Engineering
Computes ELO ratings (overall + per-surface), player form, serve stats,
H2H records, fatigue indicators, and other features for modeling.

Uses Sackmann data for computing features, then joins to odds data for backtesting.
"""
import pandas as pd
import numpy as np
import os
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
PROCESSED_DIR = os.path.join(PROJECT_ROOT, 'data', 'processed', 'tennis')


# =============================================================================
# ELO RATING SYSTEM
# =============================================================================

class TennisELO:
    """
    Tennis ELO rating system with:
    - Overall ELO
    - Surface-specific ELO (hard, clay, grass)
    - Adjustable K-factor based on tournament level and match importance
    - Margin-of-victory adjustment (sets won ratio)
    """

    def __init__(self, k_main=32, k_surface=40, initial_rating=1500,
                 home_advantage=0, mov_weight=0.5):
        self.k_main = k_main
        self.k_surface = k_surface
        self.initial_rating = initial_rating
        self.home_advantage = home_advantage  # not relevant for tennis
        self.mov_weight = mov_weight

        self.ratings = {}          # player_id -> overall ELO
        self.surface_ratings = {}  # (player_id, surface) -> surface ELO
        self.match_counts = defaultdict(int)  # player_id -> matches played
        self.surface_match_counts = defaultdict(int)

    def get_rating(self, player_id):
        return self.ratings.get(player_id, self.initial_rating)

    def get_surface_rating(self, player_id, surface):
        return self.surface_ratings.get((player_id, surface), self.initial_rating)

    def expected_score(self, rating_a, rating_b):
        return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))

    def k_factor(self, tourney_level, round_str):
        """Adjust K-factor based on tournament importance."""
        # Higher K for bigger tournaments = more responsive to important results
        level_mult = {
            'G': 1.3,   # Grand Slams
            'M': 1.1,   # Masters 1000
            'F': 1.2,   # Tour Finals
            'A': 1.0,   # ATP 250/500
            'D': 0.8,   # Davis Cup
            'C': 0.7,   # Challenger
        }
        mult = level_mult.get(tourney_level, 1.0)

        # Late rounds matter more
        round_mult = {
            'F': 1.2, 'SF': 1.1, 'QF': 1.05,
            'R16': 1.0, 'R32': 1.0, 'R64': 0.95, 'R128': 0.9,
            'RR': 1.0,
        }
        r_mult = round_mult.get(round_str, 1.0)

        return self.k_main * mult * r_mult

    def mov_multiplier(self, w_sets, l_sets, best_of):
        """Margin of victory: 6-0 6-0 is more convincing than 7-6 7-6."""
        if pd.isna(w_sets) or pd.isna(l_sets) or best_of == 0:
            return 1.0
        total_sets = w_sets + l_sets
        max_sets = best_of
        # Ratio: closer match = lower multiplier
        dominance = (w_sets - l_sets) / max_sets
        return 1.0 + self.mov_weight * dominance

    def update(self, winner_id, loser_id, surface, tourney_level, round_str,
               w_sets=None, l_sets=None, best_of=3):
        """Update ELO ratings after a match."""
        # Get current ratings
        w_overall = self.get_rating(winner_id)
        l_overall = self.get_rating(loser_id)
        w_surface = self.get_surface_rating(winner_id, surface)
        l_surface = self.get_surface_rating(loser_id, surface)

        # Expected scores
        e_w = self.expected_score(w_overall, l_overall)
        e_l = 1 - e_w
        e_w_surf = self.expected_score(w_surface, l_surface)
        e_l_surf = 1 - e_w_surf

        # K-factor and MoV
        k = self.k_factor(tourney_level, round_str)
        k_surf = self.k_surface * (k / self.k_main)  # scale surface K proportionally
        mov = self.mov_multiplier(w_sets, l_sets, best_of)

        # Update overall
        self.ratings[winner_id] = w_overall + k * mov * (1 - e_w)
        self.ratings[loser_id] = l_overall + k * mov * (0 - e_l)

        # Update surface
        self.surface_ratings[(winner_id, surface)] = w_surface + k_surf * mov * (1 - e_w_surf)
        self.surface_ratings[(loser_id, surface)] = l_surface + k_surf * mov * (0 - e_l_surf)

        self.match_counts[winner_id] += 1
        self.match_counts[loser_id] += 1
        self.surface_match_counts[(winner_id, surface)] += 1
        self.surface_match_counts[(loser_id, surface)] += 1

        # Return pre-match ratings (for feature generation)
        return {
            'w_elo': w_overall, 'l_elo': l_overall,
            'w_elo_surface': w_surface, 'l_elo_surface': l_surface,
            'w_matches': self.match_counts[winner_id] - 1,
            'l_matches': self.match_counts[loser_id] - 1,
            'w_surface_matches': self.surface_match_counts[(winner_id, surface)] - 1,
            'l_surface_matches': self.surface_match_counts[(loser_id, surface)] - 1,
        }

    def season_regression(self, factor=0.25):
        """Regress ratings toward mean at season start."""
        mean_rating = np.mean(list(self.ratings.values())) if self.ratings else self.initial_rating
        for pid in self.ratings:
            self.ratings[pid] = self.ratings[pid] * (1 - factor) + mean_rating * factor
        for key in self.surface_ratings:
            self.surface_ratings[key] = self.surface_ratings[key] * (1 - factor) + self.initial_rating * factor


# =============================================================================
# PLAYER FORM TRACKER
# =============================================================================

class PlayerFormTracker:
    """Track rolling player statistics over recent matches."""

    def __init__(self, window=10):
        self.window = window
        self.results = defaultdict(list)    # player_id -> list of (win/loss, surface, date)
        self.serve_stats = defaultdict(list) # player_id -> list of serve stat dicts
        self.surface_results = defaultdict(list)  # (player_id, surface) -> results

    def add_match(self, player_id, won, surface, date, serve_dict=None):
        """Record a match result for a player."""
        self.results[player_id].append({'won': won, 'surface': surface, 'date': date})
        self.surface_results[(player_id, surface)].append({'won': won, 'date': date})

        if serve_dict:
            self.serve_stats[player_id].append(serve_dict)

        # Keep only recent matches
        if len(self.results[player_id]) > self.window * 3:
            self.results[player_id] = self.results[player_id][-self.window * 3:]
        if len(self.surface_results[(player_id, surface)]) > self.window * 2:
            self.surface_results[(player_id, surface)] = self.surface_results[(player_id, surface)][-self.window * 2:]
        if len(self.serve_stats[player_id]) > self.window * 2:
            self.serve_stats[player_id] = self.serve_stats[player_id][-self.window * 2:]

    def get_form(self, player_id, n=10):
        """Get win rate over last n matches."""
        recent = self.results.get(player_id, [])[-n:]
        if not recent:
            return np.nan
        return np.mean([r['won'] for r in recent])

    def get_surface_form(self, player_id, surface, n=10):
        """Get win rate on a specific surface over last n matches on that surface."""
        recent = self.surface_results.get((player_id, surface), [])[-n:]
        if not recent:
            return np.nan
        return np.mean([r['won'] for r in recent])

    def get_avg_serve_stats(self, player_id, n=10):
        """Get average serve statistics over last n matches."""
        recent = self.serve_stats.get(player_id, [])[-n:]
        if not recent:
            return {}
        result = {}
        keys = recent[0].keys()
        for k in keys:
            vals = [r[k] for r in recent if k in r and not np.isnan(r.get(k, np.nan))]
            result[k] = np.mean(vals) if vals else np.nan
        return result

    def get_days_since_last(self, player_id, current_date):
        """Get days since player's last match."""
        recent = self.results.get(player_id, [])
        if not recent:
            return np.nan
        last_date = recent[-1]['date']
        if pd.isna(last_date) or pd.isna(current_date):
            return np.nan
        return (current_date - last_date).days

    def get_matches_in_period(self, player_id, current_date, days=30):
        """Count matches played in the last N days."""
        recent = self.results.get(player_id, [])
        if not recent:
            return 0
        cutoff = current_date - pd.Timedelta(days=days)
        return sum(1 for r in recent if pd.notna(r['date']) and r['date'] >= cutoff)


# =============================================================================
# HEAD-TO-HEAD TRACKER
# =============================================================================

class H2HTracker:
    """Track head-to-head records between players."""

    def __init__(self):
        self.records = defaultdict(lambda: {'wins': 0, 'total': 0})
        self.surface_records = defaultdict(lambda: {'wins': 0, 'total': 0})

    def add_match(self, winner_id, loser_id, surface):
        key = (min(winner_id, loser_id), max(winner_id, loser_id))
        self.records[key]['total'] += 1
        if winner_id == key[0]:
            self.records[key]['wins'] += 1

        surf_key = (min(winner_id, loser_id), max(winner_id, loser_id), surface)
        self.surface_records[surf_key]['total'] += 1
        if winner_id == surf_key[0]:
            self.surface_records[surf_key]['wins'] += 1

    def get_h2h(self, player_a, player_b):
        """Get h2h win rate for player_a against player_b."""
        key = (min(player_a, player_b), max(player_a, player_b))
        rec = self.records.get(key)
        if rec is None or rec['total'] == 0:
            return np.nan, 0
        if player_a == key[0]:
            return rec['wins'] / rec['total'], rec['total']
        else:
            return 1 - rec['wins'] / rec['total'], rec['total']


# =============================================================================
# MAIN FEATURE ENGINEERING
# =============================================================================

def compute_features_from_sackmann(tour='atp'):
    """
    Process Sackmann data chronologically to compute ELO, form, H2H features.
    Reads directly from raw CSV files to preserve date information.
    """
    RAW_DIR = os.path.join(PROJECT_ROOT, 'data', 'raw', 'tennis')
    frames = []
    for year in range(2000, 2026):
        fpath = os.path.join(RAW_DIR, f'{tour}_matches_{year}.csv')
        if os.path.exists(fpath):
            d = pd.read_csv(fpath, low_memory=False)
            d['year'] = year
            d['level'] = 'main'
            frames.append(d)
        if tour == 'atp':
            fpath_c = os.path.join(RAW_DIR, f'atp_matches_qual_chall_{year}.csv')
            if os.path.exists(fpath_c):
                d = pd.read_csv(fpath_c, low_memory=False)
                d['year'] = year
                d['level'] = 'challenger'
                frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    # tourney_date is int/float like 20240101.0 — convert to int string first
    df['tourney_date'] = pd.to_datetime(
        df['tourney_date'].dropna().astype(int).astype(str),
        format='%Y%m%d', errors='coerce'
    ).reindex(df.index)

    # Filter out matches with no date
    df = df[df['tourney_date'].notna()].copy()
    df = df.sort_values('tourney_date').reset_index(drop=True)

    print(f"\n  Computing features for {tour.upper()}: {len(df):,} matches...")

    elo = TennisELO(k_main=32, k_surface=40, mov_weight=0.4)
    form = PlayerFormTracker(window=15)
    h2h = H2HTracker()

    # Store per-match features (keyed by winner/loser name + date for joining)
    match_features = []
    current_year = None

    for i, row in df.iterrows():
        if i % 50000 == 0 and i > 0:
            print(f"    Processed {i:,}/{len(df):,} matches...")

        # Season regression
        match_year = row['tourney_date'].year
        if current_year is not None and match_year > current_year:
            elo.season_regression(factor=0.2)
        current_year = match_year

        winner_id = row.get('winner_id', row.get('winner_name', ''))
        loser_id = row.get('loser_id', row.get('loser_name', ''))
        surface = str(row.get('surface', 'Hard')).lower()
        if surface not in ['hard', 'clay', 'grass']:
            surface = 'hard'

        tourney_level = str(row.get('tourney_level', 'A'))
        round_str = str(row.get('round', ''))
        best_of = int(row.get('best_of', 3)) if pd.notna(row.get('best_of')) else 3
        w_sets = row.get('w_sets') if pd.notna(row.get('w_sets', np.nan)) else None
        l_sets = row.get('l_sets') if pd.notna(row.get('l_sets', np.nan)) else None
        date = row['tourney_date']

        # Get PRE-MATCH features (before update)
        elo_feats = elo.update(winner_id, loser_id, surface, tourney_level, round_str,
                               w_sets, l_sets, best_of)

        # Form features
        w_form_10 = form.get_form(winner_id, 10)
        l_form_10 = form.get_form(loser_id, 10)
        w_form_5 = form.get_form(winner_id, 5)
        l_form_5 = form.get_form(loser_id, 5)
        w_surf_form = form.get_surface_form(winner_id, surface, 10)
        l_surf_form = form.get_surface_form(loser_id, surface, 10)

        # Fatigue
        w_days_since = form.get_days_since_last(winner_id, date)
        l_days_since = form.get_days_since_last(loser_id, date)
        w_matches_14d = form.get_matches_in_period(winner_id, date, 14)
        l_matches_14d = form.get_matches_in_period(loser_id, date, 14)
        w_matches_30d = form.get_matches_in_period(winner_id, date, 30)
        l_matches_30d = form.get_matches_in_period(loser_id, date, 30)

        # Serve stats (rolling average)
        w_serve = form.get_avg_serve_stats(winner_id, 10)
        l_serve = form.get_avg_serve_stats(loser_id, 10)

        # H2H
        h2h_w_rate, h2h_total = h2h.get_h2h(winner_id, loser_id)

        # Build feature dict
        feat = {
            'winner_name': row.get('winner_name', ''),
            'loser_name': row.get('loser_name', ''),
            'winner_id': winner_id,
            'loser_id': loser_id,
            'date': date,
            'surface': surface,
            'tourney_level': tourney_level,
            'round': round_str,
            'best_of': best_of,
            'level': row.get('level', 'main'),
            'winner_rank': row.get('winner_rank'),
            'loser_rank': row.get('loser_rank'),

            # ELO
            'w_elo': elo_feats['w_elo'],
            'l_elo': elo_feats['l_elo'],
            'elo_diff': elo_feats['w_elo'] - elo_feats['l_elo'],
            'w_elo_surface': elo_feats['w_elo_surface'],
            'l_elo_surface': elo_feats['l_elo_surface'],
            'elo_surface_diff': elo_feats['w_elo_surface'] - elo_feats['l_elo_surface'],

            # Form
            'w_form_10': w_form_10, 'l_form_10': l_form_10,
            'w_form_5': w_form_5, 'l_form_5': l_form_5,
            'w_surface_form': w_surf_form, 'l_surface_form': l_surf_form,

            # Fatigue
            'w_days_since': w_days_since, 'l_days_since': l_days_since,
            'w_matches_14d': w_matches_14d, 'l_matches_14d': l_matches_14d,
            'w_matches_30d': w_matches_30d, 'l_matches_30d': l_matches_30d,

            # Serve (rolling averages)
            'w_avg_ace_pct': w_serve.get('ace_pct', np.nan),
            'l_avg_ace_pct': l_serve.get('ace_pct', np.nan),
            'w_avg_1st_pct': w_serve.get('first_pct', np.nan),
            'l_avg_1st_pct': l_serve.get('first_pct', np.nan),
            'w_avg_1st_won': w_serve.get('first_won_pct', np.nan),
            'l_avg_1st_won': l_serve.get('first_won_pct', np.nan),
            'w_avg_2nd_won': w_serve.get('second_won_pct', np.nan),
            'l_avg_2nd_won': l_serve.get('second_won_pct', np.nan),
            'w_avg_bp_saved': w_serve.get('bp_saved_pct', np.nan),
            'l_avg_bp_saved': l_serve.get('bp_saved_pct', np.nan),
            'w_avg_df_pct': w_serve.get('df_pct', np.nan),
            'l_avg_df_pct': l_serve.get('df_pct', np.nan),

            # H2H
            'h2h_w_rate': h2h_w_rate,
            'h2h_total': h2h_total,

            # Match counts (experience)
            'w_career_matches': elo_feats['w_matches'],
            'l_career_matches': elo_feats['l_matches'],
            'w_surface_matches': elo_feats['w_surface_matches'],
            'l_surface_matches': elo_feats['l_surface_matches'],
        }

        match_features.append(feat)

        # Update form tracker AFTER recording features
        w_serve_dict = {}
        l_serve_dict = {}
        if pd.notna(row.get('w_svpt', np.nan)) and row.get('w_svpt', 0) > 0:
            svpt = row['w_svpt']
            w_serve_dict = {
                'ace_pct': row['w_ace'] / svpt if pd.notna(row.get('w_ace')) else np.nan,
                'df_pct': row['w_df'] / svpt if pd.notna(row.get('w_df')) else np.nan,
                'first_pct': row['w_1stIn'] / svpt if pd.notna(row.get('w_1stIn')) else np.nan,
                'first_won_pct': row['w_1stWon'] / row['w_1stIn'] if pd.notna(row.get('w_1stWon')) and row.get('w_1stIn', 0) > 0 else np.nan,
                'second_won_pct': row['w_2ndWon'] / (svpt - row['w_1stIn']) if pd.notna(row.get('w_2ndWon')) and (svpt - row.get('w_1stIn', svpt)) > 0 else np.nan,
                'bp_saved_pct': row['w_bpSaved'] / row['w_bpFaced'] if pd.notna(row.get('w_bpSaved')) and row.get('w_bpFaced', 0) > 0 else np.nan,
            }
        if pd.notna(row.get('l_svpt', np.nan)) and row.get('l_svpt', 0) > 0:
            svpt = row['l_svpt']
            l_serve_dict = {
                'ace_pct': row['l_ace'] / svpt if pd.notna(row.get('l_ace')) else np.nan,
                'df_pct': row['l_df'] / svpt if pd.notna(row.get('l_df')) else np.nan,
                'first_pct': row['l_1stIn'] / svpt if pd.notna(row.get('l_1stIn')) else np.nan,
                'first_won_pct': row['l_1stWon'] / row['l_1stIn'] if pd.notna(row.get('l_1stWon')) and row.get('l_1stIn', 0) > 0 else np.nan,
                'second_won_pct': row['l_2ndWon'] / (svpt - row['l_1stIn']) if pd.notna(row.get('l_2ndWon')) and (svpt - row.get('l_1stIn', svpt)) > 0 else np.nan,
                'bp_saved_pct': row['l_bpSaved'] / row['l_bpFaced'] if pd.notna(row.get('l_bpSaved')) and row.get('l_bpFaced', 0) > 0 else np.nan,
            }

        form.add_match(winner_id, True, surface, date, w_serve_dict if w_serve_dict else None)
        form.add_match(loser_id, False, surface, date, l_serve_dict if l_serve_dict else None)
        h2h.add_match(winner_id, loser_id, surface)

    features_df = pd.DataFrame(match_features)
    print(f"  Computed features for {len(features_df):,} matches")
    return features_df, elo


def build_modeling_dataset(features_df, tour):
    """
    Convert winner/loser features into player1/player2 format with random assignment
    (to avoid bias from always putting winner as player 1).
    """
    rows = []
    np.random.seed(42)

    for _, feat in features_df.iterrows():
        # Randomly assign which player is p1 vs p2
        if np.random.random() < 0.5:
            # p1 = winner, p2 = loser, target = 1
            row = build_row(feat, p1_is_winner=True)
            row['target'] = 1
        else:
            # p1 = loser, p2 = winner, target = 0
            row = build_row(feat, p1_is_winner=False)
            row['target'] = 0

        row['tour'] = tour.upper()
        rows.append(row)

    return pd.DataFrame(rows)


def build_row(feat, p1_is_winner):
    """Build a feature row with p1/p2 notation."""
    if p1_is_winner:
        w, l = 'w', 'l'
        p1_name, p2_name = feat['winner_name'], feat['loser_name']
        p1_id, p2_id = feat['winner_id'], feat['loser_id']
        p1_rank, p2_rank = feat.get('winner_rank'), feat.get('loser_rank')
    else:
        w, l = 'l', 'w'
        p1_name, p2_name = feat['loser_name'], feat['winner_name']
        p1_id, p2_id = feat['loser_id'], feat['winner_id']
        p1_rank, p2_rank = feat.get('loser_rank'), feat.get('winner_rank')

    row = {
        'p1_name': p1_name, 'p2_name': p2_name,
        'p1_id': p1_id, 'p2_id': p2_id,
        'date': feat['date'],
        'surface': feat['surface'],
        'tourney_level': feat['tourney_level'],
        'round': feat['round'],
        'best_of': feat['best_of'],
        'level': feat['level'],
        'p1_rank': p1_rank, 'p2_rank': p2_rank,

        # ELO
        'p1_elo': feat[f'{w}_elo'],
        'p2_elo': feat[f'{l}_elo'],
        'elo_diff': feat[f'{w}_elo'] - feat[f'{l}_elo'],
        'p1_elo_surface': feat[f'{w}_elo_surface'],
        'p2_elo_surface': feat[f'{l}_elo_surface'],
        'elo_surface_diff': feat[f'{w}_elo_surface'] - feat[f'{l}_elo_surface'],

        # Form
        'p1_form_10': feat[f'{w}_form_10'],
        'p2_form_10': feat[f'{l}_form_10'],
        'form_diff': (feat[f'{w}_form_10'] or 0) - (feat[f'{l}_form_10'] or 0) if pd.notna(feat.get(f'{w}_form_10')) and pd.notna(feat.get(f'{l}_form_10')) else np.nan,
        'p1_form_5': feat[f'{w}_form_5'],
        'p2_form_5': feat[f'{l}_form_5'],
        'p1_surface_form': feat[f'{w}_surface_form'],
        'p2_surface_form': feat[f'{l}_surface_form'],

        # Fatigue
        'p1_days_since': feat[f'{w}_days_since'],
        'p2_days_since': feat[f'{l}_days_since'],
        'p1_matches_14d': feat[f'{w}_matches_14d'],
        'p2_matches_14d': feat[f'{l}_matches_14d'],
        'p1_matches_30d': feat[f'{w}_matches_30d'],
        'p2_matches_30d': feat[f'{l}_matches_30d'],

        # Serve stats (rolling avg)
        'p1_ace_pct': feat[f'{w}_avg_ace_pct'],
        'p2_ace_pct': feat[f'{l}_avg_ace_pct'],
        'p1_1st_pct': feat[f'{w}_avg_1st_pct'],
        'p2_1st_pct': feat[f'{l}_avg_1st_pct'],
        'p1_1st_won': feat[f'{w}_avg_1st_won'],
        'p2_1st_won': feat[f'{l}_avg_1st_won'],
        'p1_2nd_won': feat[f'{w}_avg_2nd_won'],
        'p2_2nd_won': feat[f'{l}_avg_2nd_won'],
        'p1_bp_saved': feat[f'{w}_avg_bp_saved'],
        'p2_bp_saved': feat[f'{l}_avg_bp_saved'],
        'p1_df_pct': feat[f'{w}_avg_df_pct'],
        'p2_df_pct': feat[f'{l}_avg_df_pct'],

        # H2H
        'h2h_p1_rate': feat['h2h_w_rate'] if p1_is_winner else (1 - feat['h2h_w_rate'] if pd.notna(feat['h2h_w_rate']) else np.nan),
        'h2h_total': feat['h2h_total'],

        # Experience
        'p1_career_matches': feat[f'{w}_career_matches'],
        'p2_career_matches': feat[f'{l}_career_matches'],
        'p1_surface_matches': feat[f'{w}_surface_matches'],
        'p2_surface_matches': feat[f'{l}_surface_matches'],
    }

    # Derived features
    p1_rank_val = row['p1_rank'] if pd.notna(row['p1_rank']) else 500
    p2_rank_val = row['p2_rank'] if pd.notna(row['p2_rank']) else 500
    row['rank_diff'] = p2_rank_val - p1_rank_val  # positive = p1 is higher ranked
    row['rank_ratio'] = np.log(p2_rank_val / p1_rank_val) if p1_rank_val > 0 and p2_rank_val > 0 else 0

    # Surface encoding
    row['is_clay'] = 1 if feat['surface'] == 'clay' else 0
    row['is_grass'] = 1 if feat['surface'] == 'grass' else 0
    row['is_hard'] = 1 if feat['surface'] == 'hard' else 0

    # Best of 5 flag
    row['is_bo5'] = 1 if feat['best_of'] == 5 else 0

    return row


def main():
    print("=" * 60)
    print("TENNIS FEATURE ENGINEERING")
    print("=" * 60)

    all_features = []

    for tour in ['atp', 'wta']:
        features_df, elo_system = compute_features_from_sackmann(tour)

        # Save raw features
        features_df.to_csv(os.path.join(PROCESSED_DIR, f'{tour}_match_features.csv'), index=False)
        print(f"  Saved {tour}_match_features.csv")

        # Build modeling dataset
        model_df = build_modeling_dataset(features_df, tour)
        model_df.to_csv(os.path.join(PROCESSED_DIR, f'{tour}_model_features.csv'), index=False)
        print(f"  Saved {tour}_model_features.csv ({len(model_df):,} rows)")

        all_features.append(model_df)

        # ELO summary
        top_players = sorted(elo_system.ratings.items(), key=lambda x: x[1], reverse=True)[:20]
        print(f"\n  Top 20 {tour.upper()} players by ELO:")
        for pid, rating in top_players:
            matches = elo_system.match_counts[pid]
            print(f"    {pid}: {rating:.0f} ({matches} matches)")

    # Combined dataset
    combined = pd.concat(all_features, ignore_index=True)
    combined.to_csv(os.path.join(PROCESSED_DIR, 'combined_model_features.csv'), index=False)
    print(f"\n  Saved combined_model_features.csv ({len(combined):,} rows)")

    # Feature coverage summary
    print("\n--- FEATURE COVERAGE ---")
    feature_cols = [c for c in combined.columns if c not in ['p1_name', 'p2_name', 'p1_id', 'p2_id', 'date', 'target', 'tour', 'level', 'tourney_level', 'round', 'surface']]
    for col in sorted(feature_cols):
        pct = combined[col].notna().mean() * 100
        print(f"  {col}: {pct:.1f}% non-null")


if __name__ == '__main__':
    main()
