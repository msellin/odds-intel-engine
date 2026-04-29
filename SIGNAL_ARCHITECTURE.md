# OddsIntel — Signal Architecture

> Authoritative reference for every signal we collect, store, and use for ML training.
> Last updated: 2026-04-29 — added SIG-7 through SIG-11, corrected all status entries

---

## Core Principle

A signal is any piece of information that is:
1. Available before the match ends
2. Potentially predictive of outcome or market edge
3. Independent enough from other signals to add information

We do not decide upfront which signals matter. We collect everything, store it with the time it was captured, and let accumulated match outcomes teach the model which signals have predictive power.

---

## Signal Inventory

### Group 1 — Model Signals (probability estimates)

| Signal | Where stored | When written | Status |
|--------|-------------|-------------|--------|
| `poisson_prob` | `predictions` (source='poisson') | Morning pipeline | ✅ Running |
| `xgboost_prob` | `predictions` (source='xgboost') | Morning pipeline | ✅ Running |
| `af_pred_prob` | `predictions` (source='af') | Morning pipeline | ✅ Running |
| `ensemble_prob` | `predictions` (source='ensemble') | Morning pipeline | ✅ Running |
| `model_disagreement` | `simulated_bets` + `match_feature_vectors` | Morning pipeline | ✅ Running |

Data tier system:
- **Tier A**: team in targets_v9.csv (European leagues) — Poisson + XGBoost available
- **Tier B**: team in targets_global.csv (global ELO dataset) — Poisson only
- ~~Tier C~~: removed (was Sofascore on-demand, now dropped)
- **Tier D**: no historical data — AF prediction only (ensemble = AF directly)

---

### Group 2 — Market Signals (what bookmakers think)

| Signal | Signal name in match_signals | When written | Status |
|--------|------------------------------|-------------|--------|
| Opening implied prob (home) | `market_implied_home` | Morning pipeline | ✅ Running |
| Opening implied prob (draw) | `market_implied_draw` | Morning pipeline | ✅ Running |
| Opening implied prob (away) | `market_implied_away` | Morning pipeline | ✅ Running |
| Bookmaker disagreement (max−min implied) | `bookmaker_disagreement` | Morning pipeline | ✅ Running |
| Overnight line move (yesterday close → today open) | `overnight_line_move` | Morning pipeline | ✅ Running |
| Odds drift (open → now, implied prob delta) | `odds_drift` | On bets (simulated_bets) | ✅ Running |
| Steam move flag (>3% drift) | `steam_move` | On bets | ✅ Running |
| Odds volatility (std of implied prob, 24h) | `odds_volatility` | Morning pipeline | ✅ Running |
| CLV (closing line value) | `pseudo_clv_home/draw/away` on `matches` | Settlement | ✅ Running |

> `odds_drift` and `steam_move` are currently stored on `simulated_bets` and `match_feature_vectors`, not in `match_signals`. Future: move to match_signals for all matches.

---

### Group 3 — Team Quality Signals

| Signal | Signal name in match_signals | When written | Status |
|--------|------------------------------|-------------|--------|
| ELO home | `elo_home` | Morning pipeline | ✅ Running |
| ELO away | `elo_away` | Morning pipeline | ✅ Running |
| ELO differential | `elo_diff` | Morning pipeline | ✅ Running |
| Form PPG (10-match rolling) home | `form_ppg_home` | Morning pipeline | ✅ Running |
| Form PPG (10-match rolling) away | `form_ppg_away` | Morning pipeline | ✅ Running |
| Form slope (PPG last-5 minus PPG prior-5) home | `form_slope_home` | Morning pipeline | ✅ Running |
| Form slope away | `form_slope_away` | Morning pipeline | ✅ Running |
| Season goals for avg home | `goals_for_avg_home` | Morning pipeline (Tier A only) | ✅ Running |
| Season goals against avg home | `goals_against_avg_home` | Morning pipeline (Tier A only) | ✅ Running |
| Season goals for avg away | `goals_for_avg_away` | Morning pipeline (Tier A only) | ✅ Running |
| Season goals against avg away | `goals_against_avg_away` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals for — home team at home | `goals_for_venue_home` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals against — home team at home | `goals_against_venue_home` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals for — away team at away | `goals_for_venue_away` | Morning pipeline (Tier A only) | ✅ Running |
| Venue goals against — away team at away | `goals_against_venue_away` | Morning pipeline (Tier A only) | ✅ Running |
| League position (normalised rank) home | `league_position_home` | Morning pipeline | ✅ Running |
| League position away | `league_position_away` | Morning pipeline | ✅ Running |
| Points to title home | `points_to_title_home` | Morning pipeline | ✅ Running |
| Points to title away | `points_to_title_away` | Morning pipeline | ✅ Running |
| Points to relegation home | `points_to_relegation_home` | Morning pipeline | ✅ Running |
| Points to relegation away | `points_to_relegation_away` | Morning pipeline | ✅ Running |
| H2H home win pct (last 10 meetings) | `h2h_win_pct` | Morning pipeline | ✅ Running |
| H2H total meetings | `h2h_total` | Morning pipeline | ✅ Running |
| Rest days home | `rest_days_home` | Morning pipeline | ✅ Running |
| Rest days away | `rest_days_away` | Morning pipeline | ✅ Running |

