# OddsIntel — Signal Architecture

> How we collect, store, and use prediction signals. Every match accumulates
> a set of independent signals across time. All signals are stored with
> their timestamp and value. The ML model learns which signals matter.

---

## Core Principle

A signal is any piece of information that is:
1. Available before the match ends
2. Potentially predictive of the outcome or market edge
3. Independent enough from other signals to add information

**We do not decide upfront which signals matter.** We collect everything,
store it with the time it was captured, and let accumulated match outcomes
teach the model which signals have predictive power.

A match with 3 signals is still useful training data. A match with 8 signals
is higher confidence and more valuable for training. Both get stored.

---

## Signal Inventory

### Group 1 — Model Signals (probability estimates)
These directly output a win/draw/loss probability.

| Signal | Source | When available | Markets |
|--------|--------|---------------|---------|
| `poisson` | Our Poisson model on historical goals data | T-16h (morning pipeline) | 1X2 + O/U |
| `xgboost` | Our XGBoost classifier | T-16h (morning pipeline) | 1X2 + O/U |
| `af_prediction` | API-Football /predictions endpoint | T-16h (morning pipeline) | 1X2 only |
| `ensemble` | Weighted blend of available model signals | T-16h (morning pipeline) | 1X2 + O/U |

Data quality of model signals:
- **Tier A**: team in our targets_v9 CSV (European leagues) — Poisson + XGBoost available
- **Tier B**: team in targets_global CSV (global ELO dataset) — Poisson available
- **Tier C**: team found via Sofascore last-15 API — Poisson available, lower confidence
- **Tier D**: no historical data, AF prediction only — ensemble = AF directly

### Group 2 — Market Signals (what bookmakers think)
These measure market opinion and movement.

| Signal | Source | When available | Interpretation |
|--------|--------|---------------|---------------|
| `market_implied` | Opening odds at time of morning pipeline | T-16h | Market consensus win probability |
| `odds_drift` | Odds snapshots (every 2h) | T-16h through T-0h | Direction of sharp money |
| `odds_drift_pct` | Derived from snapshots | Rolling | Normalised drift |
| `drift_velocity` | Derived | Rolling | Speed of market movement |
| `steam_move` | Threshold flag (>3% in <2h) | On detection | Sharp money signal |
| `clv` | Closing odds vs our odds at pick | T+0h (post-match) | Did we beat the market? |

### Group 3 — Team Quality Signals (who are these teams)
These measure underlying team strength.

| Signal | Source | When available | Notes |
|--------|--------|---------------|-------|
| `elo_home` / `elo_away` | Our ELO ratings (daily update) | T-16h | Scale ~1200-1800 |
| `elo_diff` | Derived | T-16h | Single quality gap number |
| `form_home` / `form_away` | 10-match rolling (T2 stats) | T-16h | Win%, PPG, goals |
| `form_momentum` | Recent attack/defense trend | T-16h | Rising vs falling |
| `xg_proxy_home/away` | Shots-based quality estimate | T-16h | 0.10×shots + 0.22×SOT |
| `league_position` | League standings (T9) | T-16h | Normalized position |
| `points_to_relegation` | Standings | T-16h | Motivation signal |
| `points_to_title` | Standings | T-16h | Motivation signal |
| `rest_days_home/away` | Schedule | T-16h | Fatigue/advantage |
| `h2h_win_pct` | Last 10 H2H meetings (T10) | T-16h | Matchup-specific pattern |
| `h2h_avg_goals` | Last 10 H2H meetings | T-16h | O/U indicator |

### Group 4 — Information Signals (things the model can't see)
These capture real-world events that shift probabilities. Highly valuable
because they are often priced slowly into the market.

| Signal | Source | When available | Notes |
|--------|--------|---------------|-------|
| `news_impact_score` | Gemini AI news checker | T-15h, T-11h, T-7h, T-3h | -1.0 to +1.0, net team impact |
| `injury_severity_home/away` | AF T3 injuries + AI | T-16h onwards | Aggregated player importance |
| `players_out_home/away` | AF T3 injuries | T-16h onwards | Count of confirmed absences |
| `players_doubtful_home/away` | AF T3 injuries | T-16h onwards | Count of doubt cases |
| `lineup_confirmed` | AF T7 lineups (60min before KO) | T-1h | True when XI published |
| `lineup_confidence` | Gemini AI assessment | T-3h onwards | 0.0-1.0 confidence on XI |
| `key_player_missing` | AI + injuries | T-16h onwards | Boolean: star player absent? |

### Group 5 — Context Signals (situational factors)
These capture match-specific context that affects motivation and tactics.

