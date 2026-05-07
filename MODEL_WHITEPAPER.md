# OddsIntel — Model Whitepaper

> Technical specification of the prediction and betting system.
> Written for data scientists, auditors, and technical stakeholders.
> Last updated: 2026-05-06

---

## 1. Problem Statement

Bookmaker odds encode probabilities of match outcomes. When a bookmaker's implied probability diverges from the true probability, a positive expected value (EV) opportunity exists. OddsIntel builds a quantitative model to:

1. Estimate match outcome probabilities independently of bookmaker odds
2. Identify matches where our estimate diverges meaningfully from the market
3. Size bets proportionally to the estimated edge using Kelly criterion
4. Track performance via Closing Line Value (CLV) — the gold standard for betting model validation

The core thesis: **bookmaker pricing is less efficient in lower-tier leagues** (divisions 2-4, smaller countries) because bookmakers invest less modelling effort there. Our model exploits this structural inefficiency.

---

## 2. Data Sources

| Source | Data | Frequency | Cost |
|--------|------|-----------|------|
| API-Football (Ultra) | Fixtures, results, odds (13 bookmakers incl. Pinnacle), lineups, injuries, standings, H2H, player stats, live data | Multiple daily | $29/mo |
| ESPN | Settlement results backup | Daily | Free |
| Gemini 2.5 Flash | AI news analysis (injuries, manager changes, tactical shifts) | 4x daily | Free |

**Coverage:** 280+ leagues, 13 bookmakers tracked (including Pinnacle), ~280 matches analysed daily.

Note: Kambi API was removed 2026-05-06 after analysis showed Unibet odds (the main Kambi source) are already included in the API-Football 13-bookmaker feed.

---

## 3. Feature Engineering

### 3.1 Feature Set (36+ features)

All rolling statistics computed from the **10 most recent matches** per team, split by home/away venue.

| Group | Features | Count |
|-------|----------|-------|
| **Home Form** | win%, PPG, goals scored/conceded, goal diff, O2.5%, BTTS%, clean sheet% | 8 |
| **Home at Home** | venue-specific: win%, goals scored/conceded, O2.5% | 4 |
| **Away Form** | Same 8 metrics for away team | 8 |
| **Away at Away** | venue-specific: win%, goals scored/conceded, O2.5% | 4 |
| **Head-to-Head** | home win%, avg goals, O2.5%, BTTS%, total meetings (last 10 H2H) | 5 |
| **League Position** | normalised rank, points to relegation/title, in-relegation flag, position diff | 7 |
| **Rest & Context** | rest days (home/away, capped 14), rest advantage, league tier (1-4) | 4 |
| **ELO** (at inference) | home ELO, away ELO, ELO differential, expected win probability from ELO | 4 |

**Defaults:** When insufficient history exists (new teams, new season), features default to league averages or neutral values (e.g. H2H defaults to 0.33 for 3-way split).

### 3.2 ELO Rating System

Custom ELO implementation tracking every team globally:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| K-factor | 30 | Standard for football — balances responsiveness and stability |
| Home advantage | +100 points | Added to home team's rating before expected score calculation |
| Goal-diff multiplier | `(|GD| + 1)^0.5` | A 3-0 win updates ratings 2x more than a 1-0 win |
| Initial rating | 1500 | Standard baseline for all new teams |

**Expected probability:** `E_home = 1 / (1 + 10^((ELO_away - ELO_home) / 400))`

**Update rule:** `new_rating = old_rating + K * GD_mult * (actual - expected)`

ELO ratings are updated daily during settlement (21:00 UTC) after match results are confirmed.

---

## 4. Model Architecture

### 4.1 Two Parallel Models

**Model A — Dixon-Coles Poisson:**

Estimates expected goals per team, then enumerates all scorelines (0-0 through 7-7) using Poisson probability mass functions:

```
exp_home = avg(home_goals_scored[-10]) * 1.08    # home advantage
exp_away = avg(away_goals_scored[-10]) * 0.92
exp_home = (exp_home + avg(away_goals_conceded[-10])) / 2   # blend with opponent
exp_away = (exp_away + avg(home_goals_conceded[-10])) / 2

For each scoreline (h, a):
    P(h, a) = Poisson(h; exp_home) * Poisson(a; exp_away) * tau(h, a)
```

