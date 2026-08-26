#!/usr/bin/env python3
"""
Tennis point-level Markov model backtest vs Pinnacle closing odds.

Model:
  P(server wins a point) from rolling 30-match serve stats
  → Markov chain: point → game → set → match
  → Compare match probability vs Pinnacle de-vigged prob
  → Compute ROI at various edge thresholds

Training data: Sackmann ATP CSVs (2000-2024), serve stats in all tour-level files
Odds data:     tennis-data.co.uk Excel files (2005-2024), PSW/PSL = Pinnacle odds

Literature baseline: Knottenbelt et al. (2012) — 3.8% ROI on ATP 2011 Grand Slams
"""

from __future__ import annotations
import glob
from pathlib import Path
from functools import lru_cache
import pandas as pd
import numpy as np
from collections import defaultdict

DATA_DIR = Path("data/raw/tennis")
SERVE_WINDOW    = 30      # rolling matches for serve stat average
MIN_MATCHES     = 10      # minimum history before trusting serve rate
DEFAULT_SERVE   = 0.630   # ATP tour average serve point win rate (fallback)
BACKTEST_YEARS  = range(2022, 2025)
TRAINING_YEARS  = range(2000, 2022)


# ─────────────────────────────────────────────────────────────────────
# Name normalisation: "Taylor Fritz" → "Fritz T."
# ─────────────────────────────────────────────────────────────────────

def normalize_name(full_name: str) -> str:
    """Convert 'Firstname [Middle] Lastname' → 'Lastname F.'"""
    parts = str(full_name).strip().split()
    if len(parts) == 0:
        return full_name
    # Handle compound surnames: "Alex De Minaur" → "De Minaur A."
    # Rule: if there are ≥3 parts and the second-to-last is a lowercase preposition, keep it
    PREPOSITIONS = {'de', 'del', 'van', 'von', 'der', 'dos', 'di', 'da', 'le', 'la', 'lo'}
    if len(parts) >= 3 and parts[-2].lower() in PREPOSITIONS:
        surname = parts[-2].capitalize() + ' ' + parts[-1]
    else:
        surname = parts[-1]
    initial = parts[0][0].upper() + '.'
    return f"{surname} {initial}"


def build_name_map(sack_names: set[str], odds_names: set[str]) -> dict[str, str]:
    """Build a mapping from normalized Sackmann name → odds file name."""
    nmap: dict[str, str] = {}
    for name in sack_names:
        norm = normalize_name(name)
        if norm in odds_names:
            nmap[name] = norm
            continue
        # Try without preposition capitalisation
        parts = str(name).strip().split()
        if len(parts) >= 2:
            alt = parts[-1] + ' ' + parts[0][0].upper() + '.'
            if alt in odds_names:
                nmap[name] = alt
    return nmap


# ─────────────────────────────────────────────────────────────────────
# Markov chain formulas
# ─────────────────────────────────────────────────────────────────────

def p_game(p: float) -> float:
    """P(server wins game) given per-point serve win probability p."""
    if p <= 0: return 0.0
    if p >= 1: return 1.0
    q = 1 - p
    # P(win at deuce, using infinite series)
    denom = p * p + q * q
    p_deuce_win = (p * p / denom) if denom > 0 else 0.5
    # Ways to win before deuce + way through deuce
    return p**4 * (1 + 4*q + 10*q**2) + 20 * p**3 * q**3 * p_deuce_win