| Signal | Source | When available | Notes |
|--------|--------|---------------|-------|
| `referee_cards_per_game` | Historical referee stats (T11/manual) | T-16h | Avg cards issued |
| `referee_home_win_pct` | Historical referee stats | T-16h | Does ref favour home? |
| `referee_over25_pct` | Historical referee stats | T-16h | O/U signal |
| `venue_altitude` | Fixture metadata | T-16h | Affects away team |
| `is_derby` | Team metadata / derived | T-16h | Derbies behave differently |
| `is_cup` | Fixture metadata | T-16h | Cup vs league motivation |
| `fixture_importance` | Derived from standings | T-16h | Title/relegation 6-pointer |
| `travel_distance` | Team location data | T-16h | Away team fatigue |
| `days_since_last_match` | Schedule | T-16h | Already have rest_days |

### Group 6 — Live Signals (in-play, change every 5 minutes)
Collected by live tracker. Used for live model updates and post-match analysis.

| Signal | Source | When available | Notes |
|--------|--------|---------------|-------|
| `live_score` | AF live | During match | Current score |
| `live_minute` | AF live | During match | Game state |
| `live_shots_home/away` | AF live | During match | Pressure indicator |
| `live_xg_home/away` | AF live | During match | Expected goals so far |
| `live_possession` | AF live | During match | Territorial dominance |
| `live_odds` | Odds snapshot is_live=true | Every 5min | Market reassessment |
| `live_red_cards` | AF events | On event | Game state change |
| `live_goals` | AF events | On event | Score change |

---

## Signal Timeline Per Match

```
T-24h   Fixtures published (AF)
T-16h   Morning pipeline runs (08:00 UTC):
          → Group 1: Model signals (Poisson, XGBoost, AF prediction, ensemble)
          → Group 2: Opening market odds
          → Group 3: All team quality signals (ELO, form, standings, H2H, rest)
          → Group 4: First injury/news scan
          → Group 5: Referee, venue, context signals
T-14h   Odds snapshot #1
T-12h   Odds snapshot #2 + news scan #2
T-8h    Odds snapshot #3
T-6h    Odds snapshot #4 + news scan #3
T-4h    Odds snapshot #5
T-2h    Odds snapshot #6 (last pre-kickoff snapshot)
T-1h    Lineups published (AF T7) → lineup_confirmed signal added
T-30m   Final news scan #4
T-0h    Match kicks off
T+5m    Live tracker starts (every 5min)
  ...   Live signals update throughout match
T+FT    Settlement: result recorded
T+1h    Post-match enrichment: T4 (HT stats), T8 (events), T12 (player ratings)
```

---

## Storage Design

### Current: `predictions` table
One row per `(match_id, market)` → will be extended with `source` column
to store each model signal separately.

```
(match_id, market, source='poisson')    ← Poisson probability
(match_id, market, source='xgboost')    ← XGBoost probability
(match_id, market, source='af')         ← AF /predictions probability
(match_id, market, source='ensemble')   ← Consensus (used by bots)
```

### Planned: `match_signals` table (Migration 010)
One row per `(match_id, signal_name, captured_at)`.
Holds all non-probability signals (Groups 2-5). Designed for append-only
time-series — the same signal (e.g. `odds_drift`) gets a new row each time
it's updated. The ML training query takes the value closest to kickoff.

```sql
match_signals (
    id              uuid primary key,
    match_id        uuid references matches(id),
    signal_name     text,        -- e.g. 'odds_drift', 'news_impact_score'
    signal_value    numeric,     -- numeric representation
    signal_text     text,        -- optional: raw text/JSON if needed
    signal_group    text,        -- 'market', 'quality', 'information', 'context'
    captured_at     timestamptz, -- when this value was recorded
    data_source     text,        -- 'af', 'kambi', 'gemini', 'derived', etc.
)
```

### `prediction_snapshots` table (already exists)
Temporal snapshots of the ensemble prediction at key moments:
- `stats_only` — morning, before news
- `post_news` — after first AI news scan
- `lineup_confirmed` — when XI is published
- `pre_kickoff` — last snapshot before match starts

---

## ML Training Schema

When we have enough settled matches, the training dataset is:

```
For each settled match × market:

INPUTS (features):
  Model signals:    poisson_prob, xgboost_prob, af_pred_prob, ensemble_prob
  Market:           opening_implied, closing_implied, odds_drift, steam_move
  Quality:          elo_diff, form_home, form_away, h2h_win_pct, rest_advantage
  Information:      news_impact_score, injury_severity, lineup_confirmed
  Context:          league_tier, fixture_importance, referee_cards_avg
  Meta:             signal_count, data_tier, model_disagreement, hours_to_kickoff

TARGETS:
  match_outcome     (home/draw/away)
  over_25           (boolean)
  clv               (did we beat the closing line?)
  won_bet           (boolean — only for matches where a bet was placed)
```