**Not yet built:**
- `xg_proxy_home/away` (shots-based xG estimate) — needs match_stats from prior matches
- `h2h_avg_goals` — only win_pct + total built so far

---

### Group 4 — Information Signals (real-world events, priced slowly by market)

| Signal | Signal name | Where stored | When written | Status |
|--------|-------------|-------------|-------------|--------|
| News impact score | `news_impact_score` | `match_signals` + `simulated_bets` | News checker (4×/day) | ✅ Running |
| Injury count home | `injury_count_home` | `match_signals` | Morning pipeline | ✅ Running |
| Injury count away | `injury_count_away` | `match_signals` | Morning pipeline | ✅ Running |
| Players out home | `players_out_home` | `match_signals` | Morning pipeline | ✅ Running |
| Players out away | `players_out_away` | `match_signals` | Morning pipeline | ✅ Running |
| Lineup confirmed | `lineup_confirmed` | `simulated_bets` | News checker | ✅ Running |
| Lineup confidence | `lineup_confidence` | `simulated_bets` | News checker | ✅ Running |

**Not yet built:**
- `key_player_missing` — boolean, requires player importance weighting (P3.3, deprioritised)
- `players_doubtful_home/away` — Questionable status tracked in match_injuries but not yet a signal

---

### Group 5 — Context Signals (situational factors)

| Signal | Signal name in match_signals | When written | Status |
|--------|------------------------------|-------------|--------|
| Referee cards per game | `referee_cards_avg` | Morning pipeline | ✅ Running |
| Referee home win pct | `referee_home_win_pct` | Morning pipeline | ✅ Running |
| Referee over 2.5 pct | `referee_over25_pct` | Morning pipeline | ✅ Running |
| Fixture importance (max urgency, 0–1) | `fixture_importance` | Morning pipeline | ✅ Running |
| Fixture importance home team | `fixture_importance_home` | Morning pipeline | ✅ Running |
| Fixture importance away team | `fixture_importance_away` | Morning pipeline | ✅ Running |
| Importance asymmetry (home − away urgency) | `importance_diff` | Morning pipeline | ✅ Running |
| League home win pct (last 200 finished) | `league_home_win_pct` | Morning pipeline | ✅ Running |
| League draw pct | `league_draw_pct` | Morning pipeline | ✅ Running |
| League avg goals | `league_avg_goals` | Morning pipeline | ✅ Running |

**Not yet built:**
- `is_derby` / `travel_distance` — needs team location data
- `venue_altitude` — needs venue metadata
- `is_cup` — fixture metadata partially available, not wired

---

### Group 6 — Live Signals (in-play, updated every 5 minutes)

| Signal | Where stored | Status |
|--------|-------------|--------|
| `live_score_home/away` | `live_match_snapshots` | ✅ Running |
| `live_minute` | `live_match_snapshots` | ✅ Running |
| `live_shots_home/away` | `live_match_snapshots` | ✅ Running |
| `live_xg_home/away` | `live_match_snapshots` | ✅ Running |
| `live_possession_home` | `live_match_snapshots` | ✅ Running |
| `live_odds` | `odds_snapshots` (is_live=true) | ✅ Running |
| `live_red_cards` | `match_events` | ✅ Running |
| `live_goals` | `match_events` | ✅ Running |

---

## Signal Timeline Per Match

