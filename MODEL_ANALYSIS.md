# OddsIntel Prediction Model — Analysis & Improvement Roadmap

> This document captures the current model architecture, an independent assessment from 4 AI evaluations,
> and a concrete improvement roadmap. Written so any agent or developer can pick this up and start working.

---

## 1. Current Model Architecture

### 1.1 Overview

Poisson + XGBoost **50/50 ensemble** producing match outcome probabilities. Compares model probabilities against bookmaker odds to find positive expected value (EV) bets.

### 1.2 Input Features (36-40 total)

| Category | Features | Scale |
|----------|----------|-------|
| **Team Form** (10-match rolling) | Win%, PPG, goals scored/conceded, goal diff, clean sheet%, O2.5%, BTTS% | Continuous |
| **Home/Away Splits** | Same form stats split by venue | Continuous |
| **ELO Ratings** | K=30, home advantage +100, goal-diff multiplier | ~1200-1800 |
| **xG Proxy** | `0.10 * shots + 0.22 * shots_on_target` | Continuous |
| **Head-to-Head** | Last 10 meetings: win%, avg goals, O2.5%, BTTS% | Continuous (default 0.33) |
| **League Position** | Normalized position, points to relegation/title, in-relegation flag | 0-1 + binary |
| **Rest Days** | Days since last match per team, rest advantage | Integer (capped 14) |
| **League Tier** | Proxy for bookmaker pricing efficiency | Categorical (1-4) |

### 1.3 Prediction Pipeline

```
Step 1: Feature extraction
  - 10-match rolling stats for home and away team
  - Compute differentials (home_form - away_form, home_elo - away_elo, etc.)

Step 2: Two parallel models
  - XGBoost Classifier → P(home), P(draw), P(away), P(over 2.5)
  - XGBoost Poisson Regressor → expected goals → enumerate 0-7 x 0-7 scorelines via PMF
    → derive P(home), P(draw), P(away), P(over 2.5)

Step 3: Ensemble
  - final_prob = 0.5 * XGBoost_prob + 0.5 * Poisson_prob

Step 4: Calibration
  - Isotonic regression (5-fold CV)
  - PROBLEM: Still 10-15% overconfident despite calibration

Step 5: Edge calculation
  - edge = model_probability - (1 / odds)
  - Bet if edge > threshold (varies by league tier and bot strategy)
```

### 1.4 What's NOT in the Model (Known Gaps)

| Signal | Status |
|--------|--------|
| News/injuries | Post-processing only (Gemini at 09:00 UTC), not a model input |
| Odds movement | Snapshots collected every 2h but **not used as features** |
| Market sentiment | Not available |
| Motivation context | Only basic position stats, no relegation/title urgency scoring |
| Lineup confirmation | Checked by Gemini but not quantified |

### 1.5 Data Tier System

| Tier | Data | Edge Requirement | Stake Cap |
|------|------|-----------------|-----------|
| A | Full history + odds calibration (18 leagues) | Base threshold | 100% |
| B | Results-only history (22 leagues) | +2% extra edge | 50% |
| C | Last 15 matches via Sofascore API | +5% extra edge | 25% |

### 1.6 Current Performance (Honest)

| Metric | Result |
|--------|--------|
| Tier 1-2 ROI | -8% to -15% |
| Tier 3-4 ROI | Near-breakeven to +5% |
| 1X2 vs O/U | 1X2 significantly better |
| Overconfidence | 10-15% systematic |
| Root cause | Statistical features (form, ELO, xG) already priced by bookmakers |

---

## 2. Four Independent Assessments — Synthesized Consensus

We submitted the current architecture and the multi-dimensional alignment proposal to 4 independent AI evaluators. Here is what they unanimously or majority agreed on.

### 2.1 Unanimous Verdicts (4/4 agree)

| Topic | Verdict |
|-------|---------|
| **Replace ensemble with alignment scoring?** | **NO.** Keep the ensemble. It captures non-linear interactions that hand-coded dimensions miss. |
| **14 dimensions collapse to ~6-7 independent signals** | The proposed 14 dimensions have severe correlation. Form, goal trend, defensive trend, xG quality all measure the same latent variable (team strength/performance). |
| **Alignment counting as probability model?** | **NO.** Counting agreeing dimensions destroys information (loses magnitude). XGBoost already captures feature agreement internally. |
| **Alignment as BET FILTER?** | **YES.** Alignment count is valuable as a confidence-in-confidence measure — not better probability, but more robust probability. Filters out fragile predictions. |
| **Odds movement is the most valuable unused signal** | Currently wasted. Should be both a model feature AND a filter. |
| **Fix calibration first** | The 10-15% overconfidence is the single biggest ROI leak. Every overconfident bet is a negative EV bet you think is positive. |
| **Use Kelly fraction, not linear edge** | Linear edge treats all odds equally. Kelly naturally handles the variance-adjusted EV per unit of capital. |
| **Real edge = lower leagues + information speed** | Not better statistical features. Bookmakers already model form/ELO/xG perfectly in Tier 1-2. |