The model we train is NOT "who wins?" — it's "was this bet +EV given these signals?"
That's the meta-model described in MODEL_ANALYSIS.md Section 8.

---

## Implementation Status

| Component | Status | Notes |
|-----------|--------|-------|
| Group 1: Model signals (Poisson/XGBoost/ensemble) | ✅ Running | Predictions table, one row per market |
| Group 1: AF prediction signal | ✅ Done 2026-04-28 | JSONB on matches + source='af' rows in predictions table (S1-AF) |
| Group 1: `source` column on predictions | ✅ Done 2026-04-28 | Migration 010 — upsert on (match_id, market, source) |
| Group 2: Opening odds | ✅ Running | odds_snapshots table |
| Group 2: Odds drift / steam move | ✅ Running | Computed in `compute_odds_movement()` |
| Group 2: overnight_line_move | ✅ Done 2026-04-28 | S3e — yesterday close vs today open, written to match_signals |
| Group 3: ELO | ✅ Running | team_elo_daily table |
| Group 3: Form / standings | ✅ Running | team_form_cache, league_standings |
| Group 3: league_position, points_to_relegation/title | ✅ Done 2026-04-28 | S3b — from league_standings, normalised rank + points gap signals |
| Group 3: rest_days_home/away | ✅ Done 2026-04-28 | S3f — computed from matches table (days since last finished match) |
| Group 3: H2H (h2h_win_pct) | ✅ Done 2026-04-28 | S3c — h2h_home_wins/total, stored in match_signals |
| Group 3: goals_for/against_avg | ✅ Done 2026-04-28 | T2 season stats wired as signals for Tier A teams |
| Group 4: News impact | ✅ Running | news_events table, `news_impact_score` on bets + match_signals |
| Group 4: Injuries | ✅ Running | match_injuries table + injury_count_home/away in match_signals |
| Group 4: Lineups | ✅ Running | lineups_home/away JSONB on matches + lineup_confirmed signal |
| Group 5: Referee signals | ✅ Done 2026-04-28 | referee_stats table (migration 011); cards_avg + home_win_pct + over25_pct in match_signals |
| Group 5: Fixture importance | ✅ Done 2026-04-28 | `compute_fixture_importance()` from standings, written to match_signals |
| Group 5: Is-derby / travel | ❌ Not built | Needs team location data |
| Group 6: Live signals | ✅ Running | live_match_snapshots, is_live odds |
| `match_signals` table | ✅ Done 2026-04-28 | Migration 010 — append-only EAV signal store |
| Pseudo-CLV for all matches | ✅ Done 2026-04-28 | S0a — `compute_and_store_pseudo_clv()` in settlement |
| `match_feature_vectors` wide table | ✅ Done 2026-04-28 | S0b — `build_match_feature_vectors()` in settlement; migration 012 adds 16 new signal columns |
| ML meta-model training | ❌ Waiting for data | ~11 days to 3000 pseudo-CLV rows (~mid-May 2026) |

---

## Priority Order

**Done (2026-04-28):**
1. ✅ S0a — Pseudo-CLV for all ~280 daily matches
2. ✅ S0b — `match_feature_vectors` nightly ETL (wide ML training table)
3. ✅ Migration 010 — `source` on predictions + `match_signals` table
4. ✅ Store poisson + xgboost as separate prediction rows (S1)

**Also done (2026-04-28):**
5. ✅ S3 — Wire signals into match_signals (opening odds, ELO, form, injuries, BDM-1, fixture importance, referee avg, news_impact)
6. ✅ S4 — Referee signals: referee_stats table + backfill + morning pipeline lookup
7. ✅ S5 — Fixture importance from standings urgency
8. ✅ S3b — Standings signals: league_position, points_to_relegation/title (home + away)
9. ✅ S3c — H2H signal: h2h_win_pct (home team win rate over last 10 H2H)
10. ✅ S3d — Referee home_win_pct + over25_pct from referee_stats
11. ✅ S3e — overnight_line_move: yesterday's close vs today's first odds snapshot
12. ✅ S3f — rest_days_home/away: days since each team's last finished match
13. ✅ S1-AF — AF predictions now stored as predictions rows with source='af' (meta-model Group 1 signal)
14. ✅ T2-scoped — Team season stats re-enabled for Tier A only; goals_for/against_avg wired as match_signals
15. ✅ Migration 012 — 16 new signal columns on match_feature_vectors

**In ~11 days (~mid-May 2026):**
8. ⬜ Train meta-model Phase 1: 5-feature logistic regression on 3000+ pseudo-CLV rows

**In 4-6 weeks:**
9. ⬜ Validate alignment filter (300+ settled bot bets with alignment data)
10. ⬜ Graduate meta-model to XGBoost with full signal set (1000+ bot bets)

**See PRIORITY_QUEUE.md for the full 37-item ordered task list.**