def p_set_given_first_server(pg_serve: float, pg_return: float) -> float:
    """
    P(first-serving player wins set) using DP over game scores.
    pg_serve  = P(first-server wins game on their own serve)
    pg_return = P(first-server wins game on opponent's serve)
    Tiebreak at 6-6: approximated as a single game with pg_serve probability.
    """
    memo: dict = {}

    def dp(ga: int, gb: int, first_serves: bool) -> float:
        # ga = games won by first-server, gb = games won by opponent
        if ga == 7 or (ga == 6 and gb <= 4):
            return 1.0
        if gb == 7 or (gb == 6 and ga <= 4):
            return 0.0
        if ga == 6 and gb == 6:
            # Tiebreak — use serve-weighted approximation
            # Server at tiebreak start has a slight edge
            return (pg_serve + pg_return) / 2 + 0.02  # small serve-first edge
        key = (ga, gb, first_serves)
        if key in memo:
            return memo[key]
        p_win_game = pg_serve if first_serves else pg_return
        val = (p_win_game * dp(ga + 1, gb, not first_serves) +
               (1 - p_win_game) * dp(ga, gb + 1, not first_serves))
        memo[key] = val
        return val

    return dp(0, 0, True)


def p_match(serve_a: float, serve_b: float, best_of: int = 3) -> float:
    """
    P(player A wins match).
    serve_a = P(A wins a point on A's serve)
    serve_b = P(B wins a point on B's serve)
    best_of = 3 or 5
    """
    pg_a  = p_game(serve_a)        # P(A wins game | A serving)
    pg_ba = 1.0 - p_game(serve_b)  # P(A wins game | B serving)

    # Set probability averaged over who serves first (50/50 since coin toss)
    p_set_a_first = p_set_given_first_server(pg_a, pg_ba)
    # If B serves first: P(A wins set) = 1 - P(B wins set | B serves first)
    pg_b  = p_game(serve_b)
    pg_ab = 1.0 - p_game(serve_a)
    p_set_b_first = 1.0 - p_set_given_first_server(pg_b, pg_ab)
    p_s = (p_set_a_first + p_set_b_first) / 2.0

    # Independent sets model for match
    sets_needed = (best_of + 1) // 2
    if sets_needed == 2:
        # Best of 3: p_s^2 * (3 - 2*p_s)
        return p_s**2 * (3 - 2 * p_s)
    else:
        # Best of 5
        q_s = 1 - p_s
        return (p_s**3 * (1 + 3*q_s + 6*q_s**2))


# ─────────────────────────────────────────────────────────────────────
# Rolling serve stats builder
# ─────────────────────────────────────────────────────────────────────

def load_sackmann_tour(years) -> pd.DataFrame:
    """Load Sackmann tour-level ATP CSVs (no challengers/quals)."""
    dfs = []
    for yr in years:
        p = DATA_DIR / f"atp_matches_{yr}.csv"
        if p.exists():
            df = pd.read_csv(p, low_memory=False)
            df['year'] = yr
            dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df['tourney_date'] = pd.to_datetime(df['tourney_date'], format='%Y%m%d', errors='coerce')
    df = df.sort_values('tourney_date').reset_index(drop=True)
    return df


def build_serve_history(df: pd.DataFrame) -> dict:
    """
    Build a per-player, per-surface rolling serve history.
    Returns: {(player_name, surface): [serve_win_rate_per_match, ...]}
    stored in chronological order so we can take the last N.
    Also stores 'ALL' surface key for fallback.
    """
    history: dict[tuple, list] = defaultdict(list)

    for _, row in df.iterrows():
        w, l = row.get('winner_name'), row.get('loser_name')
        surface = str(row.get('surface', 'Hard'))

        for player, prefix in [(w, 'w'), (l, 'l')]:
            if not isinstance(player, str):
                continue
            svpt = row.get(f'{prefix}_svpt')
            won1 = row.get(f'{prefix}_1stWon')
            won2 = row.get(f'{prefix}_2ndWon')
            try:
                svpt, won1, won2 = float(svpt), float(won1), float(won2)
                if svpt > 0 and not np.isnan(won1) and not np.isnan(won2):
                    rate = (won1 + won2) / svpt
                    if 0.3 <= rate <= 0.9:   # sanity gate
                        history[(player, surface)].append(rate)
                        history[(player, 'ALL')].append(rate)
            except (TypeError, ValueError):
                pass

    return dict(history)