### 2.2 Strong Majority (3/4 agree)

| Topic | Verdict | Dissent |
|-------|---------|---------|
| **Layer alignment on top (option B)** | Best approach: ensemble → edge → alignment filter → Kelly sizing | One evaluator preferred option C+B hybrid (new signals like odds movement INTO ensemble, alignment as filter) |
| **Shrink toward market price** | `adjusted_prob = α * model_prob + (1-α) * implied_prob` with α=0.6-0.7 is the single highest-ROI change | One evaluator preferred temperature scaling or Platt scaling instead |
| **H2H is mostly noise** | Downweight or remove. Small sample sizes, inflates confidence | One evaluator said keep if ≥5 meetings in last 5 years |

### 2.3 Notable Individual Insights

| Source | Insight |
|--------|---------|
| **Reply 2** | Build a **meta-model** that predicts bet profitability (not match outcome). Inputs: model probability, EV, dimension scores, odds movement, league tier. Reframes the problem from "who wins?" to "is this bet +EV in reality?" |
| **Reply 2** | Add **model disagreement** as a feature: when Poisson and XGBoost disagree, that's a signal of uncertainty the current 50/50 blend hides. |
| **Reply 4** | Alignment score is best understood as **regime detection**: a high-probability bet with low alignment (one extreme signal dominating) behaves differently than high-probability with broad agreement. The distribution of signal sources is meta-information. |
| **Reply 4** | Dimensions 13 (market efficiency) and 14 (lineup confirmation) are not prediction signals — they're **confidence modifiers** that should affect stake sizing, not alignment counting. |
| **Reply 3** | Track **Closing Line Value (CLV)** as the ultimate ground truth. If your placed odds are consistently worse than closing odds, you have negative expectation long-term regardless of model edge. |
| **Reply 1** | Plot **ROI vs alignment bins** (2/7, 3/7, ..., 7/7). If ROI doesn't monotonically increase with alignment, the approach isn't adding value. |
| **Reply 3** | For O/U market: switch from Poisson to **negative binomial** distribution (handles overdispersion — Poisson underestimates 0-0 and 4+ goal matches). |

---

## 3. The 7 Truly Independent Dimensions

All 4 assessments agreed the 14 proposed dimensions collapse to 6-7 genuinely independent signals. Here is the consolidated mapping:

### 3.1 Redundancy Map (What Collapses)

| Proposed Dimensions | Collapse Into | Why |
|--------------------|--------------|----|
| #1 Home form, #2 Away form, #9 Goal scoring trend, #10 Defensive trend, #5 xG quality | **Team performance** | All derived from recent match results; ~80% correlated |
| #6 Negative news, #7 Positive news | **News impact** | One dimension on -1 to +1 scale |
| #11 Motivation, #12 Rest advantage | **Situational context** | Weakly correlated, can combine |
| #13 Market efficiency, #14 Lineup confirmation | **Not prediction signals** | Meta-dimensions that modify confidence, not alignment |

### 3.2 Final 7 Independent Dimensions for Alignment

| # | Dimension | What It Measures | Scale | Data Source |
|---|-----------|-----------------|-------|-------------|
| 1 | **Strength Differential** | Long-term team quality gap | ELO difference normalized | ELO ratings |
| 2 | **Form Momentum** | Recent performance trend (combined attack + defense) | 0-1 per team → differential | 10-match rolling stats |
| 3 | **xG Over/Underperformance** | Is the team outperforming its underlying quality? | Ratio of actual goals to xG proxy | Shots data |
| 4 | **H2H Pattern** | Historical matchup tendency | Only if ≥5 meetings in 5 years, else neutral | Match history |
| 5 | **News/External Info** | Injuries, suspensions, managerial changes | -1.0 to +1.0 (severity-weighted) | Gemini AI analysis |
| 6 | **Odds Movement** | Market direction since opening | -1 (strong against) to +1 (strong for) | 2-hourly snapshots |
| 7 | **Situational Context** | Motivation (relegation/title) + rest advantage | Combined score | League table + schedule |

### 3.3 Meta-Dimensions (Affect Stake, Not Alignment)

| Dimension | How to Use |
|-----------|-----------|
| **Market Efficiency** (league tier) | Stake multiplier: Tier 3-4 → higher allocation, Tier 1-2 → lower |
| **Lineup Confirmation** | Confidence multiplier: confirmed XI → trust model more |
| **Model Agreement** | Poisson vs XGBoost disagreement → flag uncertainty |