```
T-24h   Fixtures published (AF)
T-16h   Morning pipeline runs (08:00 UTC):
          → Group 1: Model signals (Poisson, XGBoost, AF prediction, ensemble)
          → Group 2: Opening market odds + bookmaker_disagreement + overnight_line_move + odds_volatility
          → Group 3: ELO, form PPG, form slope, season stats, venue splits,
                     standings signals, H2H, rest days
          → Group 4: Injury counts
          → Group 5: Referee stats, fixture importance + asymmetry, league meta
T-14h   Odds snapshot #1
T-12h   Odds snapshot #2 + news scan #2 (news_impact_score update)
T-8h    Odds snapshot #3
T-6h    Odds snapshot #4 + news scan #3
T-4h    Odds snapshot #5
T-2h    Odds snapshot #6
T-1h    Lineups published → lineup_confirmed signal
T-30m   Final news scan #4
T-0h    Match kicks off
T+5m    Live tracker starts (every 5min) → Group 6 signals
T+FT    Settlement: result recorded, pseudo_clv computed
T+1h    Post-match enrichment: T4/T8/T12
```

---

## Storage

### `match_signals` table (append-only EAV)
One row per `(match_id, signal_name, captured_at)`. Same signal gets a new row each time it's updated. ML training query uses value closest to kickoff.

```
match_id | signal_name          | signal_value | signal_group  | data_source | captured_at
---------|----------------------|-------------|---------------|-------------|-------------
<uuid>   | elo_diff             | 85.3         | quality       | derived     | 2026-04-29T08:01Z
<uuid>   | news_impact_score    | -0.4         | information   | gemini      | 2026-04-29T09:05Z
<uuid>   | odds_volatility      | 0.003        | market        | derived     | 2026-04-29T08:01Z
```

### `predictions` table
One row per `(match_id, market, source)`.

```
(match_id, '1x2_home', 'poisson')   ← Poisson probability
(match_id, '1x2_home', 'xgboost')   ← XGBoost probability
(match_id, '1x2_home', 'af')         ← AF /predictions
(match_id, '1x2_home', 'ensemble')   ← Consensus
```

### `match_feature_vectors` table (wide ML training table)
One row per finished match. Materialized nightly by `build_match_feature_vectors()` in settlement. 36+ columns covering all signal groups.

### `matches` table
`pseudo_clv_home/draw/away` — closing line value for every finished match. Computed by settlement. Primary ML training target.

---

## How Signals Flow into the Model

```
Morning pipeline
    │
    ├─ Group 1: Poisson + XGBoost + AF → predictions table
    │           ensemble_prob = calibrated blend
    │
    ├─ Group 2-5: match_signals (EAV, ~25 signals per match)
    │
    └─ Edge calculation:
           calibrated_prob = α × model_prob + (1-α) × market_implied
           α = {T1: 0.20, T2: 0.30, T3: 0.50, T4: 0.65}
           edge = calibrated_prob - (1 / odds)
           kelly = (calibrated_prob × odds - 1) / (odds - 1)
           stake = min(kelly × 0.15 × bankroll, 0.01 × bankroll) × data_tier_mult

Settlement (nightly)
    │
    ├─ pseudo_clv = (1/open_odds) / (1/close_odds) - 1  [all ~280 matches]
    │
    └─ match_feature_vectors ETL:
           wide row per match, pivoting match_signals + predictions + ELO + form
           → ML training table

Meta-model (Phase 1 ~May 9, Phase 2 ~June)
    │
    └─ Logistic regression on match_feature_vectors
           Target: pseudo_clv > 0 (was this bet +EV?)
           Features (META-2 design — market structure gaps only):
                     edge (ensemble_prob − market_implied_home),
                     odds_drift, bookmaker_disagreement, overnight_line_move,
                     model_disagreement, league_tier,
                     news_impact_score, odds_volatility
           Note: raw ELO/form excluded — market already priced those in
```

---

## Signal Count Per Match (as of 2026-04-29)

| Group | Signals | Notes |
|-------|---------|-------|
| Group 1 (model) | 4 | poisson, xgboost, af, ensemble |
| Group 2 (market) | 8 | implied probs ×3, bdm, olm, volatility, drift, clv |
| Group 3 (quality) | 22 | ELO ×3, form ×4, goals ×8, standings ×6, H2H ×2, rest ×2 (some Tier A only) |
| Group 4 (information) | 6 | news, injuries ×4, lineup ×2 |
| Group 5 (context) | 10 | referee ×3, importance ×3, league meta ×3, importance_diff |
| Group 6 (live) | 8 | score, minute, shots, xg, possession, live_odds, cards, goals |
| **Total** | **~58** | |

---

## Open Gaps

Some signals are planned but not yet built. For task status and priority, see **PRIORITY_QUEUE.md** (single source of truth for all tasks).

Relevant queue IDs: PIN-1 (Pinnacle anchor), SIG-12 (xG overperformance), MOD-2 (learned blend weights), SIG-DERBY (is-derby/travel), P3.3 (player injury weighting).