**Dixon-Coles correction** adjusts the four low-scoring outcomes where the independence assumption breaks down. The rho parameter is estimated per league tier from historical scoreline frequencies (script: `scripts/fit_league_rho.py`, refreshed weekly). Default fallback: rho = -0.13 (literature standard) when fewer than 200 matches exist for a tier.

| Scoreline | Correction factor tau |
|-----------|----------------------|
| 0-0 | `1 - exp_h * exp_a * rho` |
| 1-0 | `1 + exp_a * rho` |
| 0-1 | `1 + exp_h * rho` |
| 1-1 | `1 - rho` |
| All other | 1.0 (no correction) |

This addresses the ~8% draw underestimation of independent Poisson. After correction, 1X2 probabilities are renormalised to sum to 1.0.

**Output:** P(home), P(draw), P(away), P(O/U 1.5), P(O/U 2.5), P(O/U 3.5), P(BTTS).

**Model B — XGBoost Classifier:**

Gradient boosted decision tree trained on historical match data:

| Hyperparameter | Value |
|----------------|-------|
| n_estimators | 200 |
| max_depth | 6 |
| learning_rate | 0.05 |
| subsample | 0.8 |
| colsample_bytree | 0.8 |
| objective | `multi:softprob` (3-class) |
| Calibration | Isotonic regression (5-fold CV) |
| Validation | TimeSeriesSplit (5 folds, no future data leakage) |

**Output:** P(home), P(draw), P(away), P(O/U 2.5), expected goals (home/away).

### 4.2 Ensemble

```
P(outcome) = w * Poisson_prob + (1 - w) * XGBoost_prob
```

Default blend weight w = 0.5 (equal). The weight is learned and stored in the `model_calibration` table (market key `blend_weight_1x2`) via `scripts/fit_blend_weights.py`, and loaded at pipeline startup — falls back to 0.5 if no learned value exists. Model disagreement (`|Poisson - XGBoost|`) is stored per bet as an uncertainty signal.

### 4.3 Data Tier Fallback

Not all matches have sufficient data for both models:

| Tier | Availability | Models Used | Stake Multiplier |
|------|-------------|-------------|-----------------|
| A | Full historical stats + odds (18 leagues) | Poisson + XGBoost ensemble | 100% |
| B | Results-only history (22+ leagues) | Poisson only | 50% |
| D | No history | API-Football prediction only | Not bet on |

---

## 5. Calibration Pipeline

Raw model probabilities are systematically overconfident (10-15%). Two-stage calibration corrects this.

### 5.1 Stage 1 — Tier-Specific Market Shrinkage

Blend model probability toward an implied probability anchor, with the blend weight depending on how efficient the market is for that league tier:

```
shrunk = alpha * model_prob + (1 - alpha) * anchor_implied_prob
```

**Anchor:** Pinnacle-implied probability when available (fallback to market-average across the 13 tracked bookmakers). Pinnacle vig is 2-3% vs 5-8% for soft books — their implied probabilities are closer to true probabilities. This applies to all markets (1X2 home/draw/away, O/U over/under) since PIN-2 (2026-05-06).

**1X2 markets (default alphas):**

| Tier | alpha | Model weight | Market weight | Rationale |
|------|-------|-------------|---------------|-----------|
| 1 (top flight) | 0.20 | 20% | 80% | EPL/La Liga: market is very efficient |
| 2 | 0.30 | 30% | 70% | Championship level |
| 3 | 0.50 | 50% | 50% | Balanced |
| 4 (lower) | 0.65 | 65% | 35% | Market least efficient, trust model more |

**CAL-ALPHA-ODDS (implemented 2026-05-06):** For bets at odds > 3.0 (longshots), `alpha = max(alpha_tier - 0.20, 0.10)` — reducing model weight to pull calibrated probability harder toward the anchor. Live data (77 settled bets) showed the 0.30-0.40 probability bin is catastrophically miscalibrated (13% actual win rate vs 35.5% predicted), driven by longshot home bets where the model overestimates vs the market.

**Goal-line markets (BTTS, O/U) use higher alpha** — the Poisson/Dixon-Coles model is specifically designed for goal totals, so we trust it more relative to the bookmaker:

| Tier | alpha (goal-line) |
|------|------------------|
| 1 | 0.35 |
| 2 | 0.45 |
| 3 | 0.65 |
| 4 | 0.80 |

All alpha values are **learned and updatable**: the pipeline loads them from the `model_calibration` table at startup (keys `shrinkage_alpha_t{tier}_{market_type}`), falling back to the hardcoded defaults above if no learned values exist.