### 3.4 Alignment Score Calculation

```python
# For each bet candidate:
alignment = 0
total_active = 0

for dim in [strength, form, xg_perf, h2h, news, odds_movement, situational]:
    if dim.has_signal():  # not neutral
        total_active += 1
        if dim.agrees_with_pick():
            alignment += 1

alignment_ratio = alignment / total_active  # e.g., 5/6 = 0.83

# Classify
if alignment_ratio >= 0.75:  # 6+/7 or 5/6
    alignment_class = "HIGH"
elif alignment_ratio >= 0.50:  # 4/7 or 3/6
    alignment_class = "MEDIUM"
else:
    alignment_class = "LOW"
```

---

## 4. Target Architecture (What to Build)

Based on all 4 assessments, this is the recommended system:

```
┌─────────────────────────────────────────────────────────┐
│ Layer 1: ENSEMBLE (keep current, fix calibration)       │
│                                                         │
│  XGBoost Classifier ──┐                                 │
│                       ├── 50/50 blend → calibrated_prob │
│  XGBoost Poisson ─────┘                                 │
│                                                         │
│  NEW: Add odds_drift, drift_velocity as features        │
│  NEW: Fix calibration (shrink toward market or Platt)   │
│  NEW: Track Poisson vs XGBoost disagreement             │
├─────────────────────────────────────────────────────────┤
│ Layer 2: EDGE CALCULATION                               │
│                                                         │
│  edge = calibrated_prob - (1 / odds)                    │
│  kelly = (calibrated_prob * odds - 1) / (odds - 1)     │
│  ev = calibrated_prob * odds - 1                        │
│                                                         │
│  Gate: edge > minimum_threshold → proceed               │
│  Gate: kelly > 0 → proceed                              │
├─────────────────────────────────────────────────────────┤
│ Layer 3: ALIGNMENT FILTER (NEW)                         │
│                                                         │
│  Compute 7 independent dimension scores                 │
│  Count alignment (how many agree with pick direction)   │
│  Classify: HIGH / MEDIUM / LOW                          │
│                                                         │
│  Gate: LOW alignment → SKIP (or quarter stake)          │
│  Veto: odds moved >5% against pick → SKIP              │
├─────────────────────────────────────────────────────────┤
│ Layer 4: STAKE SIZING (NEW)                             │
│                                                         │
│  base_stake = fractional_kelly * bankroll               │
│  base_stake = min(base_stake, 0.02 * bankroll)          │
│                                                         │
│  Multipliers:                                           │
│    × alignment_mult  (HIGH=1.0, MED=0.6, LOW=0.3)      │
│    × tier_mult       (T1=0.5, T2=0.7, T3=1.0, T4=1.0) │
│    × data_tier_mult  (A=1.0, B=0.5, C=0.25)            │
│    × lineup_mult     (confirmed=1.0, unknown=0.8)       │
├─────────────────────────────────────────────────────────┤
│ Layer 5: AUDIT & TRACKING                               │
│                                                         │
│  Store dimension_scores JSON per bet                    │
│  Track CLV (placed odds vs closing odds)                │
│  Track ROI by alignment bin (validation)                │
│  Track model disagreement (Poisson vs XGBoost)          │
└─────────────────────────────────────────────────────────┘
```

---

## 5. Implementation Priorities (Ordered by Impact)

### Priority 1: Fix Calibration (Highest Impact, Lowest Effort)

**The problem:** 10-15% systematic overconfidence. This means the model thinks a 70% bet is +3.3% edge at 1.5 odds, but the true probability is ~60%, making it actually -6.7% edge. Every overconfident bet is a negative EV bet disguised as positive.

**Three options (try in order, pick what works best on held-out data):**

```python
# Option A: Shrink toward market price (simplest, recommended first)
adjusted_prob = alpha * model_prob + (1 - alpha) * implied_prob
# Start with alpha = 0.65, optimize on held-out season data
# Expected improvement: reduces overconfidence from 10-15% to 3-5%

# Option B: Platt scaling (replace isotonic regression)
# Train logistic regression on model outputs → actual outcomes
from sklearn.linear_model import LogisticRegression
platt = LogisticRegression()
platt.fit(model_probs.reshape(-1, 1), outcomes)
calibrated = platt.predict_proba(model_probs.reshape(-1, 1))

# Option C: Temperature scaling
# Single parameter T that softens probabilities
calibrated = softmax(logits / T)
# T > 1 reduces confidence, T < 1 increases it
# Optimize T on validation set
```

**Also:** Reduce XGBoost overconfidence at source — lower `max_depth`, increase `min_child_weight`, heavier L1/L2 regularization.

**Validation:** After calibration fix, plot predicted probability vs actual win rate in 5% bins. Should be close to diagonal.