def get_serve_rate(history: dict, player: str, surface: str, before_idx: int) -> float:
    """Get rolling serve win rate for player up to before_idx matches."""
    for key in [(player, surface), (player, 'ALL')]:
        records = history.get(key, [])
        recent = records[:before_idx][-SERVE_WINDOW:]
        if len(recent) >= MIN_MATCHES:
            return float(np.mean(recent))
    return DEFAULT_SERVE


# ─────────────────────────────────────────────────────────────────────
# Pre-compute cumulative serve stats per player
# (use chronological index per player to avoid lookahead)
# ─────────────────────────────────────────────────────────────────────

def build_cumulative_history(df: pd.DataFrame) -> dict:
    """
    Returns {(player, surface): [rate1, rate2, ...]} in chronological order.
    Each entry is the serve rate from that match — index N means player has
    played N matches total on this surface; last SERVE_WINDOW of those is used.
    """
    return build_serve_history(df)


# ─────────────────────────────────────────────────────────────────────
# Backtest
# ─────────────────────────────────────────────────────────────────────

def load_odds_files(years) -> pd.DataFrame:
    dfs = []
    for yr in years:
        p = DATA_DIR / f"tennis_odds_{yr}.xlsx"
        if not p.exists():
            continue
        df = pd.read_excel(p)
        df['Year'] = yr
        dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    df = df.dropna(subset=['Date', 'Winner', 'Loser'])
    df = df[df['Comment'].str.strip() == 'Completed']
    return df.sort_values('Date').reset_index(drop=True)


def rank_prob(rank_w: float, rank_l: float, scale: float = 0.65) -> float:
    """P(W wins) from ATP rankings using calibrated logistic (scale=0.65 from prior backtest)."""
    if rank_w <= 0 or rank_l <= 0 or np.isnan(rank_w) or np.isnan(rank_l):
        return 0.5
    log_ratio = np.log(rank_l / rank_w)   # positive = W is better-ranked
    return 1.0 / (1.0 + np.exp(-scale * log_ratio))


def blend(markov: float, ranking: float, alpha: float) -> float:
    """alpha * markov + (1-alpha) * ranking"""
    return alpha * markov + (1 - alpha) * ranking


