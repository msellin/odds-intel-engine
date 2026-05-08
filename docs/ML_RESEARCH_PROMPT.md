# OddsIntel — ML Research Prompt

Send this prompt to Gemini 1.5 Pro, GPT-4o, Claude Opus, or any strong reasoning model.
Synthesise findings and add new tasks to PRIORITY_QUEUE.md under "ML Model Improvements".

---

I'm building a football match outcome prediction model for sports betting (value bet detection).
Here is the exact current stack:

## ARCHITECTURE

- **Poisson model** (mathematical, no ML): uses each team's historical goals scored/conceded →
  Dixon-Coles corrected P(H/D/A) and P(over 2.5 goals)
- **XGBoost**: 3 separate classifiers (1X2 result, Over/Under 2.5 goals, BTTS)
- **Blend**: 50/50 weighted average of Poisson + XGBoost outputs (weight fit weekly via
  logistic regression on settled bets — but currently produces one global weight, not per market)
- **Calibration**: sklearn CalibratedClassifierCV (isotonic) + Platt scaling on top
- **Fallback tiers**: Tier A (Poisson + XGBoost), Tier B (Poisson only), Tier C (AF predictions fallback)

## TRAINING DATA

- ~5,300 finished matches today, growing to ~14,000 when historical backfill completes
- **42 features**: home/away form (win%, PPG, goals scored/conceded, clean sheets, O/U%, BTTS%),
  home/away venue-specific form, H2H history (5 features), league position + relegation zone
  flags, rest days, league tier
- **TimeSeriesSplit (5-fold)** — correctly respects temporal ordering
- **Rows with ANY missing feature are dropped** — losing ~30-40% of training data
- 3 seasons of data (2023–2025), 60+ leagues, ~$29/mo API-Football data source
- XGBoost hyperparameters: n_estimators=200, max_depth=6, lr=0.05, subsample=0.8 (never tuned)

## SIGNALS COLLECTED BUT NOT IN TRAINING FEATURES

These are computed daily and stored in a `match_signals` table for live betting decisions,
but have never been included as XGBoost training features:

- ELO ratings (per team, updated after every match via standard ELO formula)
- Pinnacle closing odds (vig-removed: market_implied_home/draw/away) — sharpest market in world
- Sharp consensus signal (Pinnacle + Kambi movement direction, -1 to +1)
- Manager change days (days since current coach took over, NULL if >90 days)
- Squad disruption (arrivals in last 60 days per team)
- Venue surface (artificial turf vs grass — binary)
- Injury count + player recurrence index (career injury episodes for confirmed-out players)
- Odds volatility (pre-match movement magnitude)
- Asian Handicap line and movement
- Form vs ELO residual (team over/underperforming expected PPG given ELO)
- Half-time shot dominance (H1 shots vs full-match shots ratio, last 5 games)

## CURRENT BETTING LOGIC

- Edge = (model_prob - fair_market_prob) / fair_market_prob
- Bet when edge > threshold (varies by bot/market, 3-8%)
- 16 paper trading bots running since Apr 27 (~11 days of data so far)
- Markets: 1X2 (home/draw/away), Over/Under 2.5, BTTS
- Pinnacle used as the "sharp anchor" for fair probability

## WHAT I WANT RESEARCHED

**1. Algorithm selection at this data scale**

Given ~5K-14K training samples and 42 tabular features, which ML algorithms are most likely
to outperform XGBoost for football match outcome prediction? Specifically:
- LightGBM vs XGBoost — does native null handling change results meaningfully?
- Logistic regression as a simpler baseline — at what sample size does XGBoost clearly win?
- Small neural nets (2-3 layers) — feasible at 14K samples or overfitting risk too high?
- Isotonic regression ensembles
- Any recent football-specific ML papers (2020-2024) with benchmark comparisons?

**2. Which unmodelled signals have strongest evidence**

Of the signals we collect but don't train on (listed above), which have the strongest
evidence in sports betting or football prediction literature for predicting outcomes beyond
what form/position already captures? Please rank by expected marginal lift with citations
where available. Particularly interested in:
- Pinnacle odds as a feature (not just as an anchor) — literature on market-augmented models
- ELO ratings — do they add lift beyond form/position features?
- Manager change signal — is the post-sacking home bounce reliably detectable in data?

**3. Optimal Poisson/ML blend**

What is the academic consensus on optimal Poisson/ML blend weights for football?
- Is 50/50 well-supported or is there evidence for a different ratio?
- Should the weight differ by market (O/U vs 1X2 vs BTTS)?
- Should it differ by data tier (top leagues vs lower leagues)?

**4. Missing data strategies**

The current model drops rows with any missing feature (~30-40% data loss). What imputation
strategies work best for football prediction models where features are missing because:
- A team has played fewer matches (early season, newly promoted)
- A signal simply didn't exist yet (new endpoint added mid-season)

Options to evaluate: LightGBM native handling, mean/median imputation, KNN imputation,
multiple imputation, indicator variables for missingness.

**5. Loss function for value betting**

For a value betting system specifically (not accuracy-maximising), what training objective
gives better calibration at the tails vs the centre?
- Is log_loss the right objective, or Brier score, pinball loss, or Kelly-weighted log_loss?
- The model only bets when edge >3-8% — so calibration at odds 1.5-2.5 matters more than at odds 1.1
- Is there precedent for training with a custom XGBoost objective that weights high-edge
  prediction errors more heavily?

**6. Per-league specialisation**

Is there evidence for training separate models per league tier vs one global model?
- At what sample size does per-league specialisation become worthwhile?
- Is league tier a sufficient proxy or do specific leagues need their own models?

## FORMAT REQUESTED

Please give **ranked, specific, actionable recommendations** — not general ML advice.
The goal is 2-3% improvement in ROI on value bets, not winning a Kaggle competition.
For each recommendation, estimate: implementation effort (hours), expected lift (low/medium/high),
and confidence in the recommendation (low/medium/high based on literature support).