### Priority 2: Add Odds Movement as Model Features (Highest Unused Signal)

**We already collect 2-hourly odds snapshots.** This is the most valuable unused data.

```python
# Features to compute and feed into XGBoost:
opening_implied = 1 / opening_odds
current_implied = 1 / current_odds

odds_drift = current_implied - opening_implied          # positive = shortened
odds_drift_pct = odds_drift / opening_implied            # normalized
hours_since_open = (current_time - opening_time).hours
drift_velocity = odds_drift / max(hours_since_open, 1)  # speed of movement

# Also useful:
steam_move = 1 if abs(odds_drift_pct) > 0.03 else 0    # sharp money flag
```

**As a veto filter (hard rule):**
- Odds moved >5% against pick → **SKIP** (market knows something model doesn't)
- Odds moved >3% toward pick → **bonus confidence** (market confirms)

**Critical:** Only count movement that happened *before* bet calculation. Use T-12h to T-2h window.

### Priority 3: Implement Alignment Filter

Build the 7 independent dimensions. Use as discrete bet filter (HIGH/MEDIUM/LOW), not a continuous score requiring its own calibration.

```python
# Per bet, compute:
dimensions = {
    "strength": compute_elo_differential(home, away),      # favors pick? +1/0/-1
    "form": compute_form_momentum(home, away),             # favors pick? +1/0/-1
    "xg_perf": compute_xg_over_under(home, away),          # favors pick? +1/0/-1
    "h2h": compute_h2h(home, away, min_meetings=5),        # favors pick? +1/0/-1
    "news": get_news_impact(home, away),                   # from Gemini, -1 to +1
    "odds_move": compute_odds_direction(match),            # market agrees? +1/0/-1
    "situation": compute_motivation_rest(home, away),      # favors pick? +1/0/-1
}

agreeing = sum(1 for d in dimensions.values() if d > 0)
active = sum(1 for d in dimensions.values() if d != 0)
alignment = agreeing / max(active, 1)
```

**Validation requirement:** Before trusting this filter, track alignment vs actual outcomes for at least 500 bets. Plot ROI by alignment bin. If ROI doesn't increase with alignment, the filter isn't working.

### Priority 4: Kelly-Based Stake Sizing

Replace flat stakes and linear edge ranking.

```python
# Kelly fraction (for ranking AND sizing)
kelly = (model_prob * odds - 1) / (odds - 1)

# Fractional Kelly for actual stakes (1/4 Kelly recommended)
raw_stake = kelly * 0.25 * bankroll
max_stake = 0.02 * bankroll  # never risk more than 2%

# Apply multipliers
final_stake = min(raw_stake, max_stake)
final_stake *= alignment_multiplier    # HIGH=1.0, MED=0.6, LOW=0.3
final_stake *= league_tier_multiplier  # T1=0.5, T2=0.7, T3=1.0, T4=1.0
final_stake *= data_tier_multiplier    # A=1.0, B=0.5, C=0.25

# For bet ranking in UI:
rank_score = kelly * alignment_multiplier
```

### Priority 5: Shift Volume Toward Tier 3-4

The evidence is clear: edge exists in lower leagues, not top leagues.

- **Tier 3-4:** 70% of bankroll allocation, lower edge thresholds
- **Tier 1-2:** 20% of bankroll, **only when news alignment is HIGH** (injury + lineup confirmed + odds stable)
- **Tier B/C:** 10% with strict thresholds (+5% edge, 25% stake)
- Add more lower leagues (expand Tier B coverage)

### Priority 6: Timing Optimization

- Move news checker closer to kickoff (2-3h before, not 09:00 UTC fixed)
- Compare model probability to implied prob **at time of bet placement**, not a stale snapshot
- Track whether winning bets correlate with stable odds (information not priced in) vs moving odds (timing luck)

### Priority 7: Separate Models per Market

1X2 significantly outperforms O/U. They need different model structures:

- **1X2:** Keep current Poisson + XGBoost blend
- **O/U:** Switch to **negative binomial** distribution (handles overdispersion — Poisson underestimates 0-0 and 4+ goal matches)
- **BTTS:** Derive from goals model, not from 1X2 model

### Priority 8 (Longer Term): Meta-Model

Train a second-stage model that predicts **bet profitability** rather than match outcome:

```python
# Meta-model inputs:
features = [
    model_probability,
    edge,
    kelly_fraction,
    alignment_score,
    alignment_class,
    odds_drift,
    drift_velocity,
    news_impact,
    league_tier,
    data_tier,
    model_disagreement,  # abs(xgboost_prob - poisson_prob)
    hours_to_kickoff,
    lineup_confirmed,
]

# Meta-model target:
target = bet_was_profitable  # binary: 1 if won, 0 if lost

# This reframes the problem from "who wins?" to "is this bet +EV in reality?"
```

**Warning:** Needs substantial paper trading data to train. Don't attempt until you have 1000+ settled bets with dimension scores attached.

---

## 6. What to Store per Bet (Schema Addition)

Add to `simulated_bets` table:

```sql
-- New columns
dimension_scores      JSONB,     -- {"strength": 0.7, "form": 0.4, ...}
alignment_count       INTEGER,   -- e.g., 5
alignment_total       INTEGER,   -- e.g., 7
alignment_class       TEXT,      -- "HIGH" / "MEDIUM" / "LOW"
kelly_fraction        FLOAT,     -- Kelly-optimal fraction
odds_at_open          FLOAT,     -- opening odds (for CLV)
odds_at_close         FLOAT,     -- closing odds (for CLV)
odds_drift            FLOAT,     -- implied prob change since open
model_disagreement    FLOAT,     -- abs(xgboost_prob - poisson_prob)
calibrated_prob       FLOAT,     -- after shrinkage/Platt correction
news_impact_score     FLOAT,     -- -1 to +1 from Gemini
lineup_confirmed      BOOLEAN,   -- was XI known at bet time?
```

This data enables all future analysis: ROI by alignment bin, CLV tracking, meta-model training.

---

## 7. Validation Checkpoints

Before trusting any change in production paper trading:

| Change | Validation Required |
|--------|-------------------|
| Calibration fix | Predicted prob vs actual win rate plot (5% bins). Should be near-diagonal. |
| Odds movement features | Backtest ROI with/without features. Must improve on held-out season. |
| Alignment filter | Track ROI by alignment bin for 500+ bets. ROI must increase monotonically with alignment. |
| Kelly sizing | Simulate bankroll curves with Kelly vs flat stake on historical bets. Kelly should show higher Sharpe ratio. |
| Market shrinkage | Compare `edge_before_shrinkage` vs `edge_after_shrinkage` hit rates. After-shrinkage should be better calibrated. |
| Meta-model | Only attempt after 1000+ bets with all dimension scores stored. |

---

## 8. Key Formulas Reference

```python
# Edge (current)
edge = model_prob - (1 / odds)

# EV (correct formula — accounts for odds magnitude)
ev = model_prob * odds - 1

# Kelly fraction (for ranking and stake sizing)
kelly = (model_prob * odds - 1) / (odds - 1)

# Fractional Kelly stake (1/4 Kelly, 1.5% cap)
stake = min(kelly * 0.25 * bankroll, 0.015 * bankroll)

# Market-shrunk probability (tier-specific calibration)
# alpha = {T1: 0.55, T2: 0.65, T3: 0.80, T4: 0.85}
adjusted_prob = alpha * model_prob + (1 - alpha) * implied_prob

# CLV (closing line value — ground truth for edge detection)
clv = (odds_at_pick / odds_at_close) - 1

# Rank score for UI
rank = kelly * alignment_multiplier
```

---

## 9. Summary: Where the Edge Actually Lives

| Source | Evidence | Priority |
|--------|----------|----------|
| **Fix overconfidence** | 10-15% leak is costing more than any feature will gain | P1 |
| **Lower league inefficiency** | +5% ROI in Tier 3-4 confirmed across backtests | P5 |
| **Information speed** (news, lineups) | Only edge not already priced by bookmakers | P6 |
| **Odds movement** (sharp money signal) | Most valuable unused data we already collect | P2 |
| **Alignment filtering** (remove fragile bets) | Reduces volume but improves ROI by cutting bad bets | P3 |
| **Kelly sizing** (variance-adjusted stakes) | Proper capital allocation vs flat stakes | P4 |
| **Better statistical features** | Minimal — bookmakers already model form/ELO/xG | Low |

---

## 10. Implementation Status & Deferred Items (Updated 2026-04-27)

### Implemented (active in production pipeline)

| Priority | What | How | Key Decision |
|----------|------|-----|-------------|
| P1 | Calibration | Tier-specific α: T1=0.55, T2=0.65, T3=0.80, T4=0.85 | Higher α for lower tiers (trust model more where market is less efficient) |
| P2 | Odds movement | Soft penalty on Kelly for adverse drift, hard veto only >10% | Soft penalty over hard veto — markets overshoot |
| P3 | Alignment | 4 external-signal dimensions, LOG-ONLY mode | Only external signals (odds_move, news, lineup, situational). ELO/form/xG removed — already in model. |
| P4 | Kelly sizing | 1/4 Kelly, 1.5% max cap, data-tier multiplier | 1.5% cap (not 2%) while model is unvalidated |
| P6 | News timing | 4x/day: 09:00, 12:30, 16:30, 19:30 UTC | Catches lineup confirmations 1-2h before kickoff |

### Implemented (logging only, not affecting decisions)

| Item | What | Activate When |
|------|------|--------------|
| Alignment filter | Stores dimension_scores + alignment_class on every bet | After 300+ settled bets show ROI correlating with alignment class |
| Alignment thresholds | HIGH/MEDIUM/LOW at 0.75/0.50 (provisional) | Replace with data-driven thresholds from ROI inflection points |

### Deferred (need data accumulation)

| Item | What | Data source | Min data needed | Estimated timeline |
|------|------|-------------|-----------------|-------------------|
| **Platt scaling** | Logistic regression on model output → corrected probability | `predictions` table (ALL matches with odds, ~200/day) + match results | 500+ predictions with known outcomes | ~1-2 weeks (mid-May 2026) |
| **XGBoost in live pipeline** | ✅ DONE (2026-04-27). Loads v9a_202425 saved models, computes features from CSV, blends 50/50 with Poisson for Tier A teams. `workers/model/xgboost_ensemble.py` | — | — | — |
| **Model disagreement** | ✅ DONE. `model_disagreement = abs(poisson_prob - xgb_prob)` stored on every Tier A bet. | — | — | — |
| **Dynamic alignment thresholds** | Set HIGH/MED/LOW cutoffs from ROI inflection points | `simulated_bets` with alignment_class populated (bot bets only, ~10-20/day) | 300+ settled bot bets with alignment data | ~3-4 weeks (late May 2026) |
| **Meta-model** | Second-stage model predicting bet profitability, target=CLV not binary profit | `simulated_bets` with dimension_scores, kelly, CLV (bot bets only) | 1000+ settled bot bets with all fields | ~2-3 months (July 2026) |

**How to check readiness:** Run these queries against Supabase to see if you've hit the thresholds:
```sql
-- Platt scaling: predictions with known outcomes
SELECT COUNT(*) FROM predictions p
JOIN matches m ON p.match_id = m.id
WHERE m.status = 'finished';
-- Need: 500+

-- Dynamic alignment: settled bets with alignment data
SELECT COUNT(*) FROM simulated_bets
WHERE result != 'pending' AND alignment_class IS NOT NULL;
-- Need: 300+

-- Meta-model: settled bets with full dimension scores
SELECT COUNT(*) FROM simulated_bets
WHERE result != 'pending' AND dimension_scores IS NOT NULL AND clv IS NOT NULL;
-- Need: 1000+
```

### Deprioritized (verified low impact)

| Item | Finding | Action |
|------|---------|--------|
| **Negative binomial for O/U** | Overdispersion ratio = 1.016 (barely above 1.0). 0-0 draws underestimated by 8% but 4+ goals dead-on. Tier 3 actually underdispersed (0.986). | Not worth switching. 1X2 is our profitable market. Revisit only if O/U becomes a focus. |
| **H2H dimension** | 3/4 independent assessments flagged as noise. Small samples, narrative bias, already priced by market. | Removed from alignment dimensions. |

### Validation cadence

Check these milestones against the DB queries above.

| Milestone | Action | Script |
|-----------|--------|--------|
| 200+ predictions with results | First calibration ECE check (uses ALL predictions, not just bets) | `python scripts/validate_improvements.py` |
| 50+ settled bot bets | First alignment + Kelly check | Same script |
| 500+ predictions with results | Fit Platt scaling (replace/complement tier-specific shrinkage) | New script needed |
| 300+ settled bot bets | Evaluate alignment filter activation (check ROI by alignment bin) | Same script |
| 1000+ settled bot bets | Train simple meta-model (logistic regression, target=CLV) | New script needed |

---

## 11. AI Usage Roadmap (Consolidated from 4 Independent Assessments, 2026-04-27)

> Where AI adds value beyond current usage. Assessed by 4 independent evaluators against our
> actual data stack. Only ideas that work with data we have or are actively collecting are included.
> Generic "use deep learning" suggestions were filtered out.

### Key Takeaway

Our current AI usage (Gemini for news, XGBoost/Poisson for prediction) covers the obvious spots.
The next gains come from three areas, in order of ROI:

1. **Speed** — getting information before odds adjust (structured news extraction, lineup confidence)
2. **Live data** — exploiting our 5-min match snapshots for in-play edge (all 4 assessments agree)
3. **Market intelligence** — understanding odds movement patterns, not just magnitude (trajectory clustering, regime classification)

### Tier 1: Do Now (low effort, uses existing data)

#### 11.1 Structured News Scoring + Lineup Confidence ✅ DONE
**Consensus: 4/4 assessments recommend this.**
**Implemented:** 2026-04-27 in `workers/jobs/news_checker.py`

Gemini prompt rewritten to output structured JSON per bet:
- Per-player impact scores with position and severity (`players_out`, `players_doubtful`, `players_returning`)
- `lineup_confidence` (0.0-1.0): how certain is the XI
- `home_net_impact` / `away_net_impact`: net effect on each team
- `news_impact_score`: computed per bet based on selection direction, stored in `simulated_bets`
- `lineup_confirmed`: boolean (true when lineup_confidence >= 0.9), stored in `simulated_bets`
- Each flagged player also stored as structured row in `news_events` table (with impact_magnitude)
- `lineup_confidence` wired into alignment dimension 3 (`_dim_lineup` in `improvements.py`)

**Validation (not yet possible — needs data):**
- After 100+ matches with news flags: correlate `news_impact_score` magnitude with actual outcome divergence from base model
- After 100+ matches with lineup data: check whether `lineup_confidence=0.9` correctly predicts actual XI 90% of the time
- **Timeline: ~1-2 weeks** (4 runs/day × 20-40 matches/run = 80-160 data points/day)
- **How to check:** `SELECT COUNT(*) FROM simulated_bets WHERE news_impact_score IS NOT NULL AND result != 'pending';` — need 100+

#### 11.2 LLM Team Name Resolution ✅ DONE
**Source: Assessment #4. Unique, highly practical.**
**Implemented:** 2026-04-27 as `scripts/resolve_team_names.py`

Script batch-resolves 204 unique unmatched team names against known teams from targets_v9.csv + targets_global.csv using Gemini Flash. Results cached in `data/processed/llm_team_name_cache.json`, optionally written to `KAMBI_TO_FOOTBALL_DATA` in `team_names.py` with `--apply` flag.

**Validation (immediate — run now):**
1. Before: `wc -l data/logs/unmatched_teams.log` → 2,287 entries, 204 unique teams
2. Run: `python scripts/resolve_team_names.py --apply`
3. After: Run pipeline, check log shrinks. Count resolved teams in output.
4. Manual spot-check: verify 20 random resolved pairs are correct
- **Status: READY TO VALIDATE** — run the script to get immediate before/after numbers

#### 11.3 Market-Implied Team Strength Feature ✅ DONE
**Source: Assessment #4. Novel, uses existing data.**
**Implemented:** 2026-04-27 as `compute_market_implied_strength()` in `supabase_client.py`

Computes rolling 5-match average of a team's market-implied win probability from `odds_snapshots`. Queries recent finished matches for the team, gets latest 1X2 odds, computes 1/odds as implied probability, averages.

**Validation (deferred — needs XGBoost + data):**
- Feature is computed and callable but not yet wired into any model (live pipeline is Poisson-only)
- Wire as XGBoost input feature when XGBoost goes live in pipeline
- Backtest with/without feature on historical data to measure impact
- **Blocked on:** XGBoost in live pipeline + enough odds_snapshots for finished matches
- **Timeline: ~4-6 weeks** (needs accumulated match data with odds + XGBoost integration)
- **How to check readiness:** `SELECT COUNT(DISTINCT m.id) FROM matches m JOIN odds_snapshots o ON m.id = o.match_id WHERE m.status = 'finished';` — need 200+

### Tier 2: Do Soon (medium effort, high value)

#### 11.4 Daily Post-Mortem LLM Analysis ✅ DONE
**Consensus: 2/4 assessments recommend this.**
**Implemented:** 2026-04-27 in `workers/jobs/settlement.py` → `run_post_mortem()`

Runs automatically after settlement. Sends all settled bets (with full context: calibrated_prob, odds_drift, CLV, alignment, news_impact) to Gemini Flash. Classifies each loss as:
- `VARIANCE` — reasonable bet, bad luck
- `INFORMATION_GAP` — missed news/odds movement
- `MODEL_ERROR` — model was wrong about team strength
- `TIMING` — right pick, should have waited for lineup

Also outputs: daily summary, patterns noticed, one actionable suggestion.
Results stored in `model_evaluations` table (market="post_mortem", notes=JSON analysis).

**Cost:** ~$0.01-0.02/day.
**Validation:** After 2 weeks of daily post-mortems, check:
- Are loss categories consistent? (e.g., "70% of T1 losses are MODEL_ERROR" → confirms T1 is hard)
- Do suggestions lead to measurable changes?
- **How to check:** `SELECT notes FROM model_evaluations WHERE market = 'post_mortem' ORDER BY date DESC LIMIT 7;`
- **Timeline:** First useful patterns after ~14 days of settlement data

#### 11.5 RSS News Extraction Pipeline (Speed Edge) — DEFERRED
**Source: Assessment #4. Directly targets our core thesis.**
**Deferred:** Cost $30-90/month. Will revisit when model proves profitable enough to justify.

#### 11.6 Cross-Match Correlation / Exposure Management ✅ DONE
**Source: Assessment #4. Simple risk management we don't do.**
**Implemented:** 2026-04-27 in `workers/jobs/daily_pipeline_v2.py` → `_check_exposure_concentration()`

After all bets are placed, checks if any bot has 3+ bets in the same league on the same day. Logs a warning with total stake exposure. Correlated outcomes (same league matchday) can amplify drawdowns.

Currently: warning-only. Future: auto-reduce stakes proportionally when correlated.

**Cost:** $0 (pure computation).
**Validation:** After 4+ weeks of data, compare:
- Drawdown on days with exposure warnings vs days without
- **How to check:** Cross-reference pipeline logs with daily P&L from settlement
- **Timeline:** ~4 weeks for enough data points

### Tier 3: Do When Data Allows (2-6 months)

#### 11.7 In-Play Model at Fixed Checkpoints
**Consensus: 4/4 assessments recommend this. Highest untapped ROI.**

Train a gradient boosting model: given match state at minute 30/45/60/75 (score, xG, shots, possession, cards, live odds), predict P(Home Win), P(Over 2.5), P(BTTS). Compare to live odds for in-play edge.

Key insight from our own data: "High-xG game, 0-0 at minute 10-15 → O/U odds drift upward → potential value."

**Implementation:** Train LightGBM on `live_match_snapshots` at fixed minute checkpoints.
**Min data needed:** ~500 completed matches × 10 snapshots = 5,000 snapshot rows.
**Expected impact:** +2-5% ROI on in-play bets. Opens entirely new revenue stream.
**Timeline:** July-August 2026 (2-3 months of live data collection).

```sql
-- Check readiness:
SELECT COUNT(DISTINCT match_id) FROM live_match_snapshots;
-- Need: 500+
```

#### 11.8 Odds Trajectory Clustering
**Consensus: 2/4 assessments recommend this. Novel approach.**

Use DTW (Dynamic Time Warping) to cluster full odds timelines by shape: "steady shortening", "late steam move", "reversal", "stable". Different shapes mean different things even if total drift is identical.

**Implementation:** Cluster historical odds trajectories, map clusters to outcomes.
**Min data needed:** ~1,000 matches with full 6+ snapshot timelines.
**Expected impact:** Better alignment scoring, differentiates sharp vs public money.
**Timeline:** Late 2026.

```sql
-- Check readiness:
SELECT COUNT(*) FROM (
  SELECT match_id, COUNT(*) as snapshots
  FROM odds_snapshots GROUP BY match_id HAVING COUNT(*) >= 6
) sub;
-- Need: 1000+
```

#### 11.9 CLV Prediction / Meta-Model
**Consensus: 3/4 assessments recommend this. Target CLV, not binary profit.**

Train a model predicting: "will this bet beat the closing line?" Features: model_prob, edge, kelly, alignment, odds_drift, league_tier, hours_to_kickoff. Target: CLV (continuous), not won/lost (binary).

**Min data needed:** 1,000+ settled bot bets with all dimension fields populated.
**Timeline:** July 2026.

### Tier 4: Future / Speculative

#### 11.10 Shadow Line Model
**Source: Assessment #3. Most original idea across all assessments.**

Instead of predicting match outcomes, predict what the bookmaker's opening odds *should be*. If your model predicts opening at 2.00 but bookie opens at 2.30, fire immediately before sharp money corrects. Turns CLV into the primary objective from the start.

**Blocked on:** Systematic opening odds timestamp storage (not currently collected).

#### 11.11 Managerial Tactical Intent
**Source: Assessment #3.**

Scrape pre-match press conferences, classify intent: `[Expected_Rotation: High/Low]`, `[Tactical_Posture: Defensive/Aggressive]`. Most valuable in cup matches and late-season relegation battles.

**Blocked on:** Reliable transcript sources across leagues/languages.

#### 11.12 Referee/Venue Bias Features
**Source: Assessment #4.**

Some referees consistently produce more goals/cards. Sofascore already has referee data in their event API (we call it in `live_tracker.py`). Free signal for O/U markets.

**Implementation:** Extract referee assignment from Sofascore pre-match, compute historical referee stats, add as feature.
**Expected impact:** +1-2% on O/U markets specifically.

### Bet Explanations (Product Feature, All Tiers)

**Consensus: 3/4 assessments recommend this for user-facing product.**

Generate natural language bet justifications from dimension scores, alignment, Kelly, news. Sellable as Elite tier feature. Not a model improvement — a product feature.

**Implementation:** LLM prompt in frontend API, using stored bet data.
**Expected impact:** Zero betting ROI, high commercial ROI (subscriber retention).
**When:** When building Pro/Elite tier in frontend.