def run_backtest():
    print("=" * 70)
    print("TENNIS MARKOV BACKTEST — point-level serve model vs Pinnacle")
    print("=" * 70)

    # ── Load all Sackmann data for serve history ──
    print("\nLoading Sackmann ATP data (2000–2024)...")
    all_years = list(range(2000, 2025))
    sack_df = load_sackmann_tour(all_years)
    if sack_df.empty:
        print("ERROR: no Sackmann data found in data/raw/tennis/")
        return
    print(f"  {len(sack_df):,} tour-level matches loaded")

    # ── Build name map ──
    odds_test = load_odds_files(BACKTEST_YEARS)
    if odds_test.empty:
        print("ERROR: no odds files found")
        return

    sack_names = (set(sack_df['winner_name'].dropna()) |
                  set(sack_df['loser_name'].dropna()))
    odds_names = (set(odds_test['Winner'].dropna()) |
                  set(odds_test['Loser'].dropna()))
    name_map = build_name_map(sack_names, odds_names)
    print(f"  Name map: {len(name_map):,} players resolved "
          f"({len(name_map)/max(len(sack_names),1):.1%} of Sackmann players)")

    # ── Build cumulative serve history ──
    print("Building rolling serve history...")
    # Sackmann names → we need a way to look up their history when processing test matches
    # Map: (sackmann_name, surface) → list of per-match serve rates in order
    history = build_serve_history(sack_df)

    # How many training matches per player before backtest window
    # We need to know how many matches each player had before 2022
    # to correctly index into history without lookahead
    # Approach: separate pre-2022 history count from post-2022 to avoid lookahead
    pre_test_df = sack_df[sack_df['year'] < BACKTEST_YEARS.start]
    pre_history = build_serve_history(pre_test_df)

    # ── For the backtest, we process chronologically and update history ──
    # Start: pre_history. As we go through test matches, we update.
    live_history: dict[tuple, list] = {k: list(v) for k, v in pre_history.items()}

    # ── Build name→Sackmann reverse map ──
    # odds_name → sackmann_name (for lookup in live_history)
    odds_to_sack: dict[str, str] = {v: k for k, v in name_map.items()}

    # ── Load test odds and process ──
    results = []
    for _, row in odds_test.iterrows():
        winner_odds = row.get('Winner')  # odds name
        loser_odds  = row.get('Loser')
        psw = row.get('PSW')   # Pinnacle winner odds
        psl = row.get('PSL')   # Pinnacle loser odds
        surface = str(row.get('Surface', 'Hard'))
        best_of = int(row.get('Best of', 3)) if not pd.isna(row.get('Best of', 3)) else 3
        series  = str(row.get('Series', ''))

        if pd.isna(psw) or pd.isna(psl) or psw <= 1.0 or psl <= 1.0:
            continue

        # Pinnacle de-vigged probabilities
        pin_sum   = 1/psw + 1/psl
        pin_true_w = (1/psw) / pin_sum
        pin_true_l = (1/psl) / pin_sum
        pin_margin = pin_sum - 1

        # Rankings
        rank_w_val = row.get('WRank')
        rank_l_val = row.get('LRank')
        rp_w = rank_prob(
            float(rank_w_val) if not pd.isna(rank_w_val) else 0,
            float(rank_l_val) if not pd.isna(rank_l_val) else 0
        )

        # Look up Sackmann names
        w_sack = odds_to_sack.get(winner_odds)
        l_sack = odds_to_sack.get(loser_odds)

        if not w_sack or not l_sack:
            # Can still use ranking model even without serve stats
            markov_prob_w = rp_w   # fall back to rank model if no serve data
            serve_w = serve_l = DEFAULT_SERVE
            markov_only = False
        else:
            # Get serve rates from history BEFORE this match
            serve_w = get_serve_rate(live_history, w_sack, surface,
                                     len(live_history.get((w_sack, surface), [])))
            serve_l = get_serve_rate(live_history, l_sack, surface,
                                     len(live_history.get((l_sack, surface), [])))
            markov_prob_w = p_match(serve_w, serve_l, best_of)
            markov_only = True

        results.append({
            'winner': winner_odds, 'loser': loser_odds,
            'surface': surface, 'series': series, 'best_of': best_of,
            'psw': psw, 'psl': psl,
            'pin_margin': pin_margin,
            'pin_true_w': pin_true_w,
            'pin_true_l': pin_true_l,
            'rank_prob_w': rp_w,
            'serve_w': serve_w, 'serve_l': serve_l,
            'markov_prob_w': markov_prob_w,
            'has_serve': markov_only,
        })

        # Update live history with this match result (to build rolling stats)
        # Find this match in sack_df to get actual serve stats
        match_rows = sack_df[
            (sack_df['winner_name'] == w_sack) &
            (sack_df['loser_name'] == l_sack) &
            (sack_df['year'].isin(BACKTEST_YEARS))
        ]
        if not match_rows.empty:
            r = match_rows.iloc[0]
            for player, prefix in [(w_sack, 'w'), (l_sack, 'l')]:
                svpt = r.get(f'{prefix}_svpt')
                won1 = r.get(f'{prefix}_1stWon')
                won2 = r.get(f'{prefix}_2ndWon')
                try:
                    svpt, won1, won2 = float(svpt), float(won1), float(won2)
                    if svpt > 0 and 0.3 <= (won1+won2)/svpt <= 0.9:
                        rate = (won1+won2)/svpt
                        live_history.setdefault((player, surface), []).append(rate)
                        live_history.setdefault((player, 'ALL'), []).append(rate)
                except (TypeError, ValueError):
                    pass

    if not results:
        print("No backtest results — check data and name join")
        return

    rdf = pd.DataFrame(results)
    n_with_serve = rdf['has_serve'].sum()
    print(f"\n  {len(rdf):,} total matches with Pinnacle odds")
    print(f"  {n_with_serve:,} with serve stats joined ({n_with_serve/len(rdf):.1%})")
    print(f"  Avg Pinnacle margin: {rdf['pin_margin'].mean():.2%}")

    # ── Accuracy comparison ──
    markov_acc = (rdf['markov_prob_w'] > 0.5).mean()
    rank_acc   = (rdf['rank_prob_w'] > 0.5).mean()
    pin_acc    = (rdf['pin_true_w'] > 0.5).mean()
    print(f"\n  Accuracy — picks favourite correctly:")
    print(f"    Markov (pure serve): {markov_acc:.1%}")
    print(f"    Ranking logistic:    {rank_acc:.1%}")
    print(f"    Pinnacle:            {pin_acc:.1%}")

    def roi_at_threshold(prob_w_col: pd.Series, threshold: float) -> tuple:
        """Compute ROI for bets where edge = prob_w - pin_true_w or prob_l - pin_true_l >= threshold."""
        bets_w = rdf[(prob_w_col - rdf['pin_true_w']) >= threshold]
        bets_l = rdf[((1 - prob_w_col) - rdf['pin_true_l']) >= threshold]
        pnl_w = bets_w.apply(lambda r: r['psw'] - 1, axis=1)  # winner won → collect psw-1
        pnl_l = bets_l.apply(lambda _: -1.0, axis=1)           # loser always lost
        all_pnl = pd.concat([pnl_w, pnl_l])
        n = len(pnl_w) + len(pnl_l)
        if n == 0:
            return 0.0, 0
        return all_pnl.sum() / n, n

    # ── Blend sweep ──
    print(f"\n{'='*70}")
    print("BLEND SWEEP — alpha*Markov + (1-alpha)*Ranking")
    print(f"{'='*70}")
    print(f"  alpha   model         {'≥0%':>8}  {'≥3%':>8}  {'≥5%':>8}  {'≥8%':>8}  {'≥10%':>8}")
    print(f"  {'─'*65}")

    best_result = None
    for alpha in [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0]:
        prob_w = rdf['rank_prob_w'] * (1 - alpha) + rdf['markov_prob_w'] * alpha
        label = f"α={alpha:.1f}"
        rois = []
        for t in [0.00, 0.03, 0.05, 0.08, 0.10]:
            roi, n = roi_at_threshold(prob_w, t)
            rois.append((roi, n))
        roi_str = '  '.join(f"{r:>+6.1%}" for r, _ in rois)
        print(f"  {label}   {roi_str}")

        # Track best configuration
        for t_idx, t in enumerate([0.00, 0.03, 0.05, 0.08, 0.10]):
            roi, n = rois[t_idx]
            if roi > 0 and n >= 20:
                if best_result is None or roi > best_result[0]:
                    best_result = (roi, n, alpha, t)

    # ── Best alpha detail breakdown ──
    best_alpha = 0.0
    best_roi_at_5 = roi_at_threshold(rdf['rank_prob_w'], 0.05)[0]
    for alpha in [0.1, 0.2, 0.3, 0.5]:
        prob_w = rdf['rank_prob_w'] * (1 - alpha) + rdf['markov_prob_w'] * alpha
        r, _ = roi_at_threshold(prob_w, 0.05)
        if r > best_roi_at_5:
            best_roi_at_5 = r
            best_alpha = alpha

    best_prob_w = rdf['rank_prob_w'] * (1 - best_alpha) + rdf['markov_prob_w'] * best_alpha
    print(f"\n--- BEST BLEND (α={best_alpha}) SURFACE BREAKDOWN (edge ≥ 5%) ---")
    bets_w = rdf[(best_prob_w - rdf['pin_true_w']) >= 0.05].copy()
    bets_l = rdf[((1 - best_prob_w) - rdf['pin_true_l']) >= 0.05].copy()
    bets_w['won'] = True; bets_w['bet_odds'] = bets_w['psw']; bets_w['surf'] = bets_w['surface']
    bets_l['won'] = False; bets_l['bet_odds'] = bets_l['psl']; bets_l['surf'] = bets_l['surface']
    all_bets = pd.concat([bets_w[['surf','series','best_of','bet_odds','won']],
                           bets_l[['surf','series','best_of','bet_odds','won']]], ignore_index=True)

    for surf in ['Hard', 'Clay', 'Grass']:
        sub = all_bets[all_bets['surf'] == surf]
        if len(sub) < 5: continue
        pnl = sub.apply(lambda r: r['bet_odds'] - 1 if r['won'] else -1, axis=1)
        print(f"  {surf:8s}  n={len(sub):4d}  WR={sub['won'].mean():.1%}  "
              f"ROI={pnl.sum()/len(sub):+.1%}")

    print(f"\n--- BEST BLEND TOURNAMENT TIER (edge ≥ 5%) ---")
    tiers = [('Grand Slam','Grand Slam'), ('Masters','Masters'), ('ATP500','ATP500'), ('ATP250','ATP250')]
    for label, kw in tiers:
        sub = all_bets[all_bets['series'].str.contains(kw, case=False, na=False)]
        if len(sub) < 5: continue
        pnl = sub.apply(lambda r: r['bet_odds'] - 1 if r['won'] else -1, axis=1)
        print(f"  {label:20s}  n={len(sub):4d}  WR={sub['won'].mean():.1%}  "
              f"ROI={pnl.sum()/len(sub):+.1%}")

    print(f"\n--- SERVE STATS ONLY MATCHES (edge ≥ 5%, has_serve=True) ---")
    serve_only = rdf[rdf['has_serve']].copy()
    serve_prob = serve_only['rank_prob_w'] * (1 - best_alpha) + serve_only['markov_prob_w'] * best_alpha
    bw = serve_only[(serve_prob - serve_only['pin_true_w']) >= 0.05]
    bl = serve_only[((1 - serve_prob) - serve_only['pin_true_l']) >= 0.05]
    pnl_bw = bw.apply(lambda r: r['psw'] - 1, axis=1)
    pnl_bl = bl.apply(lambda _: -1.0, axis=1)
    all_pnl = pd.concat([pnl_bw, pnl_bl])
    ns = len(pnl_bw) + len(pnl_bl)
    if ns > 0:
        print(f"  n={ns}  WR={len(pnl_bw)/ns:.1%}  ROI={all_pnl.sum()/ns:+.1%}  P&L={all_pnl.sum():+.1f}u")

    # ── Verdict ──
    print(f"\n{'='*70}")
    print("VERDICT")
    print(f"{'='*70}")
    if best_result:
        roi, n, alpha, threshold = best_result
        print(f"  ✅ POSITIVE ROI found at α={alpha}, edge≥{threshold*100:.0f}%: ROI={roi:+.1%}  n={n}")
        print(f"  → Serve stats ADD value when blended with rankings at α={alpha}")
        print(f"  → Next: validate on 2019-2021 hold-out, then productionize")
    else:
        print("  ❌ No profitable combination found with Markov + ranking blend")
        roi_rank, n_rank = roi_at_threshold(rdf['rank_prob_w'], 0.05)
        roi_mark, n_mark = roi_at_threshold(rdf['markov_prob_w'], 0.05)
        print(f"  Pure ranking (edge≥5%): ROI={roi_rank:+.1%}  n={n_rank}")
        print(f"  Pure Markov  (edge≥5%): ROI={roi_mark:+.1%}  n={n_mark}")
        print(f"  → Serve stats don't add edge vs Pinnacle with this approach")
        print(f"  → Consider: (1) return stats, (2) serve trend/momentum signal,")
        print(f"     (3) surface-specific Elo, (4) point-by-point slam data only")


if __name__ == '__main__':
    run_backtest()
