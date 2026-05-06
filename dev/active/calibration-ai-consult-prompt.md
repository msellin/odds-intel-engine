# AI Consultation Prompt — Soccer Betting Model Calibration

Paste this into claude.ai (Opus) or any capable model for expert feedback.

---

## Context

I'm building a soccer match outcome prediction model for a betting intelligence product.
I need expert advice on calibration strategy given a specific failure pattern in my live data.

## Model Architecture

**Ensemble:** 50/50 blend of XGBoost Classifier + XGBoost Poisson regressor.
- XGBoost Classifier: trained on match features → outputs home/draw/away win probs directly
- XGBoost Poisson: models goals scored (lambda_home, lambda_away) → derives 1x2 probabilities via Poisson convolution + Dixon-Coles rho correction per league tier

**2-stage calibration pipeline:**
1. **Market shrinkage** — weighted average of model prob and market implied prob:
   ```
   shrunk = (1 - α) * model_prob + α * market_implied_prob
   α per tier: T1=0.20, T2=0.30, T3=0.50, T4=0.65
   ```
   Rationale: efficient markets (T1 Premier League) → trust market more. Tier 4 lower leagues → trust model more.

2. **Platt scaling** — sigmoid post-hoc correction fit weekly from settled outcomes:
   ```
   cal = 1 / (1 + exp(-(a * shrunk + b)))
   ```
   Fitted per market (home/draw/away, over/under). Parameters stored in DB, refit every Sunday.

**Edge + Kelly sizing:**
```
edge = calibrated_prob - implied_prob_from_odds
kelly = (calibrated_prob * odds - 1) / (odds - 1)
stake = min(kelly * 0.15 * bankroll, 1% bankroll)   # fractional kelly, capped
```

Bets are placed when edge > threshold (varies by tier: T1=3%, T2=4%, T3=6%, T4=8%).

**Data we store per bet:** calibrated_prob, model_probability (pre-Platt), odds_at_pick, 
odds_at_open (opening line), odds_drift (open→pick implied prob delta), 
sharp_consensus_home (sharp bookmaker avg implied − soft bookmaker avg implied), 
pinnacle_implied_home (from Pinnacle specifically), model_disagreement (|poisson - xgb|).

---

## Live Performance Data (77 settled bets, running since 2026-04-27)

**Overall:**
- avg calibrated_prob = 0.418
- actual win rate = 0.263 (30 won, 47 lost)
- Gap: model says 41.8%, reality is 26.3%

**Reliability diagram (calibrated_prob bins):**
| Pred range | avg_pred | actual_rate | n |
|------------|----------|-------------|---|
| 0.20-0.30  | 0.275    | 0.800       | 5 |
| 0.30-0.40  | 0.355    | 0.130       | 23 |
| 0.40-0.50  | 0.448    | 0.455       | 44 |
| 0.50-0.60  | 0.531    | 0.600       | 5 |

**By market:**
| Market/selection  | avg_pred | actual_rate | avg_odds | n  |
|-------------------|----------|-------------|----------|----|
| 1X2 home          | 0.420    | 0.258       | 3.635    | 31 |
| O/U over 1.5      | 0.483    | 0.500       | 2.627    | 18 |
| O/U under 2.5     | 0.360    | 0.222       | 3.431    | 9  |
| O/U over 2.5      | 0.359    | 0.556       | 3.640    | 9  |
| O/U under 3.5     | 0.414    | 0.667       | 3.431    | 6  |
| 1X2 away          | 0.304    | 0.333       | 4.300    | 3  |

**Key observation on 1X2 home:** These bets are being placed at avg odds of 3.635 (market implied ~27.5%). Our model says home wins 42% of the time. Actual rate: 25.8%. The market was right. We found "value" that wasn't real.

**Also notable on bin 0.30-0.40:** 23 bets in this range, only 13% won. These are likely where we're identifying "value" but the market's lower implied probability is correct.

---

## My Hypotheses for the 1X2 Home Problem

1. **Home advantage is double-counted.** Poisson encodes home advantage via separate lambda_home vs lambda_away training. XGBoost also has home as a feature. Shrinkage should pull toward market, but maybe α is too low for the tiers generating these bets.

2. **Selection bias in what we bet.** We're only betting when edge > threshold. These high-odds home bets (avg 3.6x) are in markets where the bookmaker is saying "home team is unlikely to win" — but our model disagrees and bets it. Perhaps our model systematically overestimates home win probability at high odds specifically.

3. **Platt scaling is market-wide, not market-type-specific.** The Platt fit is across all 1x2 home bets globally, not just high-odds ones. So it can't correct the specific issue with high-odds home bets.

4. **Small sample, large variance.** 31 home bets isn't enough to be confident. The actual long-run calibration might be better.

---

## Questions

1. **Is this pattern (high-odds home bet overconfidence) something you can diagnose more precisely?** Given I have: calibrated_prob, model_probability (pre-Platt), opening odds, Pinnacle odds, and sharp_consensus per bet — what analysis would best identify the root cause?

2. **Pinnacle as calibration anchor vs signal.** Currently I use Pinnacle-implied probability as an input *signal* (features: `model_prob - pinnacle_implied`). Should I instead be treating Pinnacle as the calibration ground truth — i.e., replace the "market shrinkage" step with specifically weighting toward Pinnacle rather than the average market? What are the pros/cons?

3. **Per-odds-range Platt scaling.** Instead of fitting one Platt sigmoid per market, could I fit one per (market, odds_bucket)? E.g., separate sigmoid for home bets at odds <2.0, 2.0-3.0, 3.0-5.0, >5.0? Is this sensible with ~31 data points per market, or am I overfitting?

4. **What should I actually do right now with 77 settled bets?** Given the Platt refits weekly on settled outcomes, will the calibration self-correct as data accumulates? Or is there a specific architectural fix I should make to the model/calibration pipeline before more bets go in?

5. **Draw market strategy.** I have almost no draw bets in this sample (0 listed). Is it common for draw calibration to be worse than home/away in Poisson models, and what's the standard fix?

6. **Meta-model design check.** My planned next step (~mid-May) is a logistic regression meta-model predicting whether a bet has positive EV, using features: edge, odds_drift, bookmaker_disagreement, overnight_line_move, model_disagreement, league_tier, news_impact_score, odds_volatility. Does this feature set make sense, or are there critical features missing / included features that would cause leakage?

---

## What I'm NOT asking about

- Basic model features (ELO, form, H2H) — the ensemble already has these
- Whether betting is profitable in general — this is for a product, not personal gambling
- Bookmaker access / getting accounts — not relevant here

---

## Format requested

Please give concrete recommendations, not just theory. Where relevant, suggest the specific mathematical or code-level change I should make to the calibration pipeline described above.