### 5.2 Stage 2 — Platt Sigmoid Correction

A learned sigmoid function fitted on settled prediction outcomes, correcting any remaining systematic miscalibration:

```
calibrated = 1 / (1 + exp(-(a * shrunk + b)))
```

- Parameters `a` (slope) and `b` (intercept) fitted per market (1x2_home, 1x2_draw, 1x2_away)
- Fitted by minimising negative log-likelihood on all settled predictions with known outcomes
- Stored in `model_calibration` table, refreshed weekly (every Sunday after settlement)
- Requires 100+ samples per market; graceful no-op if unavailable
- Script: `scripts/fit_platt.py`

**Known limitation:** Single Platt sigmoid is a monotonic function and cannot fix conditional miscalibration. Live data (77 settled bets, 2026-05-06) shows the 0.40-0.50 bin is well-calibrated (45.5% actual vs 44.8% predicted) while the 0.30-0.40 bin is severely miscalibrated (13% actual vs 35.5% predicted). A single sigmoid fitted to both bins simultaneously will degrade both. The weekly refit will not self-correct this.

**Planned upgrade (CAL-PLATT-UPGRADE, at 300+ settled bets):** Replace with a 2-feature logistic regression: `X = [shrunk_prob, log(odds_at_pick)]`. This allows the calibration to learn that "model says 40% at odds 3.6" should be corrected differently than "model says 40% at odds 1.8", without the overfitting risk of per-odds-bucket Platt scaling.

### 5.3 Stage 3 — Veto Gate

An additional hard filter applied after calibration, before bet placement:

**Pinnacle disagreement veto (PIN-VETO, implemented 2026-05-06):** If `calibrated_prob - pinnacle_implied > 0.12` → bet is skipped entirely. Applies to all 1X2 and O/U markets (extended to draw/away/over/under via PIN-3, 2026-05-06).

Empirical validation on 77 settled home bets: all winning bets had gap ≤ 12.9%; losing bets averaged 14.1% gap (max 21.7%). Catches 22/34 losses at the cost of filtering 6/40 wins.

This veto addresses a structural bias: both XGBoost (`is_home` feature) and Poisson (separate home/away lambdas) encode home advantage. When blended 50/50, home advantage may stack. The market already prices it in — so the model's excess confidence shows up as a large positive gap vs Pinnacle.

Threshold 0.12 is calibrated on home bets only. Draw/away/O/U thresholds should be tuned independently once 50+ settled bets per market accumulate.

**Sharp consensus gate (CAL-SHARP-GATE, implemented 2026-05-06):** For 1X2 home bets, also skipped when `sharp_consensus_home < -0.02` (sharps collectively price home less likely than soft books).

### 5.4 Validation

Calibration quality measured by **Expected Calibration Error (ECE)**:

```
ECE = sum over bins: (bin_count / total) * |actual_win_rate - predicted_probability|
```

Using 20 equal-width bins from 0% to 100%. A perfectly calibrated model has ECE = 0. Target: ECE < 3%.

Validation script: `scripts/check_calibration.py` — produces calibration table with 5% bins, flags deviations > 5%.

---

## 6. Edge Detection & Bet Sizing

### 6.1 Edge Calculation

```
edge = calibrated_prob - implied_prob
implied_prob = 1 / decimal_odds
```

A bet is placed only when edge exceeds a tier-specific threshold:

| Tier | 1X2 Favourite | 1X2 Longshot | Over/Under |
|------|--------------|-------------|------------|
| 1 | 8% | 12% | 8% |
| 2 | 5% | 8% | 6% |
| 3 | 4% | 6% | 5% |
| 4 | 3% | 5% | 4% |

Lower tiers require less edge because the market is less efficient — even a small model advantage has a higher probability of being real.

### 6.2 Kelly Criterion Stake Sizing

```
kelly_fraction = (calibrated_prob * odds - 1) / (odds - 1)
stake = min(kelly_fraction * 0.15 * bankroll, 0.01 * bankroll)
```

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Kelly fraction | 0.15x (1/6.7 Kelly) | Conservative — reduces variance at cost of slower growth |
| Max stake | 1.0% of bankroll | Hard cap prevents any single bet from dominating |
| Minimum stake | EUR 1.00 | Sub-EUR 1 bets are noise — not placed |

### 6.3 Stake Multipliers

Applied sequentially after Kelly calculation:

| Multiplier | Values | Purpose |
|------------|--------|---------|
| Data tier | A: 1.0, B: 0.5, C: 0.25 | Reduce exposure on less certain predictions |
| Odds movement penalty | 0.0 to 0.8 | Scale down when market moves against pick (see below) |

### 6.4 Odds Movement Filter

Tracks how odds have moved since opening:

```
drift = current_implied_prob - opening_implied_prob
```

| Drift | Action |
|-------|--------|
| > +1% (favourable) | No penalty — market confirms pick |
| -1% to -10% (adverse) | Soft penalty: `penalty = |drift| / 0.10 * 0.8` |
| < -10% (extreme adverse) | **Hard veto** — bet not placed |

This prevents betting against strong market signals (e.g. late injury news that moves the line).

---

## 7. Signal System

58 signals collected per match across 6 groups, stored in an append-only EAV table (`match_signals`).

### 7.1 Signal Groups

| Group | Signals | Source | Timing |
|-------|---------|--------|--------|
| 1. Model | Poisson, XGBoost, AF, ensemble probabilities | Pipeline | 05:30 UTC |
| 2. Market | Opening odds, bookmaker disagreement, overnight line move, odds volatility, CLV | Odds pipeline | Every 2h |
| 3. Team Quality | ELO, form PPG, form slope, goals, league position, H2H, rest days | Enrichment | 04:15 UTC |
| 4. Information | News impact, injury counts, lineup confirmation/confidence | News checker | 4x daily |
| 5. Context | Referee stats, fixture importance, importance asymmetry, league averages | Enrichment | 04:15 UTC |
| 6. Live | Score, minute, shots, xG, possession, live odds, events | Live tracker | Every 5 min |

### 7.2 Signal Timeline

```
T-24h    Fixtures published (daily 04:00 UTC)
T-16h    Enrichment: standings, H2H, injuries, referee stats, form
T-14h    Odds: first snapshot of the day
T-12h    Predictions: Poisson + XGBoost + ensemble
T-10h    Betting: edge detection, Kelly sizing, bet placement
T-6h     News: first Gemini analysis pass
T-3h     News: second pass (closer to kickoff)
T-1h     Lineups published, lineup signals updated
T-0      Kickoff → live signal collection every 30-60 seconds (Railway LivePoller)
T+FT     Settlement: results, P&L, CLV, ELO update, pseudo-CLV
T+FT+1h  Post-match: stats, events, player stats enrichment
```

---

## 8. Bot Strategies

24 paper trading bots run simultaneously: 16 pre-match bots (same ensemble prediction, different market/league filters) + 8 in-play bots (rule-based strategies using live xG or shot-proxy + Bayesian posterior, `workers/jobs/inplay_bot.py`). Pre-match bots differ in:

- **Which markets** they bet (1X2 home/draw/away, O/U 1.5/2.5/3.5, BTTS yes/no)
- **Which leagues** they target (all, lower tiers only, specific countries)
- **What edge threshold** they require (conservative: 10%, aggressive: 3%)
- **What odds range** they accept (e.g. 1.30-4.50 vs 2.50-3.00)
- **Which selections** they filter (e.g. draw-only, away-only, over-only)

### 8.1 Strategy Categories

| Category | Bots | Approach |
|----------|------|----------|
| Broad coverage | `bot_v10_all`, `bot_aggressive` | All leagues, lower thresholds |
| Lower-tier specialist | `bot_lower_1x2`, `bot_high_roi_global` | Tiers 2-4 where pricing is softest |
| Conservative | `bot_conservative` | 10%+ edge only, highest selectivity |
| Country/region | `bot_greek_turkish` | Specific regions with backtest-confirmed edge |
| Optimizer — away value | `bot_opt_away_british`, `bot_opt_away_europe` | Away wins at mid-range longshot odds in British Isles / top-5 Europe; cross-era validated (+16-19% ROI) |
| Optimizer — home underdog | `bot_opt_home_lower` | Home underdogs at longshot odds (3.00-5.00) in T2-4 Europe; cross-era +14% ROI |
| Optimizer — O/U | `bot_opt_ou_british`, `bot_ou25_global` | Over/Under value in British lower divisions and globally |
| Market specialist — BTTS | `bot_btts_all`, `bot_btts_conservative` | Both-teams-to-score: broad (all leagues) and selective (T1-2, 7%+ edge) |
| Market specialist — O/U | `bot_ou15_defensive`, `bot_ou35_attacking` | O/U 1.5 (defensive leagues) and O/U 3.5 (high-scoring leagues) |
| Draw specialist | `bot_draw_specialist` | Draws underbet in T2-4; odds range 2.80-4.50 |

### 8.2 Bot Timing Cohorts

All 16 bots are assigned to one of three timing windows as an A/B test to identify the optimal bet placement time:

| Cohort | UTC window | Bots | Rationale |
|--------|-----------|------|-----------|
| morning | 06:00 | `bot_v10_all`, `bot_lower_1x2`, `bot_aggressive`, `bot_ou25_global`, `bot_opt_ou_british` | Early odds capture before sharp money moves lines |
| midday | 11:00 | `bot_conservative`, `bot_greek_turkish`, `bot_high_roi_global`, `bot_ou15_defensive`, `bot_ou35_attacking`, `bot_draw_specialist` | Post-injury-news refresh, standings updated |
| pre_ko | 15:00–19:00 | `bot_opt_away_british`, `bot_opt_away_europe`, `bot_opt_home_lower`, `bot_btts_all`, `bot_btts_conservative` | Confirmed lineups, most information available |

CLV and ROI are tracked per cohort to determine which window produces the best edge.

### 8.3 Backtest Foundation

Bot strategies are validated against a 354,518-match dataset (275 leagues, 2005-2015):

- **Lower tiers outperform:** Tier 4 ROI is -7.0% vs Tier 1 at -12.4% (5% better)
- **Consistently profitable leagues:** Singapore S.League (+27.5%), Scotland League Two (+12.3%), Austria Erste Liga (+5.5%)
- **12 of 22 consistently profitable leagues are tier 3-4** (55% of winners)
- **Geographic edge:** Less commercially-covered regions (Singapore, small South American leagues) show more opportunity

---

## 9. Performance Measurement

### 9.1 Primary Metric: Closing Line Value (CLV)

CLV is the industry standard for evaluating betting models independently of short-term variance:

```
CLV (soft-book) = (odds_at_pick / soft_closing_odds) - 1
CLV (Pinnacle)  = (odds_at_pick / pinnacle_closing_odds) - 1   ← primary metric (PIN-5)
```

- **Positive CLV** means we consistently got better odds than the closing line — the market moved in our direction after our bet. This is the strongest evidence of a real edge.
- **Negative CLV** means the market moved against us — our model may be seeing phantom edges.
- **Pinnacle CLV is the stronger signal.** Pinnacle closes at the sharpest line; beating it means we found edge before the most informed market participants did.

CLV is meaningful even when P&L is negative (variance can dominate in small samples).

Both `clv` (soft-book) and `clv_pinnacle` (Pinnacle-specific) are stored on `simulated_bets` and tracked per cohort.

### 9.2 Secondary Metrics

| Metric | What it measures |
|--------|-----------------|
| ECE (Expected Calibration Error) | How well predicted probabilities match actual frequencies |
| Hit rate by confidence bin | Model's ability to rank match certainty |
| ROI by league tier | Where the model adds value vs where the market is too efficient |
| Model disagreement (Poisson vs XGBoost) | Uncertainty indicator — high disagreement = less confident bet |

### 9.3 Track Record Transparency

The public track record page displays:
- Average CLV across all settled bets
- Value bets identified vs total matches analysed
- League coverage
- Model accuracy by confidence level
- All data publicly verifiable (every settled prediction logged with timestamp)

---

## 10. Alignment System (Experimental)

An external signal filter currently in **log-only mode** — it records alignment scores on every bet but does not yet influence staking or filtering.

### 10.1 Six Dimensions

| # | Dimension | Signal | +1 | -1 |
|---|-----------|--------|----|----|
| 1 | Odds Movement | Market drift direction | Shortened (agrees) | Lengthened (disagrees) |
| 2 | News | Gemini impact analysis | Positive news for selection | Key injury/suspension |
| 3 | Lineup | Confirmation status | Confirmed | Not yet confirmed |
| 4 | Situational | Rest + home advantage in lower tiers | Favourable | Unfavourable |
| 5 | Sharp consensus | Sharp vs soft bookmaker pricing gap (`sharp_consensus_home` signal) | Sharp books agree with pick | Sharp books disagree |
| 6 | Pinnacle anchor | Pinnacle implied probability vs model probability | Pinnacle doesn't strongly disagree (gap > −3%) | Pinnacle strongly disagrees (gap < −8%) |

Note: dimensions 5 and 6 only fire for 1X2 home picks. O/U and draw picks use dimensions 1-4.

### 10.2 Activation Criteria

Alignment will be activated (move from log-only to staking modifier) after:
- 300+ settled bets with alignment data
- Statistical evidence that HIGH alignment correlates with higher ROI
- Tracking live since 2026-04-27 (~10 bets/day) — estimated activation: late May 2026

---

## 11. Known Limitations

1. **Top-tier market efficiency:** Tiers 1-2 show negative ROI historically. The model adds little beyond what bookmakers already price in for EPL, La Liga, etc.

2. **Feature overlap with market:** Form, ELO, and xG proxy are publicly available signals. Bookmakers use similar (or better) versions. The model's edge comes from lower-tier inefficiency, not from superior features.

3. **Sample size:** Live trading began 2026-04-27 (~3 days). Statistical significance requires 500+ settled bets. Current CLV and ROI numbers are directional, not conclusive.

4. **No proprietary data:** All data comes from public APIs. No private injury feeds, no in-house scouting, no pitch-level telemetry.

5. **Dixon-Coles rho needs more data:** The parameter is now estimated per league tier (not global static) from historical scoreline frequencies. However tier-level grouping is a coarse approximation — a per-league rho would be more precise but requires ~500+ matches per league to be stable. Additionally, Dixon-Coles only corrects the four low-scoring outcomes (0-0, 1-0, 0-1, 1-1) — higher-scoring draws (2-2, 3-3) remain underestimated due to the positive correlation between team scoring that results from game-state effects. Draw inflation factor (×1.08, pending CAL-DRAW-INFLATE) addresses this.

6. **Isotonic calibration is trained once:** The XGBoost model's isotonic calibration is fitted during training on historical data. It doesn't adapt to live prediction drift (Platt scaling addresses this partially).

7. **Conditional miscalibration at high odds (observed 2026-05-06):** Live data (77 settled bets) shows the model's calibration fails specifically on longshot bets (predicted 30-40%, odds > 3.0). In the 0.30-0.40 probability bin: 23 bets, 35.5% predicted, 13% actual win rate. The primary driver is likely double-counted home advantage (Poisson encodes it via separate home/away lambdas; XGBoost has it as a feature), amplified by edge detection selecting exactly the bets where the model most overestimates. The Pinnacle veto (gap > 0.12) was deployed immediately; the remaining fixes (odds-conditional alpha, sharp consensus gate) are tracked as CAL-ALPHA-ODDS and CAL-SHARP-GATE.

---

## 12. Improvement Roadmap

| Phase | Items | Status |
|-------|-------|--------|
| **Foundation** | ELO, form, Poisson, XGBoost ensemble, Kelly sizing, calibration | Done |
| **Calibration** | Tier-specific shrinkage, Platt scaling, weekly recalibration | Done |
| **Risk controls** | Odds movement penalty/veto, data tier multipliers, max stake cap | Done |
| **Signal infrastructure** | 58 signals, append-only store, wide ML training table, pseudo-CLV | Done |
| **Next: Meta-model** | Second-stage model predicting bet profitability (target = CLV) | Needs 3,000+ matches |
| **Next: Alignment activation** | Use external signal filter to modify stakes | Needs 300+ settled bets |
| **Sharp bookmaker features** | Pinnacle disagreement veto (all markets), Pinnacle implied signals (all markets), Pinnacle line movement, Pinnacle-anchored CLV, sharp/soft consensus | PIN-VETO + PIN-1..5 + P5.1 done |
| **Calibration improvements (live data)** | Odds-conditional alpha, sharp consensus gate, Pinnacle anchor, Pinnacle-anchored CLV | CAL-ALPHA-ODDS / CAL-SHARP-GATE / CAL-PIN-SHRINK done; CAL-DRAW-INFLATE / CAL-PLATT-UPGRADE pending |
| **Dynamic blend weights** | Weekly recalculation of Poisson/XGBoost blend per tier | Done — `scripts/fit_blend_weights.py`, Sunday refit |
| **Next: Historical backfill** | 43K+ matches with stats + events from API-Football (no historical odds available) | In progress — automated cron |
| **Next: XGBoost retrain on backfill** | Retrain on 43K matches with richer AF features (xG, shots, possession) | After backfill Phase 1 |
| **In-play Phase 1: Rule-based paper trading (P3.4)** | 8 strategies (A, A2, B, C, C_home, D, E, F). xG source: real AF stats for top leagues (~UCL/Libertadores/Sudamericana); shot proxy `sot*0.10 + off_target*0.03` for all others. Proxy bets use higher edge floors (+1.5–2pp). League gate (MIN=3 xG matches) only enforced for real-xG mode. All bets log `xg_source: live\|shot_proxy` for backtest segmentation. Safety: staleness <60s, score re-check, red card skip. Fixed 1-unit stake. Runs inside LivePoller every 30s. | **Live since 2026-05-06** — proxy fallback added 2026-05-07 |
| **Next: In-play Phase 2 ML** | LightGBM Poisson regression predicting `lambda_home/away_remaining` from live match state. Replaces rule-based triggers with model probability. Quarter Kelly + time-decay staking. | Needs 500+ live-tracked matches + 200 settled paper bets (~June 2026) |

---

## 13. Code Reference

| Component | File | Key function/class |
|-----------|------|--------------------|
| Poisson model | `workers/jobs/daily_pipeline_v2.py` | `_poisson_probs()` |
| XGBoost ensemble | `workers/model/xgboost_ensemble.py` | `ensemble_prediction()` |
| Calibration (shrinkage + Platt) | `workers/model/improvements.py` | `calibrate_prob()`, `apply_platt()` |
| Kelly sizing | `workers/model/improvements.py` | `compute_kelly()`, `compute_stake()` |
| Odds movement | `workers/model/improvements.py` | `compute_odds_movement()` |
| Alignment | `workers/model/improvements.py` | `compute_alignment()` |
| Platt fitting | `scripts/fit_platt.py` | `fit_and_store()` |
| DC rho fitting | `scripts/fit_league_rho.py` | `run()` |
| Calibration validation | `scripts/check_calibration.py` | `check_calibration()` |
| XGBoost training | `scripts/retrain_xgboost.py` | Main training script |
| ELO updates | `workers/jobs/settlement.py` | ELO update section |
| CLV computation | `workers/jobs/settlement.py` | Settlement + pseudo-CLV |
| Signal collection | `workers/jobs/daily_pipeline_v2.py` | Signal writing throughout pipeline |
| Bot strategies | `workers/jobs/daily_pipeline_v2.py` | `BOTS_CONFIG` dict (lines 67-340) |
| Feature vectors ETL | `workers/jobs/settlement.py` | `--ml-etl` flag |

---

## Appendix A: Glossary

| Term | Definition |
|------|-----------|
| **CLV** | Closing Line Value — ratio of odds at time of pick to odds at kickoff, minus 1. Positive = beat the closing line. |
| **ECE** | Expected Calibration Error — weighted average of |predicted - actual| across probability bins. Lower is better. |
| **Kelly criterion** | Optimal bet sizing formula: `f = (p*b - 1) / (b - 1)` where p = probability, b = decimal odds. We use 0.15x fractional Kelly. |
| **Implied probability** | `1 / decimal_odds` — the probability a bookmaker's odds represent (before margin). |
| **Edge** | `model_probability - implied_probability`. Positive = model thinks outcome is more likely than the market. |
| **Platt scaling** | Post-hoc sigmoid calibration: `1/(1+exp(-(a*p+b)))`. Corrects systematic over/underconfidence. |
| **Dixon-Coles** | Correction to bivariate Poisson for low-scoring outcomes (0-0, 1-0, 0-1, 1-1) where independence assumption fails. |
| **Data tier** | Classification of prediction quality: A (full data), B (results-only), D (API-Football prediction only). |
| **Pseudo-CLV** | CLV computed for ALL matches (not just bets) by comparing opening and closing implied probabilities. Used as ML training target. |

---

## Appendix B: Backtest Summary (354K matches)

Dataset: Beat the Bookie, 275 leagues, 2005-2015.

| Metric | Value |
|--------|-------|
| Total matches | 354,518 |
| Total bets (edge > threshold) | 187,895 |
| Overall ROI | -12.4% |
| Hit rate | 26.9% (breakeven: 29.2%) |
| Tier 4 ROI | -7.0% (best tier) |
| Best league | Singapore S.League: +27.5% (316 bets, 5/5 seasons positive) |
| Consistently profitable leagues | 22 (12 of 22 are tier 3-4) |

Key insight: the model shows edge primarily in **lower-tier, less commercially-covered leagues** where bookmaker pricing efficiency is lowest.
